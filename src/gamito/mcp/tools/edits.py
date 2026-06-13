"""Plan edit MCP tools."""

from __future__ import annotations

from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db
from gamito.planning import edits as edit_core
from gamito.retrieval.index import EmbeddingModelMismatch, NoCandidates


@tool
def swap_meal(
    plan_id: str,
    slot_key: str,
    query_en: str,
    max_price_eur: float | None = None,
) -> dict:
    """Swap a plan slot to the best local recipe for an English query."""

    with open_db() as conn:
        try:
            return edit_core.swap_meal(
                conn,
                plan_id=plan_id,
                slot_key=slot_key,
                query_en=query_en,
                max_price_eur=max_price_eur,
            )
        except NoCandidates as exc:
            raise err(
                "NO_CANDIDATES",
                str(exc),
                constraints=", ".join(exc.constraints) or "unknown",
            ) from exc
        except EmbeddingModelMismatch as exc:
            raise err(
                "EMBEDDING_MODEL_MISMATCH",
                str(exc),
                got_model=exc.got[0],
                expected_model=exc.expected[0],
            ) from exc


@tool
def rescale_meal(plan_id: str, slot_key: str, servings: int) -> dict:
    """Rescale a persisted meal slot to a new serving count."""

    with open_db() as conn:
        return edit_core.rescale_meal(
            conn,
            plan_id=plan_id,
            slot_key=slot_key,
            servings=servings,
        )
