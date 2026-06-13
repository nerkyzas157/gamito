"""Shared LangGraph state for the deterministic meal planner."""

from __future__ import annotations

from typing import Any, TypedDict

import numpy as np

from gamito.models.meal import Meal, ShoppingList
from gamito.models.planning import BudgetPlan, PlanConfig, ValidationResult
from gamito.models.profile import UserContext


class PlanningState(TypedDict, total=False):
    """State keys exchanged by planning graph nodes."""

    plan_config: PlanConfig
    user_context: UserContext
    budget_plan: BudgetPlan
    meals_by_key: dict[str, Meal]
    pending_meal_keys: list[str]
    excluded_recipe_ids: list[str]
    validation_result: ValidationResult
    shopping_list: ShoppingList
    formatted_text: str
    warnings: list[str]
    retry_count: int
    seed: int
    rng: np.random.Generator
    preserved_slots: dict[str, Meal]
    metadata: dict[str, Any]
