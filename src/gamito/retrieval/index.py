"""Exact local vector search over the committed recipe index."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from gamito.retrieval.encoder import DEFAULT_MODEL, DIMS, encode
from gamito.retrieval.filters import RecipeSearchContext, apply_filters_with_relaxation

EncodeFn = Callable[[list[str]], np.ndarray]


class RetrievalError(Exception):
    """Base exception for retrieval failures."""

    error_code = "RETRIEVAL_ERROR"


class EmbeddingModelMismatch(RetrievalError):
    """Raised when an index or vector was built with an incompatible model."""

    error_code = "EMBEDDING_MODEL_MISMATCH"

    def __init__(self, *, expected: tuple[str, int], got: tuple[str, int]) -> None:
        super().__init__(
            f"expected embedding model {expected[0]} ({expected[1]} dims), "
            f"got {got[0]} ({got[1]} dims)"
        )
        self.expected = expected
        self.got = got


class NoCandidates(RetrievalError):
    """Raised when hard filters and the relaxation ladder leave no rows."""

    error_code = "NO_CANDIDATES"

    def __init__(self, constraints: list[str] | tuple[str, ...]) -> None:
        constraints_tuple = tuple(constraints)
        super().__init__(
            "Constraints emptying the pool: "
            + (", ".join(constraints_tuple) if constraints_tuple else "unknown")
        )
        self.constraints = constraints_tuple


@dataclass(frozen=True)
class RecipeCandidate:
    """One scored recipe returned from local retrieval."""

    recipe_id: str
    title: str
    score: float
    metadata: dict[str, Any]
    relaxed_constraints: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", "dataset"))


class LocalRecipeIndex:
    """Load static index artifacts and run exact cosine retrieval."""

    def __init__(
        self,
        *,
        metadata: pd.DataFrame,
        embeddings: np.ndarray,
        manifest: dict[str, Any],
        encode_fn: EncodeFn | None = None,
        expected_model: str = DEFAULT_MODEL,
        expected_dims: int = DIMS,
    ) -> None:
        assert_compatible(manifest, model=expected_model, dims=expected_dims)
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError(f"expected embedding matrix to be 2D, got {matrix.shape}")
        if matrix.shape[0] != len(metadata):
            raise ValueError(
                f"manifest/index row mismatch: {matrix.shape[0]} vectors for "
                f"{len(metadata)} metadata rows"
            )
        if matrix.shape[1] != int(manifest["dims"]):
            raise EmbeddingModelMismatch(
                expected=(str(manifest["model"]), int(manifest["dims"])),
                got=(str(manifest["model"]), int(matrix.shape[1])),
            )
        if int(manifest["count"]) != len(metadata):
            raise ValueError(
                f"manifest count {manifest['count']} does not match metadata "
                f"rows {len(metadata)}"
            )

        self.metadata = _with_cached_filter_sets(metadata.reset_index(drop=True))
        self.embeddings = matrix
        self.manifest = manifest
        model = str(manifest["model"])
        self._encode_fn = encode_fn or (lambda texts: encode(texts, model=model))
        self._static_metadata = self.metadata.copy()
        self._static_embeddings = self.embeddings.copy()
        self._custom_conn: sqlite3.Connection | None = None
        self._custom_revision: int | None = None

    @classmethod
    def load(
        cls,
        index_dir: str | Path,
        *,
        encode_fn: EncodeFn | None = None,
        expected_model: str = DEFAULT_MODEL,
        expected_dims: int = DIMS,
    ) -> "LocalRecipeIndex":
        """Load ``embeddings.npy``, ``metadata.parquet``, and ``manifest.json``."""

        root = Path(index_dir)
        with (root / "manifest.json").open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        metadata = pd.read_parquet(root / "metadata.parquet")
        embeddings = np.load(root / "embeddings.npy")
        return cls(
            metadata=metadata,
            embeddings=embeddings,
            manifest=manifest,
            encode_fn=encode_fn,
            expected_model=expected_model,
            expected_dims=expected_dims,
        )

    def search(
        self,
        query: str,
        ctx: RecipeSearchContext | Any = None,
        *,
        k: int = 10,
        max_time_min: int | None = None,
        max_price_per_serving: float | None = None,
        exclude_recipe_ids: list[str] | tuple[str, ...] | None = None,
        preferred_cuisines: list[str] | tuple[str, ...] | None = None,
        course: str | None = None,
        include_custom: bool = True,
    ) -> list[RecipeCandidate]:
        """Search for one query string."""

        return self.search_many(
            [query],
            ctx,
            k=k,
            max_time_min=max_time_min,
            max_price_per_serving=max_price_per_serving,
            exclude_recipe_ids=exclude_recipe_ids,
            preferred_cuisines=preferred_cuisines,
            course=course,
            include_custom=include_custom,
        )[0]

    def search_many(
        self,
        queries: list[str],
        ctx: RecipeSearchContext | Any | list[Any] | tuple[Any, ...] = None,
        *,
        k: int = 10,
        max_time_min: int | None = None,
        max_price_per_serving: float | None = None,
        exclude_recipe_ids: list[str] | tuple[str, ...] | None = None,
        preferred_cuisines: list[str] | tuple[str, ...] | None = None,
        course: str | None = None,
        include_custom: bool = True,
    ) -> list[list[RecipeCandidate]]:
        """Encode queries once, filter rows, score survivors, and return top-k."""

        if k <= 0:
            return [[] for _ in queries]
        if not queries:
            return []
        self._refresh_custom_if_needed()

        query_vectors = np.asarray(self._encode_fn(queries), dtype=np.float32)
        if query_vectors.shape != (len(queries), self.embeddings.shape[1]):
            raise ValueError(
                "query encoder returned shape "
                f"{query_vectors.shape}, expected {(len(queries), self.embeddings.shape[1])}"
            )

        contexts = _contexts_for_queries(
            queries,
            ctx,
            max_time_min=max_time_min,
            max_price_per_serving=max_price_per_serving,
            exclude_recipe_ids=exclude_recipe_ids,
            preferred_cuisines=preferred_cuisines,
            course=course,
        )
        results: list[list[RecipeCandidate]] = []
        for query_vector, search_ctx in zip(query_vectors, contexts, strict=True):
            metadata = self.metadata
            if not include_custom and "source" in metadata.columns:
                metadata = metadata[metadata["source"] != "custom"]
            outcome = apply_filters_with_relaxation(metadata, search_ctx)
            if outcome.candidates.empty:
                raise NoCandidates(outcome.emptying_constraints)
            rows = outcome.candidates.index.to_numpy()
            sub = self.embeddings[rows]
            query_scores = query_vector @ sub.T
            limit = min(k, len(outcome.candidates))
            ordered = np.argsort(-query_scores, kind="stable")[:limit]
            results.append(
                [
                    self._candidate_from_row(
                        outcome.candidates.iloc[int(local_idx)],
                        float(query_scores[int(local_idx)]),
                        outcome.relaxed_constraints,
                    )
                    for local_idx in ordered
                ]
            )
        return results

    def attach_custom_layer(self, conn: sqlite3.Connection) -> "LocalRecipeIndex":
        """Attach SQLite custom recipes and reload them when the revision changes."""

        self._custom_conn = conn
        self._refresh_custom_if_needed(force=True)
        return self

    def _refresh_custom_if_needed(self, *, force: bool = False) -> None:
        if self._custom_conn is None:
            return
        from gamito.db.custom_recipes import custom_revision, custom_search_layer

        revision = custom_revision(self._custom_conn)
        if not force and revision == self._custom_revision:
            return
        custom_metadata, custom_embeddings, revision = custom_search_layer(self._custom_conn)
        if custom_metadata.empty:
            self.metadata = self._static_metadata.copy()
            self.embeddings = self._static_embeddings.copy()
        else:
            self.metadata = pd.concat(
                [self._static_metadata, custom_metadata],
                ignore_index=True,
                sort=False,
            )
            self.metadata = _with_cached_filter_sets(self.metadata.reset_index(drop=True))
            self.embeddings = np.vstack([self._static_embeddings, custom_embeddings]).astype(np.float32)
        self._custom_revision = revision

    def _candidate_from_row(
        self,
        row: pd.Series,
        score: float,
        relaxed_constraints: tuple[str, ...],
    ) -> RecipeCandidate:
        metadata = {
            key: value for key, value in row.to_dict().items() if not key.startswith("_")
        }
        title = metadata.get("recipe_title") or metadata.get("title") or ""
        return RecipeCandidate(
            recipe_id=str(metadata["recipe_id"]),
            title=str(title),
            score=score,
            metadata=metadata,
            relaxed_constraints=relaxed_constraints,
        )


def assert_compatible(manifest: dict[str, Any], *, model: str, dims: int) -> None:
    """Refuse stale or incompatible embedding manifests."""

    got = (str(manifest.get("model")), int(manifest.get("dims", -1)))
    expected = (model, dims)
    if got != expected:
        raise EmbeddingModelMismatch(expected=expected, got=got)


def _contexts_for_queries(
    queries: list[str],
    ctx: RecipeSearchContext | Any | list[Any] | tuple[Any, ...],
    *,
    max_time_min: int | None,
    max_price_per_serving: float | None,
    exclude_recipe_ids: list[str] | tuple[str, ...] | None,
    preferred_cuisines: list[str] | tuple[str, ...] | None,
    course: str | None,
) -> list[RecipeSearchContext]:
    if isinstance(ctx, (list, tuple)):
        if len(ctx) != len(queries):
            raise ValueError("ctx list length must match query count")
        raw_contexts = list(ctx)
    else:
        raw_contexts = [ctx] * len(queries)

    return [
        RecipeSearchContext.from_context(
            raw_ctx,
            max_time_min=max_time_min,
            max_price_per_serving=max_price_per_serving,
            exclude_recipe_ids=exclude_recipe_ids,
            preferred_cuisines=preferred_cuisines,
            course=course,
        )
        for raw_ctx in raw_contexts
    ]


def _with_cached_filter_sets(metadata: pd.DataFrame) -> pd.DataFrame:
    cached = metadata.copy()
    for column in ("kitchen_tools", "cuisine_list", "course_list"):
        if column in cached.columns:
            cached[f"_{column}_set"] = cached[column].map(
                lambda values: frozenset(_normalise_list(values))
            )
    return cached


def _normalise_list(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values.strip().lower(),) if values.strip() else ()
    return tuple(str(value).strip().lower() for value in values if str(value).strip())


def pack_vector(vec: np.ndarray) -> bytes:
    """Pack a single float32 vector for SQLite BLOB storage."""

    return np.ascontiguousarray(vec, dtype="<f4").tobytes()


def unpack_vector(blob: bytes, dims: int) -> np.ndarray:
    """Unpack a SQLite BLOB vector and validate its dimensionality."""

    vec = np.frombuffer(blob, dtype="<f4")
    if vec.shape != (dims,):
        raise ValueError(f"expected {dims} floats, got {vec.shape[0]}")
    return vec
