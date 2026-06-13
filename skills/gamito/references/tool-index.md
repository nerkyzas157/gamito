# Gamito Tool Index

Just-in-time reference for the 20 `gamito` MCP tools, grouped by namespace.
Call every tool by its fully qualified name, `gamito:<tool>`. Schemas are
auto-discovered at connect time; this file is the human-readable contract.

> **Note:** Money is EUR (`*_eur`), times are minutes, IDs are strings, and
> `slot_key` is always `day_N:<slot>`. Optional arrays default to `[]`; optional
> scalars default to `null`/omitted. Every success payload includes a compact,
> chat-forwardable `text` field rendered in the profile language.

## Profiles

| Tool | Purpose | Key parameters |
|---|---|---|
| `list_profiles` | List known people | — |
| `get_profile` | Full profile incl. allergies, tools, cuisines, top tags | `profile_id` |
| `save_profile` | Create (no id) or update (with id); arrays replace rows | `name`, `language`, `dietary_pref`, `allergies[]`, `disliked_ingredients[]`, `kitchen_tools[]`, `cuisine_preferences[]`, `skill_level`, `meal_prep_ok`, `leftovers_ok`, `max_time_min`, `profile_id?` |
| `update_preferences` | Apply soft taste deltas from feedback | `profile_id`, `liked_tags[]`, `disliked_tags[]` |

## Planning

| Tool | Purpose | Key parameters |
|---|---|---|
| `generate_meal_plan` | Flagship: full plan + shopping list + budget check | `profile_id`, `budget_eur`, `servings`, `num_days` (1–14), `meals_per_day` (1–3), `max_time_min?`, `exclude_recipe_ids[]?` |
| `get_meal_plan` | Fetch a stored plan; `plan_id="latest"` needs `profile_id` | `plan_id`, `profile_id?` |
| `search_recipes` | Candidate cards; profile applies hard filters | `query_en`, `profile_id?`, `max_price_per_serving_eur?`, `max_total_time_min?`, `course?`, `limit≤10`, `include_custom=true` |

## Plan Lifecycle

| Tool | Purpose | Key parameters |
|---|---|---|
| `label_plan` | Name a plan and/or mark favourite; `null` clears | `plan_id`, `label?`, `is_favorite?` |
| `list_plans` | List saved plans with summaries and avg rating | `profile_id`, `favorites_only=false`, `labelled_only=false`, `limit=20` |
| `regenerate_plan` | New plan from a source; keep liked, avoid disliked | `plan_id`, `keep_slot_keys[]?`, `avoid_recipe_ids[]?`, plus optional `budget_eur?`, `servings?`, `num_days?`, `meals_per_day?`, `max_time_min?` |

> **Note:** For `regenerate_plan`, omitting `keep_slot_keys`/`avoid_recipe_ids`
> infers them from ratings (≥ 8 keep, ≤ 4 avoid). An explicit empty array means
> "preserve/avoid nothing".

## Edits

| Tool | Purpose | Key parameters |
|---|---|---|
| `swap_meal` | Replace one slot; `query_en` always English | `plan_id`, `slot_key`, `query_en`, `max_price_eur?` (total slot cap) |
| `rescale_meal` | Pure-math quantity/cost/nutrition rescale | `plan_id`, `slot_key`, `servings` |

## Shopping & Pantry

| Tool | Purpose | Key parameters |
|---|---|---|
| `get_shopping_list` | Items + pantry split + total | `plan_id`, `use_pantry=true` |
| `get_pantry` | Canonical staples + staleness note | `profile_id` |
| `update_pantry` | Add/remove free-form labels; returns added/rejected | `profile_id`, `add_items[]`, `remove_items[]` |

## Feedback

| Tool | Purpose | Key parameters |
|---|---|---|
| `rate_meal` | Store a 1–10 rating; applies tag deltas | `plan_id`, `slot_key`, `rating` (1–10) |

## Recipes (Household Cookbook)

| Tool | Purpose | Key parameters |
|---|---|---|
| `add_recipe` | Create a custom recipe from structured fields | `title`, `ingredient_names[]`, `ingredient_amounts[]`, `directions[]`, plus optional `ingredient_units[]?`, `cuisines[]?`, `courses[]?`, `tastes[]?`, `total_time_min?`, `difficulty?`, `servings?`, `tools[]?`, `dietary_flags[]?`, `allergens[]?`, `notes?`, `added_by_profile_id?` |
| `update_recipe` | Patch any subset of fields (same flat params) | `recipe_id`, + fields to change |
| `delete_recipe` | Delete a `custom_*` recipe; `force` to orphan refs | `recipe_id`, `force=false` |
| `list_custom_recipes` | Browse the household cookbook | `query_en?`, `cuisine?`, `max_total_time_min?`, `limit≤50` |

> **Note:** Recipe ingredients use **parallel arrays** (`ingredient_names[]`,
> `ingredient_amounts[]`, optional `ingredient_units[]`), not nested objects.
> When any ingredient field is supplied to `update_recipe`, both names and
> amounts must be supplied, same length.

## Error Contract

Every failure returns `{error_code, message, hint}`. The `hint` is written for
the agent — act on it first.

| `error_code` | Meaning / recovery |
|---|---|
| `INVALID_INPUT` | Fix parameter shape/range and retry. For `plan_id="latest"`, include `profile_id`. |
| `PROFILE_NOT_FOUND` | Call `list_profiles`; create with `save_profile` after interviewing. |
| `PLAN_NOT_FOUND` / `SLOT_NOT_FOUND` | Use a valid id/slot; hint lists valid `slot_key`s. |
| `INVALID_BUDGET` / `BUDGET_TOO_LOW` | Relay the stated minimum; raise budget or reduce days. |
| `NO_CANDIDATES` | Constraints emptied the pool; propose the hinted relaxation. |
| `VALIDATION_FAILED` | Includes validator issues verbatim; surface them. |
| `LABEL_TAKEN` | Label already used; pick another or unset the existing plan's label. |
| `RECIPE_NOT_FOUND` | Only `custom_*` recipe ids are editable; call `list_custom_recipes`. |
| `RECIPE_VALIDATION_FAILED` | Provide title, paired names/amounts (≥1), directions (≥1). |
| `RECIPE_IN_USE` | Referenced by plans; pass `force=true` to delete, or update instead. |
| `EMBEDDING_MODEL_MISMATCH` | Custom recipe embedded with a stale model; re-embed via CLI. |
