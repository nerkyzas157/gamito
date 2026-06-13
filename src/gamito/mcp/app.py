"""FastMCP app instance and common tool decorator."""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from gamito.mcp.errors import GamitoError, err

F = TypeVar("F", bound=Callable[..., Any])

mcp = FastMCP(
    "gamito",
    instructions=(
        "Local Gamito meal-planning tools. Every success response includes a "
        "chat-forwardable text field; every failure returns error_code, message, and hint."
    ),
)


def tool(fn: F) -> F:
    """Register a FastMCP tool with uniform structured error serialization."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except GamitoError as exc:
            return exc.to_dict()
        except ValidationError as exc:
            return err("INVALID_INPUT", _validation_message(exc)).to_dict()
        except sqlite3.IntegrityError as exc:
            return err("INVALID_INPUT", str(exc)).to_dict()

    return mcp.tool()(wrapper)  # type: ignore[return-value]


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(item) for item in first.get("loc", ()))
    message = first.get("msg") or str(exc)
    return f"{location}: {message}" if location else str(message)
