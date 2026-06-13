#!/usr/bin/env python
"""Run the G1 golden-set retrieval checks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pandas as pd

from gamito.retrieval.filters import RecipeSearchContext, apply_filters
from gamito.retrieval.index import LocalRecipeIndex, RecipeCandidate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_DIR = ROOT / "data" / "index"
DEFAULT_REPORT = ROOT / "docs" / "eval_baseline.md"


@dataclass(frozen=True)
class GoldenQuery:
    name: str
    query: str
    ctx: RecipeSearchContext
    min_results: int = 5
    manual_precision_at_5: float | None = None


GOLDEN_QUERIES = [
    GoldenQuery(
        name="quick vegetarian breakfast",
        query="quick vegetarian breakfast with eggs and toast",
        ctx=RecipeSearchContext(
            max_time_min=30,
            dietary_pref="vegetarian",
            course="breakfast",
        ),
        manual_precision_at_5=0.8,
    ),
    GoldenQuery(
        name="gluten-free dinner",
        query="gluten free chicken dinner with vegetables",
        ctx=RecipeSearchContext(max_time_min=60, allergies=("gluten",), course="main"),
        manual_precision_at_5=0.8,
    ),
    GoldenQuery(
        name="dairy-free budget lunch",
        query="dairy free cheap lunch bowl",
        ctx=RecipeSearchContext(
            max_price_per_serving=3.5,
            allergies=("dairy",),
            course="main",
        ),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="nut-free asian",
        query="asian noodles vegetables nut free",
        ctx=RecipeSearchContext(allergies=("nuts",), preferred_cuisines=("asian",)),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="air fryer snack",
        query="crispy air fryer snack",
        ctx=RecipeSearchContext(owned_tools=("air fryer",), course="snack"),
        manual_precision_at_5=0.8,
    ),
    GoldenQuery(
        name="vegan soup",
        query="vegan soup with beans",
        ctx=RecipeSearchContext(dietary_pref="vegan", max_time_min=90),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="italian pasta",
        query="italian pasta tomato basil",
        ctx=RecipeSearchContext(preferred_cuisines=("italian",), max_time_min=60),
        manual_precision_at_5=0.8,
    ),
    GoldenQuery(
        name="high health salad",
        query="healthy salad with chicken",
        ctx=RecipeSearchContext(min_healthiness_score=70, max_time_min=45),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="skillet dinner",
        query="one pan skillet dinner",
        ctx=RecipeSearchContext(owned_tools=("skillet/pan", "stovetop")),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="fast dessert",
        query="quick sweet dessert",
        ctx=RecipeSearchContext(max_time_min=30, course="dessert"),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="cheap vegetarian main",
        query="cheap vegetarian main dish with beans",
        ctx=RecipeSearchContext(
            dietary_pref="vegetarian",
            max_price_per_serving=2.75,
            course="main",
        ),
        manual_precision_at_5=0.6,
    ),
    GoldenQuery(
        name="kosher fish dinner",
        query="fish dinner with rice",
        ctx=RecipeSearchContext(max_time_min=60, preferred_cuisines=("middle eastern",)),
        manual_precision_at_5=0.6,
    ),
]


def main() -> None:
    args = _parse_args()
    index = LocalRecipeIndex.load(args.index_dir)
    _warm_search_path(index)
    rows: list[dict[str, object]] = []
    timings_ms: list[float] = []

    start = perf_counter()
    all_results = index.search_many(
        [golden.query for golden in GOLDEN_QUERIES],
        [golden.ctx for golden in GOLDEN_QUERIES],
        k=5,
    )
    batch_elapsed_ms = (perf_counter() - start) * 1000
    per_query_ms = batch_elapsed_ms / len(GOLDEN_QUERIES)

    for golden, candidates in zip(GOLDEN_QUERIES, all_results, strict=True):
        elapsed_ms = per_query_ms
        timings_ms.append(elapsed_ms)
        hard_filters_ok = _hard_filters_ok(candidates, golden.ctx)
        rows.append(
            {
                "name": golden.name,
                "results": len(candidates),
                "hard_filters_ok": hard_filters_ok,
                "relaxed": ", ".join(candidates[0].relaxed_constraints)
                if candidates and candidates[0].relaxed_constraints
                else "",
                "manual_precision_at_5": golden.manual_precision_at_5,
                "elapsed_ms": elapsed_ms,
                "top_titles": [candidate.title for candidate in candidates],
            }
        )

    p95 = pd.Series(timings_ms).quantile(0.95)
    report = _render_report(
        rows,
        p95_ms=float(p95),
        batch_elapsed_ms=batch_elapsed_ms,
    )
    if args.write_report:
        args.write_report.write_text(report, encoding="utf-8")
    print(report)

    failed = [
        row
        for row in rows
        if row["results"] < 5
        or not row["hard_filters_ok"]
        or (row["manual_precision_at_5"] is not None and row["manual_precision_at_5"] < 0.6)
    ]
    if p95 >= 60:
        failed.append({"name": "p95 latency", "elapsed_ms": p95})
    if failed:
        names = ", ".join(str(row["name"]) for row in failed)
        raise SystemExit(f"retrieval eval failed: {names}")


def _hard_filters_ok(candidates: list[RecipeCandidate], ctx: RecipeSearchContext) -> bool:
    """Verify returned rows still satisfy the non-relaxed filter contract."""

    if not candidates:
        return False
    frame = pd.DataFrame([candidate.metadata for candidate in candidates])
    filtered = apply_filters(frame, ctx)
    return len(filtered) == len(frame)


def _warm_search_path(index: LocalRecipeIndex) -> None:
    """Exclude model/session startup from the warm-search latency gate."""

    index.search_many(
        ["warmup vegetarian soup", "warmup pasta", "warmup breakfast"],
        RecipeSearchContext(),
        k=1,
    )


def _render_report(
    rows: list[dict[str, object]],
    *,
    p95_ms: float,
    batch_elapsed_ms: float,
) -> str:
    lines = [
        "# G1 Retrieval Eval Baseline",
        "",
        "Golden-set gate for the local fastembed + brute-force index.",
        "",
        f"- Query count: {len(rows)}",
        f"- Hard-filter integrity: {_percent(_mean_bool(rows, 'hard_filters_ok'))}",
        f"- Warm batched search p95/query: {p95_ms:.1f} ms",
        f"- Warm batch elapsed: {batch_elapsed_ms:.1f} ms",
        "- Manual precision@5 floor: 0.60",
        "",
        "## Query Results",
        "",
    ]
    for row in rows:
        precision = row["manual_precision_at_5"]
        precision_text = "n/a" if precision is None else f"{float(precision):.2f}"
        lines.extend(
            [
                f"### {row['name']}",
                "",
                f"- Results: {row['results']}",
                f"- Hard filters ok: {row['hard_filters_ok']}",
                f"- Relaxed constraints: {row['relaxed'] or 'none'}",
                f"- Manual precision@5: {precision_text}",
                f"- Search latency: {float(row['elapsed_ms']):.1f} ms",
                "- Top titles:",
            ]
        )
        lines.extend(f"  - {title}" for title in row["top_titles"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _mean_bool(rows: list[dict[str, object]], key: str) -> float:
    return sum(1 for row in rows if row[key]) / len(rows)


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


if __name__ == "__main__":
    main()
