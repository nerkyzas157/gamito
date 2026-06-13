<!-- Context/Task file: 09-task-G7-benchmark.md -->

# Task: Phase G7 - Benchmark, Hardening, Docs

**Goal**: Finalize the project, run benchmarks, and complete documentation.

**Required Context**: `00-architecture-and-context.md`

### Phase G7 — Benchmark, Hardening, Docs (2–3 days) — P1

- [ ] `scripts/benchmark_agent_vs_tools.py`: N = 20 scripted requests run
      (a) Hermes freestyle vs (b) Hermes + Gamito tools, scored by the validator:
      allergy violations, budget overrun %, duplicate meals, constraint misses.
      **This is the portfolio's centrepiece number.**
- [ ] No-network guard wired into the default test run
- [ ] Concurrency sanity (parallel tool calls vs WAL SQLite)
- [ ] `docs/hermes_setup.md`, `docs/architecture.md`, README (pitch → diagram →
      benchmark table → 2-minute quickstart)
- [ ] Old repo: final salvage sweep → archive (zip or cold branch) → delete
      Pinecone index + Supabase project from dashboards (nothing on them matters)

**Accept**: README quickstart works from a fresh clone on a clean machine.

### Phase summary

| Phase | Focus | Duration | Priority |
|---|---|---|---|
| G0 | Bootstrap, salvage, models + pure logic | 1–1.5 days | P0 |
| G1 | Local retrieval + golden-set eval | 3–4 days | P0 |
| G2 | SQLite + profiles + rule tags | 2–3 days | P0 |
| G3 | Planning core, airgap proof | 2–3 days | P0 |
| G4 | MCP server (baseline 13 tools) | 3–4 days | P0 |
| G5 | Corrections & learning loop | 2–3 days | P1 |
| G5b | Plan lifecycle + custom recipes (v2.1) | 3–4 days | P1 |
| G6 | Hermes config + skill + live E2E | 1–2 days | P0 |
| G7 | Benchmark + docs + old-repo retirement | 2–3 days | P1 |
| **Total** | | **~3.5–5 weeks** | |

---

## 15. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| 384-d local embeddings retrieve worse than the old 3072-d Gemini setup | Medium | G1 golden-set gate with recorded manual grading; `bge-base` upgrade = one script re-run; hard filters (the safety layer) are embedding-independent |
| No baseline to compare against (Pinecone unqueryable) | Certain | Accepted: absolute eval criteria instead of relative (§13.4); the old notebook-03 results remain a qualitative reference |
| Porting drift: subtle behaviour changes vs old nodes | Medium | Tests travel *with* the code in G0/G3; determinism seed makes diffs reproducible |
| Cloud-LLM Hermes mis-calls tools | Low-Med | Flat schemas, ≤ 12 tools, hint-bearing errors, skill examples; G7 benchmark quantifies residual misses |
| Laptop asleep when the Sunday automation fires | Medium | Hermes queues on wake; graduate to the small VPS once the workflow proves itself — Gamito is identical on either |
| SQLite contention from parallel subagents | Low | WAL + busy_timeout + per-call connections; family ≈ single user |
| Hermes config/API churn (young project) | Medium | Integration surface = one config block + one skill file; MCP is an open standard usable from other hosts (Claude, Cursor) for demos regardless |
| Scope creep: rebuilding a UI early | Medium | §17 — no UI in MVP; MCP hosts are the interface; revisit post-G7 |
| food.com data provenance in a public portfolio repo | Low | Personal/portfolio use; attribute the source in the README; no `steal` naming anywhere in the new repo |
| User-declared allergens on custom recipes are wrong (the user forgets "contains nuts") | Medium | SKILL.md instructs Hermes to confirm declared allergens + dietary flags with the user before saving; `add_recipe` warnings include unmatched canonical names so the agent can probe (e.g. "I couldn't canonicalise 'almond meal' — is this nut-derived?"); historical plan_meals always retain ingredients_json so audits remain possible after a recipe is deleted |
| `regenerate_plan` produces plans subjectively too similar to the source (preservation set too sticky) | Medium | Auto-mode is conservative (rating ≥ 8 to preserve, ≤ 4 to avoid); the user can always pass explicit `keep_slot_keys=[]` to fall back to a full regeneration with only the avoid-set as constraint; G5b acceptance test #2 enforces that an unrated plan with an avoid-only seed produces a visibly different plan |
| Custom recipe layer drift after model upgrade | Low | Per-row `(model, dims)` check refuses mismatched vectors; `gamito custom-recipes re-embed` is the single supported migration path; bge-small is the long-term default and an upgrade is a deliberate decision |

---

## 16. Post-MVP / Stretch Tracks

| Track | Sketch | Effort |
|---|---|---|
| **Receipt price book** | `log_receipt(profile_id, lines[])` — Hermes OCRs the receipt photo (vision), Gamito canonicalises + stores real EUR prices in a `price_observations` table; median-of-observations overrides parquet estimates in shopping lists. Makes the budget promise *real*. (Concept detailed in `Pantry_Stock_Development_Plan.md` §3–4, transplanted to tools.) | ~4–5 days |
| **Thin web UI** | Read-only plan/shopping-list viewer over the same SQLite (FastAPI + HTMX or similar); useful for the portfolio's 2-minute reviewer | ~3–4 days |
| **Family recipe pack (bulk import)** | 50–100 personal/Lithuanian recipes via the `gamito custom-recipes import` CLI (a thin batch wrapper over `add_recipe`, shipped in G5b). Once the interactive `add_recipe` tool exists, this collapses to: prepare a YAML/CSV of structured fields → run the import → done. No separate enrichment pipeline needed; static dataset stays untouched. | ~1 day |
| **Per-profile recipe scoping** | Add `owned_by_profile_id` (or many-to-many) + `is_household` flag to `custom_recipes`; teach `LocalRecipeIndex` to honour the active profile when filtering custom rows. Deferred from v2.1 because there are no profiles in the user's current setup yet — revisit once profile usage is real. | ~1–2 days |
| **Pantry staleness nudges** | `last_seen_at` decay surfaced via `get_pantry` text + a scheduled Hermes check-in | ~1 day |
| **Multi-household** | Namespacing by household_id if friends abroad want their own instance data — or simpler: they run their own container | design first |
| **Nutrition goals** | per-profile daily kcal/protein targets as soft filters + plan summary lines | ~2–3 days |

---