"""Recipe CSV normalization for the local retrieval index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from gamito.models.meal import parse_json_dict, parse_json_list

LIST_COLUMNS = ("cuisine_list", "course_list", "kitchen_tools")
JSON_LIST_ALIASES = {
    "ingredients_json": "ingredients",
    "directions_json": "directions",
}
JSON_DICT_ALIASES = {
    "nutrition_per_serving_json": "nutrition_per_serving_json",
}
BOOL_COLUMNS = (
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
    "is_nut_free",
    "is_dairy_free",
    "is_gluten_free",
)
NUMERIC_COLUMNS = (
    "total_time_min",
    "est_prep_time_min",
    "est_cook_time_min",
    "healthiness_score",
    "price_total_eur",
    "price_per_serving_eur",
    "feature_coverage",
    "est_servings",
)


def dataset_sha256(path: Path) -> str:
    """Return a stable hash for manifest stale-index checks."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_recipe_metadata(
    raw: pd.DataFrame,
    *,
    min_feature_coverage: float | None = None,
) -> pd.DataFrame:
    """Normalize salvaged CSV rows into filterable/displayable metadata."""

    df = raw.copy()
    if "recipe_id" not in df.columns:
        df.insert(0, "recipe_id", [f"dataset_{idx + 1:05d}" for idx in range(len(df))])

    if min_feature_coverage is not None and "feature_coverage" in df.columns:
        coverage = pd.to_numeric(df["feature_coverage"], errors="coerce").fillna(0)
        df = df[coverage >= min_feature_coverage].copy()

    if "total_time_min" not in df.columns and "total_time" in df.columns:
        df["total_time_min"] = df["total_time"]
    if "source" not in df.columns:
        df["source"] = "dataset"

    for column in LIST_COLUMNS:
        if column not in df.columns:
            df[column] = [[] for _ in range(len(df))]
        df[column] = df[column].map(_clean_list)

    for target, source in JSON_LIST_ALIASES.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]
        if target in df.columns:
            df[target] = df[target].map(_json_list_string)

    for target, source in JSON_DICT_ALIASES.items():
        if target not in df.columns and source in df.columns:
            df[target] = df[source]
        if target in df.columns:
            df[target] = df[target].map(_json_dict_string)

    for column in BOOL_COLUMNS:
        if column in df.columns:
            df[column] = df[column].map(_to_bool)

    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    return df.reset_index(drop=True)


def build_embedding_text(row: pd.Series | dict[str, Any]) -> str:
    """Build the text encoded for one recipe row."""

    get = row.get if isinstance(row, dict) else row.get
    title = _string(get("recipe_title") or get("title"))
    cuisines = _join_list(get("cuisine_list"))
    courses = _join_list(get("course_list"))
    tastes = _join_list([get("primary_taste"), get("secondary_taste")])
    tools = _join_list(get("kitchen_tools"))
    ingredients = _join_list(parse_json_list(get("ingredients_json") or get("ingredients")))
    directions = _join_list(parse_json_list(get("directions_json") or get("directions")))

    parts = [title, cuisines, courses, tastes, tools, ingredients, directions[:500]]
    return "\n".join(part for part in parts if part)


def _clean_list(value: Any) -> list[str]:
    return [
        _string(item).strip().lower()
        for item in parse_json_list(value)
        if _string(item).strip()
    ]


def _json_list_string(value: Any) -> str:
    return json.dumps(parse_json_list(value), ensure_ascii=False)


def _json_dict_string(value: Any) -> str:
    return json.dumps(parse_json_dict(value), ensure_ascii=False)


def _join_list(value: Any) -> str:
    items = parse_json_list(value)
    return " ".join(_string(item) for item in items if _string(item).strip())


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return " ".join(_string(item) for item in value)
    if pd.isna(value):
        return ""
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}
