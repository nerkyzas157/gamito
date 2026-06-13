"""Pydantic profile models used by recommendation and planning code."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    """Survey-level household member preferences persisted by the app."""

    model_config = ConfigDict(extra="ignore")

    profile_id: str | None = None
    name: str | None = None
    language: str = "en"
    dietary_pref: str | None = None
    allergies: list[str] = Field(default_factory=list)
    disliked_ingredients: list[str] = Field(default_factory=list)
    kitchen_tools: list[str] = Field(default_factory=list)
    meal_prep_ok: bool = True
    leftovers_ok: bool = True
    cuisine_preferences: list[str] = Field(default_factory=list)
    skill_level: str = "intermediate"
    max_time_min: int | None = Field(default=None, ge=1)


class UserContext(BaseModel):
    """Query-time context passed to planning and shopping-list code."""

    model_config = ConfigDict(extra="ignore")

    profile_id: str | None = None
    positive_tags: list[str] = Field(default_factory=list)
    negative_tags: list[str] = Field(default_factory=list)
    time_ceiling_minutes: int | None = Field(default=None, ge=1)
    skill_ceiling: str = "medium"
    available_tools: list[str] = Field(default_factory=list)
    meal_prep_ok: bool = True
    leftovers_ok: bool = True
    language: str = "en"
    cuisine_hints: list[str] = Field(default_factory=list)
    dietary_pref: str | None = None
    min_healthiness_score: int | None = Field(default=None, ge=0, le=100)
    pantry_canonicals: list[str] = Field(
        default_factory=list,
        description="Canonical ingredient names the user already has at home.",
    )

    @property
    def allergies(self) -> list[str]:
        """Allergen-like negative tags understood by the validator/filter layer."""

        return self.negative_tags
