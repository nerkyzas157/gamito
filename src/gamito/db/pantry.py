"""Repository helpers for profile pantry staples."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_pantry_item(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    canonical_name: str,
    source: str = "agent",
    confidence: float | None = None,
    last_seen_at: str | None = None,
) -> None:
    """Insert or refresh a pantry item for a profile."""

    canonical = _normalise(canonical_name)
    if not canonical:
        raise ValueError("canonical_name is required")
    seen_at = last_seen_at or _now()
    with conn:
        conn.execute(
            """
            INSERT INTO pantry_items (
              profile_id, canonical_name, source, confidence, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, canonical_name)
            DO UPDATE SET source = excluded.source,
                          confidence = excluded.confidence,
                          last_seen_at = excluded.last_seen_at
            """,
            (profile_id, canonical, source, confidence, seen_at),
        )


def replace_pantry(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    canonical_names: Iterable[str],
    source: str = "agent",
) -> int:
    """Replace all pantry rows for a profile."""

    rows = list(dict.fromkeys(name for item in canonical_names if (name := _normalise(item))))
    with conn:
        conn.execute("DELETE FROM pantry_items WHERE profile_id = ?", (profile_id,))
        conn.executemany(
            """
            INSERT INTO pantry_items (profile_id, canonical_name, source, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            [(profile_id, name, source, _now()) for name in rows],
        )
    return len(rows)


def remove_pantry_items(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    canonical_names: Iterable[str],
) -> int:
    """Remove pantry items by canonical name."""

    rows = list(dict.fromkeys(name for item in canonical_names if (name := _normalise(item))))
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in rows)
    with conn:
        cursor = conn.execute(
            f"""
            DELETE FROM pantry_items
            WHERE profile_id = ? AND canonical_name IN ({placeholders})
            """,
            [profile_id, *rows],
        )
    return cursor.rowcount


def list_pantry(conn: sqlite3.Connection, profile_id: str) -> list[dict]:
    """List pantry rows for a profile."""

    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT canonical_name, source, confidence, last_seen_at
            FROM pantry_items
            WHERE profile_id = ?
            ORDER BY canonical_name
            """,
            (profile_id,),
        )
    ]


def pantry_canonicals(conn: sqlite3.Connection, profile_id: str) -> list[str]:
    """Return only canonical names for UserContext construction."""

    return [row["canonical_name"] for row in list_pantry(conn, profile_id)]


def _normalise(value: str) -> str:
    return str(value).strip().lower()
