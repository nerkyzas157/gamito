"""Structured planning inputs and outputs shared by deterministic nodes."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gamito.models.meal import Meal, MealSlot, MealType, ShoppingList, make_meal_key
from gamito.models.profile import UserContext


class PlanType(StrEnum):
    """Planner modes exposed through MCP."""

    SINGLE = "single"
    MULTI_DAY = "multi_day"


class PlanConfig(BaseModel):
    """User request for a single meal or multi-day meal plan."""

    model_config = ConfigDict(extra="ignore")

    plan_type: PlanType = PlanType.MULTI_DAY
    total_budget_eur: float = Field(gt=0)
    servings: int = Field(ge=1)
    num_days: int = Field(default=1, ge=1, le=14)
    meals_per_day: int = Field(default=3, ge=1, le=3)
    max_time_min: int | None = Field(default=None, ge=1)

    @property
    def meal_count(self) -> int:
        """Total number of slots the graph should fill."""

        return self.num_days * self.meals_per_day


class ValidationStatus(StrEnum):
    """Validator result status."""

    PASS = "pass"
    FAIL = "fail"


class BudgetMealAllocation(BaseModel):
    """Budget assigned to a single day/slot."""

    day_number: int = Field(ge=1)
    meal_slot: MealSlot
    budget_eur: float = Field(ge=0)
    servings: int = Field(ge=1)
    meal_type: MealType = MealType.NEW
    source_slot_key: str | None = None

    @property
    def key(self) -> str:
        """Stable key matching `Meal.key`."""

        return make_meal_key(self.day_number, self.meal_slot)


class BudgetPlan(BaseModel):
    """Complete budget plan produced before slot assignment."""

    allocations: list[BudgetMealAllocation]
    total_budget_eur: float = Field(ge=0)

    def by_key(self) -> dict[str, BudgetMealAllocation]:
        """Return allocations indexed by graph state key."""

        return {allocation.key: allocation for allocation in self.allocations}


class MealSlotRequest(BaseModel):
    """Single meal-assignment task."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    plan_config: PlanConfig
    user_context: UserContext
    allocation: BudgetMealAllocation
    existing_meals: list[Meal] = Field(default_factory=list)
    excluded_recipe_ids: list[str] = Field(default_factory=list)
    retry_count: int = 0
    reason: str | None = None


class ValidationIssue(BaseModel):
    """One validator finding."""

    code: str
    message: str
    meal_key: str | None = None
    severity: str = "error"


class ValidationResult(BaseModel):
    """Budget, allergy, and variety validation outcome."""

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    replan_keys: list[str] = Field(default_factory=list)
    total_cost_eur: float = Field(default=0.0, ge=0)
    budget_limit_eur: float = Field(default=0.0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Convenience boolean for graph routing."""

        return self.status == ValidationStatus.PASS


class FormattedMealPlan(BaseModel):
    """Human-readable output plus structured plan data."""

    language: str = "en"
    text: str
    meals: list[Meal]
    shopping_list: ShoppingList
    warnings: list[str] = Field(default_factory=list)
