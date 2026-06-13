"""Custom recipe MCP tools."""

from __future__ import annotations

from typing import Any

from gamito.db import custom_recipes as recipe_repo
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db, require_profile


@tool
def add_recipe(
    title: str,
    ingredient_names: list[str],
    ingredient_amounts: list[str],
    ingredient_units: list[str] | None = None,
    directions: list[str] | None = None,
    cuisines: list[str] | None = None,
    courses: list[str] | None = None,
    tastes: list[str] | None = None,
    total_time_min: int | None = None,
    difficulty: str | None = None,
    servings: int = 2,
    tools: list[str] | None = None,
    dietary_flags: list[str] | None = None,
    allergens: list[str] | None = None,
    notes: str | None = None,
    added_by_profile_id: str | None = None,
) -> dict:
    """Save a structured household recipe and make it searchable."""

    ingredients = _ingredients_from_arrays(ingredient_names, ingredient_amounts, ingredient_units)
    with open_db() as conn:
        if added_by_profile_id:
            require_profile(conn, added_by_profile_id)
        result = recipe_repo.add_recipe(
            conn,
            title=title,
            ingredients=ingredients,
            directions=directions or [],
            cuisines=cuisines or [],
            courses=courses or [],
            tastes=tastes or [],
            total_time_min=total_time_min,
            difficulty=difficulty,
            servings=servings,
            tools=tools or [],
            dietary_flags=dietary_flags or [],
            allergens=allergens or [],
            notes=notes,
            added_by_profile_id=added_by_profile_id,
        )
    return {**result, "text": _saved_text(title, result)}


@tool
def update_recipe(
    recipe_id: str,
    title: str | None = None,
    ingredient_names: list[str] | None = None,
    ingredient_amounts: list[str] | None = None,
    ingredient_units: list[str] | None = None,
    directions: list[str] | None = None,
    cuisines: list[str] | None = None,
    courses: list[str] | None = None,
    tastes: list[str] | None = None,
    total_time_min: int | None = None,
    difficulty: str | None = None,
    servings: int | None = None,
    tools: list[str] | None = None,
    dietary_flags: list[str] | None = None,
    allergens: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """Patch a custom recipe and refresh its embedding when needed."""

    updates: dict[str, Any] = {
        "title": title,
        "directions": directions,
        "cuisines": cuisines,
        "courses": courses,
        "tastes": tastes,
        "total_time_min": total_time_min,
        "difficulty": difficulty,
        "servings": servings,
        "tools": tools,
        "dietary_flags": dietary_flags,
        "allergens": allergens,
        "notes": notes,
    }
    if ingredient_names is not None or ingredient_amounts is not None or ingredient_units is not None:
        if ingredient_names is None or ingredient_amounts is None:
            raise err("RECIPE_VALIDATION_FAILED", "ingredient_names and ingredient_amounts are required together")
        updates["ingredients"] = _ingredients_from_arrays(
            ingredient_names,
            ingredient_amounts,
            ingredient_units,
        )
    updates = {key: value for key, value in updates.items() if value is not None}
    with open_db() as conn:
        result = recipe_repo.update_recipe(conn, recipe_id=recipe_id, **updates)
    return {**result, "updated_fields": sorted(updates), "text": _saved_text(recipe_id, result)}


@tool
def delete_recipe(recipe_id: str, force: bool = False) -> dict:
    """Delete a custom recipe, optionally orphaning historical plan references."""

    with open_db() as conn:
        result = recipe_repo.delete_recipe(conn, recipe_id, force=force)
    orphaned = result["orphaned_plans"]
    text = f"Deleted {recipe_id}."
    if orphaned:
        text += " Historical plans kept denormalised meal details."
    return {**result, "text": text}


@tool
def list_custom_recipes(
    query_en: str | None = None,
    cuisine: str | None = None,
    max_total_time_min: int | None = None,
    limit: int = 50,
) -> dict:
    """List saved household recipes for disambiguation and browsing."""

    if limit < 1 or limit > 50:
        raise err("INVALID_INPUT", "limit must be between 1 and 50")
    with open_db() as conn:
        recipes = recipe_repo.list_custom_recipes(
            conn,
            query_en=query_en,
            cuisine=cuisine,
            max_total_time_min=max_total_time_min,
            limit=limit,
        )
    cards = [_recipe_card(recipe) for recipe in recipes]
    titles = ", ".join(card["title"] for card in cards[:8]) or "No custom recipes found."
    return {"recipes": cards, "total": len(cards), "text": titles}


def _ingredients_from_arrays(
    names: list[str],
    amounts: list[str],
    units: list[str] | None = None,
) -> list[dict[str, str | None]]:
    if not names or len(names) != len(amounts):
        raise err("RECIPE_VALIDATION_FAILED", "ingredient_names and ingredient_amounts must be same-length non-empty arrays")
    if units is not None and len(units) != len(names):
        raise err("RECIPE_VALIDATION_FAILED", "ingredient_units must match ingredient_names length")
    return [
        {
            "name": name,
            "amount": amounts[index],
            "unit": units[index] if units else None,
        }
        for index, name in enumerate(names)
    ]


def _recipe_card(recipe: dict) -> dict:
    dietary_flags = [
        key.removeprefix("is_")
        for key, value in recipe["dietary"].items()
        if value
    ]
    return {
        "recipe_id": recipe["recipe_id"],
        "title": recipe["title"],
        "cuisines": recipe["cuisines"],
        "total_time_min": recipe["total_time_min"],
        "servings": recipe["servings"],
        "price_per_serving_eur": recipe["price_per_serving_eur"],
        "allergens": recipe["allergens"],
        "dietary_flags": dietary_flags,
        "created_at": recipe["created_at"],
    }


def _saved_text(title: str, result: dict) -> str:
    warnings = result.get("warnings") or []
    price = result.get("estimated_price_per_serving_eur")
    text = f"Saved {title!r} ({result['recipe_id']})."
    if price is not None:
        text += f" Estimated {price:.2f} EUR/serving."
    if warnings:
        text += " " + "; ".join(warnings)
    return text
