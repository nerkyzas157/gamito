"""Plan lifecycle MCP tools."""

from __future__ import annotations

from gamito.db import plans as plan_repo
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db, require_plan, require_profile
from gamito.mcp.tools.planning import _embedding_error, _validate_plan_request
from gamito.planning.lifecycle import regenerate_plan as run_regenerate_plan
from gamito.planning.nodes.assignment import AssignmentError
from gamito.retrieval.index import EmbeddingModelMismatch, NoCandidates


@tool
def label_plan(
    plan_id: str,
    label: str | None = None,
    is_favorite: bool | None = None,
) -> dict:
    """Attach a short label and/or favorite flag to a persisted plan."""

    if label is None and is_favorite is None:
        raise err("INVALID_INPUT", "label or is_favorite must be supplied")
    clean_label = label.strip() if isinstance(label, str) else label
    with open_db() as conn:
        require_plan(conn, plan_id)
        try:
            result = plan_repo.label_plan(
                conn,
                plan_id=plan_id,
                label=clean_label,
                is_favorite=is_favorite,
            )
        except plan_repo.LabelTakenError as exc:
            raise err(
                "LABEL_TAKEN",
                f"label {exc.label!r} already in use",
                label=exc.label,
                plan_id=exc.existing_plan_id or "unknown",
            ) from exc
    label_text = result["label"] or "unlabelled"
    favorite_text = "favorite" if result["is_favorite"] else "not favorite"
    return {**result, "text": f"{label_text} - {favorite_text}"}


@tool
def list_plans(
    profile_id: str,
    favorites_only: bool = False,
    labelled_only: bool = False,
    limit: int = 20,
) -> dict:
    """List persisted plans for a profile with average meal ratings."""

    if limit < 1 or limit > 50:
        raise err("INVALID_INPUT", "limit must be between 1 and 50")
    with open_db() as conn:
        require_profile(conn, profile_id)
        plans = plan_repo.list_plans(
            conn,
            profile_id=profile_id,
            favorites_only=favorites_only,
            labelled_only=labelled_only,
            limit=limit,
        )
    return {"plans": plans, "text": _plans_text(plans)}


@tool
def regenerate_plan(
    plan_id: str,
    keep_slot_keys: list[str] | None = None,
    avoid_recipe_ids: list[str] | None = None,
    budget_eur: float | None = None,
    servings: int | None = None,
    num_days: int | None = None,
    meals_per_day: int | None = None,
    max_time_min: int | None = None,
) -> dict:
    """Generate a new plan from a previous plan using ratings or overrides."""

    with open_db() as conn:
        source = require_plan(conn, plan_id)
        _validate_plan_request(
            budget_eur if budget_eur is not None else source["total_budget_eur"],
            servings if servings is not None else source["servings"],
            num_days if num_days is not None else source["num_days"],
            meals_per_day if meals_per_day is not None else source["meals_per_day"],
            max_time_min if max_time_min is not None else source["max_time_min"],
        )
        try:
            return run_regenerate_plan(
                conn,
                plan_id=plan_id,
                keep_slot_keys=keep_slot_keys,
                avoid_recipe_ids=avoid_recipe_ids,
                budget_eur=budget_eur,
                servings=servings,
                num_days=num_days,
                meals_per_day=meals_per_day,
                max_time_min=max_time_min,
            )
        except NoCandidates as exc:
            raise err(
                "NO_CANDIDATES",
                str(exc),
                constraints=", ".join(exc.constraints) or "unknown",
            ) from exc
        except AssignmentError as exc:
            raise err("NO_CANDIDATES", str(exc), constraints=str(exc)) from exc
        except EmbeddingModelMismatch as exc:
            raise _embedding_error(exc) from exc


def _plans_text(plans: list[dict]) -> str:
    if not plans:
        return "No plans found."
    lines = []
    for plan in plans:
        star = "* " if plan["is_favorite"] else ""
        label = plan["label"] or plan["plan_id"][:8]
        rating = (
            f" · {plan['avg_meal_rating']}/10"
            if plan["avg_meal_rating"] is not None
            else ""
        )
        lines.append(
            f"{star}{label} · {plan['num_days']}d x {plan['meals_per_day']} · "
            f"{(plan['total_cost_eur'] or 0):.2f} EUR{rating}"
        )
    return "\n".join(lines)
