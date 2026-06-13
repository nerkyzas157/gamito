"""Tests for retrieval metadata normalization."""

from __future__ import annotations

import unittest

import pandas as pd

from gamito.retrieval.metadata import build_embedding_text, normalize_recipe_metadata


class RetrievalMetadataTests(unittest.TestCase):
    def test_normalizes_aliases_and_structured_fields(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "recipe_title": "Pasta",
                    "ingredients": '["tomato", "pasta"]',
                    "directions": "['Boil pasta', 'Add sauce']",
                    "cuisine_list": '["italian"]',
                    "course_list": "['main']",
                    "kitchen_tools": '["stockpot"]',
                    "total_time": "25",
                    "price_per_serving_eur": "2.5",
                    "is_vegetarian": "True",
                    "feature_coverage": "0.75",
                }
            ]
        )

        metadata = normalize_recipe_metadata(raw, min_feature_coverage=0.5)
        row = metadata.iloc[0]

        self.assertEqual(row["recipe_id"], "dataset_00001")
        self.assertEqual(row["total_time_min"], 25)
        self.assertEqual(row["cuisine_list"], ["italian"])
        self.assertEqual(row["course_list"], ["main"])
        self.assertEqual(row["kitchen_tools"], ["stockpot"])
        self.assertEqual(row["ingredients_json"], '["tomato", "pasta"]')
        self.assertEqual(row["directions_json"], '["Boil pasta", "Add sauce"]')
        self.assertTrue(row["is_vegetarian"])

    def test_embedding_text_uses_expected_recipe_fields(self) -> None:
        row = {
            "recipe_title": "Tomato Pasta",
            "cuisine_list": ["italian"],
            "course_list": ["main"],
            "primary_taste": "savory",
            "secondary_taste": "sweet",
            "kitchen_tools": ["stockpot"],
            "ingredients_json": '["tomato", "pasta"]',
            "directions_json": '["Boil pasta", "Add sauce"]',
        }

        text = build_embedding_text(row)

        self.assertIn("Tomato Pasta", text)
        self.assertIn("italian", text)
        self.assertIn("stockpot", text)
        self.assertIn("tomato pasta", text)
        self.assertIn("Boil pasta", text)


if __name__ == "__main__":
    unittest.main()
