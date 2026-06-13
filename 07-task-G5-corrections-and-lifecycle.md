<!-- Context/Task file: 07-task-G5-corrections-and-lifecycle.md -->

# Task: Phase G5 & G5b - Corrections, Learning & Lifecycle

**Goal**: Implement meal swapping, rating feedback loops, plan lifecycle, and custom recipes.

**Required Context**: `00-architecture-and-context.md`, `01-mcp-api-spec.md`

### Phase G5 — Corrections & Learning (2–3 days) — P1

- [ ] `swap_meal` internals (constrained search → validator → shopping rebuild →
      audit row → diff text)
- [ ] `rescale_meal` (unit-helper math, costs + nutrition)
- [ ] `updater.py`: rating rules; weights feed context ordering **and** the
      retrieval query text ("prefers: spicy, asian" appended — cheap, measurable)
- [ ] `update_preferences` with `source='correction'`
- [ ] Tests: swap exclusions, rescale math, weight accumulation, learning-loop
      integration (rate low → regenerate → assert ranking shift)

**Accept**: the rate-→ regenerate → visibly-different loop passes as a test.

**Reference: rating deltas** (`recommendation/updater.py`) — `rate_meal` applies
rule-based weight nudges to the meal's tags (≥8 boosts, ≤4 penalises); the
middle band is intentionally inert so noise doesn't churn the profile (§9.5):

```python
def rating_deltas(rating: int, meal_tags: list[str]) -> list[tuple[str, str, int]]:
    """Returns (tag, sentiment, weight_delta) rows; source='rating' in the repo."""
    if rating >= 8:
        return [(t, "positive", +1) for t in meal_tags]
    if rating <= 4:
        return [(t, "negative", +1) for t in meal_tags]
    return []                                     # 5–7: no signal, no churn
```

### Phase G5b — Plan Lifecycle & Custom Recipes (3–4 days) — P1

The v2.1 feature track. Lands after G5 (its `meal_ratings` deltas feed the
auto-mode of `regenerate_plan`); MVP-required for the experience the user
asked for, P1-priority because Gamito is already useful without it.

**Reference: regenerate auto-mode** — when the user says "improve this plan"
without naming slots, derive the keep/avoid sets from `meal_ratings` (§9.2):
ratings ≥ 8 preserve the slot verbatim, recipes used in slots rated ≤ 4 are
banned from the candidate pool. Explicit overrides win when supplied:

```python
def infer_keep_avoid(conn, plan_id, keep_override=None, avoid_override=None):
    ratings = conn.execute(
        "SELECT slot_key, rating FROM meal_ratings WHERE plan_id = ?", (plan_id,)
    ).fetchall()
    keep = list(keep_override) if keep_override is not None else [
        r["slot_key"] for r in ratings if r["rating"] >= 8
    ]
    avoid = list(avoid_override) if avoid_override is not None else [
        recipe_id_for_slot(conn, plan_id, r["slot_key"])
        for r in ratings if r["rating"] <= 4
    ]
    return keep, avoid          # keep == every slot ⇒ duplicate-with-edits (§9.2)
```

**Reference: `label_plan` collision** — the `(profile_id, label)` unique index
(§8) turns a duplicate label into the `LABEL_TAKEN` error rather than a crash:

```python
import sqlite3

def set_label(conn, profile_id, plan_id, label):
    try:
        with conn:
            conn.execute(
                "UPDATE meal_plans SET label = ?, updated_at = ? WHERE plan_id = ?",
                (label, _now(), plan_id),
            )
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT plan_id FROM meal_plans WHERE profile_id = ? AND label = ?",
            (profile_id, label),
        ).fetchone()
        raise err("LABEL_TAKEN", f"label {label!r} already in use",
                  plan_id=existing["plan_id"])
```

**Plan lifecycle (1–1.5 days):**
- [ ] Schema migration: add `label`, `is_favorite`, `regenerated_from`, `seed`,
      `updated_at` columns to `meal_plans` (§8); create the two new indices
- [ ] `db/plans.py` extensions: label upsert (with `LABEL_TAKEN` collision),
      favourite toggle, `list_plans` query joining `meal_ratings` for averages
- [ ] `mcp/tools/lifecycle.py`: `label_plan`, `list_plans`, `regenerate_plan`
- [ ] Planner integration: `preserved_slots` and `excluded_recipe_ids` injection
      points in `[budget]` and `[assignment]` (§10); validator runs over merged
      plan unchanged
- [ ] Auto-mode: ratings ≥ 8 → keep slot; recipes used in slots rated ≤ 4 →
      avoid; ties broken by recency
- [ ] Tests: label uniqueness, favourite toggle round-trip, `list_plans` join
      math, `regenerate_plan` preserves cost-folded budget, regenerate-with-
      empty-keep collapses to a fresh plan with exclusions, regenerate-with-
      full-keep yields the same plan with a new `plan_id` and bumped seed

