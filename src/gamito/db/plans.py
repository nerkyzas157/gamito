"""Repository helpers for persisted meal plans and feedback."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from gamito.models.meal import (
    Ingredient,
    Meal,
    MealPlan,
    MealSlot,
    MealSource,
    MealType,
    Nutrition,
    ShoppingList,
)
from gamito.models.planning import PlanType


class LabelTakenError(ValueError):
    """Raised when a profile already has the requested plan label."""

    def __init__(self, label: str, existing_plan_id: str | None) -> None:
        super().__init__(label)
        self.label = label
        self.existing_plan_id = existing_plan_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_plan(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    plan_type: str | PlanType = PlanType.MULTI_DAY,
    num_days: int,
    meals_per_day: int,
    total_budget_eur: float,
    servings: int,
    max_time_min: int | None = None,
    status: str = "complete",
    total_cost_eur: float | None = None,
    warnings: Iterable[str] = (),
    label: str | None = None,
    is_favorite: bool = False,
    regenerated_from: str | None = None,
    seed: int | None = None,
    meals: Iterable[Meal] = (),
) -> str:
    """Persist a plan parent and meal fan-out in one transaction."""

    plan_id = uuid.uuid4().hex
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO meal_plans (
              plan_id, profile_id, plan_type, num_days, meals_per_day,
              total_budget_eur, servings, max_time_min, status, total_cost_eur,
              warnings_json, label, is_favorite, regenerated_from, seed,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                profile_id,
                _enum_value(plan_type),
                num_days,
                meals_per_day,
                total_budget_eur,
                servings,
                max_time_min,
                status,
                total_cost_eur,
                _json(list(warnings)),
                label,
                int(is_favorite),
                regenerated_from,
                seed,
                now,
                now,
            ),
        )
        insert_meals(conn, plan_id=plan_id, meals=meals, created_at=now)
    return plan_id


def save_plan(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    plan: MealPlan,
    servings: int,
    plan_type: str | PlanType = PlanType.MULTI_DAY,
    num_days: int | None = None,
    meals_per_day: int | None = None,
    max_time_min: int | None = None,
    label: str | None = None,
    is_favorite: bool = False,
    regenerated_from: str | None = None,
    seed: int | None = None,
) -> str:
    """Persist the public MealPlan model."""

    inferred_days = num_days or max((meal.day_number for meal in plan.meals), default=1)
    inferred_meals_per_day = meals_per_day or max(
        (
            len({meal.meal_slot for meal in plan.meals if meal.day_number == day})
            for day in range(1, inferred_days + 1)
        ),
        default=1,
    )
    return create_plan(
        conn,
        profile_id=profile_id,
        plan_type=plan_type,
        num_days=inferred_days,
        meals_per_day=inferred_meals_per_day,
        total_budget_eur=plan.total_budget_eur,
        servings=servings,
        max_time_min=max_time_min,
        total_cost_eur=plan.total_estimated_cost_eur,
        warnings=plan.warnings,
        label=label,
        is_favorite=is_favorite,
        regenerated_from=regenerated_from,
        seed=seed,
        meals=plan.meals,
    )


def insert_meals(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    meals: Iterable[Meal],
    created_at: str | None = None,
) -> None:
    """Insert meal rows for an already-created plan."""

    now = created_at or _now()
    conn.executemany(
        """
        INSERT INTO plan_meals (
          meal_id, plan_id, slot_key, day_number, meal_slot, recipe_id,
          recipe_title, meal_type, source, source_slot_key, total_time_min,
          difficulty, cuisines_json, dietary_json, nutrition_json, servings,
          cost_total_eur, cost_per_serving_eur, ingredients_json,
          directions_json, tools_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(_meal_row(plan_id, meal, now)) for meal in meals],
    )


