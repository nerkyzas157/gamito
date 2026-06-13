<!-- Context/Task file: 04-task-G2-persistence.md -->

# Task: Phase G2 - Persistence & Profiles

**Goal**: Implement SQLite persistence and profile management.

**Required Context**: `00-architecture-and-context.md`

## Reference snippets

**`db/connection.py`** — one connection per tool call, WAL + busy_timeout set on
every open, and an idempotent migration runner gated on `schema_version` (so
`gamito db init` can run twice safely, per the acceptance criterion):

```python
import sqlite3
from pathlib import Path

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    return conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] or 0


def migrate(conn: sqlite3.Connection, migrations_dir: Path) -> int:
    """Apply schema.sql then every NNN_*.sql with version > current. Idempotent."""
    applied = current_version(conn)
    files = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
    with conn:                                       # single transaction per run
        if applied == 0:
            conn.executescript((migrations_dir / "schema.sql").read_text())
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            applied = 1
        for f in files:
            version = int(f.name[:3])
            if version > applied:
                conn.executescript(f.read_text())
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                applied = version
    return applied
```

**Repo round-trip** (`db/profiles.py`) — note the parent insert + child fan-out
in one transaction; `ON DELETE CASCADE` (§8) makes deletes a single statement:

```python
import uuid, json
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def create_profile(conn, *, name, language="en", allergies=(), tools=(), cuisines=()):
    pid = uuid.uuid4().hex
    now = _now()
    with conn:
        conn.execute(
            "INSERT INTO profiles (profile_id, name, language, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, name, language, now, now),
        )
        conn.executemany(
            "INSERT INTO profile_allergies (profile_id, allergen) VALUES (?, ?)",
            [(pid, a) for a in allergies],
        )
        conn.executemany(
            "INSERT INTO profile_tools (profile_id, tool) VALUES (?, ?)",
            [(pid, t) for t in tools],
        )
        conn.executemany(
            "INSERT INTO profile_cuisines (profile_id, cuisine) VALUES (?, ?)",
            [(pid, c) for c in cuisines],
        )
    return pid
```

**`recommendation/tags.py`** — the rules that turn survey fields into
`profile_tags` rows, *no LLM* (§3.1). A flat table keeps it auditable and
table-test-friendly:

```python
DIETARY_TAGS = {
    "vegan":      [("vegan", "positive"), ("meat", "negative")],
    "vegetarian": [("vegetarian", "positive"), ("meat", "negative")],
    "omnivore":   [],
}

def tags_from_survey(*, dietary_pref, cuisines, dislikes) -> list[tuple[str, str]]:
    """Returns (tag, sentiment) rows; source='survey' is added by the repo."""
    rows = list(DIETARY_TAGS.get(dietary_pref, []))
    rows += [(c.lower(), "positive") for c in cuisines]   # loved cuisines → +tags
    rows += [(d.lower(), "negative") for d in dislikes]   # dislikes → −tags
    return rows
```

### Phase G2 — Persistence & Profiles (2–3 days) — P0

- [ ] `db/connection.py` (WAL, busy_timeout, migration runner) + `schema.sql` (§8)
- [ ] Repos: profiles (+allergies/tools/cuisines/tags), plans (+meals/ratings/
      edits), pantry
- [ ] `recommendation/tags.py` — rule table: survey fields → tag rows
- [ ] `recommendation/engine.py` — `build_user_context(profile_id)` feeding the
      pipeline's existing `UserContext` shape
- [ ] Tests against real temp-file SQLite: CRUD round-trips, cascade deletes,
      unique constraints, tag mapping table-driven cases

**Accept**: create profile → context object correct; `gamito db init` idempotent.