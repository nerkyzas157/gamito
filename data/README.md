# Data Salvage Notes

Copied from `/home/nerkyzas/Desktop/projects/gamito_mvp` during Phase G0:

- `recipes_dataset.csv`

Expected by the G0 salvage manifest but not present in the source tree searched:

- `lookups/canonical_prices.parquet`
- `lookups/parsed_name_to_canonical.parquet`
- `lookups/canonical_ingredients.parquet`
- `lookups/parsed_ingredients.parquet`
- `lookups/usda_nutrients.parquet`

Pricing and pantry tests use fixture lookups until those parquet artifacts are
restored or regenerated in a later phase.
