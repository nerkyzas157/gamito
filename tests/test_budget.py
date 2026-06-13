"""Offline tests for deterministic budget allocation."""

from __future__ import annotations

import unittest

from gamito.models.meal import MealSlot, MealType
from gamito.models.planning import PlanConfig
from gamito.models.profile import UserContext
from gamito.planning.nodes.budget import allocate_budget_deterministically


class BudgetAllocationTests(unittest.TestCase):
    def test_allocates_all_requested_slots(self) -> None:
        config = PlanConfig(total_budget_eur=60, servings=2, num_days=2, meals_per_day=3)

        plan = allocate_budget_deterministically(config)

        self.assertEqual(len(plan.allocations), 6)
        self.assertAlmostEqual(
            sum(allocation.budget_eur for allocation in plan.allocations),
            60.0,
            places=2,
        )
        self.assertEqual(
            [allocation.meal_slot for allocation in plan.allocations[:3]],
            [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
        )

    def test_marks_leftover_when_user_opts_in(self) -> None:
        config = PlanConfig(total_budget_eur=60, servings=2, num_days=2, meals_per_day=3)
        ctx = UserContext(leftovers_ok=True, meal_prep_ok=True)

        plan = allocate_budget_deterministically(config, ctx)

        by_key = plan.by_key()
        self.assertEqual(by_key["day_2:lunch"].meal_type, MealType.LEFTOVER)
        self.assertEqual(by_key["day_2:lunch"].source_slot_key, "day_1:dinner")
        self.assertEqual(by_key["day_2:lunch"].budget_eur, 0.0)
        self.assertEqual(by_key["day_1:dinner"].meal_type, MealType.MEAL_PREP)
        self.assertAlmostEqual(
            sum(a.budget_eur for a in plan.allocations),
            60.0,
            places=2,
        )

    def test_skips_leftover_routing_when_disabled(self) -> None:
        config = PlanConfig(total_budget_eur=60, servings=2, num_days=2, meals_per_day=3)
        ctx = UserContext(leftovers_ok=False, meal_prep_ok=False)

        plan = allocate_budget_deterministically(config, ctx)

        for allocation in plan.allocations:
            self.assertEqual(allocation.meal_type, MealType.NEW)


if __name__ == "__main__":
    unittest.main()
