"""Temp-file SQLite tests for G2 persistence."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from gamito.db.connection import connect, current_version, migrate
from gamito.db.pantry import list_pantry, upsert_pantry_item
from gamito.db.plans import (
    create_plan,
    get_plan,
    load_meal_plan,
    rate_meal,
    record_plan_edit,
)
from gamito.db.profiles import create_profile, delete_profile, get_profile
from gamito.models.meal import Ingredient, Meal, MealSlot
from gamito.recommendation.engine import build_user_context
from gamito.recommendation.tags import tags_from_survey


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gamito.db"
        self.conn = connect(self.db_path)
        migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_migrate_is_idempotent(self) -> None:
        self.assertEqual(current_version(self.conn), 1)

        migrate(self.conn)

        self.assertEqual(current_version(self.conn), 1)
        count = self.conn.execute("SELECT count(*) FROM schema_version").fetchone()[0]
        self.assertEqual(count, 1)

    def test_profile_to_user_context_round_trip(self) -> None:
        profile_id = create_profile(
            self.conn,
            name="Tomas",
            language="lt",
            dietary_pref="vegetarian",
            skill_level="beginner",
            max_time_min=30,
            allergies=["nuts", "nuts"],
            tools=["oven", "skillet"],
            cuisines=["Italian"],
            disliked_ingredients=["mushroom"],
        )
        upsert_pantry_item(
            self.conn,
            profile_id=profile_id,
            canonical_name="olive oil",
            source="manual",
        )

        stored = get_profile(self.conn, profile_id)
        ctx = build_user_context(profile_id, conn=self.conn)

        self.assertEqual(stored["allergies"], ["nuts"])
        self.assertEqual(ctx.language, "lt")
        self.assertEqual(ctx.skill_ceiling, "easy")
        self.assertEqual(ctx.time_ceiling_minutes, 30)
        self.assertEqual(ctx.available_tools, ["oven", "skillet"])
        self.assertEqual(ctx.cuisine_hints, ["italian"])
        self.assertIn("vegetarian", ctx.positive_tags)
        self.assertIn("meat", ctx.negative_tags)
        self.assertIn("mushroom", ctx.negative_tags)
        self.assertIn("nuts", ctx.negative_tags)
        self.assertEqual(ctx.pantry_canonicals, ["olive oil"])

    def test_unique_constraints_are_enforced(self) -> None:
        create_profile(self.conn, name="Mama")

        with self.assertRaises(sqlite3.IntegrityError):
            create_profile(self.conn, name="Mama")

    def test_plan_rating_edit_and_cascade_round_trip(self) -> None:
        profile_id = create_profile(self.conn, name="Nerijus")
        meal = _meal()
        plan_id = create_plan(
            self.conn,
            profile_id=profile_id,
            num_days=1,
            meals_per_day=1,
            total_budget_eur=10,
            servings=2,
            total_cost_eur=4.5,
            warnings=["under budget"],
            meals=[meal],
        )
        rating = rate_meal(
            self.conn,
            plan_id=plan_id,
            slot_key="day_1:dinner",
            rating=9,
        )
        edit_id = record_plan_edit(
            self.conn,
            plan_id=plan_id,
            slot_key="day_1:dinner",
            edit_type="swap",
            payload={"old": "Soup", "new": "Beans"},
        )

        stored = get_plan(self.conn, plan_id)
        roundtripped = load_meal_plan(self.conn, plan_id)

        self.assertEqual(rating["profile_id"], profile_id)
        self.assertGreater(edit_id, 0)
        self.assertEqual(stored["warnings"], ["under budget"])
        self.assertEqual(stored["meals"][0]["recipe_title"], "Beans on toast")
        self.assertEqual(roundtripped.meals[0].ingredients[0].canonical_name, "beans")

        self.assertTrue(delete_profile(self.conn, profile_id))
        for table in (
            "profiles",
            "meal_plans",
            "plan_meals",
            "meal_ratings",
            "plan_edits",
            "pantry_items",
            "profile_tags",
        ):
            count = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)

    def test_survey_tag_mapping_cases(self) -> None:
        cases = [
            (
                {"dietary_pref": "vegan", "cuisines": ["Thai"], "dislikes": ["Fish"]},
                [("vegan", "positive"), ("meat", "negative"), ("thai", "positive"), ("fish", "negative")],
            ),
            (
                {"dietary_pref": "omnivore", "cuisines": [], "dislikes": []},
                [],
            ),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(tags_from_survey(**params), expected)


def _meal() -> Meal:
    return Meal(
        day_number=1,
        meal_slot=MealSlot.DINNER,
        recipe_id="42",
        recipe_title="Beans on toast",
        servings=2,
        allocated_budget_eur=10,
        estimated_cost_total_eur=4.5,
        estimated_cost_per_serving_eur=2.25,
        total_time_min=15,
        cuisine_list=["british"],
        dietary_flags={"is_vegetarian": True},
        kitchen_tools=["skillet"],
        ingredients=[
            Ingredient(
                name="beans",
                amount="1 can",
                canonical_name="beans",
                estimated_price_eur=1.2,
            )
        ],
        directions=["Warm beans", "Toast bread"],
    )


if __name__ == "__main__":
    unittest.main()
