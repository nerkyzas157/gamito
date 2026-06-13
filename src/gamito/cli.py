"""Small CLI entry point for local development tasks."""

from __future__ import annotations

import argparse
import csv
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
        help="SQLite database path (default: project gamito.db or GAMITO_DB)",
    )
    custom_parser = subparsers.add_parser("custom-recipes", help="custom recipe batch commands")
    custom_subparsers = custom_parser.add_subparsers(dest="custom_command")
    custom_import = custom_subparsers.add_parser("import", help="import custom recipes from CSV")
    custom_import.add_argument("csv_path")
    custom_import.add_argument("--db", default=None, help="SQLite database path")
    custom_reembed = custom_subparsers.add_parser("re-embed", help="refresh custom recipe embeddings")
    custom_reembed.add_argument("--db", default=None, help="SQLite database path")
    custom_list = custom_subparsers.add_parser("list", help="list custom recipes")
    custom_list.add_argument("--db", default=None, help="SQLite database path")
    args = parser.parse_args()
    if args.version:
        from gamito import __version__

        print(__version__)
        return

    if args.command == "db" and args.db_command == "init":
        from gamito.db.connection import default_db_path, init_database

        db_path = Path(args.db) if args.db else default_db_path()
        version = init_database(db_path)
        print(f"Initialized {db_path} at schema version {version}.")
        return

    if args.command == "custom-recipes":
        from gamito.db.connection import connect, default_db_path, migrate
        from gamito.db.custom_recipes import add_recipe, list_custom_recipes, reembed_all

        db_path = Path(args.db) if args.db else default_db_path()
        conn = connect(db_path)
        try:
            migrate(conn)
            if args.custom_command == "import":
                count = 0
                with Path(args.csv_path).open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        add_recipe(
                            conn,
                            title=row["title"],
                            ingredients=_csv_ingredients(row),
                            directions=_csv_list(row.get("directions")),
                            cuisines=_csv_list(row.get("cuisines")),
                            courses=_csv_list(row.get("courses")),
                            tastes=_csv_list(row.get("tastes")),
                            tools=_csv_list(row.get("tools")),
                            servings=int(row.get("servings") or 2),
                            source="imported",
                        )
                        count += 1
                print(f"Imported {count} custom recipes.")
                return
            if args.custom_command == "re-embed":
                count = reembed_all(conn)
                print(f"Re-embedded {count} custom recipes.")
                return
            if args.custom_command == "list":
                for recipe in list_custom_recipes(conn):
                    print(f"{recipe['recipe_id']}\t{recipe['title']}")
                return
        finally:
            conn.close()

    parser.print_help()


def _csv_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def _csv_ingredients(row: dict[str, str]) -> list[dict[str, str | None]]:
    names = _csv_list(row.get("ingredient_names") or row.get("ingredients"))
    amounts = _csv_list(row.get("ingredient_amounts") or row.get("amounts"))
    units = _csv_list(row.get("ingredient_units") or row.get("units"))
    return [
        {
            "name": name,
            "amount": amounts[index] if index < len(amounts) else "1",
            "unit": units[index] if index < len(units) else None,
        }
        for index, name in enumerate(names)
    ]
