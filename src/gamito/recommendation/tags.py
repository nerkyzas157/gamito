"""Deterministic survey-to-tag rules."""

from __future__ import annotations

from typing import Iterable

DIETARY_TAGS: dict[str, list[tuple[str, str]]] = {
    "vegan": [("vegan", "positive"), ("meat", "negative")],
    "vegetarian": [("vegetarian", "positive"), ("meat", "negative")],
    "omnivore": [],
}


def tags_from_survey(
    *,
    dietary_pref: str | None,
    cuisines: Iterable[str],
    dislikes: Iterable[str],
) -> list[tuple[str, str]]:
    """Return unique ``(tag, sentiment)`` rows generated without an LLM."""

    rows: list[tuple[str, str]] = []
    rows.extend(DIETARY_TAGS.get(_normalise(dietary_pref), []))
    rows.extend((_normalise(cuisine), "positive") for cuisine in cuisines)
    rows.extend((_normalise(dislike), "negative") for dislike in dislikes)
    return [
        row
        for row in dict.fromkeys(rows)
        if row[0] and row[1] in {"positive", "negative"}
    ]


def _normalise(value: object) -> str:
    return str(value or "").strip().lower()
