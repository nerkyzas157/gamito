"""Render graph output into user-facing text."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gamito.models.meal import Meal, ShoppingList
from gamito.models.planning import PlanConfig, ValidationResult
from gamito.models.profile import UserContext
from gamito.rendering.compact import render_compact_plan


class RenderNode:
    """Build compact text and warning lines from final graph state."""

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plan_config = _coerce_plan_config(state["plan_config"])
        user_context = _coerce_user_context(state["user_context"])
        meals = list(_coerce_meals(state.get("meals_by_key", {})).values())
        shopping_list = _coerce_shopping_list(state.get("shopping_list"))
        warnings = _warnings_from_validation(state.get("validation_result"))
        text = render_compact_plan(
            meals=meals,
            shopping_list=shopping_list,
            requested_budget_eur=plan_config.total_budget_eur,
            language=user_context.language,
            warnings=warnings,
        )
        return {"formatted_text": text, "warnings": warnings}


def _warnings_from_validation(value: Any) -> list[str]:
    if value is None:
        return []
    result = (
        value if isinstance(value, ValidationResult) else ValidationResult.model_validate(value)
    )
    return [issue.message for issue in result.issues]


def _coerce_plan_config(value: Any) -> PlanConfig:
    return value if isinstance(value, PlanConfig) else PlanConfig.model_validate(value)


def _coerce_user_context(value: Any) -> UserContext:
    return value if isinstance(value, UserContext) else UserContext.model_validate(value)


def _coerce_shopping_list(value: Any) -> ShoppingList:
    return value if isinstance(value, ShoppingList) else ShoppingList.model_validate(value or {})


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}
