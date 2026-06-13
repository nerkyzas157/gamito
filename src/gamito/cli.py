"""Small CLI entry point for local development tasks."""

from __future__ import annotations

import argparse


def main() -> None:
    """Run the Gamito development CLI."""

    parser = argparse.ArgumentParser(prog="gamito")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit",
    )
    args = parser.parse_args()
    if args.version:
        from gamito import __version__

        print(__version__)
        return

    parser.print_help()
