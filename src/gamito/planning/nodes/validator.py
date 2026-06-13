"""Budget, allergy, variety, and language validation node."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gamito.models.meal import Meal, MealSlot, MealType
from gamito.models.planning import (
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)
from gamito.models.planning import PlanConfig
from gamito.models.profile import UserContext

# Chronological order used to decide which duplicate-recipe slot stays
# untouched. We keep the earliest (breakfast → lunch → dinner → snack) and
# replan the rest. Duplicated here so the validator has no formatter dependency.
_SLOT_ORDER: dict[MealSlot, int] = {
    MealSlot.BREAKFAST: 0,
    MealSlot.LUNCH: 1,
    MealSlot.DINNER: 2,
    MealSlot.SNACK: 3,
}

ALLERGEN_TO_FLAG: dict[str, str] = {
    "dairy": "is_dairy_free",
    "gluten": "is_gluten_free",
    "nuts": "is_nut_free",
    "peanuts": "is_peanut_free",
    "shellfish": "is_shellfish_free",
    "soy": "is_soy_free",
    "eggs": "is_egg_free",
    "fish": "is_fish_free",
}


class ValidatorNode:
    """Validate a complete or partial meal plan."""

    def __init__(self, budget_overage_tolerance: float = 0.15) -> None:
        self._budget_overage_tolerance = budget_overage_tolerance

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plan_config = _coerce_plan_config(state["plan_config"])
        user_context = _coerce_user_context(state["user_context"])
        meals = _coerce_meals(state.get("meals_by_key", {}))
        retry_count = int(state.get("retry_count", 0))

        result = validate_meal_plan(
            meals=list(meals.values()),
            plan_config=plan_config,
            user_context=user_context,
            budget_overage_tolerance=self._budget_overage_tolerance,
            retry_count=retry_count,
        )
        return {"validation_result": result}


def validate_meal_plan(
    meals: list[Meal],
    plan_config: PlanConfig,
    user_context: UserContext,
    budget_overage_tolerance: float = 0.15,
    retry_count: int = 0,
) -> ValidationResult:
    """Run all MVP validation checks and identify meals to re-plan."""

    issues: list[ValidationIssue] = []
    replan_keys: list[str] = []
    total_cost = round(sum(meal.estimated_cost_total_eur for meal in meals), 2)
    budget_limit = round(
        plan_config.total_budget_eur * (1 + budget_overage_tolerance), 2
    )

    if total_cost > budget_limit:
        issues.append(
            ValidationIssue(
                code="budget_total",
                message=(
                    f"Estimated total EUR {total_cost:.2f} exceeds allowed "
                    f"budget EUR {budget_limit:.2f}."
                ),
            )
        )
        replan_keys.extend(_expensive_meal_keys(meals))

    for meal in meals:
        for allergen in user_context.negative_tags:
            flag = ALLERGEN_TO_FLAG.get(allergen.strip().lower())
            if flag and meal.dietary_flags.get(flag) is False:
                issues.append(
                    ValidationIssue(
                        code="allergy",
                        message=f"{meal.recipe_title} may violate allergen: {allergen}.",
                        meal_key=meal.key,
                    )
                )
                replan_keys.append(meal.key)
            elif _contains_disliked_term(meal, allergen):
                issues.append(
                    ValidationIssue(
                        code="disliked_ingredient",
                        message=f"{meal.recipe_title} appears to contain {allergen}.",
                        meal_key=meal.key,
                    )
                )
                replan_keys.append(meal.key)

    # Variety check — flags ONLY unintentional repeats. Meals that the budget
    # planner explicitly marked as ``leftover`` or ``meal_prep`` are designed
    # to share their recipe with their source slot, so they're skipped. We
    # also collapse the issue list to one entry per repeated recipe (was: one
    # per occurrence, which produced 3 identical "Pastabos" lines downstream).
    new_meals_by_identity: dict[str, list[Meal]] = {}
    for meal in meals:
        if meal.meal_type != MealType.NEW:
            continue
        identity = _recipe_identity(meal)
        new_meals_by_identity.setdefault(identity, []).append(meal)

    for identity, offenders in new_meals_by_identity.items():
        if len(offenders) <= 1:
            continue
        # Keep the earliest (chronological) slot untouched and request a
        # replan for the remainder so the next pass can pick a distinct
        # recipe. Sorting by ``meal_slot.value`` would put ``dinner`` before
        # ``lunch`` alphabetically — wrong from a "what does the user eat
        # first" perspective.
        ordered = sorted(
            offenders,
            key=lambda m: (m.day_number, _SLOT_ORDER.get(m.meal_slot, 99)),
        )
        first = ordered[0]
        issues.append(
            ValidationIssue(
                code="variety",
                message=(
                    f"{first.recipe_title} appears in "
                    f"{len(offenders)} different slots without leftover routing."
                ),
                meal_key=first.key,
                severity="warning",
            )
        )
        replan_keys.extend(meal.key for meal in ordered[1:])

    unique_replan_keys = list(dict.fromkeys(replan_keys))
    status = ValidationStatus.FAIL if issues else ValidationStatus.PASS
    return ValidationResult(
        status=status,
        issues=issues,
        replan_keys=[] if retry_count >= 2 else unique_replan_keys,
        total_cost_eur=total_cost,
        budget_limit_eur=budget_limit,
    )


def _expensive_meal_keys(meals: list[Meal], limit: int = 2) -> list[str]:
    over_allocated = [
        meal
        for meal in meals
        if meal.estimated_cost_total_eur > meal.allocated_budget_eur
    ]
    candidates = over_allocated or meals
    ranked = sorted(
        candidates,
        key=lambda meal: meal.estimated_cost_total_eur - meal.allocated_budget_eur,
        reverse=True,
    )
    return [meal.key for meal in ranked[:limit]]


def _contains_disliked_term(meal: Meal, term: str) -> bool:
    normalized = term.strip().lower()
    if len(normalized) < 3:
        return False
    haystack = " ".join(
        [meal.recipe_title]
        + [ingredient.name for ingredient in meal.ingredients]
        + [ingredient.raw or "" for ingredient in meal.ingredients]
    ).lower()
    return normalized in haystack


def _recipe_identity(meal: Meal) -> str:
    return (meal.recipe_id or meal.recipe_title).strip().lower()


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}


def _coerce_plan_config(value: Any) -> PlanConfig:
    return value if isinstance(value, PlanConfig) else PlanConfig.model_validate(value)


def _coerce_user_context(value: Any) -> UserContext:
    return (
        value if isinstance(value, UserContext) else UserContext.model_validate(value)
    )
