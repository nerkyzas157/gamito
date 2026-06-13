"""LangGraph orchestration and public planning entry points."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from langgraph.graph import END, START, StateGraph

from gamito.config import DB_PATH, INDEX_DIR
from gamito.db.connection import connect
from gamito.db.plans import save_plan
from gamito.models.meal import Ingredient, Meal, MealPlan, MealSlot, Nutrition, ShoppingList
from gamito.models.planning import PlanConfig, ValidationResult
from gamito.models.profile import UserContext
from gamito.planning.nodes.assignment import AssignmentNode
from gamito.planning.nodes.budget import BudgetPlannerNode
from gamito.planning.nodes.render import RenderNode
from gamito.planning.nodes.shopping import ShoppingListNode
from gamito.planning.nodes.validator import ValidatorNode
from gamito.planning.state import PlanningState
from gamito.recommendation.engine import build_user_context
from gamito.retrieval.index import LocalRecipeIndex

MAX_REPLAN_RETRIES = 2


def resolve_seed(override: int | None = None) -> int:
    """Resolve deterministic planner seed from explicit input or environment."""

    if override is not None:
        return int(override)
    return int(os.environ.get("GAMITO_SEED", "1337"))


def tie_break_rng(seed: int) -> np.random.Generator:
    """Return the only RNG used by the deterministic planner."""

    return np.random.default_rng(seed)


def build_planning_graph(recipe_index: LocalRecipeIndex):
    """Build the LangGraph planning pipeline."""

    builder = StateGraph(PlanningState)
    builder.add_node("budget", _sync_node(BudgetPlannerNode()))
    builder.add_node("assignment", _sync_node(AssignmentNode(recipe_index)))
    builder.add_node("validator", _sync_node(ValidatorNode()))
    builder.add_node("prepare_replan", _sync_node(PrepareReplanNode()))
    builder.add_node("shopping", _sync_node(ShoppingListNode()))
    builder.add_node("render", _sync_node(RenderNode()))

    builder.add_edge(START, "budget")
    builder.add_edge("budget", "assignment")
    builder.add_edge("assignment", "validator")
    builder.add_conditional_edges(
        "validator",
        _route_after_validation,
        {"replan": "prepare_replan", "finish": "shopping"},
    )
    builder.add_edge("prepare_replan", "assignment")
    builder.add_edge("shopping", "render")
    builder.add_edge("render", END)
    return builder.compile()


def run_planning_graph(
    *,
    plan_config: PlanConfig,
    user_context: UserContext,
    recipe_index: LocalRecipeIndex,
    seed: int | None = None,
    exclude_recipe_ids: Iterable[str] = (),
    preserved_slots: Mapping[str, Meal] | None = None,
) -> MealPlan:
    """Run the full local graph and return the structured meal plan."""

    resolved_seed = resolve_seed(seed)
    preserved = dict(preserved_slots or {})
    initial_state: PlanningState = {
        "plan_config": plan_config,
        "user_context": user_context,
        "meals_by_key": preserved,
        "excluded_recipe_ids": list(dict.fromkeys(str(item) for item in exclude_recipe_ids)),
        "retry_count": 0,
        "seed": resolved_seed,
        "rng": tie_break_rng(resolved_seed),
        "preserved_slots": preserved,
    }
    graph = build_planning_graph(recipe_index)
    final_state = graph.invoke(initial_state)
    meals = _ordered_meals(final_state.get("meals_by_key", {}).values())
    shopping_list = _coerce_shopping_list(final_state.get("shopping_list"))
    return MealPlan(
        user_id=user_context.profile_id,
        meals=meals,
        shopping_list=shopping_list,
        total_budget_eur=plan_config.total_budget_eur,
        total_estimated_cost_eur=shopping_list.total_estimated_cost_eur,
        language=user_context.language,
        formatted_text=final_state.get("formatted_text"),
        warnings=list(final_state.get("warnings") or []),
    )


def generate_meal_plan(
    *,
    profile_id: str,
    budget_eur: float,
    servings: int,
    num_days: int,
    meals_per_day: int,
    max_time_min: int | None = None,
    exclude_recipe_ids: Iterable[str] = (),
    seed: int | None = None,
    db_path: str | Path = DB_PATH,
    conn: sqlite3.Connection | None = None,
    recipe_index: LocalRecipeIndex | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Generate, persist, and serialize a meal plan for MCP wrappers."""

    owned_conn = None
    if conn is None:
        owned_conn = connect(db_path)
        conn = owned_conn
    try:
        user_context = build_user_context(profile_id, conn=conn)
        plan_config = PlanConfig(
            total_budget_eur=budget_eur,
            servings=servings,
            num_days=num_days,
            meals_per_day=meals_per_day,
            max_time_min=max_time_min,
        )
        resolved_seed = resolve_seed(seed)
        index = recipe_index or LocalRecipeIndex.load(INDEX_DIR)
        plan = run_planning_graph(
            plan_config=plan_config,
            user_context=user_context,
            recipe_index=index,
            seed=resolved_seed,
            exclude_recipe_ids=exclude_recipe_ids,
        )
        plan_id = None
        if persist:
            plan_id = save_plan(
                conn,
                profile_id=profile_id,
                plan=plan,
                servings=servings,
                num_days=num_days,
                meals_per_day=meals_per_day,
                max_time_min=max_time_min,
                seed=resolved_seed,
            )
        return meal_plan_response(plan_id or "", plan, plan_config, seed=resolved_seed)
    finally:
        if owned_conn is not None:
            owned_conn.close()


