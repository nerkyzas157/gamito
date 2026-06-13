"""Pandas filter masks for local recipe retrieval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

ALLERGEN_TO_FLAG: dict[str, str] = {
    "dairy": "is_dairy_free",
    "gluten": "is_gluten_free",
    "nuts": "is_nut_free",
    "nut": "is_nut_free",
    "peanuts": "is_peanut_free",
    "peanut": "is_peanut_free",
    "shellfish": "is_shellfish_free",
    "soy": "is_soy_free",
    "eggs": "is_egg_free",
    "egg": "is_egg_free",
    "fish": "is_fish_free",
}


@dataclass(frozen=True)
class RecipeSearchContext:
    """Normalized retrieval constraints independent from Pydantic models."""

    max_time_min: int | None = None
    max_price_per_serving: float | None = None
    allergies: tuple[str, ...] = ()
    dietary_pref: str | None = None
    owned_tools: tuple[str, ...] = ()
    preferred_cuisines: tuple[str, ...] = ()
    exclude_recipe_ids: tuple[str, ...] = ()
    min_healthiness_score: int | None = None
    course: str | None = None

    @classmethod
    def from_context(
        cls,
        ctx: Any = None,
        *,
        max_time_min: int | None = None,
        max_price_per_serving: float | None = None,
        exclude_recipe_ids: list[str] | tuple[str, ...] | None = None,
        preferred_cuisines: list[str] | tuple[str, ...] | None = None,
        course: str | None = None,
    ) -> "RecipeSearchContext":
        """Bridge existing UserContext names to retrieval's filter names."""

        if isinstance(ctx, RecipeSearchContext):
            base = ctx
        else:
            base = cls(
                max_time_min=_first(ctx, "max_time_min", "time_ceiling_minutes"),
                max_price_per_serving=_first(
                    ctx,
                    "max_price_per_serving",
                    "max_price_per_serving_eur",
                ),
                allergies=_as_tuple(_first(ctx, "allergies", "negative_tags")),
                dietary_pref=_first(ctx, "dietary_pref"),
                owned_tools=_as_tuple(
                    _first(ctx, "owned_tools", "available_tools", "kitchen_tools")
                ),
                preferred_cuisines=_as_tuple(
                    _first(
                        ctx,
                        "preferred_cuisines",
                        "cuisine_hints",
                        "cuisine_preferences",
                    )
                ),
                exclude_recipe_ids=_as_tuple(_first(ctx, "exclude_recipe_ids")),
                min_healthiness_score=_first(ctx, "min_healthiness_score"),
                course=_first(ctx, "course"),
            )

        return replace(
            base,
            max_time_min=max_time_min
            if max_time_min is not None
            else base.max_time_min,
            max_price_per_serving=max_price_per_serving
            if max_price_per_serving is not None
            else base.max_price_per_serving,
            exclude_recipe_ids=_as_tuple(exclude_recipe_ids)
            if exclude_recipe_ids is not None
            else base.exclude_recipe_ids,
            preferred_cuisines=_as_tuple(preferred_cuisines)
            if preferred_cuisines is not None
            else base.preferred_cuisines,
            course=course if course is not None else base.course,
        )


@dataclass(frozen=True)
class FilterOutcome:
    """Filtered rows plus relaxation metadata."""

    candidates: pd.DataFrame
    relaxed_constraints: tuple[str, ...] = ()
    emptying_constraints: tuple[str, ...] = ()


def apply_filters(df: pd.DataFrame, ctx: RecipeSearchContext | Any) -> pd.DataFrame:
    """Apply hard and soft retrieval filters and return matching rows."""

    search_ctx = RecipeSearchContext.from_context(ctx)
    return df[_combined_mask(df, search_ctx)]


def apply_filters_with_relaxation(
    df: pd.DataFrame,
    ctx: RecipeSearchContext | Any,
) -> FilterOutcome:
    """Apply the G1 relaxation ladder when the initial pool is empty."""

    search_ctx = RecipeSearchContext.from_context(ctx)
    candidates = apply_filters(df, search_ctx)
    if not candidates.empty:
        return FilterOutcome(candidates=candidates)

    relaxed_steps: list[str] = []
    relaxed = search_ctx

    if relaxed.preferred_cuisines:
        relaxed = replace(relaxed, preferred_cuisines=())
        relaxed_steps.append("preferred_cuisines")
        candidates = apply_filters(df, relaxed)
        if not candidates.empty:
            return FilterOutcome(candidates=candidates, relaxed_constraints=tuple(relaxed_steps))

    if relaxed.min_healthiness_score is not None:
        relaxed = replace(relaxed, min_healthiness_score=None)
        relaxed_steps.append("min_healthiness_score")
        candidates = apply_filters(df, relaxed)
        if not candidates.empty:
            return FilterOutcome(candidates=candidates, relaxed_constraints=tuple(relaxed_steps))

    if relaxed.max_time_min is not None:
        relaxed = replace(relaxed, max_time_min=max(1, round(relaxed.max_time_min * 1.25)))
        relaxed_steps.append("max_time_min:+25%")
        candidates = apply_filters(df, relaxed)
        if not candidates.empty:
            return FilterOutcome(candidates=candidates, relaxed_constraints=tuple(relaxed_steps))

    return FilterOutcome(
        candidates=candidates,
        relaxed_constraints=tuple(relaxed_steps),
        emptying_constraints=tuple(emptying_constraints(df, search_ctx)),
    )


