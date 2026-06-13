"""In-process tests for the G4 MCP tool layer."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from gamito.db.connection import connect, migrate
from gamito.mcp.errors import HINTS, err
from gamito.mcp.tools import common, edits, feedback, lifecycle, pantry, planning, profiles, recipes
from gamito.retrieval.index import LocalRecipeIndex, NoCandidates


class McpToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gamito.db"
        self.old_db = os.environ.get("GAMITO_DB")
        os.environ["GAMITO_DB"] = str(self.db_path)
        conn = connect(self.db_path)
        try:
            migrate(conn)
        finally:
            conn.close()

    def tearDown(self) -> None:
        if self.old_db is None:
            os.environ.pop("GAMITO_DB", None)
        else:
            os.environ["GAMITO_DB"] = self.old_db
        self.tmpdir.cleanup()

    def test_profile_tools_round_trip(self) -> None:
        empty = profiles.list_profiles()
        self.assertEqual(empty["profiles"], [])

        saved = profiles.save_profile(
            name="Tomas",
            language="en",
            dietary_pref="vegetarian",
            allergies=["nuts"],
            kitchen_tools=["skillet"],
            cuisine_preferences=["Italian"],
        )
        self.assertTrue(saved["created"])
        profile_id = saved["profile_id"]

        listed = profiles.list_profiles()
        fetched = profiles.get_profile(profile_id)
        updated = profiles.update_preferences(profile_id, liked_tags=["beans"])

        self.assertEqual(listed["profiles"][0]["name"], "Tomas")
        self.assertEqual(fetched["allergies"], ["nuts"])
        self.assertIn("beans", updated["text"])

    def test_open_db_auto_migrates_fresh_database(self) -> None:
        old_db = os.environ.get("GAMITO_DB")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["GAMITO_DB"] = str(Path(tmpdir) / "fresh.db")
            try:
                with common.open_db() as conn:
                    version = conn.execute(
                        "SELECT max(version) FROM schema_version"
                    ).fetchone()[0]
            finally:
                if old_db is None:
                    os.environ.pop("GAMITO_DB", None)
                else:
                    os.environ["GAMITO_DB"] = old_db

        self.assertEqual(version, 1)

    def test_planning_search_edit_shopping_and_feedback_tools(self) -> None:
        profile_id = profiles.save_profile(name="Nerijus", language="en")["profile_id"]
        index = _fake_index()

        with patch("gamito.planning.graph.LocalRecipeIndex.load", return_value=index):
            generated = planning.generate_meal_plan(
                profile_id=profile_id,
                budget_eur=20,
                servings=2,
                num_days=1,
                meals_per_day=1,
            )

        plan_id = generated["plan_id"]
        slot_key = generated["meals"][0]["slot_key"]

        with patch("gamito.mcp.tools.planning.LocalRecipeIndex.load", return_value=index):
            search = planning.search_recipes("quick dinner", profile_id=profile_id, limit=3)

        with patch("gamito.planning.edits.LocalRecipeIndex.load", return_value=index):
            swapped = edits.swap_meal(plan_id, slot_key, "beans")

        rescaled = edits.rescale_meal(plan_id, slot_key, 3)
        shopping = pantry.get_shopping_list(plan_id)
        rating = feedback.rate_meal(plan_id, slot_key, 9)
        latest = planning.get_meal_plan("latest", profile_id=profile_id)

        self.assertEqual(generated["status"], "complete")
        self.assertIn("Meal plan", generated["text"])
        self.assertGreaterEqual(len(search["recipes"]), 1)
        self.assertIn("old_meal", swapped)
        self.assertEqual(rescaled["meal"]["servings"], 3)
        self.assertIn("300 g", rescaled["shopping_list"]["items"][0]["amount"])
        self.assertIn("total_eur", shopping)
        self.assertEqual(rating["rating"], 9)
        self.assertTrue(any(item["tag"] == "italian" for item in rating["applied"]))
        self.assertEqual(latest["plan_id"], plan_id)

    def test_learning_loop_and_lifecycle_regenerate(self) -> None:
        profile_id = profiles.save_profile(name="Learner", language="en")["profile_id"]
        index = _fake_index()
        with patch("gamito.planning.graph.LocalRecipeIndex.load", return_value=index):
            generated = planning.generate_meal_plan(profile_id, 30, 2, 1, 2)
        first_slot = generated["meals"][0]["slot_key"]
        second_slot = generated["meals"][1]["slot_key"]
        kept_recipe = generated["meals"][0]["recipe_id"]
        avoided_recipe = generated["meals"][1]["recipe_id"]

        feedback.rate_meal(generated["plan_id"], first_slot, 9)
        feedback.rate_meal(generated["plan_id"], second_slot, 2)
        labelled = lifecycle.label_plan(generated["plan_id"], "Cheap weeknights", True)
        listed = lifecycle.list_plans(profile_id, favorites_only=True)

        with patch("gamito.planning.lifecycle.LocalRecipeIndex.load", return_value=_fake_index()):
            regenerated = lifecycle.regenerate_plan(generated["plan_id"])

        by_key = {meal["slot_key"]: meal for meal in regenerated["meals"]}
        self.assertEqual(labelled["label"], "Cheap weeknights")
        self.assertEqual(listed["plans"][0]["label"], "Cheap weeknights")
        self.assertIn(first_slot, regenerated["preserved_slots"])
        self.assertIn(avoided_recipe, regenerated["avoided_recipe_ids"])
        self.assertEqual(by_key[first_slot]["recipe_id"], kept_recipe)
        self.assertNotIn(avoided_recipe, [meal["recipe_id"] for meal in regenerated["meals"]])
        self.assertIn("Regenerated from", regenerated["text"])

    def test_label_collision_returns_label_taken(self) -> None:
        profile_id = profiles.save_profile(name="Planner", language="en")["profile_id"]
        with patch("gamito.planning.graph.LocalRecipeIndex.load", return_value=_fake_index()):
            first = planning.generate_meal_plan(profile_id, 20, 2, 1, 1)
            second = planning.generate_meal_plan(profile_id, 20, 2, 1, 1)

        lifecycle.label_plan(first["plan_id"], "Budget")
        collision = lifecycle.label_plan(second["plan_id"], "Budget")

        self.assertEqual(collision["error_code"], "LABEL_TAKEN")

    def test_custom_recipe_add_search_plan_and_delete_guard(self) -> None:
        profile_id = profiles.save_profile(name="Cook", language="en")["profile_id"]
        vector = np.ones((1, 384), dtype=np.float32)
        with patch("gamito.db.custom_recipes.encode", return_value=vector):
            added = recipes.add_recipe(
                title="Mama Beans",
                ingredient_names=["beans"],
                ingredient_amounts=["1 can"],
                directions=["Warm beans."],
                courses=["main"],
                cuisines=["comfort"],
                servings=2,
                added_by_profile_id=profile_id,
            )

        recipe_id = added["recipe_id"]
        index = _custom_ready_index()
        with patch("gamito.mcp.tools.planning.LocalRecipeIndex.load", return_value=index):
            found = planning.search_recipes("beans", profile_id=profile_id, limit=3)
        with patch("gamito.planning.graph.LocalRecipeIndex.load", return_value=_custom_ready_index()):
            generated = planning.generate_meal_plan(profile_id, 20, 2, 1, 1)

        blocked = recipes.delete_recipe(recipe_id)
        forced = recipes.delete_recipe(recipe_id, force=True)

        self.assertEqual(found["recipes"][0]["recipe_id"], recipe_id)
        self.assertEqual(generated["meals"][0]["source"], "custom")
        self.assertIn("your recipe", generated["text"])
        self.assertEqual(blocked["error_code"], "RECIPE_IN_USE")
        self.assertEqual(forced["deleted"], recipe_id)

    def test_pantry_update_uses_canonical_slow_use_filter(self) -> None:
        profile_id = profiles.save_profile(name="Mama", language="en")["profile_id"]
        with (
            patch("gamito.mcp.tools.pantry.resolve_to_canonical", side_effect=lambda item: item.lower()),
            patch("gamito.mcp.tools.pantry.is_slow_use", return_value=True),
        ):
            result = pantry.update_pantry(profile_id, add_items=["Olive Oil"])
        fetched = pantry.get_pantry(profile_id)

        self.assertEqual(result["added"][0]["canonical"], "olive oil")
        self.assertEqual(fetched["items"][0]["canonical_name"], "olive oil")

    def test_tool_errors_are_structured(self) -> None:
        self.assertEqual(profiles.get_profile("missing")["error_code"], "PROFILE_NOT_FOUND")
        self.assertEqual(planning.get_meal_plan("latest")["error_code"], "INVALID_INPUT")
        self.assertEqual(planning.get_meal_plan("missing")["error_code"], "PLAN_NOT_FOUND")
        self.assertEqual(
            planning.generate_meal_plan("missing", 0, 2, 1, 1)["error_code"],
            "INVALID_BUDGET",
        )
        self.assertEqual(
            planning.generate_meal_plan("missing", 0.5, 2, 1, 1)["error_code"],
            "BUDGET_TOO_LOW",
        )

        profile_id = profiles.save_profile(name="Child", language="en")["profile_id"]
        with patch("gamito.planning.graph.LocalRecipeIndex.load", return_value=_fake_index()):
            plan_id = planning.generate_meal_plan(profile_id, 20, 2, 1, 1)["plan_id"]
        self.assertEqual(
            feedback.rate_meal(plan_id, "day_9:dinner", 8)["error_code"],
            "SLOT_NOT_FOUND",
        )
        self.assertEqual(feedback.rate_meal(plan_id, "day_1:dinner", 11)["error_code"], "INVALID_INPUT")

        with patch(
            "gamito.mcp.tools.planning.LocalRecipeIndex.load",
            return_value=_index_raising_no_candidates(),
        ):
            self.assertEqual(planning.search_recipes("impossible")["error_code"], "NO_CANDIDATES")

    def test_every_spec_error_code_has_serializable_hint(self) -> None:
        for code in HINTS:
            with self.subTest(code=code):
                payload = err(
                    code,
                    "message",
                    slot_keys="day_1:dinner",
                    servings=2,
                    slots=1,
                    minimum_eur=2.0,
                    constraints="test",
                    issues="test",
                    label="demo",
                    plan_id="plan",
                    plan_ids="plan",
                    got_model="old",
                    expected_model="new",
                ).to_dict()
                self.assertEqual(payload["error_code"], code)
                self.assertTrue(payload["hint"])


def _fake_index() -> LocalRecipeIndex:
    rows = []
    for idx in range(1, 5):
        rows.append(_row(f"b{idx}", f"Breakfast {idx}", "breakfast", 1.0))
    for idx in range(1, 8):
        rows.append(_row(f"m{idx}", f"Main {idx}", "main", 2.0))
    metadata = pd.DataFrame(rows)
    embeddings = np.ones((len(metadata), 3), dtype=np.float32)
    manifest = {"model": "test-model", "dims": 3, "count": len(metadata)}
    return LocalRecipeIndex(
        metadata=metadata,
        embeddings=embeddings,
        manifest=manifest,
        encode_fn=lambda texts: np.ones((len(texts), 3), dtype=np.float32),
        expected_model="test-model",
        expected_dims=3,
    )


def _index_raising_no_candidates():
    class EmptyIndex:
        def search(self, *args, **kwargs):
            raise NoCandidates(["max_time_min<=5"])

    return EmptyIndex()


def _custom_ready_index() -> LocalRecipeIndex:
    metadata = pd.DataFrame(
        [
            {
                "recipe_id": "static_1",
                "recipe_title": "Plain Rice",
                "total_time_min": 20,
                "price_per_serving_eur": 2.0,
                "est_servings": 2,
                "is_vegetarian": True,
                "is_vegan": True,
                "is_nut_free": True,
                "is_dairy_free": True,
                "is_gluten_free": True,
                "kitchen_tools": [],
                "cuisine_list": ["plain"],
                "course_list": ["main"],
                "source": "dataset",
                "ingredients_json": json.dumps(
                    [{"name": "rice", "amount": "200 g", "canonical": "rice"}]
                ),
                "directions_json": json.dumps(["Cook rice."]),
                "nutrition_per_serving_json": json.dumps(
                    {"kcal": 300, "protein_g": 6, "carbs_g": 60, "fat_g": 1}
                ),
            }
        ]
    )
    return LocalRecipeIndex(
        metadata=metadata,
        embeddings=np.zeros((1, 384), dtype=np.float32),
        manifest={"model": "test-model", "dims": 384, "count": 1},
        encode_fn=lambda texts: np.ones((len(texts), 384), dtype=np.float32),
        expected_model="test-model",
        expected_dims=384,
    )


def _row(recipe_id: str, title: str, course: str, price: float) -> dict:
    return {
        "recipe_id": recipe_id,
        "recipe_title": title,
        "total_time_min": 20,
        "price_per_serving_eur": price,
        "est_servings": 2,
        "is_vegetarian": True,
        "is_vegan": False,
        "is_nut_free": True,
        "is_dairy_free": True,
        "is_gluten_free": True,
        "kitchen_tools": [],
        "cuisine_list": ["italian"],
        "course_list": [course],
        "source": "dataset",
        "ingredients_json": json.dumps(
            [{"name": f"{title} ingredient", "amount": "200 g", "canonical": f"{recipe_id} ingredient"}]
        ),
        "directions_json": json.dumps([f"Cook {title}."]),
        "nutrition_per_serving_json": json.dumps(
            {"kcal": 400, "protein_g": 20, "carbs_g": 40, "fat_g": 10}
        ),
    }


if __name__ == "__main__":
    unittest.main()