class PrepareReplanNode:
    """Prepare assignment state for one bounded replan attempt."""

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        result = _coerce_validation_result(state.get("validation_result"))
        retry_count = int(state.get("retry_count", 0)) + 1
        meals_by_key = _coerce_meals(state.get("meals_by_key", {}))
        replan_keys = set(result.replan_keys)

        for key, meal in list(meals_by_key.items()):
            if key in replan_keys:
                continue
            if meal.source_slot_key in replan_keys:
                replan_keys.add(key)

        excluded = list(state.get("excluded_recipe_ids", []))
        for meal in meals_by_key.values():
            if meal.recipe_id and meal.meal_type.value == "new":
                excluded.append(meal.recipe_id)

        for key in replan_keys:
            meals_by_key.pop(key, None)

        return {
            "meals_by_key": meals_by_key,
            "pending_meal_keys": list(replan_keys),
            "excluded_recipe_ids": list(dict.fromkeys(excluded)),
            "retry_count": retry_count,
        }


def meal_plan_response(
    plan_id: str,
    plan: MealPlan,
    plan_config: PlanConfig,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """Serialize a ``MealPlan`` into the MCP planning response shape."""

    shopping = plan.shopping_list or ShoppingList()
    estimated = shopping.total_estimated_cost_eur
    requested = plan_config.total_budget_eur
    return {
        "plan_id": plan_id,
        "status": "complete",
        "meals": [_meal_payload(meal) for meal in _ordered_meals(plan.meals)],
        "shopping_list": {
            "items": [_shopping_item_payload(item) for item in shopping.items],
            "pantry_items": [_shopping_item_payload(item) for item in shopping.pantry_items],
            "total_eur": round(estimated, 2),
        },
        "budget": {
            "requested_eur": round(requested, 2),
            "estimated_eur": round(estimated, 2),
            "delta_eur": round(estimated - requested, 2),
            "within_tolerance": estimated <= round(requested * 1.15, 2),
        },
        "warnings": plan.warnings,
        "seed": seed,
        "text": plan.formatted_text or "",
    }


def _route_after_validation(state: Mapping[str, Any]) -> str:
    result = _coerce_validation_result(state.get("validation_result"))
    retry_count = int(state.get("retry_count", 0))
    if result.replan_keys and retry_count < MAX_REPLAN_RETRIES:
        return "replan"
    return "finish"


def _sync_node(node: Any):
    def invoke(state: Mapping[str, Any]) -> dict[str, Any]:
        return _resolve_sync(node(state))

    return invoke


def _resolve_sync(value: Any) -> Any:
    if not hasattr(value, "send"):
        return value
    try:
        value.send(None)
    except StopIteration as exc:
        return exc.value
    raise RuntimeError("planning node awaited asynchronously during sync graph invoke")


def _meal_payload(meal: Meal) -> dict[str, Any]:
    return {
        "slot_key": meal.key,
        "day": meal.day_number,
        "slot": meal.meal_slot.value,
        "recipe_id": meal.recipe_id,
        "title": meal.recipe_title,
        "meal_type": meal.meal_type.value,
        "source": meal.source.value,
        "source_slot_key": meal.source_slot_key,
        "time_min": meal.total_time_min,
        "cost_eur": round(meal.estimated_cost_total_eur, 2),
        "cost_per_serving_eur": round(meal.estimated_cost_per_serving_eur, 2),
        "nutrition_per_serving": _nutrition_payload(meal.nutrition_per_serving),
        "ingredients": [_ingredient_payload(ingredient) for ingredient in meal.ingredients],
        "directions": meal.directions,
        "tools": meal.kitchen_tools,
    }


def _shopping_item_payload(item: Any) -> dict[str, Any]:
    return {
        "canonical": item.name,
        "amount": item.amount,
        "est_price_eur": round(item.estimated_price_eur, 2),
        "meal_keys": item.meal_keys,
    }


def _ingredient_payload(ingredient: Ingredient) -> dict[str, Any]:
    return {
        "name": ingredient.name,
        "amount": ingredient.amount,
        "canonical": ingredient.canonical_name,
        "est_price_eur": ingredient.estimated_price_eur,
    }


def _nutrition_payload(nutrition: Nutrition | None) -> dict[str, float | None]:
    if nutrition is None:
        return {"kcal": None, "protein_g": None, "carbs_g": None, "fat_g": None}
    return {
        "kcal": nutrition.calories_kcal,
        "protein_g": nutrition.protein_g,
        "carbs_g": nutrition.carbs_g,
        "fat_g": nutrition.fat_g,
    }


def _ordered_meals(meals: Iterable[Meal]) -> list[Meal]:
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
    return sorted(
        meals,
        key=lambda meal: (meal.day_number, order.get(meal.meal_slot.value, 99)),
    )


def _coerce_shopping_list(value: Any) -> ShoppingList:
    return value if isinstance(value, ShoppingList) else ShoppingList.model_validate(value or {})


def _coerce_validation_result(value: Any) -> ValidationResult:
    return value if isinstance(value, ValidationResult) else ValidationResult.model_validate(value)


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}
