"""Tests for parsing list/dict fields from the salvaged recipe CSV."""

from __future__ import annotations

import unittest

from gamito.models.meal import parse_json_dict, parse_json_list


class RecipeMetadataParsingTests(unittest.TestCase):
    def test_json_list_field_parses(self) -> None:
        self.assertEqual(
            parse_json_list('["american_region", "italian"]'),
            ["american_region", "italian"],
        )

    def test_python_literal_list_field_parses(self) -> None:
        self.assertEqual(
            parse_json_list("['main', 'snack', 'bread']"),
            ["main", "snack", "bread"],
        )

    def test_json_dict_field_parses(self) -> None:
        parsed = parse_json_dict('{"calories_kcal": 649.01, "protein_g": 24.59}')

        self.assertEqual(parsed["calories_kcal"], 649.01)
        self.assertEqual(parsed["protein_g"], 24.59)

    def test_unknown_string_falls_back_to_single_item_list(self) -> None:
        self.assertEqual(parse_json_list("savory"), ["savory"])


if __name__ == "__main__":
    unittest.main()
