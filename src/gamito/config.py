"""Project paths and environment-backed defaults."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("GAMITO_DATA_DIR", PROJECT_ROOT / "data"))
INDEX_DIR = Path(os.environ.get("GAMITO_INDEX_DIR", DATA_DIR / "index"))
DB_PATH = Path(os.environ.get("GAMITO_DB", PROJECT_ROOT / "gamito.db"))
