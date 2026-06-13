"""Offline tests for canonical-ingredient pricing utilities."""

from __future__ import annotations

import unittest

from gamito.pricing.canonical_pricing import (
    CanonicalPriceLookup,
    PriceInfo,
    normalize_ingredient_name,
)


def _build_fixture_lookup() -> CanonicalPriceLookup:
    """Hand-rolled lookup that mirrors the parquet schema used in production."""

    prices = {
        "olive oil": PriceInfo(
            canonical="olive oil", price_eur=8.0, unit="L", category="oils"
        ),
        "red potato": PriceInfo(
            canonical="red potato",
            price_eur=2.5,
            unit="kg",
            category="vegetables",
        ),
        "salmon fillet": PriceInfo(
            canonical="salmon fillet",
            price_eur=5.0,
            unit="each",
            category="fish",
        ),
        "tomato": PriceInfo(
            canonical="tomato", price_eur=3.5, unit="kg", category="vegetables"
        ),
        "egg": PriceInfo(
            canonical="egg", price_eur=0.4, unit="each", category="dairy"
        ),
    }
    parsed_to_canonical = {
        "extra virgin olive oil": "olive oil",
        "olive oil": "olive oil",
        "red potatoes": "red potato",
        "potatoes": "red potato",
        "salmon fillets": "salmon fillet",
        "salmon fillet": "salmon fillet",
        "ripe tomatoes": "tomato",
        "tomatoes": "tomato",
        "large eggs": "egg",
        "eggs": "egg",
    }
    return CanonicalPriceLookup(
        prices=prices, parsed_to_canonical=parsed_to_canonical
    )


class NormalizeIngredientNameTests(unittest.TestCase):
    def test_strips_quantity_and_unit(self) -> None:
        self.assertEqual(
            normalize_ingredient_name("2 tablespoons extra-virgin olive oil"),
            "extra-virgin olive oil",
        )

    def test_drops_optional_modifier(self) -> None:
        self.assertEqual(
            normalize_ingredient_name("1 cup (Optional) chopped parsley"),
            "chopped parsley",
        )

    def test_keeps_each_class_token_when_it_is_the_ingredient(self) -> None:
        # "eggs" must survive normalisation so the canonicaliser can match it.
        self.assertIn("eggs", normalize_ingredient_name("2 large eggs, beaten"))

    def test_returns_empty_for_empty_input(self) -> None:
        self.assertEqual(normalize_ingredient_name(""), "")

    def test_handles_unicode_fractions(self) -> None:
        self.assertEqual(
            normalize_ingredient_name("½ cup chopped onions"),
            "chopped onions",
        )


class CanonicalPriceLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = _build_fixture_lookup()

    def test_canonicalize_via_parsed_table(self) -> None:
        self.assertEqual(
            self.lookup.canonicalize("2 tablespoons extra virgin olive oil"),
            "olive oil",
        )

    def test_canonicalize_strips_optional(self) -> None:
        self.assertEqual(
            self.lookup.canonicalize("1 cup (Optional) red potatoes"),
            "red potato",
        )

    def test_canonicalize_falls_back_to_stem(self) -> None:
        # Not in parsed_to_canonical but stems to a known canonical.
        self.assertEqual(self.lookup.canonicalize("ripe tomatoes"), "tomato")

    def test_canonicalize_returns_none_when_unknown(self) -> None:
        self.assertIsNone(self.lookup.canonicalize("dragonfruit puree"))

    def test_estimate_price_kg_unit(self) -> None:
        # 500 g of red potato @ 2.5 EUR/kg = 1.25 EUR
        price = self.lookup.estimate_price_eur("red potato", 500, "g")
        self.assertEqual(price, 1.25)

    def test_estimate_price_l_unit(self) -> None:
        # 250 ml of olive oil @ 8 EUR/L = 2.0 EUR
        price = self.lookup.estimate_price_eur("olive oil", 250, "ml")
        self.assertEqual(price, 2.0)

    def test_estimate_price_each_unit(self) -> None:
        # 2 salmon fillets @ 5 EUR each = 10 EUR
        price = self.lookup.estimate_price_eur("salmon fillet", 2, "each")
        self.assertEqual(price, 10.0)

    def test_estimate_price_falls_back_when_unit_unknown(self) -> None:
        # Unknown unit but kg canonical -> half-pack heuristic (4.0 EUR).
        price = self.lookup.estimate_price_eur("red potato", None, None)
        self.assertEqual(price, round(2.5 * 0.5, 2))

    def test_estimate_price_returns_none_for_missing_canonical(self) -> None:
        self.assertIsNone(
            self.lookup.estimate_price_eur("dragonfruit", 100, "g")
        )

    def test_estimate_price_lower_bounds_quantity(self) -> None:
        # 1 g of red potato should not produce a 0.0 EUR estimate.
        price = self.lookup.estimate_price_eur("red potato", 1, "g")
        self.assertGreater(price, 0.0)

    def test_lookup_is_case_insensitive(self) -> None:
        info = self.lookup.lookup("OLIVE OIL")
        self.assertIsNotNone(info)
        self.assertEqual(info.unit, "L")


if __name__ == "__main__":
    unittest.main()
