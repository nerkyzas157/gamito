"""Profile MCP tools."""

from __future__ import annotations

from gamito.db import profiles as profile_repo
from gamito.db.pantry import list_pantry
from gamito.mcp.app import tool
from gamito.mcp.errors import err
from gamito.mcp.tools.common import open_db, require_profile
from gamito.models.profile import Profile
from gamito.recommendation.tags import tags_from_survey


@tool
def list_profiles() -> dict:
    """List household profiles in stable display order."""

    with open_db() as conn:
        profiles = profile_repo.list_profiles(conn)
    names = ", ".join(profile["name"] for profile in profiles) or "No profiles yet."
    return {"profiles": profiles, "text": names}


@tool
def get_profile(profile_id: str) -> dict:
    """Return a full persisted profile summary."""

    with open_db() as conn:
        profile = require_profile(conn, profile_id)
        profile["pantry_item_count"] = len(list_pantry(conn, profile_id))
    return {**profile, "text": _profile_text(profile)}


@tool
def save_profile(
    name: str,
    language: str = "en",
    dietary_pref: str | None = None,
    allergies: list[str] | None = None,
    disliked_ingredients: list[str] | None = None,
    kitchen_tools: list[str] | None = None,
    cuisine_preferences: list[str] | None = None,
    skill_level: str = "intermediate",
    meal_prep_ok: bool = True,
    leftovers_ok: bool = True,
    max_time_min: int | None = None,
    profile_id: str | None = None,
) -> dict:
    """Create or update a profile from flat MCP parameters."""

    if not name or not name.strip():
        raise err("INVALID_INPUT", "name is required")
    if language not in {"en", "lt"}:
        raise err("INVALID_INPUT", "language must be 'en' or 'lt'")
    if max_time_min is not None and max_time_min < 1:
        raise err("INVALID_INPUT", "max_time_min must be >= 1")

    model = Profile(
        profile_id=profile_id,
        name=name,
        language=language,
        dietary_pref=dietary_pref,
        allergies=allergies or [],
        disliked_ingredients=disliked_ingredients or [],
        kitchen_tools=kitchen_tools or [],
        meal_prep_ok=meal_prep_ok,
        leftovers_ok=leftovers_ok,
        cuisine_preferences=cuisine_preferences or [],
        skill_level=skill_level,
        max_time_min=max_time_min,
    )
    tags_generated = len(
        tags_from_survey(
            dietary_pref=dietary_pref,
            cuisines=cuisine_preferences or [],
            dislikes=disliked_ingredients or [],
        )
    )
    with open_db() as conn:
        try:
            stored_id, created = profile_repo.save_profile(conn, model)
        except KeyError as exc:
            raise err("PROFILE_NOT_FOUND", f"profile not found: {profile_id}") from exc
    action = "Created" if created else "Updated"
    return {
        "profile_id": stored_id,
        "created": created,
        "tags_generated": tags_generated,
        "text": f"{action} profile {name.strip()} ({stored_id}).",
    }


@tool
def update_preferences(
    profile_id: str,
    liked_tags: list[str] | None = None,
    disliked_tags: list[str] | None = None,
) -> dict:
    """Apply conversational preference deltas to profile tags."""

    with open_db() as conn:
        require_profile(conn, profile_id)
        applied = profile_repo.apply_preference_deltas(
            conn,
            profile_id=profile_id,
            liked_tags=liked_tags or [],
            disliked_tags=disliked_tags or [],
        )
    if applied:
        details = ", ".join(f"{item['tag']} +{item['delta']}" for item in applied)
    else:
        details = "No preference tags supplied."
    return {"profile_id": profile_id, "applied": applied, "text": details}


def _profile_text(profile: dict) -> str:
    pieces = [
        profile["name"],
        f"language={profile['language']}",
        f"pantry={profile['pantry_item_count']}",
    ]
    if profile.get("dietary_pref"):
        pieces.append(f"diet={profile['dietary_pref']}")
    if profile.get("allergies"):
        pieces.append("allergies=" + ", ".join(profile["allergies"]))
    if profile.get("cuisines"):
        pieces.append("cuisines=" + ", ".join(profile["cuisines"][:5]))
    return " · ".join(pieces)