def get_plan(conn: sqlite3.Connection, plan_id: str) -> dict | None:
    """Load a plan plus denormalized meal rows."""

    row = conn.execute(
        "SELECT * FROM meal_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["is_favorite"] = bool(result["is_favorite"])
    result["warnings"] = _decode(result.pop("warnings_json"), default=[])
    result["meals"] = list_plan_meals(conn, plan_id)
    return result


def load_meal_plan(conn: sqlite3.Connection, plan_id: str) -> MealPlan | None:
    """Load persisted rows back into the public MealPlan model."""

    plan = get_plan(conn, plan_id)
    if plan is None:
        return None
    meals = [_row_to_meal(row) for row in plan["meals"]]
    return MealPlan(
        user_id=plan["profile_id"],
        meals=meals,
        shopping_list=ShoppingList(),
        total_budget_eur=plan["total_budget_eur"],
        total_estimated_cost_eur=plan["total_cost_eur"] or 0.0,
        warnings=plan["warnings"],
    )


def list_plan_meals(conn: sqlite3.Connection, plan_id: str) -> list[dict]:
    """Return stored meal rows in slot order."""

    rows = conn.execute(
        """
        SELECT *
        FROM plan_meals
        WHERE plan_id = ?
        ORDER BY day_number, meal_slot
        """,
        (plan_id,),
    )
    return [_decode_meal_row(dict(row)) for row in rows]


def label_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    label: str | None = None,
    is_favorite: bool | None = None,
) -> dict:
    """Set plan lifecycle label/favorite fields."""

    if label is None and is_favorite is None:
        raise ValueError("label or is_favorite must be supplied")
    current = conn.execute(
        "SELECT label, is_favorite FROM meal_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if current is None:
        raise KeyError(plan_id)
    new_label = label if label is not None else current["label"]
    new_favorite = int(is_favorite) if is_favorite is not None else current["is_favorite"]
    try:
        with conn:
            conn.execute(
                """
                UPDATE meal_plans
                SET label = ?, is_favorite = ?, updated_at = ?
                WHERE plan_id = ?
                """,
                (new_label, new_favorite, _now(), plan_id),
            )
    except sqlite3.IntegrityError as exc:
        existing = None
        if new_label:
            row = conn.execute(
                """
                SELECT plan_id
                FROM meal_plans
                WHERE profile_id = (
                  SELECT profile_id FROM meal_plans WHERE plan_id = ?
                )
                AND label = ?
                """,
                (plan_id, new_label),
            ).fetchone()
            existing = None if row is None else str(row["plan_id"])
        raise LabelTakenError(str(new_label), existing) from exc
    return {"plan_id": plan_id, "label": new_label, "is_favorite": bool(new_favorite)}


def list_plans(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    favorites_only: bool = False,
    labelled_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """List persisted plans with average rating metadata."""

    clauses = ["p.profile_id = ?"]
    params: list[Any] = [profile_id]
    if favorites_only:
        clauses.append("p.is_favorite = 1")
    if labelled_only:
        clauses.append("p.label IS NOT NULL")
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT p.plan_id, p.label, p.is_favorite, p.num_days, p.meals_per_day,
               p.total_cost_eur, p.created_at,
               (
                 SELECT avg(r.rating)
                 FROM meal_ratings r
                 WHERE r.plan_id = p.plan_id
               ) AS avg_meal_rating,
               (
                 SELECT group_concat(m.recipe_title, ', ')
                 FROM plan_meals m
                 WHERE m.plan_id = p.plan_id
                 ORDER BY m.day_number, m.meal_slot
               ) AS plan_summary
        FROM meal_plans p
        WHERE {" AND ".join(clauses)}
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        params,
    )
    return [
        {
            **dict(row),
            "is_favorite": bool(row["is_favorite"]),
            "avg_meal_rating": round(row["avg_meal_rating"], 1)
            if row["avg_meal_rating"] is not None
            else None,
        }
        for row in rows
    ]


def rate_meal(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    slot_key: str,
    rating: int,
) -> dict:
    """Store or replace a per-meal rating."""

    plan = conn.execute(
        "SELECT profile_id FROM meal_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if plan is None:
        raise KeyError(plan_id)
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO meal_ratings (profile_id, plan_id, slot_key, rating, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, plan_id, slot_key)
            DO UPDATE SET rating = excluded.rating, created_at = excluded.created_at
            """,
            (plan["profile_id"], plan_id, slot_key, rating, now),
        )
    return {
        "profile_id": plan["profile_id"],
        "plan_id": plan_id,
        "slot_key": slot_key,
        "rating": rating,
    }


def record_plan_edit(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    slot_key: str,
    edit_type: str,
    payload: dict,
) -> int:
    """Append an audit row for a persisted plan edit."""

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO plan_edits (plan_id, slot_key, edit_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (plan_id, slot_key, edit_type, _json(payload), _now()),
        )
    return int(cursor.lastrowid)


def delete_plan(conn: sqlite3.Connection, plan_id: str) -> bool:
    """Delete a plan; schema cascades meals, ratings, and edits."""

    with conn:
        result = conn.execute("DELETE FROM meal_plans WHERE plan_id = ?", (plan_id,))
    return result.rowcount > 0


def _meal_row(plan_id: str, meal: Meal, created_at: str) -> tuple:
    return (
        uuid.uuid4().hex,
        plan_id,
        meal.key,
        meal.day_number,
        _enum_value(meal.meal_slot),
        meal.recipe_id,
        meal.recipe_title,
        _enum_value(meal.meal_type),
        _enum_value(meal.source),
        meal.source_slot_key,
        meal.total_time_min,
        meal.difficulty,
        _json(meal.cuisine_list),
        _json(meal.dietary_flags),
        _json(
            meal.nutrition_per_serving.model_dump(mode="json")
            if meal.nutrition_per_serving
            else None
        ),
        meal.servings,
        meal.estimated_cost_total_eur,
        meal.estimated_cost_per_serving_eur,
        _json([ingredient.model_dump(mode="json") for ingredient in meal.ingredients]),
        _json(meal.directions),
        _json(meal.kitchen_tools),
        created_at,
    )


def _decode_meal_row(row: dict) -> dict:
    for source, target, default in (
        ("cuisines_json", "cuisines", []),
        ("dietary_json", "dietary", {}),
        ("nutrition_json", "nutrition", None),
        ("ingredients_json", "ingredients", []),
        ("directions_json", "directions", []),
        ("tools_json", "tools", []),
    ):
        row[target] = _decode(row.pop(source), default=default)
    return row


def _row_to_meal(row: dict) -> Meal:
    return Meal(
        day_number=row["day_number"],
        meal_slot=MealSlot(row["meal_slot"]),
        recipe_title=row["recipe_title"],
        servings=row["servings"],
        allocated_budget_eur=0.0,
        estimated_cost_total_eur=row["cost_total_eur"] or 0.0,
        estimated_cost_per_serving_eur=row["cost_per_serving_eur"] or 0.0,
        recipe_id=row["recipe_id"],
        meal_type=MealType(row["meal_type"]),
        source_slot_key=row["source_slot_key"],
        source=MealSource(row["source"]),
        total_time_min=row["total_time_min"],
        difficulty=row["difficulty"],
        cuisine_list=row["cuisines"],
        kitchen_tools=row["tools"],
        ingredients=[Ingredient.model_validate(item) for item in row["ingredients"]],
        directions=row["directions"],
        nutrition_per_serving=Nutrition.model_validate(row["nutrition"])
        if row["nutrition"]
        else None,
        dietary_flags=row["dietary"],
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
