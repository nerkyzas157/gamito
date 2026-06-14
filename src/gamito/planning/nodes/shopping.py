"""Shopping list assembly node.

Phase 3 upgrades:

* Dedup by **canonical** ingredient name (so ``"1 tablespoon olive oil"`` and
  ``"2 tablespoons olive oil"`` collapse into one shopping line).
* Pricing comes from the offline canonical price table built in Phase 1
  (``data/lookups/canonical_prices.parquet``) instead of a 22-entry hard-coded
  dict — most rows used to display 0.00 EUR.
* Optional ingredients (``(Optional)``) are skipped: nobody puts those on a
  grocery list.
* Leftover meals are skipped because their ingredients are already covered
  on the source ``meal_prep`` slot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from gamito.models.meal import Ingredient, Meal, MealType, ShoppingItem, ShoppingList
from gamito.models.profile import UserContext
from gamito.pantry.canonicalize import is_slow_use
from gamito.pricing import (
    CanonicalPriceLookup,
    get_canonical_price_lookup,
    normalize_ingredient_name,
)
from gamito.pricing.canonical_pricing import (
    _EACH_UNIT_TOKENS,
    _G_PER_UNIT,
    _ML_PER_UNIT,
)

# Legacy fallback prices used when both the canonical and ingredient-level
# lookups come up empty. Kept small intentionally — we want canonical pricing
# to be the source of truth.
DEFAULT_PRICE_LOOKUP_EUR: dict[str, float] = {
    "rice": 1.20,
    "pasta": 1.40,
    "potato": 0.90,
    "potatoes": 0.90,
    "chicken": 4.50,
    "beef": 6.50,
    "pork": 4.80,
    "tofu": 2.80,
    "egg": 0.25,
    "eggs": 0.25,
    "milk": 1.30,
    "cheese": 7.00,
    "tomato": 1.20,
    "tomatoes": 1.20,
    "onion": 1.00,
    "onions": 1.00,
    "carrot": 1.00,
    "carrots": 1.00,
    "olive oil": 8.00,
    "flour": 0.80,
    "beans": 2.00,
    "lentils": 2.00,
}

_OPTIONAL_RE = re.compile(r"\boptional\b", re.IGNORECASE)
_MIN_LINE_PRICE_EUR = 0.10


class ShoppingListNode:
    """Deduplicate ingredients and estimate shopping cost."""

    def __init__(
        self,
        price_lookup: Mapping[str, float] | None = None,
        canonical_lookup: CanonicalPriceLookup | None = None,
    ) -> None:
        self._price_lookup = {
            key.strip().lower(): value
            for key, value in (price_lookup or DEFAULT_PRICE_LOOKUP_EUR).items()
        }
        self._canonical_lookup = canonical_lookup

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        meals = _coerce_meals(state.get("meals_by_key", {}))
        user_context = _coerce_user_context(state.get("user_context"))
        shopping_list = build_shopping_list(
            list(meals.values()),
            price_lookup=self._price_lookup,
            canonical_lookup=self._get_canonical_lookup(),
            pantry_canonicals=list(user_context.pantry_canonicals or []),
        )
        return {"shopping_list": shopping_list}

    def _get_canonical_lookup(self) -> CanonicalPriceLookup | None:
        if self._canonical_lookup is not None:
            return self._canonical_lookup
        try:
            self._canonical_lookup = get_canonical_price_lookup()
        except Exception:  # pragma: no cover - data file optional
            self._canonical_lookup = None
        return self._canonical_lookup


def build_shopping_list(
    meals: list[Meal],
    price_lookup: Mapping[str, float] | None = None,
    canonical_lookup: CanonicalPriceLookup | None = None,
    pantry_canonicals: list[str] | None = None,
) -> ShoppingList:
    """Build a deduplicated shopping list from all meal ingredients."""

    legacy_lookup = {
        key.strip().lower(): value
        for key, value in (price_lookup or DEFAULT_PRICE_LOOKUP_EUR).items()
    }

    if canonical_lookup is None:
        try:
            canonical_lookup = get_canonical_price_lookup()
        except Exception:  # pragma: no cover - data file optional
            canonical_lookup = None

    pantry_keys = {
        (c or "").strip().lower()
        for c in pantry_canonicals or []
        if c and is_slow_use(c, canonical_lookup)
    }

    aggregators: dict[str, _IngredientAggregator] = {}
    pantry_aggregators: dict[str, _IngredientAggregator] = {}

    for meal in meals:
        if meal.meal_type == MealType.LEFTOVER:
            # Leftover slots reuse the source meal — counting them again would
            # double the grocery list.
            continue
        for ingredient in meal.ingredients:
            if _is_optional(ingredient):
                continue

            canonical_name = _resolve_canonical_name(ingredient, canonical_lookup)
            key = (canonical_name or ingredient.name or "").strip().lower()
            if not key:
                continue

            bucket = pantry_aggregators if key in pantry_keys else aggregators
            aggregator = bucket.get(key)
            if aggregator is None:
                aggregator = _IngredientAggregator(
                    key=key,
                    canonical_name=canonical_name,
                    display_name=canonical_name or ingredient.name.strip(),
                )
                bucket[key] = aggregator
            aggregator.add(meal, ingredient)

    items: list[ShoppingItem] = []
    for aggregator in aggregators.values():
        price = _estimate_price(aggregator, canonical_lookup, legacy_lookup)
        items.append(
            ShoppingItem(
                name=_humanise_name(aggregator.display_name),
                amount=aggregator.format_amount(),
                estimated_price_eur=round(price, 2),
                meal_keys=sorted(aggregator.meal_keys),
            )
        )

    pantry_items: list[ShoppingItem] = []
    for aggregator in pantry_aggregators.values():
        pantry_items.append(
            ShoppingItem(
                name=_humanise_name(aggregator.display_name),
                amount=aggregator.format_amount(),
                estimated_price_eur=0.0,
                meal_keys=sorted(aggregator.meal_keys),
            )
        )

    items.sort(key=lambda item: item.name.lower())
    pantry_items.sort(key=lambda item: item.name.lower())
    item_total = round(sum(item.estimated_price_eur for item in items), 2)
    fallback_total = round(
        sum(
            meal.estimated_cost_total_eur
            for meal in meals
            if meal.meal_type != MealType.LEFTOVER
        ),
        2,
    )
    total_estimate = item_total
    if _should_use_recipe_cost_fallback(canonical_lookup) and fallback_total > item_total:
        total_estimate = fallback_total

    return ShoppingList(
        items=items,
        pantry_items=pantry_items,
        total_estimated_cost_eur=total_estimate,
    )


def _should_use_recipe_cost_fallback(
    canonical_lookup: CanonicalPriceLookup | None,
) -> bool:
    return canonical_lookup is None or not canonical_lookup.is_loaded


def _coerce_user_context(value: Any) -> UserContext:
    return (
        value if isinstance(value, UserContext) else UserContext.model_validate(value)
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _IngredientAggregator:
    """Accumulate ingredient occurrences across the plan into one shopping row."""

    __slots__ = (
        "key",
        "canonical_name",
        "display_name",
        "total_grams",
        "total_milliliters",
        "total_each",
        "unmatched_amounts",
        "ingredient_prices",
        "occurrences",
        "meal_keys",
    )

    def __init__(
        self,
        *,
        key: str,
        canonical_name: str | None,
        display_name: str,
    ) -> None:
        self.key = key
        self.canonical_name = canonical_name
        self.display_name = display_name
        self.total_grams: float = 0.0
        self.total_milliliters: float = 0.0
        self.total_each: float = 0.0
        self.unmatched_amounts: list[str] = []
        self.ingredient_prices: list[float] = []
        self.occurrences = 0
        self.meal_keys: set[str] = set()

    def add(self, meal: Meal, ingredient: Ingredient) -> None:
        self.occurrences += 1
        self.meal_keys.add(meal.key)

        quantity, unit = _resolve_quantity(ingredient)
        amount_text = ingredient.amount or _trim_quantity_prefix(ingredient.raw)

        if quantity is not None and unit is not None:
            unit_lower = unit.lower()
            if unit_lower in _G_PER_UNIT:
                self.total_grams += quantity * _G_PER_UNIT[unit_lower]
            elif unit_lower in _ML_PER_UNIT:
                self.total_milliliters += quantity * _ML_PER_UNIT[unit_lower]
            elif unit_lower in _EACH_UNIT_TOKENS:
                self.total_each += quantity
            else:
                if amount_text:
                    self.unmatched_amounts.append(amount_text)
        elif quantity is not None and unit is None:
            self.total_each += quantity
        elif amount_text:
            self.unmatched_amounts.append(amount_text)

        if ingredient.estimated_price_eur is not None:
            self.ingredient_prices.append(float(ingredient.estimated_price_eur))

    def format_amount(self) -> str | None:
        parts: list[str] = []
        if self.total_grams > 0:
            parts.append(_format_grams(self.total_grams))
        if self.total_milliliters > 0:
            parts.append(_format_milliliters(self.total_milliliters))
        if self.total_each > 0:
            quantity = (
                int(self.total_each)
                if self.total_each.is_integer()
                else round(self.total_each, 1)
            )
            parts.append(f"{quantity} vnt.")
        for amount in self.unmatched_amounts:
            parts.append(amount.strip())
        if not parts:
            return None
        seen: set[str] = set()
        deduped: list[str] = []
        for part in parts:
            if not part:
                continue
            if part in seen:
                continue
            seen.add(part)
            deduped.append(part)
        return ", ".join(deduped)


def _resolve_canonical_name(
    ingredient: Ingredient,
    lookup: CanonicalPriceLookup | None,
) -> str | None:
    if ingredient.canonical_name:
        return ingredient.canonical_name
    if lookup is None:
        return None
    raw = ingredient.raw or ingredient.name
    canonical = lookup.canonicalize(raw)
    if canonical:
        ingredient.canonical_name = canonical
    return canonical


def _is_optional(ingredient: Ingredient) -> bool:
    sources = (ingredient.raw or "", ingredient.amount or "", ingredient.name or "")
    return any(_OPTIONAL_RE.search(source) for source in sources)


def _resolve_quantity(ingredient: Ingredient) -> tuple[float | None, str | None]:
    quantity: float | None = ingredient.quantity
    unit: str | None = ingredient.unit

    if quantity is not None and unit is not None:
        return quantity, unit

    if ingredient.amount:
        parsed_qty, parsed_unit = _parse_quantity_unit(ingredient.amount)
        quantity = quantity if quantity is not None else parsed_qty
        unit = unit if unit is not None else parsed_unit

    if quantity is None and ingredient.raw:
        parsed_qty, parsed_unit = _parse_quantity_unit(ingredient.raw)
        quantity = parsed_qty
        unit = unit if unit is not None else parsed_unit

    return quantity, unit


_NUMBER_TOKEN_RE = re.compile(r"\b(" r"\d+\s*\d*/\d+|\d+/\d+|\d+\.\d+|\d+" r")\b")
_UNIT_TOKEN_RE = re.compile(r"\b([A-Za-z]+)\b")


def _parse_quantity_unit(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    quantity = _parse_quantity(text)
    unit = None
    for match in _UNIT_TOKEN_RE.finditer(text):
        token = match.group(1).lower()
        if token in _G_PER_UNIT or token in _ML_PER_UNIT or token in _EACH_UNIT_TOKENS:
            unit = token
            break
    return quantity, unit


def _parse_quantity(text: str) -> float | None:
    match = _NUMBER_TOKEN_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        if " " in raw:
            whole_part, frac = raw.split(maxsplit=1)
            whole = float(whole_part)
            num, den = frac.split("/", maxsplit=1)
            return whole + float(num) / float(den)
        if "/" in raw:
            num, den = raw.split("/", maxsplit=1)
            return float(num) / float(den)
        return float(raw)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _format_grams(grams: float) -> str:
    if grams >= 1000:
        return f"{grams / 1000:.2f} kg".replace(".00 kg", " kg")
    return f"{int(round(grams))} g"


def _format_milliliters(milliliters: float) -> str:
    if milliliters >= 1000:
        return f"{milliliters / 1000:.2f} L".replace(".00 L", " L")
    return f"{int(round(milliliters))} ml"


def _trim_quantity_prefix(text: str | None) -> str | None:
    if not text:
        return None
    norm = normalize_ingredient_name(text)
    return norm or None


def _humanise_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        return cleaned
    return cleaned[0].upper() + cleaned[1:]


def _estimate_price(
    aggregator: _IngredientAggregator,
    canonical_lookup: CanonicalPriceLookup | None,
    legacy_lookup: Mapping[str, float],
) -> float:
    canonical_price: float | None = None

    if canonical_lookup is not None and aggregator.canonical_name:
        canonical_price = _estimate_canonical_price(aggregator, canonical_lookup)

    if canonical_price is None and aggregator.ingredient_prices:
        canonical_price = sum(aggregator.ingredient_prices)

    if canonical_price is None:
        canonical_price = _legacy_lookup_price(aggregator, legacy_lookup)

    if canonical_price is None or canonical_price <= 0:
        return _MIN_LINE_PRICE_EUR

    if canonical_price < _MIN_LINE_PRICE_EUR:
        return _MIN_LINE_PRICE_EUR
    return canonical_price


def _estimate_canonical_price(
    aggregator: _IngredientAggregator,
    lookup: CanonicalPriceLookup,
) -> float | None:
    info = lookup.lookup(aggregator.canonical_name or "")
    if info is None or info.price_eur is None:
        return None

    base_unit = (info.unit or "").lower()
    quantity, unit = _aggregate_for_unit(aggregator, base_unit)
    if quantity is None:
        # Fall back to a per-occurrence estimate so users still see a
        # believable, non-zero price even when units couldn't be parsed.
        per_occurrence = lookup.estimate_price_eur(
            aggregator.canonical_name or "", None, None
        )
        if per_occurrence is None:
            return None
        return round(per_occurrence * max(aggregator.occurrences, 1), 2)
    estimate = lookup.estimate_price_eur(
        aggregator.canonical_name or "", quantity, unit
    )
    return estimate


def _aggregate_for_unit(
    aggregator: _IngredientAggregator,
    base_unit: str,
) -> tuple[float | None, str | None]:
    if base_unit == "kg":
        if aggregator.total_grams > 0:
            return aggregator.total_grams, "g"
        return None, None
    if base_unit == "l":
        if aggregator.total_milliliters > 0:
            return aggregator.total_milliliters, "ml"
        return None, None
    if base_unit == "each":
        if aggregator.total_each > 0:
            return aggregator.total_each, "each"
        # Treat each occurrence as one unit when quantities are missing.
        return float(aggregator.occurrences or 1), "each"
    return None, None


def _legacy_lookup_price(
    aggregator: _IngredientAggregator,
    lookup: Mapping[str, float],
) -> float | None:
    name = (aggregator.canonical_name or aggregator.display_name or "").strip().lower()
    if not name:
        return None
    if name in lookup:
        return lookup[name]
    for legacy_key, price in lookup.items():
        if legacy_key in name or name in legacy_key:
            return price
    return None


def _coerce_meals(value: Any) -> dict[str, Meal]:
    if isinstance(value, dict):
        return {
            key: meal if isinstance(meal, Meal) else Meal.model_validate(meal)
            for key, meal in value.items()
        }
    return {}
