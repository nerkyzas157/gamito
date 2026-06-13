"""Pydantic models for the photo-based pantry feature."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DetectedIngredient(BaseModel):
    """A single ingredient candidate produced by the pantry vision pipeline."""

    model_config = ConfigDict(extra="ignore")

    raw_label: str = Field(
        description="Exact label the vision model returned (e.g. 'extra virgin olive oil').",
    )
    canonical: str | None = Field(
        default=None,
        description=(
            "Canonical ingredient name resolved against canonical_prices. "
            "``None`` when no canonical match was found."
        ),
    )
    eligible: bool = Field(
        default=False,
        description="True iff the canonical passes the slow-use predicate.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Model-reported 0..1 confidence for this detection.",
    )


class PantryAnalysis(BaseModel):
    """Aggregate result of running detections through the canonicaliser."""

    model_config = ConfigDict(extra="ignore")

    detections: list[DetectedIngredient] = Field(
        default_factory=list,
        description="Slow-use eligible canonicals the user can confirm.",
    )
    rejected: list[str] = Field(
        default_factory=list,
        description="raw_labels that didn't map / weren't eligible.",
    )