def emptying_constraints(df: pd.DataFrame, ctx: RecipeSearchContext | Any) -> list[str]:
    """Identify constraints that first empty the candidate pool."""

    search_ctx = RecipeSearchContext.from_context(ctx)
    mask = pd.Series(True, index=df.index)
    emptying: list[str] = []
    for label, step_mask in _mask_steps(df, search_ctx):
        before_had_rows = bool(mask.any())
        mask &= step_mask
        if before_had_rows and not mask.any():
            emptying.append(label)
    return emptying


def _combined_mask(df: pd.DataFrame, ctx: RecipeSearchContext) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for _, step_mask in _mask_steps(df, ctx):
        mask &= step_mask
    return mask


def _mask_steps(
    df: pd.DataFrame,
    ctx: RecipeSearchContext,
) -> list[tuple[str, pd.Series]]:
    steps: list[tuple[str, pd.Series]] = []

    if ctx.max_time_min is not None:
        steps.append(
            (
                f"max_time_min<={ctx.max_time_min}",
                _numeric(df, "total_time_min") <= ctx.max_time_min,
            )
        )

    if ctx.max_price_per_serving is not None:
        steps.append(
            (
                f"price_per_serving_eur<={ctx.max_price_per_serving}",
                _numeric(df, "price_per_serving_eur") <= ctx.max_price_per_serving,
            )
        )

    for allergen in ctx.allergies:
        flag = ALLERGEN_TO_FLAG.get(_normalise(allergen))
        if not flag:
            continue
        steps.append((flag, _bool_column(df, flag)))

    dietary = _normalise(ctx.dietary_pref)
    if dietary == "vegan":
        steps.append(("is_vegan", _bool_column(df, "is_vegan")))
    elif dietary == "vegetarian":
        steps.append(("is_vegetarian", _bool_column(df, "is_vegetarian")))

    if ctx.owned_tools:
        owned = frozenset(_normalise(tool) for tool in ctx.owned_tools)
        steps.append(
            (
                "kitchen_tools<=owned_tools",
                _set_column(df, "kitchen_tools").map(lambda tools: tools <= owned),
            )
        )

    if ctx.exclude_recipe_ids:
        excluded = set(ctx.exclude_recipe_ids)
        steps.append(("exclude_recipe_ids", ~df["recipe_id"].isin(excluded)))

    if ctx.course:
        course = _normalise(ctx.course)
        steps.append(
            (
                f"course={course}",
                _set_column(df, "course_list").map(lambda courses: course in courses),
            )
        )

    if ctx.min_healthiness_score is not None:
        steps.append(
            (
                f"healthiness_score>={ctx.min_healthiness_score}",
                _numeric(df, "healthiness_score") >= ctx.min_healthiness_score,
            )
        )

    if ctx.preferred_cuisines:
        preferred = frozenset(_normalise(cuisine) for cuisine in ctx.preferred_cuisines)
        steps.append(
            (
                "preferred_cuisines",
                _set_column(df, "cuisine_list").map(
                    lambda cuisines: bool(cuisines & preferred)
                ),
            )
        )

    return steps


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(float("nan"), index=df.index)
    return pd.to_numeric(df[column], errors="coerce")


def _bool_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    values = df[column]
    if values.dtype == bool:
        return values
    return values.map(lambda value: str(value).strip().lower() == "true")


def _set_column(df: pd.DataFrame, column: str) -> pd.Series:
    cached = f"_{column}_set"
    if cached in df.columns:
        return df[cached]
    if column not in df.columns:
        return pd.Series(frozenset(), index=df.index)
    return df[column].map(lambda values: frozenset(_as_tuple(values)))


def _first(ctx: Any, *names: str) -> Any:
    if ctx is None:
        return None
    for name in names:
        if isinstance(ctx, dict) and name in ctx:
            value = ctx[name]
        else:
            value = getattr(ctx, name, None)
        if value is not None:
            return value
    return None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_normalise(value),) if value.strip() else ()
    return tuple(_normalise(item) for item in value if _normalise(item))


def _normalise(value: Any) -> str:
    return str(value).strip().lower()