**Reference: `add_recipe` side effects** (`db/custom_recipes.py`) — the full
chain from §9.7: canonicalise ingredient names through the same parquet lookups
the shopping list uses, price via the canonical chain, embed with the same
encoder (§7.5), insert both rows, and bump the revision so `LocalRecipeIndex`
reloads the custom layer on the next search:

```python
def add_recipe(conn, *, title, ingredients, directions, cuisines=(), courses=(),
               tastes=(), tools=(), servings=2, dietary_flags=None,
               allergens=(), added_by_profile_id=None):
    if not title or not ingredients or not directions:
        raise err("RECIPE_VALIDATION_FAILED", "title, ingredients, directions required")

    recipe_id = f"custom_{uuid.uuid4().hex}"
    canon = [canonicalise(i["name"]) for i in ingredients]      # parquet chain (§4.1)
    price = price_per_serving(canon, servings)                  # canonical pricing

    embed_text = build_embed_text(title, cuisines, courses, tastes,
                                  tools, ingredients, directions)   # §7.2 step 2
    vec = encode([embed_text])[0]                              # same fastembed model
    now = _now()
    with conn:                                                # one transaction
        conn.execute("INSERT INTO custom_recipes (...) VALUES (...)", (...))
        conn.execute(
            "INSERT INTO custom_recipe_embeddings "
            "(recipe_id, model, dims, vector, embed_text, encoded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (recipe_id, DEFAULT_MODEL, DIMS, pack_vector(vec), embed_text, now),
        )
        conn.execute("UPDATE custom_recipes_meta SET revision = revision + 1")
    return {"recipe_id": recipe_id,
            "canonicalisation": [{"name": i["name"], "canonical": c.name,
                                  "matched": c.matched} for i, c in zip(ingredients, canon)],
            "estimated_price_per_serving_eur": price}
```

**Reference: `delete_recipe` guard** — `RECIPE_IN_USE` unless `force=true`; with
force, historical `plan_meals` keep their denormalised columns so old plans
still render (§9.7):

```python
def delete_recipe(conn, recipe_id, force=False):
    if not recipe_id.startswith("custom_"):
        raise err("RECIPE_NOT_FOUND", "only custom_* recipe_ids are editable")
    used = [r["plan_id"] for r in conn.execute(
        "SELECT DISTINCT plan_id FROM plan_meals WHERE recipe_id = ?", (recipe_id,)
    )]
    if used and not force:
        raise err("RECIPE_IN_USE", f"referenced by plans {used}")
    with conn:                                    # CASCADE drops the embedding row
        conn.execute("DELETE FROM custom_recipes WHERE recipe_id = ?", (recipe_id,))
        conn.execute("UPDATE custom_recipes_meta SET revision = revision + 1")
    return {"deleted": recipe_id, "orphaned_plans": used if force else []}
```

**Custom recipes (2–2.5 days):**
- [ ] Schema migration: `custom_recipes`, `custom_recipe_embeddings`,
      `custom_recipes_meta` (§8); revision-bumping wrapper
- [ ] `db/custom_recipes.py`: CRUD with canonicalisation (parquet chain),
      pricing chain reuse, embedding encode/decode (float32 LE BLOB)
- [ ] `retrieval/custom.py`: load-from-SQLite layer; manifest cross-check;
      revision-watch + lazy reload
- [ ] `retrieval/index.py`: merged search across static + custom layers;
      `source` column propagated; `include_custom` switch wired through
      `search_recipes`
- [ ] `mcp/tools/recipes.py`: `add_recipe`, `update_recipe`, `delete_recipe`,
      `list_custom_recipes`; full error coverage (§9.6 new codes)
- [ ] CLI: `gamito custom-recipes import <csv>` (batch wrapper over
      `add_recipe`), `gamito custom-recipes re-embed` (model-upgrade path)
- [ ] Renderer: `compact.py` flags custom recipes ("⭐ Mama's lasagne — your
      recipe") so plans visibly include household additions
- [ ] Tests: round-trip add → search → appears in candidates, dietary/allergen
      hard-filter on custom recipes, `RECIPE_IN_USE` blocks delete unless
      forced, model-mismatch refusal, batched re-embed restores searchability,
      revision-watch invalidation under interleaved CRUD + search calls

**Accept**:
1. Run `add_recipe` for a custom dish → call `generate_meal_plan` →
   the custom recipe appears in at least one slot when its semantic match is
   strong enough, with the "⭐ your recipe" flag visible in `text`.
2. Rate two slots of an existing plan (9 and 2) → `regenerate_plan(plan_id)`
   with no overrides → new plan keeps the 9-rated slot verbatim and never
   uses the 2-rated recipe; diff is visible in `text`.
3. `label_plan(plan_id, "Cheap weeknights", is_favorite=true)` →
   `list_plans(profile_id, favorites_only=true)` returns it.