# Gamito Local

Gamito Local is a greenfield rebuild of the old Gamito MVP as a fully local
meal-planning engine. The core is deterministic Python code and local files; any
language understanding is expected to happen in the Hermes agent before it calls
Gamito's MCP tools.

## Bootstrap Status

Phase G0 starts by salvaging the high-value local artifacts from the old
`gamito_mvp` project:

- `data/recipes_dataset.csv` has been copied and currently contains 14,619
  recipes across 36 columns.
- `data/lookups/canonical_prices.parquet` and
  `data/lookups/parsed_name_to_canonical.parquet` are expected lookup assets for
  production pricing/canonicalisation, but were not present in the searched
  source tree. See `data/README.md`.
- Pure code with no cloud dependencies is ported under `src/gamito`.

Run the local test suite with:

```bash
scripts/test
```

`make test` is also available on systems with `make` installed.
