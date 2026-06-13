<!-- Context/Task file: 03-task-G1-retrieval.md -->

# Task: Phase G1 - Local Retrieval

**Goal**: Implement the local embedding and retrieval system.

**Required Context**: `00-architecture-and-context.md`

## 7. Local Retrieval Design

### 7.1 Embedding model

- Default: **`BAAI/bge-small-en-v1.5`** via fastembed — 384-dim, English, ~130 MB,
  strong MTEB-retrieval for its size, CPU-fast (~5–15 ms/query).
- Upgrade path if the golden-set eval (§13.3) disappoints: `bge-base-en-v1.5`
  (768-dim) — one re-run of `build_local_index.py`; brute force makes dimension
  changes free.
- English-only is deliberate: recipes are English, and the skill instructs Hermes
  to compose `query_en` in English regardless of chat language.

### 7.2 Index build (`scripts/build_local_index.py`)

1. Load `recipes_dataset.csv`; optional `feature_coverage ≥ 0.5` filter.
2. Build embedding text per recipe — same recipe as the old ingest script:
   `title + cuisines + courses + tastes + tools + ingredients + directions[:500]`.
3. Encode in batches (CPU, resumable, progress bar) → `embeddings.npy`
   (float32, L2-normalised rows, so cosine = dot product).
4. Write `metadata.parquet` — every filterable/displayable column, including
   pre-parsed list columns (`cuisine_list`, `kitchen_tools`) and the heavy JSON
   strings (`ingredients_json`, `directions_json`, `nutrition_per_serving_json`).
5. Write `manifest.json` (model, dims, count, dataset hash) — the index loader
   refuses mismatched manifests (prevents silent stale-index bugs).

### 7.3 Query path (`LocalRecipeIndex`)

```
candidates = metadata                      # pandas DataFrame, loaded once
  .pipe(mask: total_time        <= max_time)          # if set
  .pipe(mask: price_per_serving <= slot_budget/serv)  # if set
  .pipe(mask: is_<allergen>_free == True)             # per active allergen
  .pipe(mask: is_vegan/vegetarian == True)            # per dietary pref
  .pipe(mask: kitchen_tools ⊆ owned_tools)            # set containment
  .pipe(mask: cuisine_list ∩ preferred ≠ ∅)           # optional soft filter
  .pipe(mask: recipe_id ∉ excluded_recipe_ids)
scores = embeddings[candidates.index] @ query_vec      # exact cosine
return top_k(candidates, scores, k)
```

Semantics mirror the old Pinecone filters (`$lte`/`$in`/bool `$eq`) so the ported
`meal_agent` assignment logic is untouched. `search_many` = one batched encode +
vectorised scoring per slot (no per-slot model calls).

Reference implementation of the lazy, cached encoder (`retrieval/encoder.py`) —
fastembed is imported lazily so test collection and `gamito db init` never pay
the ONNX load cost:

```python
from __future__ import annotations
import functools
import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DIMS = 384


@functools.lru_cache(maxsize=1)
def _model(name: str = DEFAULT_MODEL):
    from fastembed import TextEmbedding  # lazy: heavy ONNX import

    return TextEmbedding(model_name=name)


def encode(texts: list[str], *, model: str = DEFAULT_MODEL) -> np.ndarray:
    """Encode and L2-normalise so cosine == dot product. Shape: (n, DIMS)."""
    vecs = np.asarray(list(_model(model).embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-12, None)
```

The query path of `LocalRecipeIndex` (`retrieval/index.py`) — filter first, then
score only the survivors so the dot product runs over the masked slice:

```python
def search_many(self, queries: list[str], ctx: UserContext, k: int = 10):
    candidates = apply_filters(self.metadata, ctx)        # §7.3 pandas masks
    if candidates.empty:
        candidates = relax(self.metadata, ctx)            # filter-relaxation ladder
        if candidates.empty:
            raise NoCandidates(emptying_constraints(ctx))
    rows = candidates.index.to_numpy()
    sub = self.embeddings[rows]                            # (m, DIMS) view
    q = encode(queries)                                    # (n, DIMS), one batch
    scores = q @ sub.T                                     # (n, m) exact cosine
    top = np.argpartition(-scores, range(min(k, scores.shape[1])), axis=1)[:, :k]
    return [candidates.iloc[idx] for idx in top]
```

