"""Build planner-ready user context from persisted profile state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gamito.db.connection import DEFAULT_DB_PATH, connect
from gamito.db.pantry import pantry_canonicals
from gamito.db.profiles import get_profile
from gamito.models.profile import UserContext

SKILL_TO_CEILING = {
    "beginner": "easy",
    "intermediate": "medium",
    "advanced": "hard",
}


def build_user_context(
    profile_id: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> UserContext:
    """Load a profile and return the existing planning ``UserContext`` shape."""

    if conn is not None:
        return _build_user_context(conn, profile_id)

    owned_conn = connect(db_path)
    try:
        return _build_user_context(owned_conn, profile_id)
    finally:
        owned_conn.close()


def _build_user_context(conn: sqlite3.Connection, profile_id: str) -> UserContext:
    profile = get_profile(conn, profile_id)
    if profile is None:
        raise KeyError(profile_id)

    positive_tags = _tags_by_sentiment(profile["tags"], "positive")
    negative_tags = list(
        dict.fromkeys([*profile["allergies"], *_tags_by_sentiment(profile["tags"], "negative")])
    )
    skill_level = (profile.get("skill_level") or "intermediate").lower()
    return UserContext(
        profile_id=profile_id,
        positive_tags=positive_tags,
        negative_tags=negative_tags,
        time_ceiling_minutes=profile.get("max_time_min"),
        skill_ceiling=SKILL_TO_CEILING.get(skill_level, "medium"),
        available_tools=profile["tools"],
        meal_prep_ok=profile["meal_prep_ok"],
        leftovers_ok=profile["leftovers_ok"],
        language=profile["language"],
        cuisine_hints=profile["cuisines"],
        dietary_pref=profile.get("dietary_pref"),
        pantry_canonicals=pantry_canonicals(conn, profile_id),
    )


def _tags_by_sentiment(tags: list[dict], sentiment: str) -> list[str]:
    matching = [tag for tag in tags if tag["sentiment"] == sentiment]
    matching.sort(key=lambda tag: (-tag["weight"], tag["tag"]))
    return list(dict.fromkeys(tag["tag"] for tag in matching))
