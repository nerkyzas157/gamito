"""Shopping-list and pantry MCP tools."""

from __future__ import annotations

from datetime import datetime, timezone

from gamito.db import pantry as pantry_repo
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db, require_profile, shopping_payload
from gamito.pantry.canonicalize import is_slow_use, resolve_to_canonical
from gamito.planning.nodes.shopping import build_shopping_list
from gamito.recommendation.engine import build_user_context
from gamito.rendering.labels import labels_for
from gamito.db.plans import load_meal_plan


@tool
def get_shopping_list(plan_id: str, use_pantry: bool = True) -> dict:
    """Return a rebuilt shopping list for a persisted plan."""

    with open_db() as conn:
        plan = load_meal_plan(conn, plan_id)
        if plan is None:
            raise err("PLAN_NOT_FOUND", f"plan not found: {plan_id}")
        profile_id = conn.execute(
            "SELECT profile_id FROM meal_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()["profile_id"]
        ctx = build_user_context(profile_id, conn=conn)
        shopping = build_shopping_list(
            plan.meals,
            pantry_canonicals=ctx.pantry_canonicals if use_pantry else [],
        )
    payload = shopping_payload(shopping)
    payload["text"] = _shopping_text(payload)
    return payload


@tool
def get_pantry(profile_id: str) -> dict:
    """Return canonical pantry staples for a profile."""

    with open_db() as conn:
        require_profile(conn, profile_id)
        items = pantry_repo.list_pantry(conn, profile_id)
    return {"items": items, "text": _pantry_text(items)}


@tool
def update_pantry(
    profile_id: str,
    add_items: list[str] | None = None,
    remove_items: list[str] | None = None,
) -> dict:
    """Canonicalise and update slow-use pantry staples."""

    added: list[dict] = []
    rejected: list[dict] = []
    with open_db() as conn:
        require_profile(conn, profile_id)
        for label in add_items or []:
            canonical = resolve_to_canonical(label)
            if canonical is None:
                rejected.append({"label": label, "reason": "unrecognised"})
                continue
            if not is_slow_use(canonical):
                rejected.append({"label": label, "reason": "perishable"})
                continue
            pantry_repo.upsert_pantry_item(
                conn,
                profile_id=profile_id,
                canonical_name=canonical,
            )
            added.append({"label": label, "canonical": canonical})

        removed = []
        missing = []
        for label in remove_items or []:
            canonical = resolve_to_canonical(label) or str(label).strip().lower()
            if not canonical:
                continue
            count = pantry_repo.remove_pantry_items(
                conn,
                profile_id=profile_id,
                canonical_names=[canonical],
            )
            if count:
                removed.append({"label": label, "canonical": canonical})
            else:
                missing.append({"label": label, "reason": "missing"})

        rejected.extend(missing)
        pantry_size = len(pantry_repo.list_pantry(conn, profile_id))

    text = f"Pantry updated: +{len(added)}, -{len(removed)}, {pantry_size} total."
    if rejected:
        text += " Rejected: " + ", ".join(item["label"] for item in rejected[:5])
    return {
        "added": added,
        "removed": removed,
        "rejected": rejected,
        "pantry_size": pantry_size,
        "text": text,
    }


def _shopping_text(payload: dict) -> str:
    count = len(payload["items"])
    total = payload["total_eur"]
    preview = ", ".join(item["canonical"] for item in payload["items"][:8])
    if not preview:
        preview = "No shopping items."
    return f"Shopping list: {count} items, {total:.2f} EUR. {preview}"


def _pantry_text(items: list[dict]) -> str:
    if not items:
        return "Pantry is empty."
    oldest = min(_parse_time(item["last_seen_at"]) for item in items)
    age_days = (datetime.now(timezone.utc) - oldest).days
    names = ", ".join(item["canonical_name"] for item in items[:12])
    labels = labels_for("en")
    text = f"{labels['pantry']}: {names}"
    if age_days > 45:
        text += f" (oldest seen {age_days} days ago; refresh pantry when convenient)"
    return text


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
