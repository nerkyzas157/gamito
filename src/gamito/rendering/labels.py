"""Small label tables for deterministic renderers."""

from __future__ import annotations

from gamito.models.meal import MealSlot

LABELS = {
    "en": {
        "plan": "Meal plan",
        "day": "Day",
        "breakfast": "Breakfast",
        "lunch": "Lunch",
        "dinner": "Dinner",
        "snack": "Snack",
        "leftover": "leftover",
        "shopping": "Shopping",
        "pantry": "Already at home",
        "budget": "Budget",
        "warnings": "Notes",
        "total": "total",
    },
    "lt": {
        "plan": "Valgiaraštis",
        "day": "Diena",
        "breakfast": "Pusryčiai",
        "lunch": "Pietūs",
        "dinner": "Vakarienė",
        "snack": "Užkandis",
        "leftover": "likučiai",
        "shopping": "Pirkinių sąrašas",
        "pantry": "Jau namuose",
        "budget": "Biudžetas",
        "warnings": "Pastabos",
        "total": "viso",
    },
}


def labels_for(language: str | None) -> dict[str, str]:
    """Return labels for a supported language, falling back to English."""

    key = (language or "en").strip().lower()
    return LABELS.get(key, LABELS["en"])


def slot_label(slot: MealSlot | str, language: str | None) -> str:
    """Translate a meal slot label."""

    value = slot.value if isinstance(slot, MealSlot) else str(slot)
    return labels_for(language).get(value, value.title())
