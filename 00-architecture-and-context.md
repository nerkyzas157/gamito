<!-- Context/Task file: 00-architecture-and-context.md -->

# Gamito Local — Greenfield Rebuild & Hermes Integration Plan
**Version 2.1 | A fresh, fully-local project exposing Gamito's engineering as MCP tools for Hermes Agent**

> **What this document is**: a complete, self-contained blueprint for a **new
> repository**. The current `gamito_mvp/` directory is treated as a *salvage
> source only* — valuable data files and battle-tested modules are carried over,
> everything cloud-bound is left behind. Build the new project from this document
> plus the salvage manifest in §4.
>
> **Locked decisions (2026-06-09)**:
> 1. **Greenfield** — new repo, git from day zero; `gamito_mvp/` is retired after
>    salvage (kept read-only until §12 Phase G3 tests pass, then archived).
> 2. **Fully local core** — zero external API calls in Gamito: no Pinecone, no
>    Google/Anthropic/Tavily/LangSmith, no Supabase. Local libraries, files, and
>    processes only.
> 3. **No cloud data matters** — the existing Pinecone index holds Gemini-3072-dim
>    vectors that cannot be queried without the Gemini API, so it is *useless* for
>    a local system. Supabase holds no real user data. **Nothing is migrated;
>    both services are simply abandoned/deleted.** The recipe dataset and lookup
>    parquets in the repo are the only data that matters.
> 4. **Host**: laptop or small VPS (~2 vCPU / 4 GB RAM) → ONNX-based embedding
>    (`fastembed`), no torch, everything sized for 4 GB.
> 5. **Hermes runs a cloud LLM** (user's choice/config) — strong tool-calling
>    assumed. "Fully local" applies to the Gamito core, not the agent driving it.
>
> **v2.1 additions (2026-06-13)** — locked decisions for two new feature tracks:
> 6. **Plan lifecycle**: every plan is already persisted; on top of that, plans
>    can be **labelled, favourited, listed, and regenerated-while-preserving**.
>    A new `regenerate_plan` tool consumes the previous plan + existing per-meal
>    ratings (or explicit overrides) to produce a new plan that keeps liked
>    slots verbatim and excludes disliked recipes. Per-meal `rate_meal` is the
>    single rating channel — no separate plan-level rating tool.
> 7. **Custom recipes**: a household-shared `custom_recipes` table with full
>    CRUD via `add_recipe` / `update_recipe` / `delete_recipe`. Recipes are
>    accepted as **structured params only** (Hermes parses free text/photo into
>    fields before calling) — no LLM enters the Gamito core. Embeddings are
>    computed online with the same fastembed encoder and merged into
>    `LocalRecipeIndex` at search time. Profile-scoping (per-person cookbooks
>    vs household) is deliberately deferred to future work — see §16.

---

## 1. System Overview

**Product**: a meal-planning *engine* for households, operated entirely through a
self-hosted [Hermes Agent](https://github.com/nousresearch/hermes-agent). Family
members talk to Hermes in Telegram/WhatsApp/CLI in any language; Hermes calls
Gamito's MCP tools; Gamito deterministically produces budget-disciplined,
allergy-safe meal plans with deduplicated shopping lists, learns from ratings,
and persists everything in one SQLite file.

**Division of labour** (the core thesis):

| Concern | Owner | Why |
|---|---|---|
| Understanding requests, interviews, chit-chat, translation | Hermes (cloud LLM) | Fuzzy language work — the agent's strength |
| Photo → ingredient labels (pantry) | Hermes vision | Multimodal lives agent-side |
| Allergy/diet/tool/time/price filtering | Gamito (metadata filters) | Must be deterministic, never "remembered" |
| Budget allocation, slot semantics, leftover routing | Gamito (rule-based planner) | Must be arithmetic, not vibes |
| Recipe retrieval | Gamito (local embeddings + exact search) | Curated 10,667-recipe dataset + household-added custom recipes (§7.5) |
| Validation, replanning, dedup, shopping list | Gamito (pure code) | Correctness guarantees |
| Plan lifecycle (label, favourite, regenerate-preserving) | Gamito (deterministic) | Same-input/same-seed reproducibility extends to "regenerate from plan X" |
| Free-text → structured recipe parse (`add_recipe` inputs) | Hermes (LLM) | Fuzzy parsing stays agent-side; Gamito only stores+canonicalises the structured result |
| Memory of soft preferences ("liked the curry") | Hermes memory → `update_preferences` tool | Soft context, agent-side; hard state lands in SQLite via tools |
| Scheduling ("every Sunday 18:00") | Hermes automations | Built-in natural-language cron |

Feasibility rests on a property the old project already proved: **every LLM node
in the planning graph has a deterministic fallback that passes the offline test
suite** (deterministic benchmark: 4.63 s for a 3-day × 3-meal plan *including*
Pinecone round-trips). The local rebuild keeps only the deterministic paths and
should land **< 2 s** per plan.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ HOST MACHINE (laptop or ~2 vCPU / 4 GB VPS)                            │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │ HERMES AGENT (self-hosted, cloud LLM via user's config)          │ │
│  │  Telegram / WhatsApp / Discord / CLI gateways                    │ │
│  │  Persistent memory · Vision · Scheduled automations              │ │
│  │  `gamito` SKILL.md  ← usage judgment (when/how to call tools)    │ │
│  └───────────────┬──────────────────────────────────────────────────┘ │
│                  │ MCP over stdio (no ports, no network)              │
│  ┌───────────────▼──────────────────────────────────────────────────┐ │
│  │ GAMITO MCP SERVER  (`gamito-mcp`, FastMCP)                       │ │
│  │                                                                  │ │
│  │  profiles:  list_profiles · get_profile · save_profile          │ │
│  │             update_preferences                                   │ │
│  │  planning:  generate_meal_plan · get_meal_plan · search_recipes │ │
│  │  lifecycle: label_plan · list_plans · regenerate_plan           │ │
│  │  edits:     swap_meal · rescale_meal                            │ │
│  │  recipes:   add_recipe · update_recipe · delete_recipe ·        │ │
│  │             list_custom_recipes                                  │ │
│  │  shopping:  get_shopping_list                                    │ │
│  │  pantry:    get_pantry · update_pantry                          │ │
│  │  feedback:  rate_meal                                            │ │
│  └────┬──────────────────────┬──────────────────────┬───────────────┘ │
│       ▼                      ▼                      ▼                 │
│  ┌───────────┐   ┌─────────────────────────┐   ┌──────────────┐       │
│  │ Planning  │   │ LocalRecipeIndex        │   │ SQLite       │       │
│  │ pipeline  │   │ embeddings.npy (384-d)  │   │ gamito.db    │       │
│  │ (LangGraph│   │ + custom_recipes BLOB   │   │ (WAL)        │       │
│  │ determin- │   │ vectors merged in       │   │ profiles,    │       │
│  │ istic     │   │ metadata.parquet ⊕ DB   │   │ plans (incl. │       │
│  │ nodes)    │   │ fastembed query encoder │   │ label/fav),  │       │
│  │           │   │ pandas filters +        │   │ tags,        │       │
│  │           │   │ numpy cosine top-k      │   │ ratings,     │       │
│  │           │   │                         │   │ pantry,      │       │
│  │           │   │                         │   │ custom_      │       │
│  │           │   │                         │   │ recipes      │       │
│  └───────────┘   └─────────────────────────┘   └──────────────┘       │
│                                                                        │
│  ZERO outbound network calls below the MCP line.                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Design Principles

### 3.1 The agent is the LLM
The Gamito core makes **zero** model calls. Language understanding happens in
Hermes *before* a tool call (English query composition, correction classification)
or is replaced by rules (budget allocation, tag weighting, survey→tags mapping).

### 3.2 Hard constraints never live in agent memory
Every planning/search tool takes `profile_id`; allergies, dietary flags, and
owned tools resolve from SQLite inside Gamito. Hermes memory holds only soft
context. A forgetful agent can produce a boring plan — never a dangerous one.

### 3.3 Deterministic core, fuzzy shell
Same input ⇒ same plan (modulo the random seed used for tie-breaking, which is
fixed and logged). All variation and personality come from the agent layer.

### 3.4 Every tool returns machine + human output
Structured JSON for the agent's reasoning **plus** a `text` field (compact,
chat-sized, markdown-light) it can forward verbatim to Telegram. Errors are
structured `{error_code, message, hint}` — hints written *for an LLM to act on*.

### 3.5 One file of state
`gamito.db` (SQLite, WAL). Backup = copy one file. No services, no migrations
infrastructure beyond a versioned `schema.sql` + tiny migration runner.

### 3.6 English data, any-language conversation
Recipe content stays English (the dataset's native language). Structured output
labels ship in EN + LT tables. Hermes converses and paraphrases in whatever
language the family member uses.

---

## 5. New Repository Layout

```
gamito/                                  # new repo (suggested name: gamito)
├── pyproject.toml                       # uv-managed; entry point: gamito-mcp
├── README.md
├── .gitignore                           # .venv, gamito.db, __pycache__, .env
├── .python-version                      # 3.12
│
├── data/
│   ├── recipes_dataset.csv              # salvaged (≈19 MB — committed)
│   ├── lookups/
│   │   ├── canonical_prices.parquet
│   │   └── parsed_name_to_canonical.parquet
│   ├── index/                           # generated by build_local_index.py
│   │   ├── embeddings.npy               # ≈16 MB (10,667 × 384 float32) — committed
│   │   ├── metadata.parquet             # ≈25 MB — committed
│   │   └── manifest.json                # model name, dims, build date, row count
│   └── provenance/                      # cold storage: pipeline intermediates
│
├── src/gamito/
│   ├── __init__.py
│   ├── config.py                        # paths, env (GAMITO_DB, GAMITO_INDEX_DIR), constants
│   │
│   ├── models/                          # ported pydantic
│   │   ├── meal.py                      # Meal, Ingredient, ShoppingList (+pantry_items), MealPlan
│   │   ├── profile.py                   # Profile, UserContext
│   │   ├── planning.py                  # BudgetPlan, SlotRequest, ValidationResult
│   │   └── pantry.py
│   │
│   ├── retrieval/
│   │   ├── encoder.py                   # fastembed wrapper (lazy, cached)
│   │   ├── index.py                     # LocalRecipeIndex: load, filter, top-k
│   │   ├── filters.py                   # UserContext/params → pandas masks
│   │   └── custom.py                    # SQLite custom_recipes → in-memory rows
│   │                                    #  + dynamic merge with static index
│   │
│   ├── planning/                        # ported LangGraph pipeline (deterministic)
│   │   ├── graph.py                     # build + run; replan loop
│   │   ├── state.py
│   │   └── nodes/
│   │       ├── budget.py                # allocator + leftover routing
│   │       ├── assignment.py            # batch retrieval + greedy distinct assignment
│   │       ├── validator.py             # budget / allergy / variety
│   │       └── shopping.py              # canonical dedup + pricing + pantry split
│   │
│   ├── pricing/                         # ported canonical lookup
│   ├── pantry/
│   │   └── canonicalize.py              # ported; slow-use whitelist
│   │
│   ├── recommendation/
│   │   ├── tags.py                      # rule-based survey→tags
│   │   ├── engine.py                    # build_user_context(profile_id)
│   │   └── updater.py                   # rating / preference deltas → weights
│   │
│   ├── rendering/
│   │   ├── labels.py                    # EN + LT label tables
│   │   ├── full.py                      # ported formatter (structured text)
│   │   └── compact.py                   # chat-sized renderer (< 3,500 chars for 3×3)
│   │
│   ├── db/
│   │   ├── schema.sql                   # §8 DDL, versioned
│   │   ├── connection.py                # sqlite3 + WAL + busy_timeout + migration runner
│   │   ├── profiles.py                  # profile/allergy/tool/cuisine/tag repos
│   │   ├── plans.py                     # plan (+label/fav/rating) + meals + ratings + edits
│   │   ├── pantry.py
│   │   └── custom_recipes.py            # CRUD for custom_recipes + embeddings BLOB
│   │
│   └── mcp/
│       ├── server.py                    # FastMCP app, stdio; tool registration
│       ├── errors.py                    # error codes + hint texts (§9.6)
│       └── tools/
│           ├── profiles.py  planning.py  lifecycle.py  edits.py
│           ├── recipes.py   pantry.py    feedback.py
│
├── scripts/
│   ├── build_local_index.py             # CSV → embeddings.npy + metadata.parquet
│   ├── eval_retrieval.py                # golden-set eval (§13.3)
│   ├── seed_demo.py                     # demo profile + example plan
│   └── benchmark_agent_vs_tools.py      # §12 G7 showcase benchmark
│
├── skills/gamito/SKILL.md               # Hermes skill (§11.2)
├── docs/
│   ├── hermes_setup.md
│   └── architecture.md
└── tests/                               # ported + new (§13)
```

---

## 6. Environment & Dependencies

Python 3.12, `uv`-managed. The **complete** runtime dependency list:

```toml
[project]
name = "gamito"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.2",                # FastMCP server, stdio transport
    "fastembed>=0.5",          # ONNX embeddings (BAAI/bge-small-en-v1.5)
    "numpy>=2.0",
    "pandas>=2.2",
    "pyarrow>=17.0",           # parquet I/O
    "pydantic>=2.7",
    "langgraph>=1.0",          # planning pipeline (see §10 decision)
]

[project.scripts]
gamito-mcp = "gamito.mcp.server:main"
gamito = "gamito.cli:main"     # db init / build-index / seed-demo / eval
```

Notably **absent**: `langchain-*` provider packages, `anthropic`, `google-genai`,
`pinecone`, `supabase`, `psycopg2`, `tavily`, `gradio`, `torch`. Dev-only extras:
test runner (stdlib `unittest`), `ruff`/`black` to taste.

First-boot model download: fastembed pulls `bge-small-en-v1.5` (~130 MB) into its
cache on first use — the only network event in the system's life, and it can be
pre-seeded for true airgap (`fastembed` supports local model dirs).

`.gitignore` from day zero: `.venv/`, `__pycache__/`, `gamito.db*`, `.env`. The
data files (CSV, lookups, built index ≈ 60 MB total) **are committed** — clone →
`uv sync` → `gamito db init` → run, no build steps, no secrets.

---

## 8. SQLite Schema (complete DDL)

`src/gamito/db/schema.sql` — applied by `gamito db init`; `schema_version` table
+ sequential migration files handle future changes.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE profiles (
  profile_id    TEXT PRIMARY KEY,                  -- uuid4 hex
  name          TEXT NOT NULL UNIQUE,              -- "Tomas", "Mama"
  language      TEXT NOT NULL DEFAULT 'en',        -- 'en' | 'lt'
  dietary_pref  TEXT,                              -- 'omnivore'|'vegetarian'|'vegan'|...
  skill_level   TEXT,                              -- 'beginner'|'intermediate'|'advanced'
  meal_prep_ok  INTEGER NOT NULL DEFAULT 1,
  leftovers_ok  INTEGER NOT NULL DEFAULT 1,
  max_time_min  INTEGER,
  created_at    TEXT NOT NULL,                     -- ISO-8601 UTC
  updated_at    TEXT NOT NULL
);

CREATE TABLE profile_allergies (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  allergen    TEXT NOT NULL,                       -- 'nuts'|'gluten'|'dairy'|...
  UNIQUE (profile_id, allergen)
);

CREATE TABLE profile_tools (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  tool        TEXT NOT NULL,                       -- canonical: 'oven','skillet/pan',...
  UNIQUE (profile_id, tool)
);

CREATE TABLE profile_cuisines (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  cuisine     TEXT NOT NULL,
  UNIQUE (profile_id, cuisine)
);

CREATE TABLE profile_tags (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  tag         TEXT NOT NULL,
  sentiment   TEXT NOT NULL CHECK (sentiment IN ('positive','negative')),
  weight      INTEGER NOT NULL DEFAULT 1,
  source      TEXT NOT NULL CHECK (source IN ('survey','rating','correction')),
  updated_at  TEXT NOT NULL,
  UNIQUE (profile_id, tag, sentiment)
);
CREATE INDEX idx_tags_profile ON profile_tags(profile_id);

CREATE TABLE meal_plans (
  plan_id          TEXT PRIMARY KEY,               -- uuid4 hex
  profile_id       TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  plan_type        TEXT NOT NULL CHECK (plan_type IN ('single','multi_day')),
  num_days         INTEGER NOT NULL,
  meals_per_day    INTEGER NOT NULL,
  total_budget_eur REAL NOT NULL,
  servings         INTEGER NOT NULL,
  max_time_min     INTEGER,
  status           TEXT NOT NULL DEFAULT 'complete'
                   CHECK (status IN ('complete','error')),
  total_cost_eur   REAL,
  warnings_json    TEXT,                           -- JSON array of strings
  -- v2.1 plan-lifecycle fields (label / favourite / regenerate provenance)
  label            TEXT,                           -- user-given short name; unique per profile when set
  is_favorite      INTEGER NOT NULL DEFAULT 0,     -- 0/1
  regenerated_from TEXT REFERENCES meal_plans(plan_id) ON DELETE SET NULL,
  seed             INTEGER,                        -- GAMITO_SEED used (recorded for reproducibility)
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX idx_plans_profile     ON meal_plans(profile_id, created_at);
CREATE INDEX idx_plans_favorites   ON meal_plans(profile_id, is_favorite) WHERE is_favorite = 1;
CREATE UNIQUE INDEX idx_plans_label ON meal_plans(profile_id, label) WHERE label IS NOT NULL;

CREATE TABLE plan_meals (
  meal_id           TEXT PRIMARY KEY,              -- uuid4 hex
  plan_id           TEXT NOT NULL REFERENCES meal_plans(plan_id) ON DELETE CASCADE,
  slot_key          TEXT NOT NULL,                 -- 'day_1:dinner' (shared constant)
  day_number        INTEGER NOT NULL,
  meal_slot         TEXT NOT NULL CHECK (meal_slot IN ('breakfast','lunch','dinner','snack')),
  recipe_id         TEXT,                          -- dataset row id
  recipe_title      TEXT NOT NULL,
  meal_type         TEXT NOT NULL CHECK (meal_type IN ('new','meal_prep','leftover')),
  source_slot_key   TEXT,                          -- leftovers point at their source
  total_time_min    INTEGER,
  difficulty        TEXT,
  cuisines_json     TEXT,                          -- JSON arrays / objects as TEXT
  dietary_json      TEXT,
  nutrition_json    TEXT,
  servings          INTEGER NOT NULL,
  cost_total_eur    REAL,
  cost_per_serving_eur REAL,
  ingredients_json  TEXT NOT NULL,
  directions_json   TEXT NOT NULL,
  tools_json        TEXT,
  created_at        TEXT NOT NULL,
  UNIQUE (plan_id, slot_key)
);

CREATE TABLE meal_ratings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  plan_id     TEXT NOT NULL,
  slot_key    TEXT NOT NULL,
  rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 10),
  created_at  TEXT NOT NULL,
  UNIQUE (profile_id, plan_id, slot_key)
);

CREATE TABLE plan_edits (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id      TEXT NOT NULL,
  slot_key     TEXT NOT NULL,
  edit_type    TEXT NOT NULL CHECK (edit_type IN ('swap','rescale')),
  payload_json TEXT NOT NULL,                      -- old/new diff for audit
  created_at   TEXT NOT NULL
);

CREATE TABLE pantry_items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id     TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  canonical_name TEXT NOT NULL,
  source         TEXT NOT NULL DEFAULT 'agent'
                 CHECK (source IN ('agent','manual')),
  confidence     REAL,
  last_seen_at   TEXT NOT NULL,
  UNIQUE (profile_id, canonical_name)
);
CREATE INDEX idx_pantry_profile ON pantry_items(profile_id);

-- ───────────────────────────────────────────────────────────────────────
-- v2.1 — household custom recipes (no profile_id; profile-scoping is
-- future work, see §16). Identical metadata shape to the static dataset
-- so the same filter pipeline (§7.3) works without forks.
-- ───────────────────────────────────────────────────────────────────────

CREATE TABLE custom_recipes (
  recipe_id           TEXT PRIMARY KEY,             -- 'custom_<uuid4hex>'
  title               TEXT NOT NULL,
  cuisines_json       TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
  courses_json        TEXT NOT NULL DEFAULT '[]',
  tastes_json         TEXT NOT NULL DEFAULT '[]',
  total_time_min      INTEGER,
  difficulty          TEXT,                         -- 'easy'|'medium'|'hard'
  servings            INTEGER NOT NULL DEFAULT 2,
  ingredients_json    TEXT NOT NULL,                -- [{name, amount, unit?, canonical?}]
  directions_json     TEXT NOT NULL,                -- [string, ...]
  tools_json          TEXT NOT NULL DEFAULT '[]',   -- canonical tool names
  dietary_json        TEXT NOT NULL DEFAULT '{}',   -- {is_vegan, is_vegetarian, is_gluten_free, is_dairy_free, ...}
  allergens_json      TEXT NOT NULL DEFAULT '[]',   -- ["nuts","dairy",...]  user-declared
  price_per_serving_eur REAL,                       -- nullable; computed via canonical pricing chain on save
  cost_total_eur      REAL,
  nutrition_json      TEXT,                         -- optional, user-declared
  notes               TEXT,                         -- free user notes
  source              TEXT NOT NULL DEFAULT 'user'
                      CHECK (source IN ('user','imported')),
  added_by_profile_id TEXT REFERENCES profiles(profile_id) ON DELETE SET NULL,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
CREATE INDEX idx_custom_recipes_title ON custom_recipes(title);

CREATE TABLE custom_recipe_embeddings (
  recipe_id    TEXT PRIMARY KEY REFERENCES custom_recipes(recipe_id) ON DELETE CASCADE,
  model        TEXT NOT NULL,                       -- e.g. 'BAAI/bge-small-en-v1.5'
  dims         INTEGER NOT NULL,                    -- 384
  vector       BLOB NOT NULL,                       -- float32 LE, dims floats
  embed_text   TEXT NOT NULL,                       -- exact text fed to encoder (debug/repro)
  encoded_at   TEXT NOT NULL
);

-- One-row counter bumped by every CRUD op on custom_recipes; LocalRecipeIndex
-- watches this to invalidate its in-memory custom layer (§7.5).
CREATE TABLE custom_recipes_meta (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  revision  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO custom_recipes_meta (id, revision) VALUES (1, 0);
```

Translation conventions vs. the old Postgres schema: `UUID → TEXT` (uuid4 hex),
`JSONB → TEXT` (JSON string), `TIMESTAMPTZ → TEXT` (ISO-8601 UTC),
`DECIMAL → REAL`. Connection policy: one connection per tool call,
`busy_timeout=5000`, WAL — family scale never stresses this.

---

## 13. Testing Strategy

1. **Unit** (every phase): ported deterministic suites + new filter/repo/tool
   tests. Target: the old repo's ~118-test discipline carries over.
2. **Integration**: profile → context → plan → shopping list → persistence on
   temp SQLite + real index files. Runs in the default suite (everything is
   local — no mocks needed for infra, which is a quiet upgrade over the old repo).
3. **Airgap guard**: `socket`-poisoning fixture on the integration subset; CI
   fails if any code path phones home.
4. **Golden-set retrieval eval** (G1 gate, then frozen as regression): ~12
   queries spanning cuisines, diets, allergies, tools, time, and price.
   Automatic assertions on hard-filter integrity + candidate counts; one-time
   manual precision@5 grading recorded in `docs/eval_baseline.md`. Note: no
   Pinecone comparison is possible or needed — the old index is unqueryable
   locally (Gemini embeddings) and holds nothing else of value.
5. **Determinism**: same inputs + same seed ⇒ byte-identical plan JSON. For
   `regenerate_plan`, same `(source_plan_id, keep, avoid, seed)` ⇒ byte-identical
   regenerated plan.
6. **Custom-recipe round-trip** (G5b): add → revision-bumped → search returns
   the recipe → plan can include it → delete-with-force orphans plan_meals
   without breaking historical plans (denormalised columns survive).
7. **Live E2E** (G6): the §11.3 script — the only test that touches a network,
   and only Hermes's side of the line.
8. **Benchmark** (G7): agent-freestyle vs agent+tools constraint scorecard.

---

## 14. Performance & Footprint Budget

| Item | Estimate | Notes |
|---|---|---|
| Repo clone (with data + index) | ≈ 60–70 MB | CSV 19 MB + lookups + index ~41 MB; no build step after clone |
| Encoder (bge-small via fastembed) | ~130 MB disk / ~150–250 MB RAM | ONNX; downloaded once to cache; pre-seedable for airgap |
| Index in RAM | ~45 MB (embeddings + metadata DataFrame) | loaded lazily, once per process |
| Custom-recipe layer in RAM | ≪ 1 MB per 1,000 custom recipes | 384 floats + small metadata; 5,000-recipe ceiling stays well under 5 MB |
| `add_recipe` (encode + canonicalise + insert) | 30–80 ms | dominated by fastembed encode + parquet lookup chain |
| Query encode + exact top-k | 15–40 ms | brute force; no ANN tuning ever; merging custom layer adds < 1 ms at typical sizes |
| Full 3×3 plan (pipeline + SQLite) | **< 2 s** | old deterministic benchmark was 4.63 s *with* network |
| `regenerate_plan` (best case, full preserve) | < 200 ms | no assignment; just budget recompute + validate + shopping rebuild + persist |
| `regenerate_plan` (worst case, nothing preserved) | < 2 s | identical cost to `generate_meal_plan` plus exclusion-set bookkeeping |
| `swap_meal` | < 500 ms | one search + validate + shopping rebuild |
| Idle footprint | ~0 (stdio server spawned by Hermes on demand) | |
| Recurring cost | **€0.00** | the only model spend is Hermes's own cloud LLM |

Fits the 4 GB host with room to spare for Hermes itself.

---

## 17. What Is Intentionally Left Behind

Explicit, so nothing creeps back in:

- ❌ **Gradio UI** — the agent (and any MCP host) is the interface; a thin
  read-only viewer is a stretch track, not MVP.
- ❌ **All cloud services** — Pinecone, Supabase, HF Spaces hosting, Tavily,
  LangSmith. Nothing on them is migrated because nothing on them has value:
  the Pinecone vectors are dimensionally tied to a paid API, and Supabase is
  empty of real users.
- ❌ **All LLM calls inside Gamito** — budget LLM, selector LLM, translator,
  tag-generator LLM, pantry vision. Replaced by rules, exact search, label
  tables, and agent-side intelligence respectively.
- ❌ **Auth** — profiles are trust-based (family instance behind Hermes's own
  user allowlist). Not a multi-tenant product.
- ❌ **The old repo itself** — after G7: archived, referenced never.

---

*Document version 2.1 (2026-06-13) — adds plan lifecycle (label / favourite /
regenerate-preserving) and household custom recipes (CRUD + dynamic index
merge) on top of v2.0's greenfield blueprint. Supersedes v2.0 (which
superseded v1.0). Companions:
`Gamito_MVP_Development_Plan.md` (architectural reference for ported internals),
`Pantry_Stock_Development_Plan.md` (receipt price-book concept),
`Gamito_Idea_Evaluation_and_V2_Plan.md` (idea evaluation; its Gradio-centric
roadmap is superseded by this greenfield plan).*