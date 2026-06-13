"""Core plan-edit operations used by MCP wrappers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from gamito.config import INDEX_DIR
from gamito.db.plans import insert_meals, load_meal_plan, record_plan_edit
from gamito.mcp.errors import err
from gamito.mcp.slots import parse_slot_key
from gamito.models.meal import Meal, MealPlan, MealSlot, MealType
from gamito.models.planning import BudgetMealAllocation, PlanConfig, ValidationStatus
from gamito.planning.graph import meal_plan_response
from gamito.planning.nodes.assignment import _candidate_to_meal, _clone_leftover
from gamito.planning.nodes.shopping import build_shopping_list
from gamito.planning.nodes.validator import validate_meal_plan
from gamito.recommendation.engine import build_user_context
from gamito.rendering.compact import render_compact_plan
from gamito.retrieval.index import LocalRecipeIndex


def swap_meal(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    slot_key: str,
    query_en: str,
    max_price_eur: float | None = None,
    index_dir: str | Path = INDEX_DIR,
    recipe_index: LocalRecipeIndex | None = None,
) -> dict[str, Any]:
    """Replace one persisted meal with the best constrained recipe candidate."""

    if not query_en or not query_en.strip():
        raise err("INVALID_INPUT", "query_en is required")
    if max_price_eur is not None and max_price_eur <= 0:
        raise err("INVALID_INPUT", "max_price_eur must be positive")

    stored, plan, meals = _load_plan_state(conn, plan_id)
    old = _meal_for_slot(meals, slot_key)
    ctx = build_user_context(stored["profile_id"], conn=conn)
    index = recipe_index or LocalRecipeIndex.load(index_dir)
    excluded = [meal.recipe_id for meal in meals if meal.recipe_id]
    price_cap = round(max_price_eur / old.servings, 2) if max_price_eur else None
    candidates = index.search(
        query_en,
        ctx,
        k=10,
        max_price_per_serving=price_cap,
        exclude_recipe_ids=excluded,
        course="breakfast" if old.meal_slot == MealSlot.BREAKFAST else "main",
    )
    if not candidates:
        raise err("NO_CANDIDATES", f"no candidates for {slot_key}", constraints=query_en)

    allocation = BudgetMealAllocation(
        day_number=old.day_number,
        meal_slot=old.meal_slot,
        budget_eur=old.allocated_budget_eur or max_price_eur or old.estimated_cost_total_eur,
        servings=old.servings,
    )
    new = _candidate_to_meal(candidates[0], allocation, old.servings)
    updated = _replace_meal_and_dependents(meals, old, new)
    return _validate_persist_and_respond(
        conn,
        stored=stored,
        plan_id=plan_id,
        meals=updated,
        edit_type="swap",
        slot_key=slot_key,
        payload={"old": old.recipe_title, "new": new.recipe_title},
        text_prefix=f"{slot_key}: {old.recipe_title} -> {new.recipe_title}",
        response_extra={"old_meal": _meal_summary(old), "new_meal": _meal_summary(new)},
    )


def rescale_meal(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    slot_key: str,
    servings: int,
) -> dict[str, Any]:
    """Rescale one meal's serving count and derived costs."""

    if servings < 1:
        raise err("INVALID_INPUT", "servings must be >= 1")
    stored, plan, meals = _load_plan_state(conn, plan_id)
    old = _meal_for_slot(meals, slot_key)
    if old.servings == servings:
        new = old
    else:
        ratio = servings / old.servings
        ingredients = [
            ingredient.model_copy(
                update={
                    "estimated_price_eur": (
                        round(ingredient.estimated_price_eur * ratio, 2)
                        if ingredient.estimated_price_eur is not None
                        else None
                    )
                }
            )
            for ingredient in old.ingredients
        ]
        new = old.model_copy(
            update={
                "servings": servings,
                "estimated_cost_total_eur": round(old.estimated_cost_per_serving_eur * servings, 2),
                "ingredients": ingredients,
            },
            deep=True,
        )
    updated = _replace_meal_and_dependents(meals, old, new)
    return _validate_persist_and_respond(
        conn,
        stored=stored,
        plan_id=plan_id,
        meals=updated,
        edit_type="rescale",
        slot_key=slot_key,
        payload={"old_servings": old.servings, "new_servings": servings},
        text_prefix=f"{slot_key}: {old.recipe_title} rescaled to {servings} servings",
        response_extra={"meal": _meal_summary(new)},
    )


