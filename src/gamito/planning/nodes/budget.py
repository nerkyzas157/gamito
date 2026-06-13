"""Budget allocation node for the meal-planning graph."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from gamito.models.meal import Meal, MealSlot, MealType, make_meal_key
from gamito.models.planning import (
    BudgetMealAllocation,
    BudgetPlan,
    PlanConfig,
    PlanType,
)
from gamito.models.profile import UserContext

BudgetPlanner = Callable[[PlanConfig, UserContext], BudgetPlan | Awaitable[BudgetPlan]]

MEAL_SLOTS_BY_COUNT: dict[int, list[MealSlot]] = {
    1: [MealSlot.DINNER],
    2: [MealSlot.LUNCH, MealSlot.DINNER],
    3: [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
}

DEFAULT_SLOT_WEIGHTS: dict[MealSlot, float] = {
    MealSlot.BREAKFAST: 0.22,
    MealSlot.LUNCH: 0.33,
    MealSlot.DINNER: 0.45,
    MealSlot.SNACK: 0.15,
}


class BudgetPlannerNode:
    """Create a per-slot budget plan.

    A custom planner can be injected for tests. Without one, the node uses a
    deterministic allocation that keeps the graph runnable offline and still
    validates the output structure.
    """

    def __init__(self, planner: BudgetPlanner | None = None) -> None:
        self._planner = planner

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plan_config = _coerce_plan_config(state["plan_config"])
        user_context = _coerce_user_context(state["user_context"])

        if self._planner is None:
            budget_plan = allocate_budget_deterministically(plan_config, user_context)
        else:
            result = self._planner(plan_config, user_context)
            budget_plan = await result if inspect.isawaitable(result) else result

        budget_plan = BudgetPlan.model_validate(budget_plan)
        preserved = _coerce_meals(state.get("preserved_slots", {}))
        if preserved:
            budget_plan = _fold_preserved_costs(budget_plan, preserved)
        return {
            "budget_plan": budget_plan,
            "pending_meal_keys": [
                allocation.key for allocation in budget_plan.allocations
                if allocation.key not in preserved
            ],
        }


def allocate_budget_deterministically(
    plan_config: PlanConfig,
    user_context: UserContext | None = None,
) -> BudgetPlan:
    """Allocate the total budget across configured meal slots.

    When ``user_context.leftovers_ok`` and ``user_context.meal_prep_ok`` are
    both enabled and the plan spans more than one day, every day-N+1 lunch is
    flagged as a leftover sourced from the day-N dinner. The freed budget is
    folded into the source slot so the cook recipe scales for two servings.
    """

    slots = _slots_for_config(plan_config)
    per_day_weight = sum(DEFAULT_SLOT_WEIGHTS[slot] for slot in slots)
    allocations: list[BudgetMealAllocation] = []

    day_count = 1 if plan_config.plan_type == PlanType.SINGLE else plan_config.num_days
    for day_number in range(1, day_count + 1):
        for slot in slots:
            day_budget = plan_config.total_budget_eur / day_count
            raw_budget = day_budget * (DEFAULT_SLOT_WEIGHTS[slot] / per_day_weight)
            allocations.append(
                BudgetMealAllocation(
                    day_number=day_number,
                    meal_slot=slot,
                    budget_eur=round(raw_budget, 2),
                    servings=plan_config.servings,
                )
            )

    _apply_leftover_routing(allocations, slots, user_context, day_count)
    _rebalance_rounding(allocations, plan_config.total_budget_eur)
    return BudgetPlan(
        allocations=allocations,
        total_budget_eur=round(plan_config.total_budget_eur, 2),
    )


def _slots_for_config(plan_config: PlanConfig) -> list[MealSlot]:
    if plan_config.plan_type == PlanType.SINGLE:
        return [MealSlot.DINNER]
    return MEAL_SLOTS_BY_COUNT[plan_config.meals_per_day]


def _apply_leftover_routing(
    allocations: list[BudgetMealAllocation],
    slots: list[MealSlot],
    user_context: UserContext | None,
    day_count: int,
) -> None:
    """Mark next-day lunches as leftovers from prior-day dinners.

    Skipped entirely when leftover/meal-prep are disabled, when the plan only
    covers a single day, or when the slot configuration doesn't include both
    a lunch and a dinner.
    """

    if user_context is None:
        return
    if not (user_context.leftovers_ok and user_context.meal_prep_ok):
        return
    if day_count < 2:
        return
    if MealSlot.DINNER not in slots or MealSlot.LUNCH not in slots:
        return

    by_key = {alloc.key: alloc for alloc in allocations}

    for day in range(2, day_count + 1):
        target_key = make_meal_key(day, MealSlot.LUNCH)
        source_key = make_meal_key(day - 1, MealSlot.DINNER)
        target = by_key.get(target_key)
        source = by_key.get(source_key)
        if target is None or source is None:
            continue
        if target.meal_type != MealType.NEW or source.meal_type != MealType.NEW:
            continue

        freed_budget = target.budget_eur
        target.meal_type = MealType.LEFTOVER
        target.source_slot_key = source.key
        target.budget_eur = 0.0

        source.meal_type = MealType.MEAL_PREP
        source.budget_eur = round(source.budget_eur + freed_budget, 2)


def _rebalance_rounding(
    allocations: list[BudgetMealAllocation],
    total_budget_eur: float,
) -> None:
    if not allocations:
        return

    current_total = round(sum(item.budget_eur for item in allocations), 2)
    delta = round(total_budget_eur - current_total, 2)
    if delta == 0:
        return
    # Apply the rounding delta to the last `new`/`meal_prep` allocation so the
    # leftover slots stay at exactly 0.0 EUR.
    for allocation in reversed(allocations):
        if allocation.meal_type != MealType.LEFTOVER:
            allocation.budget_eur = round(allocation.budget_eur + delta, 2)
            return
    allocations[-1].budget_eur = round(allocations[-1].budget_eur + delta, 2)


def _fold_preserved_costs(
    budget_plan: BudgetPlan,
    preserved: dict[str, Meal],
) -> BudgetPlan:
    """Reserve known preserved-slot cost and scale the remaining allocations."""

    allocations = [allocation.model_copy(deep=True) for allocation in budget_plan.allocations]
    remaining = [
        allocation
        for allocation in allocations
        if allocation.key not in preserved and allocation.meal_type != MealType.LEFTOVER
    ]
    preserved_cost = 0.0
    for allocation in allocations:
        meal = preserved.get(allocation.key)
        if meal is None:
            continue
        allocation.budget_eur = round(meal.estimated_cost_total_eur, 2)
        allocation.meal_type = meal.meal_type
        allocation.source_slot_key = meal.source_slot_key
        preserved_cost += allocation.budget_eur

    current_remaining = round(sum(allocation.budget_eur for allocation in remaining), 2)
    target_remaining = max(round(budget_plan.total_budget_eur - preserved_cost, 2), 0.0)
    if current_remaining > 0 and remaining:
        scale = target_remaining / current_remaining
        for allocation in remaining:
            allocation.budget_eur = round(allocation.budget_eur * scale, 2)
    _rebalance_rounding(allocations, budget_plan.total_budget_eur)
    return BudgetPlan(allocations=allocations, total_budget_eur=budget_plan.total_budget_eur)


def _coerce_plan_config(value: Any) -> PlanConfig:
    return value if isinstance(value, PlanConfig) else PlanConfig.model_validate(value)


def _coerce_user_context(value: Any) -> UserContext:
    return (
        value if isinstance(value, UserContext) else UserContext.model_validate(value)
    )


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}
