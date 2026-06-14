"""Integration tests for the G3 deterministic planning core."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from gamito.db.connection import connect, migrate
from gamito.db.plans import get_plan
from gamito.db.profiles import create_profile
from gamito.models.meal import MealSlot, MealType
from gamito.models.planning import PlanConfig
from gamito.models.profile import UserContext
from gamito.planning.graph import generate_meal_plan, run_planning_graph
from gamito.planning.nodes.assignment import AssignmentNode
from gamito.retrieval.index import LocalRecipeIndex


class PlanningCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "gamito.db"
        self.conn = connect(self.db_path)
        migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()

    def test_generate_meal_plan_runs_airgapped_and_persists(self) -> None:
        profile_id = create_profile(self.conn, name="Tomas", language="en")
        index = _fake_index()
        real_socket = socket.socket

        def blocked_socket(*args, **kwargs):
            raise RuntimeError("network call attempted in airgapped core")

        socket.socket = blocked_socket
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        response = generate_meal_plan(
            profile_id=profile_id,
            budget_eur=50,
            servings=2,
            num_days=3,
            meals_per_day=3,
            conn=self.conn,
            recipe_index=index,
            seed=7,
        )

        self.assertEqual(response["status"], "complete")
        self.assertEqual(len(response["meals"]), 9)
        self.assertEqual(response["seed"], 7)
        self.assertIn("Meal plan", response["text"])
        stored = get_plan(self.conn, response["plan_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(len(stored["meals"]), 9)

    def test_leftovers_are_routed_and_new_recipes_are_distinct(self) -> None:
        profile_id = create_profile(
            self.conn,
            name="Nerijus",
            meal_prep_ok=True,
            leftovers_ok=True,
        )

        response = generate_meal_plan(
            profile_id=profile_id,
            budget_eur=60,
            servings=2,
            num_days=3,
            meals_per_day=3,
            conn=self.conn,
            recipe_index=_fake_index(),
            seed=11,
        )

        by_key = {meal["slot_key"]: meal for meal in response["meals"]}
        self.assertEqual(by_key["day_2:lunch"]["meal_type"], MealType.LEFTOVER.value)
        self.assertEqual(by_key["day_2:lunch"]["source_slot_key"], "day_1:dinner")
        self.assertEqual(by_key["day_2:lunch"]["cost_eur"], 0.0)
        recipe_ids = [
            meal["recipe_id"]
            for meal in response["meals"]
            if meal["meal_type"] != MealType.LEFTOVER.value
        ]
        self.assertEqual(len(recipe_ids), len(set(recipe_ids)))

    def test_same_seed_produces_same_assignments(self) -> None:
        config = PlanConfig(total_budget_eur=35, servings=2, num_days=2, meals_per_day=2)
        ctx = UserContext(language="lt", leftovers_ok=False, meal_prep_ok=False)
        first = run_planning_graph(
            plan_config=config,
            user_context=ctx,
            recipe_index=_fake_index(),
            seed=42,
        )
        second = run_planning_graph(
            plan_config=config,
            user_context=ctx,
            recipe_index=_fake_index(),
            seed=42,
        )

        self.assertEqual(
            [meal.recipe_id for meal in first.meals],
            [meal.recipe_id for meal in second.meals],
        )
        self.assertEqual(first.formatted_text, second.formatted_text)
        self.assertIn("Valgiaraštis", first.formatted_text)

    def test_assignment_batches_slot_queries(self) -> None:
        encode_calls: list[list[str]] = []
        config = PlanConfig(total_budget_eur=20, servings=2, num_days=1, meals_per_day=3)
        ctx = UserContext()
        state = {
            "plan_config": config,
            "user_context": ctx,
            "budget_plan": __import__(
                "gamito.planning.nodes.budget",
                fromlist=["allocate_budget_deterministically"],
            ).allocate_budget_deterministically(config, ctx),
            "excluded_recipe_ids": [],
            "seed": 1,
        }

        result = __import__("asyncio").run(
            AssignmentNode(_fake_index(encode_calls))(state)
        )

        self.assertEqual(len(encode_calls), 1)
        self.assertEqual(len(encode_calls[0]), 3)
        self.assertEqual(len(result["meals_by_key"]), 3)

    def test_assignment_prefers_candidate_near_budget_target(self) -> None:
        config = PlanConfig(total_budget_eur=10, servings=2, num_days=1, meals_per_day=1)
        ctx = UserContext()
        state = {
            "plan_config": config,
            "user_context": ctx,
            "budget_plan": __import__(
                "gamito.planning.nodes.budget",
                fromlist=["allocate_budget_deterministically"],
            ).allocate_budget_deterministically(config, ctx),
            "excluded_recipe_ids": [],
            "seed": 1,
        }
        index = _index_from_rows(
            [
                _row("cheap", "Cheap Main", "main", 1.0),
                _row("target", "Target Main", "main", 4.0),
            ]
        )

        result = __import__("asyncio").run(AssignmentNode(index)(state))

        meal = result["meals_by_key"]["day_1:dinner"]
        self.assertEqual(meal.recipe_id, "target")
        self.assertAlmostEqual(meal.estimated_cost_total_eur, 8.0)

    def test_assignment_preserves_price_cap(self) -> None:
        config = PlanConfig(total_budget_eur=10, servings=2, num_days=1, meals_per_day=1)
        ctx = UserContext()
        state = {
            "plan_config": config,
            "user_context": ctx,
            "budget_plan": __import__(
                "gamito.planning.nodes.budget",
                fromlist=["allocate_budget_deterministically"],
            ).allocate_budget_deterministically(config, ctx),
            "excluded_recipe_ids": [],
            "seed": 1,
        }
        index = _index_from_rows(
            [
                _row("too_expensive", "Too Expensive Main", "main", 6.0),
                _row("target", "Target Main", "main", 4.0),
            ]
        )

        result = __import__("asyncio").run(AssignmentNode(index)(state))

        meal = result["meals_by_key"]["day_1:dinner"]
        self.assertEqual(meal.recipe_id, "target")
        self.assertLessEqual(meal.estimated_cost_per_serving_eur, 5.0)

    def test_budget_targeting_is_seed_deterministic(self) -> None:
        config = PlanConfig(total_budget_eur=10, servings=2, num_days=1, meals_per_day=1)
        ctx = UserContext()
        budget_plan = __import__(
            "gamito.planning.nodes.budget",
            fromlist=["allocate_budget_deterministically"],
        ).allocate_budget_deterministically(config, ctx)
        index = _index_from_rows(
            [
                _row("cheap", "Cheap Main", "main", 1.0),
                _row("target_a", "Target A Main", "main", 4.0),
                _row("target_b", "Target B Main", "main", 4.0),
            ]
        )

        first = __import__("asyncio").run(
            AssignmentNode(index)(
                {
                    "plan_config": config,
                    "user_context": ctx,
                    "budget_plan": budget_plan,
                    "excluded_recipe_ids": [],
                    "seed": 42,
                }
            )
        )
        second = __import__("asyncio").run(
            AssignmentNode(index)(
                {
                    "plan_config": config,
                    "user_context": ctx,
                    "budget_plan": budget_plan,
                    "excluded_recipe_ids": [],
                    "seed": 42,
                }
            )
        )

        self.assertEqual(
            first["meals_by_key"]["day_1:dinner"].recipe_id,
            second["meals_by_key"]["day_1:dinner"].recipe_id,
        )


def _fake_index(encode_calls: list[list[str]] | None = None) -> LocalRecipeIndex:
    rows = []
    for idx in range(1, 8):
        rows.append(_row(f"b{idx}", f"Breakfast {idx}", "breakfast", 1.0))
    for idx in range(1, 12):
        rows.append(_row(f"m{idx}", f"Main {idx}", "main", 1.8))
    return _index_from_rows(rows, encode_calls)


def _index_from_rows(
    rows: list[dict],
    encode_calls: list[list[str]] | None = None,
) -> LocalRecipeIndex:
    metadata = pd.DataFrame(rows)
    embeddings = np.ones((len(metadata), 3), dtype=np.float32)
    manifest = {"model": "test-model", "dims": 3, "count": len(metadata)}

    def encode_fn(texts: list[str]) -> np.ndarray:
        if encode_calls is not None:
            encode_calls.append(texts)
        return np.ones((len(texts), 3), dtype=np.float32)

    return LocalRecipeIndex(
        metadata=metadata,
        embeddings=embeddings,
        manifest=manifest,
        encode_fn=encode_fn,
        expected_model="test-model",
        expected_dims=3,
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
        "cuisine_list": [],
        "course_list": [course],
        "source": "dataset",
        "ingredients_json": json.dumps(
            [
                {
                    "name": f"{title} ingredient",
                    "amount": "200 g",
                    "canonical": f"{recipe_id} ingredient",
                }
            ]
        ),
        "directions_json": json.dumps([f"Cook {title}."]),
        "nutrition_per_serving_json": json.dumps(
            {"kcal": 400, "protein_g": 20, "carbs_g": 40, "fat_g": 10}
        ),
    }


if __name__ == "__main__":
    unittest.main()
