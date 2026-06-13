"""Offline validator tests for the deterministic local core."""

from __future__ import annotations

import unittest

from gamito.planning.nodes.validator import validate_meal_plan
from gamito.models.meal import Ingredient, Meal, MealSlot, MealType
from gamito.models.planning import PlanConfig
from gamito.models.profile import UserContext


def _meal(
    title: str,
    cost: float,
    slot: MealSlot = MealSlot.DINNER,
    flags: dict[str, bool] | None = None,
    recipe_id: str | None = None,
    meal_type: MealType = MealType.NEW,
    source_slot_key: str | None = None,
    day_number: int = 1,
) -> Meal:
    return Meal(
        day_number=day_number,
        meal_slot=slot,
        recipe_title=title,
        recipe_id=recipe_id,
        servings=2,
        allocated_budget_eur=5,
        estimated_cost_total_eur=cost,
        estimated_cost_per_serving_eur=cost / 2,
        dietary_flags=flags or {"is_nut_free": True},
        ingredients=[Ingredient(name="rice")],
        meal_type=meal_type,
        source_slot_key=source_slot_key,
    )


class ValidatorTests(unittest.TestCase):
    def test_budget_overage_fails_and_selects_expensive_meals(self) -> None:
        result = validate_meal_plan(
            meals=[
                _meal("Budget meal", 4),
                _meal("Expensive meal", 20, MealSlot.LUNCH),
            ],
            plan_config=PlanConfig(total_budget_eur=10, servings=2),
            user_context=UserContext(),
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].code, "budget_total")
        self.assertIn("day_1:lunch", result.replan_keys)

    def test_allergy_violation_fails(self) -> None:
        result = validate_meal_plan(
            meals=[_meal("Peanut noodles", 4, flags={"is_nut_free": False})],
            plan_config=PlanConfig(total_budget_eur=10, servings=2),
            user_context=UserContext(negative_tags=["nuts"]),
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].code, "allergy")
        self.assertEqual(result.replan_keys, ["day_1:dinner"])

    def test_duplicate_recipe_warns_for_variety(self) -> None:
        result = validate_meal_plan(
            meals=[
                _meal("Same meal", 3, MealSlot.LUNCH, recipe_id="same"),
                _meal("Same meal", 3, MealSlot.DINNER, recipe_id="same"),
            ],
            plan_config=PlanConfig(total_budget_eur=20, servings=2),
            user_context=UserContext(),
        )

        self.assertFalse(result.passed)
        variety_issues = [i for i in result.issues if i.code == "variety"]
        self.assertEqual(
            len(variety_issues),
            1,
            "Validator should collapse variety issues to one per recipe.",
        )
        # Keep the earliest slot intact, replan the duplicate.
        self.assertEqual(result.replan_keys, ["day_1:dinner"])

    def test_variety_issue_collapsed_for_three_repeats(self) -> None:
        meals = [
            _meal("Same meal", 3, MealSlot.BREAKFAST, recipe_id="x", day_number=1),
            _meal("Same meal", 3, MealSlot.LUNCH, recipe_id="x", day_number=1),
            _meal("Same meal", 3, MealSlot.DINNER, recipe_id="x", day_number=1),
        ]
        result = validate_meal_plan(
            meals=meals,
            plan_config=PlanConfig(total_budget_eur=30, servings=2, meals_per_day=3),
            user_context=UserContext(),
        )

        variety_issues = [i for i in result.issues if i.code == "variety"]
        self.assertEqual(len(variety_issues), 1)
        # Earliest slot (breakfast) is preserved; lunch and dinner are replanned.
        self.assertEqual(set(result.replan_keys), {"day_1:lunch", "day_1:dinner"})

    def test_intentional_leftover_does_not_trigger_variety(self) -> None:
        meals = [
            _meal(
                "Mediterranean stew",
                6,
                MealSlot.DINNER,
                recipe_id="stew",
                meal_type=MealType.MEAL_PREP,
                day_number=1,
            ),
            _meal(
                "Mediterranean stew",
                0,
                MealSlot.LUNCH,
                recipe_id="stew",
                meal_type=MealType.LEFTOVER,
                source_slot_key="day_1:dinner",
                day_number=2,
            ),
        ]
        result = validate_meal_plan(
            meals=meals,
            plan_config=PlanConfig(total_budget_eur=30, servings=2, num_days=2),
            user_context=UserContext(leftovers_ok=True, meal_prep_ok=True),
        )

        variety_issues = [i for i in result.issues if i.code == "variety"]
        self.assertEqual(
            variety_issues,
            [],
            "Intentional leftover/meal_prep repeats must not trigger variety issues.",
        )
        self.assertEqual(result.replan_keys, [])

    def test_retry_limit_suppresses_replan_keys(self) -> None:
        result = validate_meal_plan(
            meals=[_meal("Still expensive", 30)],
            plan_config=PlanConfig(total_budget_eur=10, servings=2),
            user_context=UserContext(),
            retry_count=2,
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.replan_keys, [])

    def test_validator_result_omits_language_mismatch_state(self) -> None:
        result = validate_meal_plan(
            meals=[_meal("Chicken salad", 4, MealSlot.LUNCH)],
            plan_config=PlanConfig(total_budget_eur=20, servings=2),
            user_context=UserContext(language="lt"),
        )

        self.assertFalse(hasattr(result, "language_mismatch"))


if __name__ == "__main__":
    unittest.main()
