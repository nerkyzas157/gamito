# Data Directory

This directory contains the local assets needed by Gamito's deterministic
retrieval, pricing, and pantry layers. For deploys to small VPS instances, copy
this whole directory instead of rebuilding the retrieval index on the server.

## Current contents

- `recipes_dataset.csv` — 14,619 recipes, 36 columns, approximately 26 MB.
- `index/` — prebuilt retrieval index generated from `recipes_dataset.csv` with
  `BAAI/bge-small-en-v1.5`.
- `index/embeddings.npy` — 384-dimensional recipe embeddings.
- `index/metadata.parquet` — normalized recipe metadata used for hard filters.
- `index/manifest.json` — index provenance, including dataset hash, model name,
  embedding dimensions, and recipe count.
- `lookups/` — reserved for pricing and pantry lookup artifacts.
- `provenance/` — reserved for source/provenance notes for auxiliary artifacts.

## Deployment note

The index build is intentionally offline and deterministic, but it is
resource-heavy. Running `uv run python scripts/build_local_index.py` directly on
a 2 vCPU / 4 GB VPS can saturate memory and swap, especially if duplicate builds
are started. Prefer this workflow:

```bash
# On a workstation or another machine that already has data/index:
rsync -az --delete ./data/ hermes:/home/nerkyzas/projects/gamito/data/
```

After copying, verify that the deploy checkout has:

```bash
ls data/index
# embeddings.npy  manifest.json  metadata.parquet
```

## Dataset notes

- `recipes_dataset.csv` was copied from
  `/home/nerkyzas/Desktop/projects/gamito_mvp` during Phase G0.
- List-like source columns are not fully uniform: `cuisine_list`,
  `ingredients`, `directions`, and `kitchen_tools` are JSON arrays, while
  `course_list` is a Python literal list string in the current file.
- The index build normalizes fields such as `total_time` to `total_time_min`,
  `ingredients` to `ingredients_json`, and `directions` to `directions_json`
  before writing `metadata.parquet`.

## Auxiliary lookup status

Pricing and pantry tests use fixture lookups until those parquet artifacts are
restored or regenerated:

- `lookups/canonical_prices.parquet`
- `lookups/parsed_name_to_canonical.parquet`
- `lookups/canonical_ingredients.parquet`
- `lookups/parsed_ingredients.parquet`
- `lookups/usda_nutrients.parquet`

When `lookups/` is empty, planning still uses recipe-level prices from
`index/metadata.parquet` for assignment. Shopping totals fall back to the
selected recipes' estimated total costs when ingredient-level pricing would
undercount the plan, so small deploys can produce useful budget summaries with
only `data/index/` present.
