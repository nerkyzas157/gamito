"""Small CLI entry point for local development tasks."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    """Run the Gamito development CLI."""

    parser = argparse.ArgumentParser(prog="gamito")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit",
    )
    subparsers = parser.add_subparsers(dest="command")
    db_parser = subparsers.add_parser("db", help="database maintenance commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_init = db_subparsers.add_parser("init", help="initialise or migrate SQLite")
    db_init.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: ./gamito.db)",
    )
    args = parser.parse_args()
    if args.version:
        from gamito import __version__

        print(__version__)
        return

    if args.command == "db" and args.db_command == "init":
        from gamito.db.connection import DEFAULT_DB_PATH, init_database

        db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
        version = init_database(db_path)
        print(f"Initialized {db_path} at schema version {version}.")
        return

    parser.print_help()
