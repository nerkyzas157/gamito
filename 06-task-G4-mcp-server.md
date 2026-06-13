<!-- Context/Task file: 06-task-G4-mcp-server.md -->

# Task: Phase G4 - MCP Server

**Goal**: Expose the core logic as FastMCP tools.

**Required Context**: `00-architecture-and-context.md`, `01-mcp-api-spec.md`

## Reference snippets

**Shared `slot_key` constants** (`mcp/slots.py`) — one module imported by both
the pipeline and the tools so the format never drifts (the §9.6
`SLOT_NOT_FOUND` hint enumerates valid keys from here):

```python
MEAL_SLOTS = ("breakfast", "lunch", "dinner", "snack")

def slot_key(day: int, slot: str) -> str:
    if slot not in MEAL_SLOTS:
        raise ValueError(f"unknown meal slot: {slot!r}")
    return f"day_{day}:{slot}"                      # e.g. 'day_1:dinner'

def parse_slot_key(key: str) -> tuple[int, str]:
    day_part, slot = key.split(":")
    return int(day_part.removeprefix("day_")), slot
```

**Error contract** (`mcp/errors.py`) — codes carry LLM-actionable hint text
(§9.6). A `GamitoError` raised anywhere in the call is caught at the tool
boundary and serialised to the `{error_code, message, hint}` shape:

```python
class GamitoError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def to_dict(self) -> dict:
        return {"error_code": self.code, "message": self.message, "hint": self.hint}

HINTS = {
    "PROFILE_NOT_FOUND": "Call list_profiles; create one with save_profile after "
                         "interviewing the user.",
    "LABEL_TAKEN":       "Profile already has a plan with this label. Pick another "
                         "label or unset the existing one.",
    # ... one entry per row of the §9.6 table ...
}

def err(code: str, message: str, **fmt) -> GamitoError:
    return GamitoError(code, message, HINTS.get(code, "").format(**fmt))
```

**Thin FastMCP tool wrapper** (`mcp/tools/planning.py`) — tools are *thin*: open
a connection, call the core, translate exceptions, attach the human-readable
`text` (§3.4). A small decorator keeps every tool's error handling identical:

```python
import functools
from gamito.mcp.app import mcp           # FastMCP() instance
from gamito.mcp.errors import GamitoError

def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GamitoError as e:
            return e.to_dict()           # structured, hint-bearing error
    return mcp.tool()(wrapper)

@tool
def generate_meal_plan(profile_id: str, budget_eur: float, servings: int,
                       num_days: int, meals_per_day: int,
                       max_time_min: int | None = None,
                       exclude_recipe_ids: list[str] | None = None) -> dict:
    with connect(DB_PATH) as conn:
        result = run_pipeline(conn, profile_id, budget_eur, servings,
                              num_days, meals_per_day, max_time_min,
                              exclude_recipe_ids or [])
    result["text"] = render_compact(result, language=result["language"])
    return result
```

### Phase G4 — MCP Server (3–4 days) — P0

- [ ] FastMCP app, stdio, entry point `gamito-mcp`; baseline 13 tools as thin
      wrappers (profiles 4 · planning 3 · edits 2 · shopping/pantry 3 · feedback 1)
- [ ] Error contract (§9.6) with LLM-actionable hints; input validation
- [ ] `slot_key` constants shared between pipeline and tools (one module)
- [ ] `scripts/seed_demo.py` — demo profile + canned plan for instant testing
- [ ] In-process tool tests: happy paths, every error code, `text` snapshots
- [ ] Manual smoke via MCP inspector (`mcp dev`): schema review with "would an
      LLM misread this?" eyes

**Accept**: all baseline tools callable over stdio; tool tests cover every error code.