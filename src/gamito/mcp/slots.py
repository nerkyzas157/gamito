"""Shared meal-slot keys used by the planner and MCP tools."""

from __future__ import annotations

MEAL_SLOTS = ("breakfast", "lunch", "dinner", "snack")


def slot_key(day: int, slot: str) -> str:
    """Return the canonical key for a day/meal slot pair."""

    if day < 1:
        raise ValueError(f"day must be >= 1: {day!r}")
    normalized = str(slot).strip().lower()
    if normalized not in MEAL_SLOTS:
        raise ValueError(f"unknown meal slot: {slot!r}")
    return f"day_{day}:{normalized}"


def parse_slot_key(key: str) -> tuple[int, str]:
    """Parse ``day_N:<slot>`` and validate the slot component."""

    try:
        day_part, slot = str(key).split(":", maxsplit=1)
        day = int(day_part.removeprefix("day_"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid slot_key: {key!r}") from exc
    return day, slot_key(day, slot).split(":", maxsplit=1)[1]
