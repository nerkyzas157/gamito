"""Shared helpers for thin MCP tool wrappers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gamito.db.connection import connect, default_db_path, migrate
from gamito.db.plans import get_plan, load_meal_plan
from gamito.db.profiles import get_profile
from gamito.mcp.errors import err
from gamito.models.meal import MealPlan, ShoppingList
from gamito.models.planning import PlanConfig
from gamito.planning.graph import meal_plan_response
from gamito.planning.nodes.shopping import build_shopping_list
from gamito.recommendation.engine import build_user_context
from gamito.rendering.compact import render_compact_plan


def configured_db_path() -> Path:
    """Resolve the database path at call time for tests and MCP hosts."""

    return default_db_path()


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    """Open one configured SQLite connection for a tool call."""

    conn = connect(configured_db_path())
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


def require_profile(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    """Load a profile or raise the public MCP error."""

    profile = get_profile(conn, profile_id)
    if profile is None:
        raise err("PROFILE_NOT_FOUND", f"profile not found: {profile_id}")
    return profile


def require_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    """Load a plan or raise the public MCP error."""

    plan = get_plan(conn, plan_id)
    if plan is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
    return plan


def latest_plan_id(conn: sqlite3.Connection, profile_id: str) -> str | None:
    """Return the newest plan for a profile."""

    row = conn.execute(
        """
        SELECT plan_id
        FROM meal_plans
        WHERE profile_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (profile_id,),
    ).fetchone()
    return None if row is None else str(row["plan_id"])


def slot_keys_for_plan(plan: dict[str, Any]) -> list[str]:
    """Return valid slot keys in persisted plan order."""

    return [str(meal["slot_key"]) for meal in plan.get("meals", [])]


def require_slot(plan: dict[str, Any], slot_key: str) -> dict[str, Any]:
    """Load a stored meal row by slot key or raise ``SLOT_NOT_FOUND``."""

    for meal in plan.get("meals", []):
        if meal["slot_key"] == slot_key:
            return meal
    raise err(
        "SLOT_NOT_FOUND",
        f"slot not found: {slot_key}",
        slot_keys=", ".join(slot_keys_for_plan(plan)) or "(none)",
    )


def stored_plan_response(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    use_pantry: bool = True,
) -> dict[str, Any]:
    """Render a persisted plan in the same shape as ``generate_meal_plan``."""

    stored = require_plan(conn, plan_id)
    plan = load_meal_plan(conn, plan_id)
    if plan is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
    ctx = build_user_context(stored["profile_id"], conn=conn)
    shopping = build_shopping_list(
        plan.meals,
        pantry_canonicals=ctx.pantry_canonicals if use_pantry else [],
    )
    plan = MealPlan(
        user_id=stored["profile_id"],
        meals=plan.meals,
        shopping_list=shopping,
        total_budget_eur=stored["total_budget_eur"],
        total_estimated_cost_eur=shopping.total_estimated_cost_eur,
        language=ctx.language,
        formatted_text=render_compact_plan(
            meals=plan.meals,
            shopping_list=shopping,
            requested_budget_eur=stored["total_budget_eur"],
            language=ctx.language,
            warnings=stored["warnings"],
        ),
        warnings=stored["warnings"],
    )
    config = PlanConfig(
        total_budget_eur=stored["total_budget_eur"],
        servings=stored["servings"],
        num_days=stored["num_days"],
        meals_per_day=stored["meals_per_day"],
        max_time_min=stored["max_time_min"],
    )
    return meal_plan_response(plan_id, plan, config, seed=stored["seed"])


def shopping_payload(shopping: ShoppingList) -> dict[str, Any]:
    """Serialize a shopping list for standalone shopping tools."""

    return {
        "items": [_shopping_item_payload(item) for item in shopping.items],
        "pantry_items": [_shopping_item_payload(item) for item in shopping.pantry_items],
        "total_eur": round(shopping.total_estimated_cost_eur, 2),
    }


def _shopping_item_payload(item: Any) -> dict[str, Any]:
    return {
        "canonical": item.name,
        "amount": item.amount,
        "est_price_eur": round(item.estimated_price_eur, 2),
        "meal_keys": item.meal_keys,
    }
