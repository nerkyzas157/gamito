"""Lazy fastembed wrapper used by local retrieval."""

from __future__ import annotations

import functools

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DIMS = 384


@functools.lru_cache(maxsize=1)
def _model(name: str = DEFAULT_MODEL):
    """Load the ONNX embedding model only when retrieval actually needs it."""

    from fastembed import TextEmbedding

    return TextEmbedding(model_name=name)


def encode(texts: list[str], *, model: str = DEFAULT_MODEL) -> np.ndarray:
    """Encode and L2-normalise so cosine similarity is a dot product."""

    vecs = np.asarray(list(_model(model).embed(texts)), dtype=np.float32)
    if vecs.ndim != 2:
        raise ValueError(f"expected a 2D embedding matrix, got shape {vecs.shape}")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-12, None)
