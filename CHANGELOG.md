# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-13

### Added

- Phase G1 local retrieval: resumable fastembed index builder, lazy encoder, pandas
  filters, relaxation diagnostics, golden eval report, and committed `data/index`
  artifacts.
- Commitizen release automation (`cz bump`) for semver and changelog management.

### Changed

- Replaced the salvaged recipe CSV with the refreshed 14,619-recipe / 36-column
  dataset and updated architecture/retrieval docs with the new size estimates.
- Documented the current dataset normalization expectations for G1
  (`total_time`, `ingredients`, and `directions` aliases).
- Made recipe metadata parsing accept both JSON arrays and Python literal list
  strings from the refreshed dataset, with regression coverage.

## [0.1.0] - 2026-06-13

### Added

- Bootstrap Gamito local repository with project scaffolding (`pyproject.toml`, `Makefile`, `.gitignore`).
- Salvaged local recipe data (`data/recipes_dataset.csv`) from the legacy Gamito MVP.
- Ported deterministic local core modules under `src/gamito` with no cloud dependencies.
- Architecture and task specification documents for the G0–G7 implementation phases.
- Local test suite via `scripts/test` and `make test`.

[Unreleased]: https://github.com/nerkyzas157/gamito/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/nerkyzas157/gamito/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nerkyzas157/gamito/releases/tag/v0.1.0
