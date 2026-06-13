"""Meal feedback MCP tool."""

from __future__ import annotations

from gamito.db import plans as plan_repo
from gamito.db import profiles as profile_repo
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db, require_plan, require_slot


@tool
def rate_meal(plan_id: str, slot_key: str, rating: int) -> dict:
    """Store a per-meal rating and apply deterministic tag deltas."""

    if rating < 1 or rating > 10:
        raise err("INVALID_INPUT", "rating must be between 1 and 10")
    with open_db() as conn:
        plan = require_plan(conn, plan_id)
        meal = require_slot(plan, slot_key)
        stored = plan_repo.rate_meal(conn, plan_id=plan_id, slot_key=slot_key, rating=rating)
        liked, disliked = _tags_for_rating(meal, rating)
        applied = profile_repo.apply_preference_deltas(
            conn,
            profile_id=stored["profile_id"],
            liked_tags=liked,
            disliked_tags=disliked,
            source="rating",
        )
    if rating >= 8:
        text = "Noted - more like this."
    elif rating <= 4:
        text = "Noted - less like this."
    else:
        text = "Rating saved."
    if applied:
        text += " " + ", ".join(f"{item['tag']} +{item['delta']}" for item in applied)
    return {**stored, "applied": applied, "text": text}


def _tags_for_rating(meal: dict, rating: int) -> tuple[list[str], list[str]]:
    tags = list(dict.fromkeys([*meal.get("cuisines", []), *_dietary_tags(meal)]))
    if rating >= 8:
        return tags, []
    if rating <= 4:
        return [], tags
    return [], []


def _dietary_tags(meal: dict) -> list[str]:
    tags = []
    for key, value in meal.get("dietary", {}).items():
        if value and key.startswith("is_"):
            tags.append(key.removeprefix("is_"))
    return tags
