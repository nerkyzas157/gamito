"""Full structured text formatter for persisted meal plans."""

from __future__ import annotations

from collections import defaultdict

from gamito.models.meal import Meal, MealType, ShoppingList
from gamito.rendering.labels import labels_for, slot_label


def render_full_plan(
    *,
    meals: list[Meal],
    shopping_list: ShoppingList,
    requested_budget_eur: float,
    language: str = "en",
    warnings: list[str] | None = None,
) -> str:
    """Render a detailed deterministic text plan."""

    labels = labels_for(language)
    lines = [
        f"# {labels['plan']}",
        (
            f"{labels['budget']}: {shopping_list.total_estimated_cost_eur:.2f} / "
            f"{requested_budget_eur:.2f} EUR"
        ),
        "",
    ]

    meals_by_day: dict[int, list[Meal]] = defaultdict(list)
    for meal in sorted(meals, key=lambda item: (item.day_number, _slot_order(item))):
        meals_by_day[meal.day_number].append(meal)

    for day, day_meals in meals_by_day.items():
        lines.append(f"## {labels['day']} {day}")
        for meal in day_meals:
            leftover = (
                f" ({labels['leftover']} from {meal.source_slot_key})"
                if meal.meal_type == MealType.LEFTOVER
                else ""
            )
            lines.append(
                f"### {slot_label(meal.meal_slot, language)}: "
                f"{meal.recipe_title}{leftover}"
            )
            detail = [
                f"{meal.estimated_cost_total_eur:.2f} EUR",
                f"{meal.total_time_min} min" if meal.total_time_min is not None else None,
                f"{meal.servings} servings",
            ]
            lines.append(" · ".join(part for part in detail if part))
            if meal.ingredients:
                lines.append(
                    "Ingredients: "
                    + ", ".join(ingredient.name for ingredient in meal.ingredients[:12])
                )
            if meal.directions:
                lines.append("Directions: " + " ".join(meal.directions[:3]))
            lines.append("")

    if shopping_list.items:
        lines.append(f"## {labels['shopping']}")
        for item in shopping_list.items:
            amount = f" ({item.amount})" if item.amount else ""
            lines.append(f"- {item.name}{amount}: {item.estimated_price_eur:.2f} EUR")
        lines.append("")

    if shopping_list.pantry_items:
        lines.append(f"## {labels['pantry']}")
        for item in shopping_list.pantry_items:
            amount = f" ({item.amount})" if item.amount else ""
            lines.append(f"- {item.name}{amount}")
        lines.append("")

    warning_lines = [warning for warning in warnings or [] if warning]
    if warning_lines:
        lines.append(f"## {labels['warnings']}")
        lines.extend(f"- {warning}" for warning in warning_lines)

    return "\n".join(lines).strip()


def _slot_order(meal: Meal) -> int:
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
    return order.get(meal.meal_slot.value, 99)
