"""Repository helpers for profiles and preference tags."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Iterable

from gamito.models.profile import Profile
from gamito.recommendation.tags import tags_from_survey


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_profile(
    conn: sqlite3.Connection,
    *,
    name: str,
    language: str = "en",
    dietary_pref: str | None = None,
    skill_level: str | None = None,
    meal_prep_ok: bool = True,
    leftovers_ok: bool = True,
    max_time_min: int | None = None,
    allergies: Iterable[str] = (),
    tools: Iterable[str] = (),
    cuisines: Iterable[str] = (),
    disliked_ingredients: Iterable[str] = (),
) -> str:
    """Create a profile plus child rows in one transaction."""

    profile_id = uuid.uuid4().hex
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO profiles (
              profile_id, name, language, dietary_pref, skill_level,
              meal_prep_ok, leftovers_ok, max_time_min, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                name,
                language,
                dietary_pref,
                skill_level,
                int(meal_prep_ok),
                int(leftovers_ok),
                max_time_min,
                now,
                now,
            ),
        )
        _replace_profile_children(
            conn,
            profile_id=profile_id,
            allergies=allergies,
            tools=tools,
            cuisines=cuisines,
        )
        replace_survey_tags(
            conn,
            profile_id=profile_id,
            dietary_pref=dietary_pref,
            cuisines=cuisines,
            disliked_ingredients=disliked_ingredients,
            updated_at=now,
        )
    return profile_id


def update_profile(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    name: str,
    language: str = "en",
    dietary_pref: str | None = None,
    skill_level: str | None = None,
    meal_prep_ok: bool = True,
    leftovers_ok: bool = True,
    max_time_min: int | None = None,
    allergies: Iterable[str] = (),
    tools: Iterable[str] = (),
    cuisines: Iterable[str] = (),
    disliked_ingredients: Iterable[str] = (),
) -> None:
    """Update a profile and replace supplied array-style children."""

    now = _now()
    with conn:
        result = conn.execute(
            """
            UPDATE profiles
            SET name = ?, language = ?, dietary_pref = ?, skill_level = ?,
                meal_prep_ok = ?, leftovers_ok = ?, max_time_min = ?, updated_at = ?
            WHERE profile_id = ?
            """,
            (
                name,
                language,
                dietary_pref,
                skill_level,
                int(meal_prep_ok),
                int(leftovers_ok),
                max_time_min,
                now,
                profile_id,
            ),
        )
        if result.rowcount == 0:
            raise KeyError(profile_id)
        _replace_profile_children(
            conn,
            profile_id=profile_id,
            allergies=allergies,
            tools=tools,
            cuisines=cuisines,
        )
        replace_survey_tags(
            conn,
            profile_id=profile_id,
            dietary_pref=dietary_pref,
            cuisines=cuisines,
            disliked_ingredients=disliked_ingredients,
            updated_at=now,
        )


def save_profile(conn: sqlite3.Connection, profile: Profile) -> tuple[str, bool]:
    """Create or update from the public Profile model."""

    kwargs = {
        "name": profile.name or "",
        "language": profile.language,
        "dietary_pref": profile.dietary_pref,
        "skill_level": profile.skill_level,
        "meal_prep_ok": profile.meal_prep_ok,
        "leftovers_ok": profile.leftovers_ok,
        "max_time_min": profile.max_time_min,
        "allergies": profile.allergies,
        "tools": profile.kitchen_tools,
        "cuisines": profile.cuisine_preferences,
        "disliked_ingredients": profile.disliked_ingredients,
    }
    if not kwargs["name"].strip():
        raise ValueError("profile name is required")
    if profile.profile_id:
        update_profile(conn, profile.profile_id, **kwargs)
        return profile.profile_id, False
    return create_profile(conn, **kwargs), True


def get_profile(conn: sqlite3.Connection, profile_id: str) -> dict | None:
    """Return a full profile dictionary including child arrays and tags."""

    row = conn.execute(
        "SELECT * FROM profiles WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["meal_prep_ok"] = bool(result["meal_prep_ok"])
    result["leftovers_ok"] = bool(result["leftovers_ok"])
    result["allergies"] = _child_values(
        conn, "profile_allergies", "allergen", profile_id
    )
    result["tools"] = _child_values(conn, "profile_tools", "tool", profile_id)
    result["cuisines"] = _child_values(conn, "profile_cuisines", "cuisine", profile_id)
    result["tags"] = [
        dict(tag)
        for tag in conn.execute(
            """
            SELECT tag, sentiment, weight, source, updated_at
            FROM profile_tags
            WHERE profile_id = ?
            ORDER BY weight DESC, tag
            """,
            (profile_id,),
        )
    ]
    return result


def list_profiles(conn: sqlite3.Connection) -> list[dict]:
    """List profiles in stable display order."""

    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT profile_id, name, language
            FROM profiles
            ORDER BY lower(name)
            """
        )
    ]


