"""Offline tests for the slow-use canonicaliser."""

from __future__ import annotations

import unittest
from pathlib import Path

from gamito.models.pantry import DetectedIngredient
from gamito.pantry.canonicalize import (
    is_slow_use,
    merge_detections,
    resolve_to_canonical,
)
from gamito.pricing.canonical_pricing import CanonicalPriceLookup, PriceInfo

_PARQUET_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "lookups"
    / "canonical_prices.parquet"
)


def _build_fixture_lookup() -> CanonicalPriceLookup:
    prices = {
        "olive oil": PriceInfo(
            canonical="olive oil", price_eur=8.0, unit="L", category="pantry"
        ),
        "salt": PriceInfo(
            canonical="salt", price_eur=0.6, unit="kg", category="pantry"
        ),
        "black pepper": PriceInfo(
            canonical="black pepper", price_eur=2.5, unit="each", category="spices"
        ),
        "milk": PriceInfo(
            canonical="milk", price_eur=1.2, unit="L", category="dairy"
        ),
        "water": PriceInfo(
            canonical="water", price_eur=0.3, unit="L", category="beverage"
        ),
        "orange juice": PriceInfo(
            canonical="orange juice", price_eur=2.0, unit="L", category="beverage"
        ),
        "chicken": PriceInfo(
            canonical="chicken", price_eur=4.5, unit="kg", category="meat"
        ),
        "tomato": PriceInfo(
            canonical="tomato", price_eur=2.5, unit="kg", category="produce"
        ),
        "leftover_rice": PriceInfo(
            canonical="leftover_rice", price_eur=0.0, unit="kg", category="pantry"
        ),
    }
    parsed_to_canonical = {
        "extra virgin olive oil": "olive oil",
        "olive oil": "olive oil",
        "salt": "salt",
        "milk": "milk",
        "chicken breast": "chicken",
    }
    return CanonicalPriceLookup(prices=prices, parsed_to_canonical=parsed_to_canonical)


class ResolveToCanonicalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = _build_fixture_lookup()

    def test_resolves_via_parsed_table(self) -> None:
        self.assertEqual(
            resolve_to_canonical("2 tablespoons extra virgin olive oil", self.lookup),
            "olive oil",
        )

    def test_returns_none_for_blank(self) -> None:
        self.assertIsNone(resolve_to_canonical("   ", self.lookup))

    def test_returns_none_for_unknown(self) -> None:
        self.assertIsNone(resolve_to_canonical("dragonfruit puree", self.lookup))


class IsSlowUseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = _build_fixture_lookup()

    def test_pantry_category_passes(self) -> None:
        self.assertTrue(is_slow_use("salt", self.lookup))
        self.assertTrue(is_slow_use("olive oil", self.lookup))

    def test_spices_category_passes(self) -> None:
        self.assertTrue(is_slow_use("black pepper", self.lookup))

    def test_dairy_category_passes(self) -> None:
        self.assertTrue(is_slow_use("milk", self.lookup))

    def test_meat_category_blocked(self) -> None:
        self.assertFalse(is_slow_use("chicken", self.lookup))

    def test_produce_category_blocked(self) -> None:
        self.assertFalse(is_slow_use("tomato", self.lookup))

    def test_beverage_only_water_passes(self) -> None:
        self.assertTrue(is_slow_use("water", self.lookup))
        self.assertFalse(is_slow_use("orange juice", self.lookup))

    def test_leftover_prefix_blocked(self) -> None:
        self.assertFalse(is_slow_use("leftover_rice", self.lookup))

    def test_unknown_canonical_blocked(self) -> None:
        self.assertFalse(is_slow_use("not_in_table", self.lookup))


class MergeDetectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = _build_fixture_lookup()

    def test_merges_llm_and_manual_dedupes(self) -> None:
        raw = [
            DetectedIngredient(raw_label="olive oil", confidence=0.9),
            DetectedIngredient(raw_label="chicken breast", confidence=0.8),
            DetectedIngredient(raw_label="extra virgin olive oil", confidence=0.7),
        ]
        analysis = merge_detections(raw, manual_extras=["salt"], lookup=self.lookup)

        canonicals = {d.canonical for d in analysis.detections}
        self.assertIn("olive oil", canonicals)
        self.assertIn("salt", canonicals)
        self.assertNotIn("chicken", canonicals)
        # Olive oil must appear once even though two raw labels mapped to it.
        self.assertEqual(
            sum(1 for d in analysis.detections if d.canonical == "olive oil"), 1
        )
        # The chicken breast detection is rejected (perishable / produce-like).
        self.assertIn("chicken breast", analysis.rejected)

    def test_unknown_labels_land_in_rejected(self) -> None:
        raw = [DetectedIngredient(raw_label="dragonfruit puree", confidence=0.5)]
        analysis = merge_detections(raw, manual_extras=None, lookup=self.lookup)
        self.assertEqual(analysis.detections, [])
        self.assertIn("dragonfruit puree", analysis.rejected)


@unittest.skipUnless(
    _PARQUET_PATH.exists(),
    "data/lookups/canonical_prices.parquet not present",
)
class IsSlowUseProductionLookupTests(unittest.TestCase):
    """Smoke-test the production parquet mapping for a few well-known items."""

    def test_olive_oil_is_slow_use(self) -> None:
        self.assertTrue(is_slow_use("olive oil"))

    def test_salt_is_slow_use(self) -> None:
        self.assertTrue(is_slow_use("salt"))

    def test_chicken_not_slow_use(self) -> None:
        self.assertFalse(is_slow_use("chicken"))


if __name__ == "__main__":
    unittest.main()
