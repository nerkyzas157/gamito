# Gamito Local

Gamito Local is a fully local, deterministic meal-planning engine exposed to AI
agents through the [Model Context Protocol](https://modelcontextprotocol.io)
(MCP). It turns a household's profile, budget, pantry, and taste history into
budget-disciplined, allergy-safe weekly meal plans, shopping lists, and meal
swaps — entirely on your own machine, with no cloud calls.

The system ships in two halves that are designed to work together:

- **The MCP server** (`gamito-mcp`) — 20 deterministic tools backed by a local
  recipe index and a local SQLite store. All math (budgeting, pricing,
  rescaling, hard allergy filters) is plain Python; nothing is left to a model.
- **The Agent Skill** (`skills/gamito/SKILL.md`) — the *judgment layer* that
  teaches an orchestrating assistant (e.g. Hermes) **when** to call which tool,
  in what order, and how to recover from errors. The server enforces safety; the
  skill supplies the workflow.

> **Design principle:** Language understanding happens in the agent. Gamito's
> tools never parse free-form intent — the agent translates a request into a
> structured tool call, and the server returns deterministic, chat-forwardable
> results. The SQLite store (not conversation memory) is the single source of
> truth for every safety constraint.

## Why MCP + Skill?

An MCP server alone exposes *tool schemas*, but a model still has to guess how to
sequence them safely. The paired skill closes that gap:

| Layer | Responsibility |
|---|---|
| MCP server | Deterministic execution, hard allergy/diet filters, pricing, persistence |
| Skill | Identity rules, onboarding interview, tool ordering, error recovery |

This separation means allergies and dietary restrictions are enforced as hard
filters in code — never as a polite request to the model — while the skill keeps
the conversation coherent across planning, edits, feedback, and the household
cookbook.

## Use Cases

What the MCP + skill pairing is built to do:

- **Plan a week of meals** — "Plan 5 dinners for 2 people on a 60 EUR budget."
  Generates a full plan, shopping list, and budget check in one call.
- **Stay allergy- and diet-safe** — allergies, dislikes, and dietary preferences
  are stored per profile and applied as hard filters to every plan and swap.
- **Respect a food budget** — every plan is cost-checked against the stated
  budget; the engine reports the minimum feasible budget when constraints are
  too tight.
- **Swap, rescale, and refine meals** — "Make Tuesday dinner cheaper / vegan /
  faster" or "scale Friday up to 4 servings" without rebuilding the plan.
- **Generate shopping lists with pantry awareness** — items are split into
  "need to buy" vs. "already have" using the household pantry.
- **Track the pantry from a photo** — the agent reads long-shelf-life staples
  off a shelf/fridge photo; the server canonicalises and stores them.
- **Learn household tastes** — numeric ratings and soft feedback ("less spicy
  next time") bias future plans toward liked recipes and away from disliked ones.
- **Improve a previous plan** — regenerate from a source plan, automatically
  keeping highly-rated slots and avoiding poorly-rated ones.
- **Keep a household cookbook** — save "mama's recipe" from text or a photo as a
  custom recipe that competes for slots alongside the base dataset.
- **Save and favourite plans** — label plans ("Cheap weeknights"), mark
  favourites, and list them later.
- **Automate recurring plans** — e.g. "Every Sunday 18:00, generate next week's
  plan and send the text to the family group."

The 20 tools span six namespaces: **Profiles**, **Planning**, **Plan
Lifecycle**, **Edits**, **Shopping & Pantry**, **Feedback**, and **Recipes**.
See [`skills/gamito/references/tool-index.md`](skills/gamito/references/tool-index.md)
for the full signatures and error contract.

## Architecture

```
Household / Hermes agent
        │  natural language
        ▼
  skills/gamito/SKILL.md      ← judgment: when/which tool, ordering, recovery
        │  structured tool calls (gamito:<tool>)
        ▼
  gamito-mcp (FastMCP, stdio) ← 20 deterministic tools
        │
        ├── retrieval/   fastembed + brute-force vector index (data/index/)
        ├── planning/    LangGraph pipeline: assign → budget → shopping → render
        ├── pricing/     canonical ingredient pricing
        ├── pantry/      canonicalisation
        └── db/          SQLite store (profiles, plans, ratings, pantry, recipes)
```

## Installation

Requirements: **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Clone
git clone https://github.com/nerkyzas157/gamito.git
cd gamito

# 2. Install dependencies into a local virtualenv
uv sync

# 3. Initialise the SQLite store (profiles, plans, pantry, recipes)
uv run gamito db init

# 4. Ensure data/index exists before serving requests.
# Prefer copying a prebuilt data/ directory from a workstation:
rsync -az --delete /path/to/prebuilt/gamito/data/ ./data/
```

The index build encodes the bundled `data/recipes_dataset.csv` with the
`BAAI/bge-small-en-v1.5` model into `data/index/` (embeddings, metadata, and a
manifest). It is resumable — re-run the command to continue an interrupted
build — but it is CPU/RAM intensive enough to overwhelm a small VPS. For
low-memory hosts, build once on a stronger machine and copy the whole `data/`
folder to the deploy checkout instead of running the builder in production.

If you do need to rebuild locally:

```bash
uv run python scripts/build_local_index.py
```

### Optional: seed a demo profile and plan

```bash
uv run python scripts/seed_demo.py
```

### Configuration

Paths are overridable via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GAMITO_DATA_DIR` | `./data` | Dataset and index root |
| `GAMITO_INDEX_DIR` | `./data/index` | Prebuilt retrieval index |
| `GAMITO_DB` | `./gamito.db` | SQLite store path |

## Running the MCP server

The server speaks MCP over stdio:

```bash
uv run gamito-mcp
```

To register it with an MCP client (e.g. Claude Desktop, Cursor, or Hermes), add
an entry like:

```json
{
  "mcpServers": {
    "gamito": {
      "command": "uv",
      "args": ["run", "gamito-mcp"],
      "cwd": "/absolute/path/to/gamito"
    }
  }
}
```

Then make the skill available to the orchestrating agent by pointing it at
`skills/gamito/SKILL.md`. The agent calls tools by their fully-qualified name,
e.g. `gamito:generate_meal_plan`.

## Data origins

The bundled recipe corpus is derived from the public Kaggle dataset:

- **Extended Recipes Dataset — 64k Dishes**, by Wafaa El Husseini —
  <https://www.kaggle.com/datasets/wafaaelhusseini/extended-recipes-dataset-64k-dishes>

`data/recipes_dataset.csv` is a salvaged, normalised subset of that source
(currently **14,619 recipes across 36 columns**). At index-build time the
retrieval pipeline normalises fields (e.g. `total_time` → `total_time_min`,
list-like columns → JSON) before embedding. The committed index manifest records
the source dataset's SHA-256 for reproducibility.

Please refer to the original Kaggle dataset page for its license and terms of
use. Pricing and pantry canonicalisation rely on local lookup tables; see
[`data/README.md`](data/README.md) for the provenance and current status of
those auxiliary assets.

## Repository layout

```
src/gamito/
  retrieval/    fastembed encoder + brute-force vector index + hard filters
  planning/     LangGraph plan pipeline and nodes
  pricing/      canonical ingredient pricing
  pantry/       ingredient canonicalisation
  db/           SQLite schema, connection, and data access
  models/       Pydantic models (profile, pantry, planning, meal)
  rendering/    chat-forwardable text rendering (compact / full / labels)
  mcp/          FastMCP app, server entry point, and the 20 tools
  cli.py        dev CLI (db init, custom-recipe import/list/re-embed)
skills/gamito/  Agent Skill (SKILL.md) + tool-index reference
scripts/        index build, retrieval eval, demo seed, test runner
data/           recipe dataset, prebuilt index, lookups
docs/           retrieval eval baseline
tests/          local unittest suite
```

## Development

Run the local test suite:

```bash
scripts/test          # or: make test
uv run python -m unittest discover -s tests
```

Evaluate retrieval quality against the golden set:

```bash
uv run python scripts/eval_retrieval.py
```

The current retrieval baseline (12-query golden set) is recorded in
[`docs/eval_baseline.md`](docs/eval_baseline.md): 100% hard-filter integrity,
~17 ms warm p95 latency per query, and a manual precision@5 floor of 0.60.

## Versioning

This project follows [Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/); releases are managed with
[Commitizen](https://commitizen-tools.github.io/commitizen/). See
[`CHANGELOG.md`](CHANGELOG.md) for the release history.