def delete_profile(conn: sqlite3.Connection, profile_id: str) -> bool:
    """Delete a profile; schema cascades dependent rows."""

    with conn:
        result = conn.execute(
            "DELETE FROM profiles WHERE profile_id = ?",
            (profile_id,),
        )
    return result.rowcount > 0


def replace_survey_tags(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    dietary_pref: str | None,
    cuisines: Iterable[str] = (),
    disliked_ingredients: Iterable[str] = (),
    updated_at: str | None = None,
) -> int:
    """Replace source='survey' rows from deterministic survey rules."""

    now = updated_at or _now()
    rows = tags_from_survey(
        dietary_pref=dietary_pref,
        cuisines=cuisines,
        dislikes=disliked_ingredients,
    )
    conn.execute(
        "DELETE FROM profile_tags WHERE profile_id = ? AND source = 'survey'",
        (profile_id,),
    )
    conn.executemany(
        """
        INSERT INTO profile_tags (profile_id, tag, sentiment, weight, source, updated_at)
        VALUES (?, ?, ?, 1, 'survey', ?)
        ON CONFLICT(profile_id, tag, sentiment)
        DO UPDATE SET weight = excluded.weight, source = excluded.source,
                      updated_at = excluded.updated_at
        """,
        [(profile_id, tag, sentiment, now) for tag, sentiment in rows],
    )
    return len(rows)


def apply_preference_deltas(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    liked_tags: Iterable[str] = (),
    disliked_tags: Iterable[str] = (),
    source: str = "correction",
) -> list[dict]:
    """Increment correction/rating tag weights and return applied deltas."""

    now = _now()
    rows = [
        (_normalise(tag), "positive")
        for tag in liked_tags
        if _normalise(tag)
    ] + [
        (_normalise(tag), "negative")
        for tag in disliked_tags
        if _normalise(tag)
    ]
    applied: list[dict] = []
    with conn:
        for tag, sentiment in rows:
            conn.execute(
                """
                INSERT INTO profile_tags (
                  profile_id, tag, sentiment, weight, source, updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(profile_id, tag, sentiment)
                DO UPDATE SET weight = profile_tags.weight + 1,
                              source = excluded.source,
                              updated_at = excluded.updated_at
                """,
                (profile_id, tag, sentiment, source, now),
            )
            applied.append(
                {"tag": tag, "sentiment": sentiment, "delta": 1, "source": source}
            )
    return applied


def _replace_profile_children(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    allergies: Iterable[str],
    tools: Iterable[str],
    cuisines: Iterable[str],
) -> None:
    conn.execute("DELETE FROM profile_allergies WHERE profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM profile_tools WHERE profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM profile_cuisines WHERE profile_id = ?", (profile_id,))
    conn.executemany(
        "INSERT INTO profile_allergies (profile_id, allergen) VALUES (?, ?)",
        [(profile_id, allergen) for allergen in _unique_normalised(allergies)],
    )
    conn.executemany(
        "INSERT INTO profile_tools (profile_id, tool) VALUES (?, ?)",
        [(profile_id, tool) for tool in _unique_normalised(tools)],
    )
    conn.executemany(
        "INSERT INTO profile_cuisines (profile_id, cuisine) VALUES (?, ?)",
        [(profile_id, cuisine) for cuisine in _unique_normalised(cuisines)],
    )


def _child_values(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    profile_id: str,
) -> list[str]:
    return [
        row[column]
        for row in conn.execute(
            f"SELECT {column} FROM {table} WHERE profile_id = ? ORDER BY {column}",
            (profile_id,),
        )
    ]


def _unique_normalised(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for item in values if (value := _normalise(item))))


def _normalise(value: str) -> str:
    return str(value).strip().lower()
