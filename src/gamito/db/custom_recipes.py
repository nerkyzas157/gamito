"""SQLite CRUD helpers for household custom recipes."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from gamito.mcp.errors import err
from gamito.models.meal import Ingredient, Meal, MealSlot
from gamito.planning.nodes.shopping import build_shopping_list
from gamito.pricing import get_canonical_price_lookup
from gamito.retrieval.encoder import DEFAULT_MODEL, DIMS, encode
from gamito.retrieval.index import pack_vector, unpack_vector

EncodeFn = Callable[[list[str]], np.ndarray]

_DIETARY_FLAG_COLUMNS = (
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
    "is_nut_free",
    "is_peanut_free",
    "is_dairy_free",
    "is_gluten_free",
    "is_shellfish_free",
    "is_soy_free",
    "is_egg_free",
    "is_fish_free",
)


def add_recipe(
    conn: sqlite3.Connection,
    *,
    title: str,
    ingredients: list[dict[str, Any]],
    directions: list[str],
    cuisines: Iterable[str] = (),
    courses: Iterable[str] = (),
    tastes: Iterable[str] = (),
    total_time_min: int | None = None,
    difficulty: str | None = None,
    servings: int = 2,
    tools: Iterable[str] = (),
    dietary_flags: Iterable[str] | None = None,
    allergens: Iterable[str] = (),
    notes: str | None = None,
    added_by_profile_id: str | None = None,
    source: str = "user",
    encode_fn: EncodeFn | None = None,
) -> dict[str, Any]:
    """Create a custom recipe plus embedding row and bump the revision."""

    _validate_recipe(title=title, ingredients=ingredients, directions=directions, servings=servings)
    recipe_id = f"custom_{uuid.uuid4().hex}"
    now = _now()
    canonicalised, canonicalisation = _canonicalise_ingredients(ingredients)
    dietary = _dietary_json(dietary_flags)
    price = _price_per_serving(
        title=title,
        ingredients=canonicalised,
        servings=servings,
        cuisines=cuisines,
        tools=tools,
        dietary=dietary,
        total_time_min=total_time_min,
        difficulty=difficulty,
    )
    embed_text = build_embed_text(title, cuisines, courses, tastes, tools, canonicalised, directions)
    vector = _encode(embed_text, encode_fn)
    with conn:
        conn.execute(
            """
            INSERT INTO custom_recipes (
              recipe_id, title, cuisines_json, courses_json, tastes_json,
              total_time_min, difficulty, servings, ingredients_json,
              directions_json, tools_json, dietary_json, allergens_json,
              price_per_serving_eur, cost_total_eur, notes, source,
              added_by_profile_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                title.strip(),
                _json(_normalised(cuisines)),
                _json(_normalised(courses)),
                _json(_normalised(tastes)),
                total_time_min,
                difficulty,
                servings,
                _json(canonicalised),
                _json([step.strip() for step in directions if step.strip()]),
                _json(_normalised(tools)),
                _json(dietary),
                _json(_normalised(allergens)),
                price,
                round(price * servings, 2) if price is not None else None,
                notes,
                source,
                added_by_profile_id,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO custom_recipe_embeddings
              (recipe_id, model, dims, vector, embed_text, encoded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (recipe_id, DEFAULT_MODEL, DIMS, pack_vector(vector), embed_text, now),
        )
        bump_revision(conn)
    return {
        "recipe_id": recipe_id,
        "canonicalisation": canonicalisation,
        "estimated_price_per_serving_eur": price,
        "warnings": _canonical_warnings(canonicalisation),
    }


def update_recipe(
    conn: sqlite3.Connection,
    *,
    recipe_id: str,
    encode_fn: EncodeFn | None = None,
    **updates: Any,
) -> dict[str, Any]:
    """Patch a custom recipe and refresh embedding/filter metadata."""

    current = get_recipe(conn, recipe_id)
    if current is None:
        raise err("RECIPE_NOT_FOUND", f"recipe not found: {recipe_id}")
    title = updates.get("title") if updates.get("title") is not None else current["title"]
    ingredients = (
        updates.get("ingredients")
        if updates.get("ingredients") is not None
        else current["ingredients"]
    )
    directions = (
        updates.get("directions")
        if updates.get("directions") is not None
        else current["directions"]
    )
    servings = updates.get("servings") if updates.get("servings") is not None else current["servings"]
    _validate_recipe(title=title, ingredients=ingredients, directions=directions, servings=servings)

    cuisines = updates.get("cuisines") if updates.get("cuisines") is not None else current["cuisines"]
    courses = updates.get("courses") if updates.get("courses") is not None else current["courses"]
    tastes = updates.get("tastes") if updates.get("tastes") is not None else current["tastes"]
    tools = updates.get("tools") if updates.get("tools") is not None else current["tools"]
    allergens = updates.get("allergens") if updates.get("allergens") is not None else current["allergens"]
    dietary_flags = (
        updates.get("dietary_flags")
        if updates.get("dietary_flags") is not None
        else _true_dietary_flags(current["dietary"])
    )
    total_time_min = (
        updates.get("total_time_min")
        if "total_time_min" in updates
        else current["total_time_min"]
    )
    difficulty = updates.get("difficulty") if "difficulty" in updates else current["difficulty"]
    notes = updates.get("notes") if "notes" in updates else current["notes"]

    canonicalised, canonicalisation = _canonicalise_ingredients(ingredients)
    dietary = _dietary_json(dietary_flags)
    price = _price_per_serving(
        title=title,
        ingredients=canonicalised,
        servings=servings,
        cuisines=cuisines,
        tools=tools,
        dietary=dietary,
        total_time_min=total_time_min,
        difficulty=difficulty,
    )
    embed_text = build_embed_text(title, cuisines, courses, tastes, tools, canonicalised, directions)
    vector = _encode(embed_text, encode_fn)
    now = _now()
    with conn:
        conn.execute(
            """
            UPDATE custom_recipes
            SET title = ?, cuisines_json = ?, courses_json = ?, tastes_json = ?,
                total_time_min = ?, difficulty = ?, servings = ?,
                ingredients_json = ?, directions_json = ?, tools_json = ?,
                dietary_json = ?, allergens_json = ?, price_per_serving_eur = ?,
                cost_total_eur = ?, notes = ?, updated_at = ?
            WHERE recipe_id = ?
            """,
            (
                title.strip(),
                _json(_normalised(cuisines)),
                _json(_normalised(courses)),
                _json(_normalised(tastes)),
                total_time_min,
                difficulty,
                servings,
                _json(canonicalised),
                _json([step.strip() for step in directions if step.strip()]),
                _json(_normalised(tools)),
                _json(dietary),
                _json(_normalised(allergens)),
                price,
                round(price * servings, 2) if price is not None else None,
                notes,
                now,
                recipe_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO custom_recipe_embeddings
              (recipe_id, model, dims, vector, embed_text, encoded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(recipe_id) DO UPDATE SET
              model = excluded.model, dims = excluded.dims, vector = excluded.vector,
              embed_text = excluded.embed_text, encoded_at = excluded.encoded_at
            """,
            (recipe_id, DEFAULT_MODEL, DIMS, pack_vector(vector), embed_text, now),
        )
        bump_revision(conn)
    return {
        "recipe_id": recipe_id,
        "canonicalisation": canonicalisation,
        "estimated_price_per_serving_eur": price,
        "warnings": _canonical_warnings(canonicalisation),
    }


def delete_recipe(conn: sqlite3.Connection, recipe_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a custom recipe, guarding historical plan references by default."""

    if not recipe_id.startswith("custom_") or get_recipe(conn, recipe_id) is None:
        raise err("RECIPE_NOT_FOUND", f"recipe not found: {recipe_id}")
    used = [
        str(row["plan_id"])
        for row in conn.execute(
            "SELECT DISTINCT plan_id FROM plan_meals WHERE recipe_id = ? ORDER BY plan_id",
            (recipe_id,),
        )
    ]
    if used and not force:
        raise err("RECIPE_IN_USE", f"recipe is referenced by plans {used}", plan_ids=", ".join(used))
    with conn:
        conn.execute("DELETE FROM custom_recipes WHERE recipe_id = ?", (recipe_id,))
        bump_revision(conn)
    return {"deleted": recipe_id, "orphaned_plans": used if force else []}


def get_recipe(conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM custom_recipes WHERE recipe_id = ?", (recipe_id,)).fetchone()
    return None if row is None else _decode_recipe(dict(row))


def list_custom_recipes(
    conn: sqlite3.Connection,
    *,
    query_en: str | None = None,
    cuisine: str | None = None,
    max_total_time_min: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query_en:
        clauses.append("lower(title) LIKE ?")
        params.append(f"%{query_en.strip().lower()}%")
    if max_total_time_min is not None:
        clauses.append("(total_time_min IS NULL OR total_time_min <= ?)")
        params.append(max_total_time_min)
    if cuisine and cuisine.strip():
        wanted = cuisine.strip().lower()
        clauses.append("cuisines_json LIKE ?")
        params.append(f'%"{wanted}"%')
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT *
        FROM custom_recipes
        {"WHERE " + " AND ".join(clauses) if clauses else ""}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    )
    recipes = [_decode_recipe(dict(row)) for row in rows]
    return recipes


def custom_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT revision FROM custom_recipes_meta WHERE id = 1").fetchone()
    return 0 if row is None else int(row["revision"])


def bump_revision(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE custom_recipes_meta SET revision = revision + 1 WHERE id = 1")


def custom_search_layer(conn: sqlite3.Connection) -> tuple[pd.DataFrame, np.ndarray, int]:
    """Return metadata and embedding matrix for custom recipes."""

    rows = conn.execute(
        """
        SELECT r.*, e.model, e.dims, e.vector
        FROM custom_recipes r
        JOIN custom_recipe_embeddings e ON e.recipe_id = r.recipe_id
        ORDER BY r.created_at, r.recipe_id
        """
    )
    metadata: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    for raw in rows:
        row = dict(raw)
        dims = int(row["dims"])
        if row["model"] != DEFAULT_MODEL or dims != DIMS:
            from gamito.retrieval.index import EmbeddingModelMismatch

            raise EmbeddingModelMismatch(
                expected=(DEFAULT_MODEL, DIMS),
                got=(str(row["model"]), dims),
            )
        recipe = _decode_recipe(row)
        metadata.append(_metadata_row(recipe))
        vectors.append(unpack_vector(row["vector"], dims))
    if not vectors:
        return pd.DataFrame(metadata), np.empty((0, DIMS), dtype=np.float32), custom_revision(conn)
    return pd.DataFrame(metadata), np.vstack(vectors).astype(np.float32), custom_revision(conn)


def reembed_all(conn: sqlite3.Connection, *, encode_fn: EncodeFn | None = None) -> int:
    """Refresh embeddings for every custom recipe using the current encoder."""

    recipes = list_custom_recipes(conn, limit=10_000)
    now = _now()
    with conn:
        for recipe in recipes:
            embed_text = build_embed_text(
                recipe["title"],
                recipe["cuisines"],
                recipe["courses"],
                recipe["tastes"],
                recipe["tools"],
                recipe["ingredients"],
                recipe["directions"],
            )
            vector = _encode(embed_text, encode_fn)
            conn.execute(
                """
                INSERT INTO custom_recipe_embeddings
                  (recipe_id, model, dims, vector, embed_text, encoded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                  model = excluded.model, dims = excluded.dims, vector = excluded.vector,
                  embed_text = excluded.embed_text, encoded_at = excluded.encoded_at
                """,
                (recipe["recipe_id"], DEFAULT_MODEL, DIMS, pack_vector(vector), embed_text, now),
            )
        if recipes:
            bump_revision(conn)
    return len(recipes)


def build_embed_text(
    title: str,
    cuisines: Iterable[str],
    courses: Iterable[str],
    tastes: Iterable[str],
    tools: Iterable[str],
    ingredients: list[dict[str, Any]],
    directions: Iterable[str],
) -> str:
    parts = [
        title,
        "cuisines " + " ".join(_normalised(cuisines)),
        "courses " + " ".join(_normalised(courses)),
        "tastes " + " ".join(_normalised(tastes)),
        "tools " + " ".join(_normalised(tools)),
        "ingredients " + " ".join(item["name"] for item in ingredients),
        "directions " + " ".join(step for step in directions if step),
    ]
    return "\n".join(part for part in parts if part.strip())


def _validate_recipe(
    *,
    title: str,
    ingredients: list[dict[str, Any]],
    directions: list[str],
    servings: int,
) -> None:
    if not title or not title.strip() or not ingredients or not directions or servings < 1:
        raise err("RECIPE_VALIDATION_FAILED", "title, ingredients, directions, and servings are required")
    if not any(str(step).strip() for step in directions):
        raise err("RECIPE_VALIDATION_FAILED", "directions must contain at least one step")
    for ingredient in ingredients:
        if not str(ingredient.get("name") or "").strip() or not str(ingredient.get("amount") or "").strip():
            raise err("RECIPE_VALIDATION_FAILED", "ingredient name and amount are required")


def _canonicalise_ingredients(
    ingredients: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        lookup = get_canonical_price_lookup()
    except Exception:  # pragma: no cover - data files optional
        lookup = None
    canonicalised: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for item in ingredients:
        name = str(item.get("name") or "").strip()
        amount = str(item.get("amount") or "").strip()
        canonical = lookup.canonicalize(name) if lookup is not None else None
        stored = {
            "name": name,
            "amount": amount,
            "unit": item.get("unit"),
            "canonical": canonical,
        }
        canonicalised.append(stored)
        report.append({"name": name, "canonical": canonical, "matched": canonical is not None})
    return canonicalised, report


def _price_per_serving(
    *,
    title: str,
    ingredients: list[dict[str, Any]],
    servings: int,
    cuisines: Iterable[str],
    tools: Iterable[str],
    dietary: dict[str, bool],
    total_time_min: int | None,
    difficulty: str | None,
) -> float | None:
    meal = Meal(
        day_number=1,
        meal_slot=MealSlot.DINNER,
        recipe_title=title,
        servings=servings,
        allocated_budget_eur=0,
        estimated_cost_total_eur=0,
        estimated_cost_per_serving_eur=0,
        total_time_min=total_time_min,
        difficulty=difficulty,
        cuisine_list=_normalised(cuisines),
        kitchen_tools=_normalised(tools),
        ingredients=[
            Ingredient(
                name=item["name"],
                amount=item.get("amount"),
                unit=item.get("unit"),
                canonical_name=item.get("canonical"),
            )
            for item in ingredients
        ],
        directions=[],
        dietary_flags=dietary,
    )
    shopping = build_shopping_list([meal])
    return round(shopping.total_estimated_cost_eur / servings, 2) if servings else None


def _metadata_row(recipe: dict[str, Any]) -> dict[str, Any]:
    dietary = recipe["dietary"]
    row = {
        "recipe_id": recipe["recipe_id"],
        "recipe_title": recipe["title"],
        "total_time_min": recipe["total_time_min"],
        "price_per_serving_eur": recipe["price_per_serving_eur"],
        "cost_total_eur": recipe["cost_total_eur"],
        "est_servings": recipe["servings"],
        "difficulty": recipe["difficulty"],
        "kitchen_tools": recipe["tools"],
        "cuisine_list": recipe["cuisines"],
        "course_list": recipe["courses"],
        "source": "custom",
        "ingredients_json": _json(recipe["ingredients"]),
        "directions_json": _json(recipe["directions"]),
        "nutrition_per_serving_json": _json(recipe["nutrition"] or {}),
        "allergens_json": _json(recipe["allergens"]),
    }
    for flag in _DIETARY_FLAG_COLUMNS:
        row[flag] = bool(dietary.get(flag, False))
    return row


def _decode_recipe(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "recipe_id": row["recipe_id"],
        "title": row["title"],
        "cuisines": _decode(row.get("cuisines_json"), []),
        "courses": _decode(row.get("courses_json"), []),
        "tastes": _decode(row.get("tastes_json"), []),
        "total_time_min": row.get("total_time_min"),
        "difficulty": row.get("difficulty"),
        "servings": row["servings"],
        "ingredients": _decode(row.get("ingredients_json"), []),
        "directions": _decode(row.get("directions_json"), []),
        "tools": _decode(row.get("tools_json"), []),
        "dietary": _decode(row.get("dietary_json"), {}),
        "allergens": _decode(row.get("allergens_json"), []),
        "price_per_serving_eur": row.get("price_per_serving_eur"),
        "cost_total_eur": row.get("cost_total_eur"),
        "nutrition": _decode(row.get("nutrition_json"), None),
        "notes": row.get("notes"),
        "source": row.get("source"),
        "added_by_profile_id": row.get("added_by_profile_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _dietary_json(flags: Iterable[str] | None) -> dict[str, bool]:
    true_flags = {f"is_{flag.strip().lower().removeprefix('is_')}" for flag in flags or [] if flag.strip()}
    return {column: column in true_flags for column in _DIETARY_FLAG_COLUMNS}


def _true_dietary_flags(dietary: dict[str, bool]) -> list[str]:
    return [key.removeprefix("is_") for key, value in dietary.items() if value]


def _canonical_warnings(report: list[dict[str, Any]]) -> list[str]:
    missing = [item["name"] for item in report if not item["matched"]]
    return [] if not missing else ["unmatched ingredients: " + ", ".join(missing)]


def _encode(text: str, encode_fn: EncodeFn | None) -> np.ndarray:
    vectors = np.asarray((encode_fn or encode)([text]), dtype=np.float32)
    if vectors.shape != (1, DIMS):
        raise ValueError(f"encoder returned {vectors.shape}, expected {(1, DIMS)}")
    return vectors[0]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _normalised(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip().lower() for value in values or [] if str(value).strip()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
