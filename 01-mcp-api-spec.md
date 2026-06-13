<!-- Context/Task file: 01-mcp-api-spec.md -->

## 9. MCP Tool API Specification

This is the implementation contract for `gamito-mcp`. Tool wrappers stay thin:
validate inputs, open one SQLite connection, call core code, catch `GamitoError`,
and return either a success payload or the §9.6 error shape.

Conventions:

- **20 tools across 8 namespaces**: profiles 4, planning 3, lifecycle 3,
  edits 2, shopping 1, pantry 2, feedback 1, recipes 4.
- **Baseline = 13 tools** (everything except lifecycle + recipes); v2.1 adds
  3 plan-lifecycle tools and 4 custom-recipe tools.
- Inputs use **flat scalar/array params** where possible. Recipe ingredients use
  parallel arrays instead of nested objects for reliable tool calling.
- Money is EUR (`*_eur`), times are minutes, IDs are strings (`uuid4` hex or
  `custom_<uuid4hex>`), and `slot_key` is always `day_N:<slot>`.
- Every success response carries `text`, already compact and chat-forwardable
  in the profile language where a profile/plan is known.
- Optional arrays default to `[]`. Optional scalars default to `null`/omitted.
  For update-style tools, `null` clears only where explicitly stated.
- Core validation limits: `num_days` 1–14, `meals_per_day` 1–3, `servings` ≥ 1,
  list limits as documented, positive budgets/prices, ratings 1–10.

### 9.1 Profiles

**`list_profiles()`** → `{profiles: [{profile_id, name, language}], text}`

**`get_profile(profile_id)`** → full profile incl. allergies, tools, cuisines,
top ±tags by weight, pantry item count, `text` summary.

**`save_profile(...)`** — create (no `profile_id`) or update (with it):

```
params: name, language, dietary_pref, allergies[], disliked_ingredients[],
        kitchen_tools[], cuisine_preferences[], skill_level,
        meal_prep_ok, leftovers_ok, max_time_min, profile_id?
returns: {profile_id, created: bool, tags_generated: int, text}
```

Structured fields map to `profile_tags` by **rules** (dietary pref → tag;
cuisines → positive tags; dislikes → negative tags; no LLM).
On update, supplied arrays replace the profile's allergy/tool/cuisine rows to
keep behaviour simple and predictable; callers that want to preserve values
should pass the current values back.

**`update_preferences(profile_id, liked_tags[], disliked_tags[])`** →
`{profile_id, applied: [{tag, sentiment, delta, source}], text}`. Used when
Hermes classifies conversational feedback ("šįkart be aitriųjų" →
`disliked_tags: ["spicy"]`). Gamito stores these as `source='correction'`.

### 9.2 Planning

**`generate_meal_plan`** — the flagship tool:

```
params:  profile_id, budget_eur, servings, num_days (1–14),
         meals_per_day (1–3), max_time_min?, exclude_recipe_ids[]?
returns: {
  plan_id, status,
  meals: [{slot_key, day, slot, recipe_id, title, meal_type, source,
           source_slot_key?,
           time_min, cost_eur, cost_per_serving_eur,
           nutrition_per_serving: {kcal, protein_g, carbs_g, fat_g},
           ingredients: [{name, amount, canonical, est_price_eur}],
           directions: [...], tools: [...]}],
  shopping_list: {items: [{canonical, amount, est_price_eur, meal_keys[]}],
                  pantry_items: [...0 EUR...], total_eur},
  budget: {requested_eur, estimated_eur, delta_eur, within_tolerance},
  warnings: [...],
  text: "<compact chat rendering, profile language>"
}
```

Runs the full pipeline (§10), persists plan + meals, returns in < 2 s.
`source` is `'dataset'|'custom'`; `recipe_id` is present for every non-leftover
meal and copied from the source slot for leftovers.

**`get_meal_plan(plan_id, profile_id?)`** → stored plan, same shape. Also accepts
`plan_id="latest"` when `profile_id` is supplied; without `profile_id`, `latest`
returns `INVALID_INPUT` rather than guessing across people.

**`search_recipes(query_en, profile_id?, max_price_per_serving_eur?,
max_total_time_min?, course?, limit≤10, include_custom=true)`** → candidate
cards (id, title, time, price/serving, kcal/serving, dietary flags,
`source: 'dataset'|'custom'`). With `profile_id`, hard filters apply
automatically. Custom recipes (§7.5) are included by default; pass
`include_custom=false` to query only the static dataset.

#### Plan lifecycle tools

**`label_plan(plan_id, label?, is_favorite?)`** — attach a short name and/or
mark a plan favourite. Either field optional; passing `null` clears the
field. Returns `{plan_id, label, is_favorite, text}`. The `(profile_id, label)`
pair is unique — collisions return `LABEL_TAKEN` with the existing plan_id.
At least one of `label` or `is_favorite` must be supplied.

