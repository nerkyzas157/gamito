"""Pydantic models for meals, ingredients, shopping lists, and plans."""

from __future__ import annotations

import ast
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MealSlot(StrEnum):
    """Supported meal slots in the MVP planner."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class MealType(StrEnum):
    """How the meal should be treated by the UI."""

    NEW = "new"
    MEAL_PREP = "meal_prep"
    LEFTOVER = "leftover"


class MealSource(StrEnum):
    """Where a meal recipe came from."""

    DATASET = "dataset"
    CUSTOM = "custom"


class Nutrition(BaseModel):
    """Nutrition values shown per meal or per serving."""

    model_config = ConfigDict(extra="ignore")

    calories_kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class Ingredient(BaseModel):
    """Ingredient display data with optional price estimate."""

    model_config = ConfigDict(extra="ignore")

    name: str
    amount: str | None = None
    quantity: float | None = None
    unit: str | None = None
    estimated_price_eur: float | None = None
    raw: str | None = None
    canonical_name: str | None = None

    @property
    def shopping_key(self) -> str:
        """Normalized key used when deduplicating shopping items.

        Prefers the canonical name resolved against the offline lookup table
        so that ``"1 tablespoon olive oil"`` and ``"2 tablespoons olive oil"``
        merge into a single shopping line. Falls back to a lowercased ``name``
        when canonicalization isn't available.
        """

        if self.canonical_name:
            return self.canonical_name.strip().lower()
        return self.name.strip().lower()


class Meal(BaseModel):
    """Structured meal returned by a meal agent and persisted by the UI layer."""

    model_config = ConfigDict(extra="ignore")

    day_number: int = Field(ge=1)
    meal_slot: MealSlot
    recipe_title: str
    servings: int = Field(ge=1)
    allocated_budget_eur: float = Field(ge=0)
    estimated_cost_total_eur: float = Field(ge=0)
    estimated_cost_per_serving_eur: float = Field(ge=0)
    recipe_id: str | None = None
    meal_type: MealType = MealType.NEW
    source_slot_key: str | None = None
    source: MealSource = MealSource.DATASET
    prep_time_min: int | None = Field(default=None, ge=0)
    cook_time_min: int | None = Field(default=None, ge=0)
    total_time_min: int | None = Field(default=None, ge=0)
    difficulty: str | None = None
    cuisine_list: list[str] = Field(default_factory=list)
    course_list: list[str] = Field(default_factory=list)
    kitchen_tools: list[str] = Field(default_factory=list)
    ingredients: list[Ingredient] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    nutrition_per_serving: Nutrition | None = None
    dietary_flags: dict[str, bool] = Field(default_factory=dict)
    healthiness_score: int | None = Field(default=None, ge=0, le=100)
    raw_candidate: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable state key for graph aggregation and re-planning."""

        return make_meal_key(self.day_number, self.meal_slot)

    @field_validator("recipe_title")
    @classmethod
    def recipe_title_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("recipe_title cannot be empty")
        return value.strip()


class ShoppingItem(BaseModel):
    """Deduplicated shopping-list row."""

    name: str
    amount: str | None = None
    estimated_price_eur: float = Field(default=0.0, ge=0)
    meal_keys: list[str] = Field(default_factory=list)


class ShoppingList(BaseModel):
    """Full deduplicated shopping list for a generated plan."""

    items: list[ShoppingItem] = Field(default_factory=list)
    pantry_items: list[ShoppingItem] = Field(
        default_factory=list,
        description="Ingredients treated as already at home; priced at 0 EUR.",
    )
    total_estimated_cost_eur: float = Field(default=0.0, ge=0)


class MealPlan(BaseModel):
    """Final structured plan produced by the graph."""

    user_id: str | None = None
    meals: list[Meal] = Field(default_factory=list)
    shopping_list: ShoppingList | None = None
    total_budget_eur: float = Field(ge=0)
    total_estimated_cost_eur: float = Field(default=0.0, ge=0)
    language: str = "lt"
    formatted_text: str | None = None
    warnings: list[str] = Field(default_factory=list)


def make_meal_key(day_number: int, meal_slot: MealSlot | str) -> str:
    """Return the canonical state key for a meal slot."""

    slot = meal_slot.value if isinstance(meal_slot, MealSlot) else str(meal_slot)
    return f"day_{day_number}:{slot}"


def parse_json_list(value: Any) -> list[Any]:
    """Decode list-like metadata values from local recipe metadata.

    The salvaged CSV contains both JSON arrays (``["italian"]``) and Python
    literal lists (``['main']``), so the parser accepts either format.
    """

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        decoded = _decode_structured_string(stripped)
        return decoded if isinstance(decoded, list) else [decoded]
    return [value]


def parse_json_dict(value: Any) -> dict[str, Any]:
    """Decode dict-like metadata values from local recipe metadata."""

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = _decode_structured_string(value.strip())
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _decode_structured_string(value: str) -> Any:
    """Decode JSON first, then safe Python literals for legacy CSV fields."""

    if not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value
