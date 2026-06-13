"""SQLite connection and migration helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from gamito.config import DB_PATH

DEFAULT_DB_PATH = DB_PATH
MIGRATIONS_DIR = Path(__file__).resolve().parent


def default_db_path() -> Path:
    """Resolve the default database path shared by CLI, MCP, and library calls."""

    return Path(os.environ.get("GAMITO_DB", str(DB_PATH)))


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open one SQLite connection configured for Gamito tool calls."""

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    """Return the latest applied schema version, or 0 for an empty DB."""

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    return conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] or 0


def migrate(
    conn: sqlite3.Connection,
    migrations_dir: str | Path = MIGRATIONS_DIR,
) -> int:
    """Apply schema.sql then every NNN_*.sql migration newer than the DB."""

    migrations_path = Path(migrations_dir)
    applied = current_version(conn)
    files = sorted(migrations_path.glob("[0-9][0-9][0-9]_*.sql"))
    with conn:
        if applied == 0:
            conn.executescript((migrations_path / "schema.sql").read_text())
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            applied = 1
        for migration in files:
            version = int(migration.name[:3])
            if version > applied:
                conn.executescript(migration.read_text())
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                applied = version
    return applied


def init_database(
    db_path: str | Path | None = None,
    migrations_dir: str | Path = MIGRATIONS_DIR,
) -> int:
    """Open a database, run migrations, and close the connection."""

    conn = connect(db_path or default_db_path())
    try:
        return migrate(conn, migrations_dir)
    finally:
        conn.close()
