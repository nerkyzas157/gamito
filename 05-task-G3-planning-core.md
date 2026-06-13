<!-- Context/Task file: 05-task-G3-planning-core.md -->

# Task: Phase G3 - Planning Core

**Goal**: Port the LangGraph planning pipeline and wire it to SQLite & Retrieval.

**Required Context**: `00-architecture-and-context.md`

## 10. Planning Pipeline in the New Project

**Decision: keep LangGraph.** Rationale: the ported nodes/state/replan loop are
already LangGraph-shaped (porting = moving files, not redesigning); the replan
loop with conditional edges is its natural idiom; and "LangGraph pipeline with
deterministic guarantees behind MCP" is the portfolio sentence. Cost: one
moderate dependency. (A pure-Python orchestrator would also work — revisit only
if the dependency ever becomes a problem.)

The graph, simplified from the old project:

```
[START]
   ▼
[budget]        deterministic allocator; leftover routing when profile allows
   ▼               (day-N+1 lunch ← day-N dinner, budget folded into source)
[assignment]    batched: encode all slot queries (one fastembed call)
   │            → LocalRecipeIndex.search_many → greedy global assignment,
   │            DISTINCT recipe_ids for 'new' slots; leftovers clone source;
   │            filter-relaxation ladder on empty pools
   ▼
[validator]     budget ≤ 115% · allergy · unintentional-repeat variety
   ├─ PASS ──► [shopping]   canonical dedup, qty aggregation, pantry split,
   │              ▼          parquet pricing chain
   │          [render]      structured text (EN/LT labels) + compact chat text
   │              ▼
   │            [END]
   └─ FAIL ──► [prepare_replan]  excluded_recipe_ids ∪ placed 'new' ids;
                   │             leftover slots of replanned sources re-queued
                   └──► [assignment]   (max 2 retries)
```

Removed vs. old graph: `translator` node (and its routing), Tavily fallback,
all LLM adapters, the language-mismatch heuristic. The graph is invoked
in-process by `generate_meal_plan`; `swap_meal` reuses `validator` + `shopping`
directly without re-running the whole graph.

`regenerate_plan` (§9.2) reuses the same graph with two surgical injections
into the initial state: `preserved_slots` (already-assigned slots whose recipe,
servings, and cost are folded into `[budget]`'s baseline so the allocator only
distributes the remaining EUR over the remaining slots) and `excluded_recipe_ids`
(merged with the standard replan-exclusion set in `[assignment]`). The
`[validator]` and `[shopping]` nodes always operate over the merged plan, so
correctness invariants extend to the preserved slots without special-casing.

Determinism: tie-breaks seeded (`GAMITO_SEED`, default fixed); same inputs ⇒
same plan; the seed is recorded on the plan row. For `regenerate_plan`, the
*source* plan's recorded seed is incremented by 1 by default (so "regenerate
again" yields a different result without changing the global seed) — the new
plan stores its own `seed` and `regenerated_from = <source plan_id>` for
auditability.

The only entropy in the system is the tie-break RNG. It is constructed *per
plan* from the resolved seed and threaded into the assignment node, so the same
`(inputs, seed)` always breaks ties identically:

```python
import os, numpy as np

def resolve_seed(override: int | None = None) -> int:
    if override is not None:
        return override
    return int(os.environ.get("GAMITO_SEED", "1337"))

def tie_break_rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)            # all "random" choices go here

# assignment node: deterministic shuffle of equal-score candidates
def pick(equal_score_rows, rng: np.random.Generator):
    order = rng.permutation(len(equal_score_rows))
    return equal_score_rows[order[0]]
```

The deterministic budget allocator (`planning/nodes/budget.py`) — pure
arithmetic, no model call: split the budget across slots by weight, then fold a
leftover slot's cost into its source day so day-N+1 lunch costs €0:

```python
def allocate(total_eur: float, slots: list[Slot], leftovers_ok: bool) -> dict[str, float]:
    new_slots = [s for s in slots if s.meal_type != "leftover" or not leftovers_ok]
    weight_sum = sum(SLOT_WEIGHT[s.meal_slot] for s in new_slots)
    per_slot = {
        s.slot_key: total_eur * SLOT_WEIGHT[s.meal_slot] / weight_sum
        for s in new_slots
    }
    for s in slots:                               # leftovers ride on their source
        if s.slot_key not in per_slot:
            per_slot[s.slot_key] = 0.0
    return per_slot
```

**No-network proof test** — the `socket`-poison fixture that proves the core is
airgapped (G3 acceptance + the CI guard in §13.3). Any attempt to open a socket
raises, so a stray network call fails the suite instead of silently phoning home:

```python
import socket, unittest

class NoNetwork(unittest.TestCase):
    def setUp(self):
        self._real = socket.socket
        def _blocked(*a, **k):
            raise RuntimeError("network call attempted in airgapped core")
        socket.socket = _blocked
        self.addCleanup(lambda: setattr(socket, "socket", self._real))

    def test_full_plan_runs_airgapped(self):
        pid = create_demo_profile(self.conn)      # local SQLite temp file
        plan = generate_meal_plan(pid, budget_eur=50, servings=2,
                                  num_days=3, meals_per_day=3)
        self.assertEqual(plan["status"], "complete")
```

---

### Phase G3 — Planning Core (2–3 days) — P0

- [ ] Port graph + nodes per §10 (budget, assignment, validator, shopping)
      wired to `LocalRecipeIndex` + SQLite persistence
- [ ] Rendering: ported full formatter + EN label tables + `compact.py`
- [ ] Seeded determinism (`GAMITO_SEED`)
- [ ] **No-network proof test**: monkeypatch `socket.socket` to raise → full
      profile → plan → shopping list run passes airgapped
- [ ] Ported planning tests green (leftover routing, distinct-recipe
      enforcement, replan exclusion, budget tolerance, variety collapse)

**Accept**: 3-day × 3-meal plan < 2 s on the laptop; old repo can be archived.