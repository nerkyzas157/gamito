"""Compact chat-sized meal plan rendering."""

from __future__ import annotations

from collections import defaultdict

from gamito.models.meal import Meal, MealType, ShoppingList
from gamito.rendering.labels import labels_for, slot_label


def render_compact_plan(
    *,
    meals: list[Meal],
    shopping_list: ShoppingList,
    requested_budget_eur: float,
    language: str = "en",
    warnings: list[str] | None = None,
) -> str:
    """Render a concise plan suitable for MCP ``text`` responses."""

    labels = labels_for(language)
    lines = [
        f"{labels['plan']} - {len({meal.day_number for meal in meals})}d x "
        f"{len({meal.meal_slot for meal in meals})} meals",
        (
            f"{labels['budget']}: {shopping_list.total_estimated_cost_eur:.2f} / "
            f"{requested_budget_eur:.2f} EUR"
        ),
    ]

    meals_by_day: dict[int, list[Meal]] = defaultdict(list)
    for meal in sorted(meals, key=lambda item: (item.day_number, _slot_order(item))):
        meals_by_day[meal.day_number].append(meal)

    for day, day_meals in meals_by_day.items():
        lines.append(f"{labels['day']} {day}:")
        for meal in day_meals:
            suffix = ""
            if meal.meal_type == MealType.LEFTOVER:
                suffix = f" ({labels['leftover']})"
            lines.append(
                f"- {slot_label(meal.meal_slot, language)}: "
                f"{meal.recipe_title}{suffix} - {meal.estimated_cost_total_eur:.2f} EUR"
            )

    if shopping_list.items:
        preview = ", ".join(item.name for item in shopping_list.items[:8])
        if len(shopping_list.items) > 8:
            preview += f", +{len(shopping_list.items) - 8}"
        lines.append(
            f"{labels['shopping']}: {preview} "
            f"({shopping_list.total_estimated_cost_eur:.2f} EUR {labels['total']})"
        )

    if shopping_list.pantry_items:
        lines.append(
            f"{labels['pantry']}: "
            + ", ".join(item.name for item in shopping_list.pantry_items[:8])
        )

    warning_lines = [warning for warning in warnings or [] if warning]
    if warning_lines:
        lines.append(f"{labels['warnings']}: " + "; ".join(warning_lines[:3]))

    text = "\n".join(lines)
    if len(text) <= 3500:
        return text
    return text[:3490].rstrip() + "\n..."


def _slot_order(meal: Meal) -> int:
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
    return order.get(meal.meal_slot.value, 99)
