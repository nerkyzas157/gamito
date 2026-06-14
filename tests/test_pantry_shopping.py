"""Integration tests: ShoppingListNode splits pantry vs purchasable items."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from gamito.planning.nodes.shopping import ShoppingListNode, build_shopping_list
from gamito.models.meal import Ingredient, Meal, MealSlot, ShoppingItem
from gamito.models.profile import UserContext
from gamito.pricing.canonical_pricing import CanonicalPriceLookup, PriceInfo


def _build_lookup() -> CanonicalPriceLookup:
    prices = {
        "olive oil": PriceInfo(
            canonical="olive oil", price_eur=8.0, unit="L", category="pantry"
        ),
        "salt": PriceInfo(
            canonical="salt", price_eur=0.6, unit="kg", category="pantry"
        ),
        "chicken": PriceInfo(
            canonical="chicken", price_eur=4.5, unit="kg", category="meat"
        ),
    }
    parsed_to_canonical = {
        "olive oil": "olive oil",
        "extra virgin olive oil": "olive oil",
        "salt": "salt",
        "chicken breast": "chicken",
    }
    return CanonicalPriceLookup(prices=prices, parsed_to_canonical=parsed_to_canonical)


def _build_meal() -> Meal:
    return Meal(
        day_number=1,
        meal_slot=MealSlot.DINNER,
        recipe_title="Chicken with olive oil",
        servings=2,
        allocated_budget_eur=8.0,
        estimated_cost_total_eur=6.0,
        estimated_cost_per_serving_eur=3.0,
        ingredients=[
            Ingredient(
                name="chicken breast",
                amount="400 g",
                quantity=400,
                unit="g",
                canonical_name="chicken",
            ),
            Ingredient(
                name="olive oil",
                amount="1 tbsp",
                quantity=1,
                unit="tbsp",
                canonical_name="olive oil",
            ),
            Ingredient(
                name="salt",
                amount="1 tsp",
                quantity=1,
                unit="tsp",
                canonical_name="salt",
            ),
        ],
    )


class BuildShoppingListPantryTests(unittest.TestCase):
    def test_empty_pantry_keeps_all_in_items(self) -> None:
        sl = build_shopping_list(
            [_build_meal()],
            canonical_lookup=_build_lookup(),
            pantry_canonicals=[],
        )
        names = sorted(item.name.lower() for item in sl.items)
        self.assertEqual(names, ["chicken", "olive oil", "salt"])
        self.assertEqual(sl.pantry_items, [])

    def test_olive_oil_in_pantry_removes_it_from_items(self) -> None:
        sl = build_shopping_list(
            [_build_meal()],
            canonical_lookup=_build_lookup(),
            pantry_canonicals=["olive oil"],
        )
        item_names = {item.name.lower() for item in sl.items}
        pantry_names = {item.name.lower() for item in sl.pantry_items}
        self.assertNotIn("olive oil", item_names)
        self.assertIn("olive oil", pantry_names)
        # Pantry item priced at 0 EUR, total reflects only purchasable items.
        self.assertEqual(sl.pantry_items[0].estimated_price_eur, 0.0)
        self.assertEqual(
            sl.total_estimated_cost_eur,
            round(sum(item.estimated_price_eur for item in sl.items), 2),
        )

    def test_unknown_pantry_name_is_ignored(self) -> None:
        sl = build_shopping_list(
            [_build_meal()],
            canonical_lookup=_build_lookup(),
            pantry_canonicals=["unicorn meat"],
        )
        # Unknown / non-slow-use canonicals don't move anything to pantry.
        self.assertEqual(sl.pantry_items, [])
        names = sorted(item.name.lower() for item in sl.items)
        self.assertEqual(names, ["chicken", "olive oil", "salt"])

    def test_meat_canonical_not_treated_as_pantry(self) -> None:
        # Even if a user somehow added "chicken" to their pantry, the
        # is_slow_use predicate must keep meat in the purchase list.
        sl = build_shopping_list(
            [_build_meal()],
            canonical_lookup=_build_lookup(),
            pantry_canonicals=["chicken"],
        )
        item_names = {item.name.lower() for item in sl.items}
        self.assertIn("chicken", item_names)
        self.assertEqual(sl.pantry_items, [])

    def test_recipe_estimate_is_used_when_legacy_pricing_undercounts(self) -> None:
        with patch(
            "gamito.planning.nodes.shopping.get_canonical_price_lookup",
            side_effect=FileNotFoundError,
        ):
            sl = build_shopping_list(
                [_build_meal()],
                price_lookup={"chicken": 1.0},
                canonical_lookup=None,
                pantry_canonicals=[],
            )

        self.assertEqual(sl.total_estimated_cost_eur, 6.0)

    def test_recipe_estimate_is_used_when_canonical_lookup_is_empty(self) -> None:
        from gamito.pricing.canonical_pricing import CanonicalPriceLookup

        sl = build_shopping_list(
            [_build_meal()],
            price_lookup={"chicken": 1.0},
            canonical_lookup=CanonicalPriceLookup(prices={}, parsed_to_canonical={}),
            pantry_canonicals=[],
        )

        self.assertEqual(sl.total_estimated_cost_eur, 6.0)


class ShoppingListNodeIntegrationTests(unittest.TestCase):
    def test_node_passes_pantry_canonicals_through(self) -> None:
        meal = _build_meal()
        state = {
            "meals_by_key": {meal.key: meal},
            "user_context": UserContext(pantry_canonicals=["olive oil", "salt"]),
        }
        node = ShoppingListNode(canonical_lookup=_build_lookup())
        result = asyncio.run(node(state))
        sl = result["shopping_list"]
        purchase_names = {item.name.lower() for item in sl.items}
        pantry_names = {item.name.lower() for item in sl.pantry_items}
        self.assertEqual(purchase_names, {"chicken"})
        self.assertEqual(pantry_names, {"olive oil", "salt"})


class ShoppingItemSerialisationTests(unittest.TestCase):
    def test_pantry_items_round_trip(self) -> None:
        from gamito.models.meal import ShoppingList

        original = ShoppingList(
            items=[ShoppingItem(name="chicken", estimated_price_eur=4.5)],
            pantry_items=[ShoppingItem(name="salt", estimated_price_eur=0.0)],
        )
        roundtripped = ShoppingList(**original.model_dump())
        self.assertEqual(roundtripped.pantry_items[0].name, "salt")


if __name__ == "__main__":
    unittest.main()
