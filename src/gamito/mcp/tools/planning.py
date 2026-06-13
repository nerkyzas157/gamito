"""Planning and recipe-search MCP tools."""

from __future__ import annotations

from typing import Any

from gamito.config import INDEX_DIR
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import (
    latest_plan_id,
    open_db,
    require_plan,
    require_profile,
    stored_plan_response,
)
from gamito.models.meal import parse_json_dict
from gamito.planning.graph import generate_meal_plan as run_generate_meal_plan
from gamito.planning.nodes.assignment import AssignmentError
from gamito.recommendation.engine import build_user_context
from gamito.retrieval.index import EmbeddingModelMismatch, LocalRecipeIndex, NoCandidates


@tool
def generate_meal_plan(
    profile_id: str,
    budget_eur: float,
    servings: int,
    num_days: int,
    meals_per_day: int,
    max_time_min: int | None = None,
    exclude_recipe_ids: list[str] | None = None,
) -> dict:
    """Generate and persist a deterministic meal plan."""

    _validate_plan_request(budget_eur, servings, num_days, meals_per_day, max_time_min)
    with open_db() as conn:
        require_profile(conn, profile_id)
        try:
            return run_generate_meal_plan(
                profile_id=profile_id,
                budget_eur=budget_eur,
                servings=servings,
                num_days=num_days,
                meals_per_day=meals_per_day,
                max_time_min=max_time_min,
                exclude_recipe_ids=exclude_recipe_ids or [],
                conn=conn,
            )
        except KeyError as exc:
            raise err("PROFILE_NOT_FOUND", f"profile not found: {profile_id}") from exc
        except NoCandidates as exc:
            raise err(
                "NO_CANDIDATES",
                str(exc),
                constraints=", ".join(exc.constraints) or "unknown",
            ) from exc
        except AssignmentError as exc:
            raise err("NO_CANDIDATES", str(exc), constraints=str(exc)) from exc
        except EmbeddingModelMismatch as exc:
            raise _embedding_error(exc) from exc


@tool
def get_meal_plan(plan_id: str, profile_id: str | None = None) -> dict:
    """Return a stored meal plan, accepting plan_id='latest' with profile_id."""

    with open_db() as conn:
        resolved_id = plan_id
        if plan_id == "latest":
            if not profile_id:
                raise err("INVALID_INPUT", "profile_id is required for plan_id='latest'")
            require_profile(conn, profile_id)
            resolved_id = latest_plan_id(conn, profile_id)
            if resolved_id is None:
                raise err("PLAN_NOT_FOUND", f"no plans found for profile: {profile_id}")
        stored = require_plan(conn, str(resolved_id))
        if profile_id and stored["profile_id"] != profile_id:
            raise err("PLAN_NOT_FOUND", f"plan not found for profile: {profile_id}")
        return stored_plan_response(conn, str(resolved_id))


@tool
def search_recipes(
    query_en: str,
    profile_id: str | None = None,
    max_price_per_serving_eur: float | None = None,
    max_total_time_min: int | None = None,
    course: str | None = None,
    limit: int = 10,
    include_custom: bool = True,
) -> dict:
    """Search the local recipe index with optional profile hard filters."""

    if not query_en or not query_en.strip():
        raise err("INVALID_INPUT", "query_en is required")
    if limit < 1 or limit > 10:
        raise err("INVALID_INPUT", "limit must be between 1 and 10")
    if max_price_per_serving_eur is not None and max_price_per_serving_eur <= 0:
        raise err("INVALID_INPUT", "max_price_per_serving_eur must be positive")
    if max_total_time_min is not None and max_total_time_min < 1:
        raise err("INVALID_INPUT", "max_total_time_min must be >= 1")

    with open_db() as conn:
        ctx = None
        if profile_id:
            require_profile(conn, profile_id)
            ctx = build_user_context(profile_id, conn=conn)
        try:
            candidates = LocalRecipeIndex.load(INDEX_DIR).search(
                query_en,
                ctx,
                k=limit,
                max_time_min=max_total_time_min,
                max_price_per_serving=max_price_per_serving_eur,
                course=course,
            )
        except NoCandidates as exc:
            raise err(
                "NO_CANDIDATES",
                str(exc),
                constraints=", ".join(exc.constraints) or "unknown",
            ) from exc
        except EmbeddingModelMismatch as exc:
            raise _embedding_error(exc) from exc

    cards = [_candidate_card(candidate) for candidate in candidates]
    if not include_custom:
        cards = [card for card in cards if card["source"] == "dataset"]
    titles = ", ".join(card["title"] for card in cards[:5]) or "No matching recipes."
    return {"recipes": cards, "text": titles}


def _validate_plan_request(
    budget_eur: float,
    servings: int,
    num_days: int,
    meals_per_day: int,
    max_time_min: int | None,
) -> None:
    if budget_eur <= 0:
        raise err("INVALID_BUDGET", "budget_eur must be positive")
    if servings < 1:
        raise err("INVALID_INPUT", "servings must be >= 1")
    if not 1 <= num_days <= 14:
        raise err("INVALID_INPUT", "num_days must be between 1 and 14")
    if not 1 <= meals_per_day <= 3:
        raise err("INVALID_INPUT", "meals_per_day must be between 1 and 3")
    if max_time_min is not None and max_time_min < 1:
        raise err("INVALID_INPUT", "max_time_min must be >= 1")
    minimum = servings * num_days * meals_per_day * 0.5
    if budget_eur < minimum:
        raise err(
            "BUDGET_TOO_LOW",
            f"budget_eur is below the rough minimum {minimum:.2f} EUR",
            servings=servings,
            slots=num_days * meals_per_day,
            minimum_eur=minimum,
        )


def _candidate_card(candidate: Any) -> dict[str, Any]:
    metadata = candidate.metadata
    nutrition = parse_json_dict(
        metadata.get("nutrition_per_serving_json")
        or metadata.get("nutrition_json")
        or metadata.get("nutrition_per_serving")
    )
    flags = {
        key: _jsonable(value)
        for key, value in metadata.items()
        if key.startswith("is_") and isinstance(_jsonable(value), bool)
    }
    return {
        "recipe_id": candidate.recipe_id,
        "title": candidate.title,
        "time_min": _jsonable(metadata.get("total_time_min")),
        "price_per_serving_eur": _jsonable(metadata.get("price_per_serving_eur")),
        "kcal_per_serving": nutrition.get("kcal") or nutrition.get("calories_kcal"),
        "dietary_flags": flags,
        "source": candidate.source,
    }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def _embedding_error(exc: EmbeddingModelMismatch):
    return err(
        "EMBEDDING_MODEL_MISMATCH",
        str(exc),
        got_model=exc.got[0],
        expected_model=exc.expected[0],
    )