def _load_plan_state(
    conn: sqlite3.Connection,
    plan_id: str,
) -> tuple[dict[str, Any], MealPlan, list[Meal]]:
    row = conn.execute("SELECT * FROM meal_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if row is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
    plan = load_meal_plan(conn, plan_id)
    if plan is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
    return dict(row), plan, list(plan.meals)


def _meal_for_slot(meals: list[Meal], key: str) -> Meal:
    parse_slot_key(key)
    for meal in meals:
        if meal.key == key:
            return meal
    raise err(
        "SLOT_NOT_FOUND",
        f"slot not found: {key}",
        slot_keys=", ".join(meal.key for meal in meals) or "(none)",
    )


def _replace_meal_and_dependents(meals: list[Meal], old: Meal, new: Meal) -> list[Meal]:
    updated: list[Meal] = []
    for meal in meals:
        if meal.key == old.key:
            updated.append(new)
        elif meal.source_slot_key == old.key and meal.meal_type == MealType.LEFTOVER:
            allocation = BudgetMealAllocation(
                day_number=meal.day_number,
                meal_slot=meal.meal_slot,
                budget_eur=0,
                servings=meal.servings,
                meal_type=MealType.LEFTOVER,
                source_slot_key=old.key,
            )
            updated.append(_clone_leftover(new, allocation))
        else:
            updated.append(meal)
    return updated


def _validate_persist_and_respond(
    conn: sqlite3.Connection,
    *,
    stored: dict[str, Any],
    plan_id: str,
    meals: list[Meal],
    edit_type: str,
    slot_key: str,
    payload: dict[str, Any],
    text_prefix: str,
    response_extra: dict[str, Any],
) -> dict[str, Any]:
    ctx = build_user_context(stored["profile_id"], conn=conn)
    config = PlanConfig(
        total_budget_eur=stored["total_budget_eur"],
        servings=stored["servings"],
        num_days=stored["num_days"],
        meals_per_day=stored["meals_per_day"],
        max_time_min=stored["max_time_min"],
    )
    validation = validate_meal_plan(meals, config, ctx, retry_count=2)
    issues = [issue.message for issue in validation.issues if issue.severity == "error"]
    if validation.status == ValidationStatus.FAIL and issues:
        raise err("VALIDATION_FAILED", "; ".join(issues), issues="; ".join(issues))

    shopping = build_shopping_list(meals, pantry_canonicals=ctx.pantry_canonicals)
    warnings = [issue.message for issue in validation.issues]
    text = render_compact_plan(
        meals=meals,
        shopping_list=shopping,
        requested_budget_eur=stored["total_budget_eur"],
        language=ctx.language,
        warnings=warnings,
    )
    plan = MealPlan(
        user_id=stored["profile_id"],
        meals=meals,
        shopping_list=shopping,
        total_budget_eur=stored["total_budget_eur"],
        total_estimated_cost_eur=shopping.total_estimated_cost_eur,
        language=ctx.language,
        formatted_text=f"{text_prefix}\n{text}",
        warnings=warnings,
    )
    _persist_meals(conn, plan_id=plan_id, meals=meals, total_cost=shopping.total_estimated_cost_eur, warnings=warnings)
    record_plan_edit(conn, plan_id=plan_id, slot_key=slot_key, edit_type=edit_type, payload=payload)
    response = meal_plan_response(plan_id, plan, config, seed=stored["seed"])
    response_extra.update(
        {
            "budget": response["budget"],
            "shopping_list": response["shopping_list"],
            "warnings": response["warnings"],
            "text": response["text"],
        }
    )
    return response_extra


def _persist_meals(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    meals: list[Meal],
    total_cost: float,
    warnings: list[str],
) -> None:
    with conn:
        conn.execute("DELETE FROM plan_meals WHERE plan_id = ?", (plan_id,))
        insert_meals(conn, plan_id=plan_id, meals=meals)
        conn.execute(
            """
            UPDATE meal_plans
            SET total_cost_eur = ?, warnings_json = ?, updated_at = datetime('now')
            WHERE plan_id = ?
            """,
            (round(total_cost, 2), json.dumps(warnings), plan_id),
        )


def _meal_summary(meal: Meal) -> dict[str, Any]:
    return {
        "slot_key": meal.key,
        "recipe_id": meal.recipe_id,
        "title": meal.recipe_title,
        "servings": meal.servings,
        "cost_eur": round(meal.estimated_cost_total_eur, 2),
    }
