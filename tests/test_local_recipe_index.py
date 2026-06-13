"""Tests for exact local recipe retrieval."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from gamito.retrieval.filters import RecipeSearchContext
from gamito.retrieval.index import (
    EmbeddingModelMismatch,
    LocalRecipeIndex,
    NoCandidates,
    pack_vector,
    unpack_vector,
)


def _index(encode_calls: list[list[str]] | None = None) -> LocalRecipeIndex:
    metadata = pd.DataFrame(
        [
            {
                "recipe_id": "r1",
                "recipe_title": "Tomato Pasta",
                "total_time_min": 25,
                "price_per_serving_eur": 2.0,
                "is_vegetarian": True,
                "is_vegan": False,
                "is_nut_free": True,
                "kitchen_tools": ["stockpot"],
                "cuisine_list": ["italian"],
                "course_list": ["main"],
                "source": "dataset",
            },
            {
                "recipe_id": "r2",
                "recipe_title": "Bean Chili",
                "total_time_min": 35,
                "price_per_serving_eur": 1.5,
                "is_vegetarian": True,
                "is_vegan": True,
                "is_nut_free": True,
                "kitchen_tools": ["stockpot"],
                "cuisine_list": ["mexican"],
                "course_list": ["main"],
                "source": "dataset",
            },
            {
                "recipe_id": "r3",
                "recipe_title": "Peanut Noodles",
                "total_time_min": 20,
                "price_per_serving_eur": 2.5,
                "is_vegetarian": True,
                "is_vegan": True,
                "is_nut_free": False,
                "kitchen_tools": ["skillet/pan"],
                "cuisine_list": ["asian"],
                "course_list": ["main"],
                "source": "dataset",
            },
        ]
    )
    embeddings = np.asarray(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    manifest = {"model": "test-model", "dims": 3, "count": 3}

    def encode_fn(texts: list[str]) -> np.ndarray:
        if encode_calls is not None:
            encode_calls.append(texts)
        vectors = {
            "pasta": [0.9, 0.1, 0.0],
            "chili": [0.0, 1.0, 0.0],
            "noodles": [0.0, 0.0, 1.0],
        }
        return np.asarray([vectors[text] for text in texts], dtype=np.float32)

    return LocalRecipeIndex(
        metadata=metadata,
        embeddings=embeddings,
        manifest=manifest,
        encode_fn=encode_fn,
        expected_model="test-model",
        expected_dims=3,
    )


class LocalRecipeIndexTests(unittest.TestCase):
    def test_search_orders_by_dot_product(self) -> None:
        results = _index().search("pasta", k=2)

        self.assertEqual([candidate.recipe_id for candidate in results], ["r1", "r2"])
        self.assertGreater(results[0].score, results[1].score)

    def test_search_many_batches_query_encoding(self) -> None:
        calls: list[list[str]] = []

        results = _index(calls).search_many(["pasta", "chili"], k=1)

        self.assertEqual(calls, [["pasta", "chili"]])
        self.assertEqual([row[0].recipe_id for row in results], ["r1", "r2"])

    def test_search_many_accepts_per_query_contexts(self) -> None:
        results = _index().search_many(
            ["pasta", "chili"],
            [
                RecipeSearchContext(preferred_cuisines=("italian",)),
                RecipeSearchContext(dietary_pref="vegan"),
            ],
            k=1,
        )

        self.assertEqual([row[0].recipe_id for row in results], ["r1", "r2"])

    def test_filters_before_scoring(self) -> None:
        results = _index().search(
            "noodles",
            k=3,
            preferred_cuisines=["asian"],
            exclude_recipe_ids=["r3"],
        )

        self.assertNotIn("r3", [candidate.recipe_id for candidate in results])

    def test_no_candidates_raises_diagnostics(self) -> None:
        with self.assertRaises(NoCandidates) as raised:
            _index().search("pasta", k=1, max_time_min=1)

        self.assertEqual(raised.exception.constraints, ("max_time_min<=1",))

    def test_manifest_model_mismatch_is_refused(self) -> None:
        with self.assertRaises(EmbeddingModelMismatch):
            LocalRecipeIndex(
                metadata=pd.DataFrame([{"recipe_id": "r1"}]),
                embeddings=np.asarray([[1.0]], dtype=np.float32),
                manifest={"model": "old-model", "dims": 1, "count": 1},
                encode_fn=lambda texts: np.ones((len(texts), 1), dtype=np.float32),
                expected_model="new-model",
                expected_dims=1,
            )

    def test_pack_unpack_vector_round_trips_float32(self) -> None:
        vector = np.asarray([0.1, 0.2, 0.3], dtype=np.float32)

        unpacked = unpack_vector(pack_vector(vector), dims=3)

        np.testing.assert_allclose(unpacked, vector)


if __name__ == "__main__":
    unittest.main()
