# Data Salvage Notes

Copied from `/home/nerkyzas/Desktop/projects/gamito_mvp` during Phase G0:

- `recipes_dataset.csv` — currently 14,619 recipes, 36 columns, ≈26 MB.

Dataset notes:

- List-like columns are not fully uniform: `cuisine_list`, `ingredients`,
  `directions`, and `kitchen_tools` are JSON arrays, while `course_list` is a
  Python literal list string in the current file.
- G1 should normalize `total_time` to `total_time_min`, `ingredients` to
  `ingredients_json`, and `directions` to `directions_json` when writing
  `metadata.parquet`.

Expected by the G0 salvage manifest but not present in the source tree searched:

- `lookups/canonical_prices.parquet`
- `lookups/parsed_name_to_canonical.parquet`
- `lookups/canonical_ingredients.parquet`
- `lookups/parsed_ingredients.parquet`
- `lookups/usda_nutrients.parquet`

Pricing and pantry tests use fixture lookups until those parquet artifacts are
restored or regenerated in a later phase.
