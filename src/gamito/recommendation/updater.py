"""Rule-based preference updates from ratings and corrections."""

from __future__ import annotations


def rating_deltas(rating: int, meal_tags: list[str]) -> list[tuple[str, str, int]]:
    """Return ``(tag, sentiment, weight_delta)`` rows for a meal rating."""

    tags = list(dict.fromkeys(tag.strip().lower() for tag in meal_tags if tag.strip()))
    if rating >= 8:
        return [(tag, "positive", 1) for tag in tags]
    if rating <= 4:
        return [(tag, "negative", 1) for tag in tags]
    return []


def preference_query_suffix(positive_tags: list[str], *, limit: int = 5) -> str:
    """Build the cheap retrieval-query suffix used to bias semantic search."""

    tags = [tag for tag in positive_tags[:limit] if tag]
    return "" if not tags else "prefers: " + ", ".join(tags)
