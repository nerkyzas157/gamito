"""Recipe assignment node backed by ``LocalRecipeIndex``."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from gamito.models.meal import (
    Ingredient,
    Meal,
    MealSlot,
    MealSource,
    MealType,
    Nutrition,
    parse_json_dict,
    parse_json_list,
)
from gamito.models.planning import BudgetMealAllocation, BudgetPlan, PlanConfig
from gamito.models.profile import UserContext
from gamito.recommendation.updater import preference_query_suffix
from gamito.retrieval.filters import RecipeSearchContext
from gamito.retrieval.index import LocalRecipeIndex, NoCandidates, RecipeCandidate

_DIETARY_FLAG_COLUMNS = (
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
    "is_nut_free",
    "is_peanut_free",
    "is_dairy_free",
    "is_gluten_free",
    "is_shellfish_free",
    "is_soy_free",
    "is_egg_free",
    "is_fish_free",
)


class AssignmentError(RuntimeError):
    """Raised when no local recipe can fill a requested slot."""


class AssignmentNode:
    """Assign recipes to pending budget slots using batched local retrieval."""

    def __init__(
        self,
        recipe_index: LocalRecipeIndex,
        *,
        candidate_pool_size: int = 25,
    ) -> None:
        self._recipe_index = recipe_index
        self._candidate_pool_size = candidate_pool_size

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plan_config = _coerce_plan_config(state["plan_config"])
        user_context = _coerce_user_context(state["user_context"])
        budget_plan = _coerce_budget_plan(state["budget_plan"])
        rng = _coerce_rng(state)
        meals_by_key = _coerce_meals(state.get("meals_by_key", {}))
        pending = list(state.get("pending_meal_keys") or budget_plan.by_key())
        excluded = set(str(recipe_id) for recipe_id in state.get("excluded_recipe_ids", []))

        allocations = budget_plan.by_key()
        pending_allocations = [
            allocations[key]
            for key in pending
            if key in allocations and allocations[key].meal_type != MealType.LEFTOVER
        ]
        leftovers = [
            allocations[key]
            for key in pending
            if key in allocations and allocations[key].meal_type == MealType.LEFTOVER
        ]

        new_assignments = self._assign_new_slots(
            pending_allocations,
            plan_config=plan_config,
            user_context=user_context,
            excluded_recipe_ids=excluded,
            already_used_recipe_ids=_used_new_recipe_ids(meals_by_key.values()),
            leftover_counts=_leftover_counts(budget_plan),
            rng=rng,
        )
        meals_by_key.update({meal.key: meal for meal in new_assignments})

        for allocation in leftovers:
            source = meals_by_key.get(allocation.source_slot_key or "")
            if source is not None:
                meals_by_key[allocation.key] = _clone_leftover(source, allocation)

        return {
            "meals_by_key": dict(sorted(meals_by_key.items())),
            "pending_meal_keys": [],
            "rng": rng,
        }

    def _assign_new_slots(
        self,
        allocations: list[BudgetMealAllocation],
        *,
        plan_config: PlanConfig,
        user_context: UserContext,
        excluded_recipe_ids: set[str],
        already_used_recipe_ids: set[str],
        leftover_counts: Counter[str],
        rng: np.random.Generator,
    ) -> list[Meal]:
        if not allocations:
            return []

        queries = [_query_for_slot(allocation, user_context) for allocation in allocations]
        contexts = [
            _search_context(
                allocation,
                plan_config=plan_config,
                user_context=user_context,
                excluded_recipe_ids=excluded_recipe_ids | already_used_recipe_ids,
                source_servings=_source_servings(allocation, leftover_counts),
            )
            for allocation in allocations
        ]
        pools = self._search_many_with_relaxation(queries, contexts)

        used_recipe_ids = set(already_used_recipe_ids)
        assigned: list[Meal] = []
        for allocation, candidates in zip(allocations, pools, strict=True):
            ordered = _stable_score_order(candidates, rng)
            candidate = _pick_distinct_candidate(ordered, used_recipe_ids, excluded_recipe_ids)
            if candidate is None:
                raise AssignmentError(f"no candidates for {allocation.key}")
            used_recipe_ids.add(candidate.recipe_id)
            source_servings = _source_servings(allocation, leftover_counts)
            assigned.append(_candidate_to_meal(candidate, allocation, source_servings))
        return assigned

    def _search_many_with_relaxation(
        self,
        queries: list[str],
        contexts: list[RecipeSearchContext],
    ) -> list[list[RecipeCandidate]]:
        try:
            return self._recipe_index.search_many(
                queries,
                contexts,
                k=max(self._candidate_pool_size, len(queries) * 3),
            )
        except NoCandidates:
            relaxed = [
                RecipeSearchContext(
                    max_time_min=ctx.max_time_min,
                    max_price_per_serving=None,
                    allergies=ctx.allergies,
                    dietary_pref=ctx.dietary_pref,
                    owned_tools=ctx.owned_tools,
                    preferred_cuisines=ctx.preferred_cuisines,
                    exclude_recipe_ids=ctx.exclude_recipe_ids,
                    min_healthiness_score=ctx.min_healthiness_score,
                    course=ctx.course,
                )
                for ctx in contexts
            ]
            return self._recipe_index.search_many(
                queries,
                relaxed,
                k=max(self._candidate_pool_size, len(queries) * 3),
            )


def _query_for_slot(allocation: BudgetMealAllocation, user_context: UserContext) -> str:
    parts = [allocation.meal_slot.value]
    if allocation.meal_slot == MealSlot.BREAKFAST:
        parts.extend(["quick", "breakfast"])
    else:
        parts.extend(["main", "meal"])
    parts.extend(user_context.cuisine_hints[:3])
    parts.extend(user_context.positive_tags[:5])
    if user_context.dietary_pref:
        parts.append(user_context.dietary_pref)
    query = " ".join(dict.fromkeys(part for part in parts if part))
    suffix = preference_query_suffix(user_context.positive_tags)
    return f"{query} {suffix}".strip()


def _search_context(
    allocation: BudgetMealAllocation,
    *,
    plan_config: PlanConfig,
    user_context: UserContext,
    excluded_recipe_ids: set[str],
    source_servings: int,
) -> RecipeSearchContext:
    time_ceiling = plan_config.max_time_min or user_context.time_ceiling_minutes
    price_cap = None
    if allocation.budget_eur > 0 and source_servings > 0:
        price_cap = round(allocation.budget_eur / source_servings, 2)
    return RecipeSearchContext.from_context(
        user_context,
        max_time_min=time_ceiling,
        max_price_per_serving=price_cap,
        exclude_recipe_ids=tuple(sorted(excluded_recipe_ids)),
        preferred_cuisines=user_context.cuisine_hints,
        course="breakfast" if allocation.meal_slot == MealSlot.BREAKFAST else "main",
    )


def _stable_score_order(
    candidates: list[RecipeCandidate],
    rng: np.random.Generator,
) -> list[RecipeCandidate]:
    ordered: list[RecipeCandidate] = []
    index = 0
    while index < len(candidates):
        score = round(candidates[index].score, 10)
        group: list[RecipeCandidate] = []
        while index < len(candidates) and round(candidates[index].score, 10) == score:
            group.append(candidates[index])
            index += 1
        if len(group) > 1:
            order = rng.permutation(len(group))
            ordered.extend(group[int(i)] for i in order)
        else:
            ordered.extend(group)
    return ordered


def _pick_distinct_candidate(
    candidates: list[RecipeCandidate],
    used_recipe_ids: set[str],
    excluded_recipe_ids: set[str],
) -> RecipeCandidate | None:
    for candidate in candidates:
        if candidate.recipe_id not in used_recipe_ids and candidate.recipe_id not in excluded_recipe_ids:
            return candidate
    for candidate in candidates:
        if candidate.recipe_id not in excluded_recipe_ids:
            return candidate
    return None


def _candidate_to_meal(
    candidate: RecipeCandidate,
    allocation: BudgetMealAllocation,
    servings: int,
) -> Meal:
    metadata = candidate.metadata
    price_per_serving = _price_per_serving(metadata)
    total_cost = round(price_per_serving * servings, 2)
    return Meal(
        day_number=allocation.day_number,
        meal_slot=allocation.meal_slot,
        recipe_id=candidate.recipe_id,
        recipe_title=candidate.title,
        servings=servings,
        allocated_budget_eur=allocation.budget_eur,
        estimated_cost_total_eur=total_cost,
        estimated_cost_per_serving_eur=round(price_per_serving, 2),
        meal_type=allocation.meal_type,
        source_slot_key=allocation.source_slot_key,
        source=MealSource.CUSTOM if candidate.source == "custom" else MealSource.DATASET,
        prep_time_min=_int_or_none(metadata.get("est_prep_time_min")),
        cook_time_min=_int_or_none(metadata.get("est_cook_time_min")),
        total_time_min=_int_or_none(metadata.get("total_time_min")),
        difficulty=_string_or_none(metadata.get("difficulty")),
        cuisine_list=_string_list(metadata.get("cuisine_list")),
        course_list=_string_list(metadata.get("course_list")),
        kitchen_tools=_string_list(metadata.get("kitchen_tools")),
        ingredients=_ingredients_from_metadata(metadata),
        directions=[str(step) for step in parse_json_list(metadata.get("directions_json") or metadata.get("directions"))],
        nutrition_per_serving=_nutrition_from_metadata(metadata),
        dietary_flags=_dietary_flags(metadata),
        healthiness_score=_int_or_none(metadata.get("healthiness_score")),
        raw_candidate=metadata,
    )


def _clone_leftover(source: Meal, allocation: BudgetMealAllocation) -> Meal:
    clone = source.model_copy(deep=True)
    return clone.model_copy(
        update={
            "day_number": allocation.day_number,
            "meal_slot": allocation.meal_slot,
            "servings": allocation.servings,
            "allocated_budget_eur": 0.0,
            "estimated_cost_total_eur": 0.0,
            "estimated_cost_per_serving_eur": 0.0,
            "meal_type": MealType.LEFTOVER,
            "source_slot_key": allocation.source_slot_key,
        },
        deep=True,
    )


def _ingredients_from_metadata(metadata: Mapping[str, Any]) -> list[Ingredient]:
    raw_items = parse_json_list(metadata.get("ingredients_json") or metadata.get("ingredients"))
    ingredients: list[Ingredient] = []
    for item in raw_items:
        if isinstance(item, Mapping):
            name = str(item.get("name") or item.get("ingredient") or item.get("raw") or "").strip()
            if not name:
                continue
            ingredients.append(
                Ingredient(
                    name=name,
                    amount=_string_or_none(item.get("amount")),
                    quantity=_float_or_none(item.get("quantity")),
                    unit=_string_or_none(item.get("unit")),
                    estimated_price_eur=_float_or_none(item.get("estimated_price_eur") or item.get("est_price_eur")),
                    raw=_string_or_none(item.get("raw")),
                    canonical_name=_string_or_none(item.get("canonical") or item.get("canonical_name")),
                )
            )
        else:
            text = str(item).strip()
            if text:
                ingredients.append(Ingredient(name=text, raw=text))
    return ingredients


def _nutrition_from_metadata(metadata: Mapping[str, Any]) -> Nutrition | None:
    raw = (
        metadata.get("nutrition_per_serving_json")
        or metadata.get("nutrition_per_serving")
        or metadata.get("nutrition_json")
    )
    values = parse_json_dict(raw)
    if not values:
        return None
    mapped = {
        "calories_kcal": values.get("calories_kcal") or values.get("kcal") or values.get("calories"),
        "protein_g": values.get("protein_g") or values.get("protein"),
        "carbs_g": values.get("carbs_g") or values.get("carbohydrates_g") or values.get("carbs"),
        "fat_g": values.get("fat_g") or values.get("fat"),
    }
    return Nutrition.model_validate({key: value for key, value in mapped.items() if value is not None})


def _dietary_flags(metadata: Mapping[str, Any]) -> dict[str, bool]:
    return {
        column: bool(value)
        for column in _DIETARY_FLAG_COLUMNS
        if (value := _bool_or_none(metadata.get(column))) is not None
    }


def _price_per_serving(metadata: Mapping[str, Any]) -> float:
    explicit = _float_or_none(metadata.get("price_per_serving_eur"))
    if explicit is not None:
        return max(explicit, 0.0)
    total = _float_or_none(metadata.get("price_total_eur") or metadata.get("cost_total_eur"))
    servings = _float_or_none(metadata.get("est_servings") or metadata.get("servings"))
    if total is not None and servings and servings > 0:
        return max(total / servings, 0.0)
    return 0.0


def _source_servings(allocation: BudgetMealAllocation, counts: Counter[str]) -> int:
    if allocation.meal_type == MealType.MEAL_PREP:
        return allocation.servings * (1 + counts[allocation.key])
    return allocation.servings


def _leftover_counts(budget_plan: BudgetPlan) -> Counter[str]:
    return Counter(
        allocation.source_slot_key
        for allocation in budget_plan.allocations
        if allocation.meal_type == MealType.LEFTOVER and allocation.source_slot_key
    )


def _used_new_recipe_ids(meals: Any) -> set[str]:
    return {
        meal.recipe_id
        for meal in meals
        if meal.recipe_id and meal.meal_type != MealType.LEFTOVER
    }


def _coerce_plan_config(value: Any) -> PlanConfig:
    return value if isinstance(value, PlanConfig) else PlanConfig.model_validate(value)


def _coerce_user_context(value: Any) -> UserContext:
    return value if isinstance(value, UserContext) else UserContext.model_validate(value)


def _coerce_budget_plan(value: Any) -> BudgetPlan:
    return value if isinstance(value, BudgetPlan) else BudgetPlan.model_validate(value)


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}


def _coerce_rng(state: Mapping[str, Any]) -> np.random.Generator:
    rng = state.get("rng")
    if isinstance(rng, np.random.Generator):
        return rng
    return np.random.default_rng(int(state.get("seed", 1337)))


def _string_list(value: Any) -> list[str]:
    return [str(item).strip().lower() for item in parse_json_list(value) if str(item).strip()]


def _string_or_none(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(parsed) else parsed


def _int_or_none(value: Any) -> int | None:
    parsed = _float_or_none(value)
    return None if parsed is None else int(round(parsed))


def _bool_or_none(value: Any) -> bool | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
