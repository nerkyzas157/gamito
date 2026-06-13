#!/usr/bin/env python3
"""Seed a demo profile and canned plan for MCP smoke testing."""

from __future__ import annotations

import argparse
from pathlib import Path

from gamito.db.connection import DEFAULT_DB_PATH, connect, migrate
from gamito.db.pantry import upsert_pantry_item
from gamito.db.plans import create_plan
from gamito.db.profiles import create_profile
from gamito.models.meal import Ingredient, Meal, MealSlot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = connect(db_path)
    try:
        migrate(conn)
        profile_id = _demo_profile(conn)
        plan_id = _demo_plan(conn, profile_id)
    finally:
        conn.close()

    print(f"Seeded demo profile {profile_id}")
    print(f"Seeded demo plan {plan_id}")


def _demo_profile(conn) -> str:
    row = conn.execute(
        "SELECT profile_id FROM profiles WHERE name = ?",
        ("Demo Household",),
    ).fetchone()
    if row is not None:
        return str(row["profile_id"])
    profile_id = create_profile(
        conn,
        name="Demo Household",
        language="en",
        dietary_pref="omnivore",
        skill_level="beginner",
        tools=["oven", "skillet"],
        cuisines=["italian", "mexican"],
        disliked_ingredients=["cilantro"],
    )
    upsert_pantry_item(conn, profile_id=profile_id, canonical_name="olive oil", source="manual")
    return profile_id


def _demo_plan(conn, profile_id: str) -> str:
    row = conn.execute(
        "SELECT plan_id FROM meal_plans WHERE profile_id = ? AND label = ?",
        (profile_id, "demo-plan"),
    ).fetchone()
    if row is not None:
        return str(row["plan_id"])
    return create_plan(
        conn,
        profile_id=profile_id,
        num_days=1,
        meals_per_day=3,
        total_budget_eur=18.0,
        servings=2,
        total_cost_eur=11.4,
        label="demo-plan",
        is_favorite=True,
        meals=_demo_meals(),
    )


def _demo_meals() -> list[Meal]:
    return [
        Meal(
            day_number=1,
            meal_slot=MealSlot.BREAKFAST,
            recipe_id="demo_breakfast",
            recipe_title="Yogurt Berry Bowl",
            servings=2,
            allocated_budget_eur=4.0,
            estimated_cost_total_eur=3.2,
            estimated_cost_per_serving_eur=1.6,
            total_time_min=5,
            ingredients=[
                Ingredient(name="Greek yogurt", amount="300 g", canonical_name="yogurt"),
                Ingredient(name="berries", amount="150 g", canonical_name="berries"),
            ],
            directions=["Spoon yogurt into bowls.", "Top with berries."],
        ),
        Meal(
            day_number=1,
            meal_slot=MealSlot.LUNCH,
            recipe_id="demo_lunch",
            recipe_title="Tomato Chickpea Toast",
            servings=2,
            allocated_budget_eur=5.0,
            estimated_cost_total_eur=3.8,
            estimated_cost_per_serving_eur=1.9,
            total_time_min=12,
            ingredients=[
                Ingredient(name="bread", amount="4 slices", canonical_name="bread"),
                Ingredient(name="chickpeas", amount="1 can", canonical_name="chickpeas"),
                Ingredient(name="tomato", amount="2", canonical_name="tomato"),
            ],
            directions=["Toast bread.", "Warm chickpeas with tomato.", "Serve on toast."],
        ),
        Meal(
            day_number=1,
            meal_slot=MealSlot.DINNER,
            recipe_id="demo_dinner",
            recipe_title="Skillet Bean Pasta",
            servings=2,
            allocated_budget_eur=9.0,
            estimated_cost_total_eur=4.4,
            estimated_cost_per_serving_eur=2.2,
            total_time_min=25,
            kitchen_tools=["skillet"],
            ingredients=[
                Ingredient(name="pasta", amount="200 g", canonical_name="pasta"),
                Ingredient(name="beans", amount="1 can", canonical_name="beans"),
                Ingredient(name="olive oil", amount="1 tbsp", canonical_name="olive oil"),
            ],
            directions=["Boil pasta.", "Warm beans in skillet.", "Toss with olive oil."],
        ),
    ]


if __name__ == "__main__":
    main()
