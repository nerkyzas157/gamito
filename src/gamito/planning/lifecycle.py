"""Plan lifecycle operations built on the deterministic planner."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from gamito.config import INDEX_DIR
from gamito.db import plans as plan_repo
from gamito.mcp.errors import err
from gamito.models.meal import Meal, MealPlan, ShoppingList
from gamito.models.planning import PlanConfig
from gamito.planning.graph import meal_plan_response, resolve_seed, run_planning_graph
from gamito.planning.nodes.shopping import build_shopping_list
from gamito.recommendation.engine import build_user_context
from gamito.rendering.compact import render_compact_plan
from gamito.retrieval.index import LocalRecipeIndex


def infer_keep_avoid(
    conn: sqlite3.Connection,
    plan_id: str,
    *,
    keep_override: Iterable[str] | None = None,
    avoid_override: Iterable[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Infer regenerate keep/avoid sets from per-slot ratings."""

    if keep_override is not None:
        keep = _unique(keep_override)
    else:
        keep = [
            str(row["slot_key"])
            for row in conn.execute(
                """
                SELECT slot_key
                FROM meal_ratings
                WHERE plan_id = ? AND rating >= 8
                ORDER BY created_at DESC
                """,
                (plan_id,),
            )
        ]
    if avoid_override is not None:
        avoid = _unique(avoid_override)
    else:
        avoid = [
            str(row["recipe_id"])
            for row in conn.execute(
                """
                SELECT m.recipe_id
                FROM meal_ratings r
                JOIN plan_meals m
                  ON m.plan_id = r.plan_id AND m.slot_key = r.slot_key
                WHERE r.plan_id = ? AND r.rating <= 4 AND m.recipe_id IS NOT NULL
                ORDER BY r.created_at DESC
                """,
                (plan_id,),
            )
        ]
    return _unique(keep), _unique(avoid)


def regenerate_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    keep_slot_keys: Iterable[str] | None = None,
    avoid_recipe_ids: Iterable[str] | None = None,
    budget_eur: float | None = None,
    servings: int | None = None,
    num_days: int | None = None,
    meals_per_day: int | None = None,
    max_time_min: int | None = None,
    index_dir: str | Path = INDEX_DIR,
    recipe_index: LocalRecipeIndex | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Create a new plan from a previous one, preserving/avoiding requested slots."""

    stored = plan_repo.get_plan(conn, plan_id)
    source_plan = plan_repo.load_meal_plan(conn, plan_id)
    if stored is None or source_plan is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")

    keep, avoid = infer_keep_avoid(
        conn,
        plan_id,
        keep_override=keep_slot_keys,
        avoid_override=avoid_recipe_ids,
    )
    source_by_key = {meal.key: meal for meal in source_plan.meals}
    missing = [key for key in keep if key not in source_by_key]
    if missing:
        raise err(
            "SLOT_NOT_FOUND",
            f"slot not found: {missing[0]}",
            slot_keys=", ".join(source_by_key) or "(none)",
        )

    config = PlanConfig(
        total_budget_eur=budget_eur if budget_eur is not None else stored["total_budget_eur"],
        servings=servings if servings is not None else stored["servings"],
        num_days=num_days if num_days is not None else stored["num_days"],
        meals_per_day=meals_per_day if meals_per_day is not None else stored["meals_per_day"],
        max_time_min=max_time_min if max_time_min is not None else stored["max_time_min"],
    )
    preserved = {key: source_by_key[key] for key in keep}
    ctx = build_user_context(stored["profile_id"], conn=conn)
    resolved_seed = resolve_seed(
        seed if seed is not None else (int(stored["seed"]) + 1 if stored.get("seed") is not None else None)
    )
    index = recipe_index or LocalRecipeIndex.load(index_dir)
    if hasattr(index, "attach_custom_layer"):
        index.attach_custom_layer(conn)
    plan = run_planning_graph(
        plan_config=config,
        user_context=ctx,
        recipe_index=index,
        seed=resolved_seed,
        exclude_recipe_ids=avoid,
        preserved_slots=preserved,
    )
    new_plan_id = plan_repo.save_plan(
        conn,
        profile_id=stored["profile_id"],
        plan=plan,
        servings=config.servings,
        num_days=config.num_days,
        meals_per_day=config.meals_per_day,
        max_time_min=config.max_time_min,
        regenerated_from=plan_id,
        seed=resolved_seed,
    )
    response = meal_plan_response(new_plan_id, plan, config, seed=resolved_seed)
    response.update(
        {
            "regenerated_from": plan_id,
            "preserved_slots": keep,
            "avoided_recipe_ids": avoid,
        }
    )
    response["text"] = _diff_text(plan_id, source_plan.meals, plan.meals, keep, avoid) + "\n" + response["text"]
    return response


def stored_plan_as_response(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    """Render a stored plan in planning response shape."""

    stored = plan_repo.get_plan(conn, plan_id)
    plan = plan_repo.load_meal_plan(conn, plan_id)
    if stored is None or plan is None:
        raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
    ctx = build_user_context(stored["profile_id"], conn=conn)
    shopping = build_shopping_list(plan.meals, pantry_canonicals=ctx.pantry_canonicals)
    rendered = MealPlan(
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
    return meal_plan_response(plan_id, rendered, config, seed=stored["seed"])


def _diff_text(
    source_plan_id: str,
    old_meals: list[Meal],
    new_meals: list[Meal],
    keep: list[str],
    avoid: list[str],
) -> str:
    old_by_key = {meal.key: meal for meal in old_meals}
    changed = []
    for meal in new_meals:
        old = old_by_key.get(meal.key)
        if old and old.recipe_id != meal.recipe_id:
            changed.append(f"{meal.key}: {old.recipe_title} -> {meal.recipe_title}")
    prefix = f"Regenerated from {source_plan_id}."
    details = []
    if keep:
        details.append("preserved " + ", ".join(keep))
    if avoid:
        details.append("avoided " + ", ".join(avoid))
    if changed:
        details.append("changed " + "; ".join(changed[:5]))
    return prefix + (" " + "; ".join(details) if details else "")


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