**`list_plans(profile_id, favorites_only=false, labelled_only=false, limit=20)`**
→ `{plans: [{plan_id, label?, is_favorite, num_days, meals_per_day,
total_cost_eur, plan_summary, avg_meal_rating?, created_at}], text}`.
`avg_meal_rating` is computed from `meal_ratings` rows that match this
plan. The `text` field renders a compact list ("⭐ Cheap weeknights · 5d×2 ·
9.3/10 · 27 May").

**`regenerate_plan`** — produce a new plan based on a previous one, preserving
liked slots and avoiding disliked recipes:

```
params:  plan_id,
         keep_slot_keys[]?,           # explicit overrides; if omitted,
                                      #   inferred from meal_ratings (≥ 8 keep)
         avoid_recipe_ids[]?,         # explicit; if omitted, inferred from
                                      #   meal_ratings (≤ 4 avoid)
         budget_eur?, servings?, num_days?, meals_per_day?, max_time_min?
                                      # any overrides default to source plan's
returns: same shape as generate_meal_plan, plus
         {regenerated_from: <source plan_id>,
          preserved_slots: [...],
          avoided_recipe_ids: [...]}
```

Internally: load the source plan, compute the keep/avoid sets, freeze the
preserved slots into the planner's initial assignment, run the same graph
(§10) for the remaining slots only, persist a new `plan_id` with
`regenerated_from = <source>`. Cost-fold of preserved slots into the budget
allocator; validator runs over the merged plan. If user-supplied
`keep_slot_keys` cover the whole plan, this collapses to a duplicate-with-
edits and skips assignment entirely.
If `keep_slot_keys` is supplied as an empty array, no slots are preserved even
when high ratings exist; `avoid_recipe_ids` follows the same explicit-override
rule.

### 9.3 Edits

**`swap_meal(plan_id, slot_key, query_en, max_price_eur?)`**:
constrained search (profile filters + plan-wide recipe-id exclusion) → best
candidate replaces the slot → validator re-checks plan → shopping list rebuilt →
persisted + `plan_edits` audit row.
Returns `{old_meal, new_meal, budget, shopping_list, warnings, text}` — `text`
is a human diff ("Vakarienė d.2: Mushroom Risotto → Lentil Curry, −1.20 EUR").
`query_en` is always English; Hermes handles translation before the call.
`max_price_eur` is a total slot cap, not price per serving.

**`rescale_meal(plan_id, slot_key, servings)`** → pure-math quantity/cost/
nutrition rescale (ported unit helpers), shopping list rebuilt. Returns
`{meal, budget, shopping_list, warnings, text}`.

### 9.4 Shopping & pantry

**`get_shopping_list(plan_id, use_pantry=true)`** → items + pantry split + total.

**`get_pantry(profile_id)`** → canonical staples + `last_seen_at` + staleness
note in `text` if oldest > 45 days.

**`update_pantry(profile_id, add_items[], remove_items[])`**:
free-form labels in (from Hermes vision on a shelf photo, or the user's words) →
local canonicalisation + slow-use filter →
`{added: [{label, canonical}], rejected: [{label, reason}], pantry_size, text}`.
Rejections carry reasons (`'perishable'`, `'unrecognised'`) so the agent can
explain or ask.
`remove_items[]` accepts the same free-form labels and removes by resolved
canonical name; missing items are ignored and may be reported in `rejected`.

### 9.5 Feedback

**`rate_meal(plan_id, slot_key, rating 1–10)`** → stores rating; applies rule
deltas (≥8 → +1 positive on the meal's tags, ≤4 → +1 negative); returns applied
deltas + `text` ("Noted — more like this. asian +1, tofu +1").
The profile is derived from the plan row, so the tool does not accept a separate
`profile_id`.

Per-meal ratings are also the implicit signal for `regenerate_plan` (§9.2)
when the user calls "improve this plan" without specifying which slots to
keep — ratings ≥ 8 are preserved, recipes used in slots rated ≤ 4 are excluded
from the regeneration's candidate pool.

### 9.6 Error contract

```
{error_code, message, hint}
```

| Code | Hint style (written for the LLM) |
|---|---|
| `INVALID_INPUT` | "Fix the parameter shape/range and retry. For plan_id='latest', include profile_id." |
| `PROFILE_NOT_FOUND` | "Call list_profiles; create one with save_profile after interviewing the user." |
| `PLAN_NOT_FOUND` / `SLOT_NOT_FOUND` | "Valid slot_keys for this plan: day_1:lunch, day_1:dinner, …" |
| `INVALID_BUDGET` / `BUDGET_TOO_LOW` | "Minimum feasible for 2 servings × 9 slots is ≈ 18.40 EUR; ask the user to raise the budget or reduce days." |
| `NO_CANDIDATES` | "Constraints emptying the pool: gluten_free + max 15 min + air_fryer-only. Suggest relaxing time to 25 min." |
| `VALIDATION_FAILED` | includes the validator issues verbatim |
| `LABEL_TAKEN` | "Profile already has plan labelled 'Cheap weeknights' (plan_id=…). Pick another label or unset the existing one." |
| `RECIPE_NOT_FOUND` | "Call list_custom_recipes; only `custom_*` recipe_ids are editable." |
| `RECIPE_VALIDATION_FAILED` | "Required fields: title, ingredient_names/ingredient_amounts (≥1, same length), directions (≥1). Provide amounts even if approximate ('to taste' is fine)." |
| `RECIPE_IN_USE` | "Recipe is referenced by plans <ids>; pass `force=true` to delete and orphan those references, or update instead." |
| `EMBEDDING_MODEL_MISMATCH` | "Custom recipe was embedded with model X but index expects Y. Run `gamito custom-recipes re-embed`." |

### 9.7 Recipes (custom / household cookbook)

Household-shared CRUD over `custom_recipes` (§8). Mutating tools bump the
`custom_recipes_meta.revision` counter so `LocalRecipeIndex` reloads the custom
layer (§7.5) on the next search.

**`add_recipe`** — create a new custom recipe (Hermes parses free text/photo
into these fields *before* calling; Gamito itself does no LLM work):

```
params:
  title:                 string                    # required
  ingredient_names[]:    [string]                  # required, ≥ 1
  ingredient_amounts[]:  [string]                  # required, same length as names
  ingredient_units[]?:   [string]                  # optional; if supplied, same length
  directions[]:          [string]                  # required, ≥ 1
  cuisines[]?:           [string]                  # e.g. ["italian","comfort"]
  courses[]?:            [string]                  # ["dinner"] etc.
  tastes[]?:             [string]                  # ["savory","mild"] etc.
  total_time_min?:       int
  difficulty?:           'easy'|'medium'|'hard'
  servings?:             int (default 2)
  tools[]?:              [string]                  # canonical list (oven, skillet, ...)
  dietary_flags[]?:      [string]                  # declared true flags: vegan,
                                                    # vegetarian, gluten_free,
                                                    # dairy_free, nut_free, ...
  allergens[]?:          [string]                  # ["dairy","nuts"] user-declared
  notes?:                string
  added_by_profile_id?:  string                    # provenance only

returns:
  {recipe_id,                                     # 'custom_<uuid4hex>'
   canonicalisation: [{name, canonical, matched: bool}],
   estimated_price_per_serving_eur?: float,       # via canonical pricing chain
   warnings: [...],                               # missing canonicals, etc.
   text: "Saved 'Mama's lasagne' (custom_a3b…). 8 ingredients, 6 canonicalised
          (2 unmatched: 'fior di latte', 'tipo 00 flour' — staying free-text).
          Estimated 4.20 EUR/serving. Now searchable in plans."}
```

Side effects: canonicalises ingredient names through the same parquet chain
the shopping list uses (§4.1), runs the canonical pricing chain to estimate
`price_per_serving_eur`, encodes the embedding text (§7.5), inserts both rows,
bumps the revision counter.
The wrapper converts the parallel ingredient arrays into
`[{name, amount, unit?, canonical?}]` before storing `ingredients_json`.
`dietary_flags[]` maps to the `dietary_json` booleans with omitted flags treated
as false/unknown for hard-filter purposes.

**`update_recipe(recipe_id, ...same optional fields...)`** — patch any subset
of fields using the same flat params as `add_recipe`. If any ingredient field
is supplied, `ingredient_names[]` and `ingredient_amounts[]` must both be
supplied and valid together; omitted fields stay unchanged, while explicit empty
arrays are invalid for ingredients/directions. Ingredient changes trigger
re-canonicalisation and re-pricing. Re-embedding runs iff the embedding text
changes (title, cuisines, courses, tastes, tools, ingredients, or directions).
Dietary flag/allergen changes update filter metadata without re-embedding.
Returns the same shape as `add_recipe` (with `updated_fields[]`).

**`delete_recipe(recipe_id, force=false)`** — only `custom_*` recipe_ids are
deletable. If the recipe is referenced by any persisted `plan_meals` row,
returns `RECIPE_IN_USE` unless `force=true`; with `force`, the recipe row is
deleted and historical plan_meals rows keep their denormalised
`recipe_title` / `ingredients_json` / `directions_json` (they were copied at
plan generation time, so nothing breaks).

**`list_custom_recipes(query_en?, cuisine?, max_total_time_min?, limit≤50)`**
→ `{recipes: [{recipe_id, title, cuisines, total_time_min, servings,
price_per_serving_eur, allergens, dietary_flags, created_at}], total, text}`.
Used by the agent for "show me what's in our cookbook" and for confirming
edits ("which lasagne do you mean — Mama's or the new one from last week?").

CLI counterparts (`gamito custom-recipes import / re-embed / list`) cover the
batch workflow that the §16 stretch *Family recipe pack* used to require its
own tooling for.

---