"""FastMCP stdio server entry point for Gamito tools."""

from __future__ import annotations

from gamito.mcp.app import mcp

# Importing the package registers every @tool wrapper on the shared FastMCP app.
import gamito.mcp.tools  # noqa: F401


def main() -> None:
    """Start the MCP server over stdio."""

    mcp.run(transport="stdio")
