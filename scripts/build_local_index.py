#!/usr/bin/env python
"""Build the committed local recipe retrieval index."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gamito.retrieval.encoder import DEFAULT_MODEL, DIMS, encode
from gamito.retrieval.metadata import (
    build_embedding_text,
    dataset_sha256,
    normalize_recipe_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "recipes_dataset.csv"
DEFAULT_INDEX_DIR = ROOT / "data" / "index"


def main() -> None:
    args = _parse_args()
    dataset_path = args.dataset
    index_dir = args.output_dir
    index_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {dataset_path}", file=sys.stderr)
    raw = pd.read_csv(dataset_path)
    metadata = normalize_recipe_metadata(
        raw,
        min_feature_coverage=args.min_feature_coverage,
    )
    texts = [build_embedding_text(row) for _, row in metadata.iterrows()]
    dataset_hash = dataset_sha256(dataset_path)

    manifest = {
        "model": args.model,
        "dims": args.dims,
        "count": len(metadata),
        "dataset_path": _display_path(dataset_path),
        "dataset_sha256": dataset_hash,
        "feature_coverage_min": args.min_feature_coverage,
        "built_at_utc": datetime.now(UTC).isoformat(),
    }

    metadata.to_parquet(index_dir / "metadata.parquet", index=False)
    embeddings = _encode_resumable(
        texts,
        index_dir=index_dir,
        manifest=manifest,
        batch_size=args.batch_size,
        resume=not args.no_resume,
    )
    np.save(index_dir / "embeddings.npy", embeddings)
    (index_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _cleanup_partials(index_dir)
    print(
        f"Wrote {len(metadata)} rows to {index_dir} "
        f"({embeddings.shape[1]} dims, {embeddings.nbytes / 1024 / 1024:.1f} MiB)",
        file=sys.stderr,
    )


def _encode_resumable(
    texts: list[str],
    *,
    index_dir: Path,
    manifest: dict[str, Any],
    batch_size: int,
    resume: bool,
) -> np.ndarray:
    partial_path = index_dir / "embeddings.partial.dat"
    progress_path = index_dir / "embeddings.progress.json"
    shape = (len(texts), int(manifest["dims"]))
    start = 0

    if resume and partial_path.exists() and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if _progress_matches(progress, manifest):
            start = int(progress.get("encoded", 0))
            mode = "r+"
            print(f"Resuming at row {start}", file=sys.stderr)
        else:
            start = 0
            mode = "w+"
    else:
        mode = "w+"

    matrix = np.memmap(partial_path, dtype=np.float32, mode=mode, shape=shape)
    for batch_start in range(start, len(texts), batch_size):
        batch_end = min(batch_start + batch_size, len(texts))
        batch = encode(texts[batch_start:batch_end], model=str(manifest["model"]))
        if batch.shape != (batch_end - batch_start, int(manifest["dims"])):
            raise ValueError(
                f"encoder returned {batch.shape}, expected "
                f"{(batch_end - batch_start, int(manifest['dims']))}"
            )
        matrix[batch_start:batch_end] = batch
        matrix.flush()
        progress_path.write_text(
            json.dumps(
                {
                    "encoded": batch_end,
                    "model": manifest["model"],
                    "dims": manifest["dims"],
                    "count": manifest["count"],
                    "dataset_sha256": manifest["dataset_sha256"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Encoded {batch_end}/{len(texts)}", file=sys.stderr)

    return np.asarray(matrix, dtype=np.float32)


def _progress_matches(progress: dict[str, Any], manifest: dict[str, Any]) -> bool:
    keys = ("model", "dims", "count", "dataset_sha256")
    return all(progress.get(key) == manifest.get(key) for key in keys)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _cleanup_partials(index_dir: Path) -> None:
    for name in ("embeddings.partial.dat", "embeddings.progress.json"):
        path = index_dir / name
        if path.exists():
            path.unlink()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dims", type=int, default=DIMS)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--min-feature-coverage", type=float, default=None)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()
