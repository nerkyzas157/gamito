<!-- Context/Task file: 02-task-G0-bootstrap.md -->

# Task: Phase G0 - Bootstrap & Salvage

**Goal**: Setup the new repository and salvage data from the old one.

**Required Context**: `00-architecture-and-context.md`

## 4. Salvage Manifest (from `gamito_mvp/`)

Verbs: **COPY** (file as-is), **PORT** (code moved with import-path/storage
adaptations, tests carried), **REWRITE** (concept kept, implementation new),
**DROP** (left behind; git history of the old repo is the archive).

### 4.1 Data assets (the crown jewels)

| Asset | Action | Notes |
|---|---|---|
| `data/recipes_dataset.csv` (14,619 enriched recipes, 36 cols) | **COPY** | The single most valuable artifact. Source for the local index build. |
| `data/lookups/canonical_prices.parquet` | **COPY** | Powers shopping-list pricing + pantry slow-use categories. |
| `data/lookups/parsed_name_to_canonical.parquet` | **COPY** | Ingredient → canonical mapping for dedup + pantry. |
| `data/lookups/canonical_ingredients.parquet`, `parsed_ingredients.parquet`, `usda_nutrients.parquet` | COPY (cold storage `data/provenance/`) | Pipeline intermediates; only needed to re-run enrichment. |
| `data/recipes_extended*.csv`, `data/steal/*`, `data/recipes.csv` | **DROP** | Raw scrape stages; ~390 MB of dead weight. Keep in the old archive only. |
| Pinecone index `gamito-vector-db` | **ABANDON/DELETE** | Gemini-3072-dim vectors; unqueryable without the Gemini API → worthless locally. Delete from the dashboard at leisure. |
| Supabase project | **ABANDON/DELETE** | No real user data exists. |

### 4.2 Source modules

| Module (old repo) | Action | Notes |
|---|---|---|
| `src/models/meal.py`, `user.py`, `agent_io.py`, `pantry.py` | **PORT** | Pydantic models are storage-agnostic; trim Pinecone/web-source fields. |
| `src/agents/state.py`, `graph.py` (wiring, replan logic) | **PORT** | Keep LangGraph (see §10 decision); remove translator route + LLM node imports. |
| `src/agents/nodes/budget_planner.py` — deterministic allocator + leftover routing | **PORT** | The LLM adapter half is dropped; the deterministic half is the planner now. |
| `src/agents/nodes/meal_agent.py` — batching, greedy distinct assignment, leftover cloning | **PORT** | Strip Gemini selector + Tavily fallback; retrieval backend swapped to `LocalRecipeIndex`. |
| `src/agents/nodes/validator.py` | **PORT** | Drop the language-mismatch heuristic (no translator). Budget/allergy/variety logic intact. |
| `src/agents/nodes/shopping_list.py` + `src/pricing/` | **PORT** | Pure code + parquet lookups; already local. |
| `src/agents/nodes/formatter.py` | **PORT + EXTEND** | Add EN label tables next to existing LT; add compact chat renderer. |
| `src/agents/nodes/translator.py`, `src/prompts/*` | **DROP** | No LLM in core. |
| `src/rag/retriever.py` (Pinecone), `web_fallback.py` (Tavily) | **DROP** | Filter-semantics logic is *referenced* when writing the pandas filters (§7.3). |
| `src/pantry/canonicalize.py` | **PORT** | Pure Python + parquet; the heart of pantry locally. |
| `src/pantry/vision.py` | **DROP** | Vision moves agent-side (Hermes). |
| `src/pantry/repo.py`, `src/database/meal_plan_repo.py` | **REWRITE** | psycopg2 → SQLite layer (§8); logic/shape preserved. |
| `src/database/user_repo.py`, `tag_repo.py`, `client.py`, `src/recommendation/*`, `src/agents/correction_graph.py` | **REWRITE** (were stubs) | Built fresh: rule-based tags, context builder, rating updater. |
| `src/ui/*` (Gradio) | **DROP** | No UI in the new project's MVP (§17); MCP hosts are the interface. |
| `scripts/feature_engineering/*` | COPY (cold storage `scripts/provenance/`) | One-time pipeline, already run; archival + provenance. |
| `scripts/ingest_recipes.py` | **DROP** (reference only) | Its embedding-text builder is the template for `build_local_index.py`. |
| `scripts/init_db/create_sql_db.py` | **REWRITE** | → `schema.sql` + `gamito db init`. |

### 4.3 Tests

| Old test | Action |
|---|---|
| `test_agents.py`, `test_validator.py`, `test_formatter.py`, `test_pricing.py`, `test_pantry_canonicalize.py`, `test_pantry_shopping.py` | **PORT** (drop LLM-adapter and Pinecone-mocked cases; deterministic cases carry) |
| `test_translator.py`, `test_pantry_vision.py`, `test_online_agents.py`, `test_retrieval.py` (Pinecone) | **DROP** |
| `test_pantry_repo.py`, `test_meal_plan_repo.py` | **REWRITE** against real temp-file SQLite (better than the old mocks) |
| `test_latency_benchmark.py` | **PORT** (local backend; new < 2 s ceiling) |

---

## 12. Development Phases & Acceptance Criteria

> Each phase ends green (`uv run python -m unittest discover -s tests`) plus the
> listed acceptance criteria. Old repo stays read-only until G3 passes.

### Phase G0 — Bootstrap & Salvage (1–1.5 days) — P0

- [ ] New repo: `uv init`, Python 3.12, layout from §5, `.gitignore`, **first
      commit before any code**
- [ ] Copy data assets per §4.1 (`recipes_dataset.csv`, two lookup parquets;
      provenance files to cold storage)
- [ ] Port pydantic models + pure logic that has zero infra deps
      (`pricing/`, `pantry/canonicalize.py`, models) **with their tests**
- [ ] CI habit established: tests run on every commit (even just a local
      pre-commit hook or `make test`)

**Accept**: fresh clone → `uv sync` → ported tests pass; repo has ≥ 3 commits.