The individual masks (`retrieval/filters.py`) are plain boolean Series so they
compose with `&` and stay trivially debuggable. Allergen/dietary/tool masks are
**hard** (never relaxed); cuisine is soft:

```python
import pandas as pd

def apply_filters(df: pd.DataFrame, ctx: UserContext) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if ctx.max_time_min is not None:
        mask &= df["total_time_min"] <= ctx.max_time_min
    if ctx.max_price_per_serving is not None:
        mask &= df["price_per_serving_eur"] <= ctx.max_price_per_serving
    for allergen in ctx.allergies:                          # HARD — never relaxed
        mask &= df[f"is_{allergen}_free"]
    if ctx.dietary_pref == "vegan":
        mask &= df["is_vegan"]
    elif ctx.dietary_pref == "vegetarian":
        mask &= df["is_vegetarian"]
    if ctx.owned_tools:                                     # tools ⊆ owned (HARD)
        owned = frozenset(ctx.owned_tools)
        mask &= df["kitchen_tools"].apply(lambda ts: set(ts) <= owned)
    if ctx.exclude_recipe_ids:
        mask &= ~df["recipe_id"].isin(ctx.exclude_recipe_ids)
    if ctx.preferred_cuisines:                              # SOFT — first to drop
        pref = frozenset(ctx.preferred_cuisines)
        mask &= df["cuisine_list"].apply(lambda cs: bool(set(cs) & pref))
    return df[mask]
```

**Filter-relaxation ladder** (replaces the Tavily fallback): if a slot's pool is
empty → drop cuisine preference → drop healthiness preference → +25 % time
ceiling → return `NO_CANDIDATES` with the constraints that emptied the pool
(never relax allergens, dietary flags, or tools).

### 7.4 Why brute force beats a vector DB here

10,667 × 384 float32 = **15.7 MB**. A full exact scan is < 10 ms on 2 vCPUs —
faster than any network hop, zero infrastructure, exact results, trivially
debuggable, and filter-then-score avoids ANN-with-filters complexity entirely.
Revisit only if the corpus grows ~50×.

### 7.5 Custom recipes — dynamic index merge

Household-added recipes live in SQLite (§8 `custom_recipes` +
`custom_recipe_embeddings`) and are merged into `LocalRecipeIndex` at search
time so they participate in the same filter+score path as the static dataset.

**Embedding policy (online, structured-only).** `add_recipe` builds the same
embedding text used for the static index (§7.2 step 2):
`title + cuisines + courses + tastes + tools + ingredients + directions[:500]`
from the structured params and encodes it with the same fastembed model. The
float32 vector is L2-normalised, packed as little-endian bytes, and stored as a
BLOB next to the row. Encode cost: ~10–30 ms — well inside the tool's budget.

**Manifest binding.** The static `manifest.json` carries `model` + `dims`.
`custom_recipe_embeddings` rows store the same `model` + `dims` per row. On
load, `LocalRecipeIndex` refuses to merge any custom row whose `(model, dims)`
disagrees with the manifest — same safety property the static loader has,
extended to user data. (If the model is ever upgraded, `gamito custom-recipes
re-embed` re-encodes everything in one pass.)

The vector is packed as little-endian float32 bytes for the BLOB column and
unpacked back into a row of the custom embedding matrix:

```python
import numpy as np

def pack_vector(vec: np.ndarray) -> bytes:
    return np.ascontiguousarray(vec, dtype="<f4").tobytes()

def unpack_vector(blob: bytes, dims: int) -> np.ndarray:
    vec = np.frombuffer(blob, dtype="<f4")
    if vec.shape != (dims,):
        raise ValueError(f"expected {dims} floats, got {vec.shape[0]}")
    return vec
```

