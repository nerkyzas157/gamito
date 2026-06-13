"""Tests for local retrieval filter masks."""

from __future__ import annotations

import unittest

import pandas as pd

from gamito.models.profile import UserContext
from gamito.retrieval.filters import (
    RecipeSearchContext,
    apply_filters,
    apply_filters_with_relaxation,
)


def _metadata() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "recipe_id": "r1",
                "recipe_title": "Vegan Lentil Soup",
                "total_time_min": 30,
                "price_per_serving_eur": 1.8,
                "is_vegan": True,
                "is_vegetarian": True,
                "is_gluten_free": True,
                "is_dairy_free": True,
                "is_nut_free": True,
                "healthiness_score": 80,
                "kitchen_tools": ["stockpot", "stovetop"],
                "cuisine_list": ["middle eastern"],
                "course_list": ["main"],
            },
            {
                "recipe_id": "r2",
                "recipe_title": "Cheesy Pasta",
                "total_time_min": 45,
                "price_per_serving_eur": 3.4,
                "is_vegan": False,
                "is_vegetarian": True,
                "is_gluten_free": False,
                "is_dairy_free": False,
                "is_nut_free": True,
                "healthiness_score": 55,
                "kitchen_tools": ["stockpot", "stovetop"],
                "cuisine_list": ["italian"],
                "course_list": ["main"],
            },
            {
                "recipe_id": "r3",
                "recipe_title": "Nutty Dessert",
                "total_time_min": 20,
                "price_per_serving_eur": 4.0,
                "is_vegan": False,
                "is_vegetarian": True,
                "is_gluten_free": True,
                "is_dairy_free": True,
                "is_nut_free": False,
                "healthiness_score": 40,
                "kitchen_tools": ["oven"],
                "cuisine_list": ["american"],
                "course_list": ["dessert"],
            },
        ]
    )


class RetrievalFilterTests(unittest.TestCase):
    def test_applies_time_price_diet_allergen_and_course_filters(self) -> None:
        ctx = RecipeSearchContext(
            max_time_min=40,
            max_price_per_serving=2.0,
            dietary_pref="vegan",
            allergies=("gluten",),
            course="main",
        )

        result = apply_filters(_metadata(), ctx)

        self.assertEqual(result["recipe_id"].tolist(), ["r1"])

    def test_excludes_recipe_ids(self) -> None:
        ctx = RecipeSearchContext(exclude_recipe_ids=("r1", "r3"))

        result = apply_filters(_metadata(), ctx)

        self.assertEqual(result["recipe_id"].tolist(), ["r2"])

    def test_owned_tools_require_recipe_tools_subset(self) -> None:
        ctx = RecipeSearchContext(owned_tools=("stockpot", "stovetop"))

        result = apply_filters(_metadata(), ctx)

        self.assertEqual(result["recipe_id"].tolist(), ["r1", "r2"])

    def test_user_context_names_are_supported(self) -> None:
        ctx = UserContext(
            time_ceiling_minutes=35,
            available_tools=["stockpot", "stovetop"],
            cuisine_hints=["middle eastern"],
            dietary_pref="vegan",
        )

        result = apply_filters(_metadata(), ctx)

        self.assertEqual(result["recipe_id"].tolist(), ["r1"])

    def test_relaxation_drops_cuisine_before_hard_filters(self) -> None:
        ctx = RecipeSearchContext(
            dietary_pref="vegan",
            preferred_cuisines=("italian",),
        )

        outcome = apply_filters_with_relaxation(_metadata(), ctx)

        self.assertEqual(outcome.candidates["recipe_id"].tolist(), ["r1"])
        self.assertEqual(outcome.relaxed_constraints, ("preferred_cuisines",))

    def test_empty_pool_reports_emptying_constraint(self) -> None:
        ctx = RecipeSearchContext(owned_tools=("microwave",))

        outcome = apply_filters_with_relaxation(_metadata(), ctx)

        self.assertTrue(outcome.candidates.empty)
        self.assertEqual(outcome.emptying_constraints, ("kitchen_tools<=owned_tools",))


if __name__ == "__main__":
    unittest.main()