The manifest cross-check that refuses stale or mismatched vectors (static loader
*and* per-custom-row) — this is the guard called out in §7.2 step 5 and the
`EMBEDDING_MODEL_MISMATCH` error of §9.6:

```python
def assert_compatible(manifest: dict, *, model: str, dims: int) -> None:
    if (model, dims) != (manifest["model"], manifest["dims"]):
        raise EmbeddingModelMismatch(
            expected=(manifest["model"], manifest["dims"]),
            got=(model, dims),
        )
```

**Merge & invalidation.** `LocalRecipeIndex` keeps two layers in memory:

1. The static layer (DataFrame + `embeddings_static`) loaded once at startup.
2. A custom layer (DataFrame + `embeddings_custom`) loaded from SQLite at
   startup and rebuilt **on demand** whenever the `custom_recipes_revision`
   counter (a single-row table bumped by every CRUD op) advances. At <~5,000
   custom recipes the rebuild is sub-50 ms — simpler than diff/patch logic.

The revision-watch is a cheap single-row read on each search; the in-memory
layer is only rebuilt when the counter actually moved:

```python
def _ensure_fresh(self, conn) -> None:
    rev = conn.execute("SELECT revision FROM custom_recipes_meta").fetchone()[0]
    if rev == self._custom_rev:
        return                                  # hot path: nothing changed
    rows = conn.execute("SELECT * FROM custom_recipes").fetchall()
    embeds = conn.execute(
        "SELECT recipe_id, model, dims, vector FROM custom_recipe_embeddings"
    ).fetchall()
    for r in embeds:
        assert_compatible(self.manifest, model=r["model"], dims=r["dims"])
    self.custom_meta = build_metadata_frame(rows)        # same shape as static
    self.embeddings_custom = np.vstack(
        [unpack_vector(r["vector"], self.manifest["dims"]) for r in embeds]
    ) if embeds else np.empty((0, self.manifest["dims"]), dtype=np.float32)
    self._custom_rev = rev
```

Searches concatenate both layers virtually (`np.concatenate` is lazy enough at
this scale; alternatively keep two separate `score → top-k` calls and merge),
apply the same pandas filter pipeline (§7.3), and return unified candidates.
Custom recipes carry a `source = 'custom'` column so the validator and shopping
nodes can flag them in `text` output ("⭐ Mama's lasagne — your recipe").

**Why not append to `metadata.parquet` + `embeddings.npy`?** Those files are
build artefacts (committed, immutable between `build_local_index.py` runs).
Mixing live writes into them wrecks reproducibility, makes git diffs ugly, and
collides with `manifest.json`'s dataset-hash check. SQLite is the live layer;
parquet/npy stay declarative.

**Allergen / dietary integrity.** Custom recipes pass the same hard filters
(allergens, dietary, tools) as static ones — but using **user-declared**
flags from `add_recipe`. The user is the source of truth for "contains nuts"
on their own recipe; Gamito does not infer. This is called out in §15 risks
and the SKILL.md guidance instructs Hermes to pass the structured flags
explicitly when parsing free text, and to surface the assumption back to the
user before saving.

---

### Phase G1 — Local Retrieval (3–4 days) — P0

- [ ] `scripts/build_local_index.py` (§7.2) — resumable, writes manifest
- [ ] `retrieval/encoder.py` (fastembed, lazy singleton),
      `retrieval/index.py`, `retrieval/filters.py` (§7.3 semantics)
- [ ] Filter-relaxation ladder + `NO_CANDIDATES` diagnostics
- [ ] **Golden-set eval** (`scripts/eval_retrieval.py`, §13.3) — the old
      Pinecone baseline is gone (Gemini dims, unqueryable locally), so the gate
      is absolute, not comparative: hard-filter integrity 100 %, every golden
      query returns ≥ 5 plausible candidates, manual precision@5 ≥ 0.6 recorded
      in the repo
- [ ] Unit tests: every filter operator, exclusion lists, empty pools, top-k
      ordering, manifest mismatch refusal

**Accept**: eval report committed; `search` p95 < 60 ms warm on 2 vCPU.