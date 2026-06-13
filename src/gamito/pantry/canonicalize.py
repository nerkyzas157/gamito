"""Canonical mapping + slow-use eligibility for the photo-based pantry.

The vision wrapper produces free-form English labels (``"olive oil"``,
``"all-purpose flour"``, ``"chicken breast"``). This module:

1. Maps each label to a canonical ingredient name using the shared
   :class:`CanonicalPriceLookup` (same lookup the shopping list relies on).
2. Filters the canonical to a slow-use whitelist (spices, oils, flours,
   pasta, rice, condiments, dairy with long shelf life, water) so the
   pantry never accumulates fresh meat / produce that spoils in days.
3. Provides :func:`merge_detections`, which combines the LLM detections with
   any free-text manual entries the user typed and returns a
   :class:`PantryAnalysis` ready for the confirmation UI.
"""

from __future__ import annotations

import re

from gamito.models.pantry import DetectedIngredient, PantryAnalysis
from gamito.pricing import CanonicalPriceLookup, get_canonical_price_lookup

_SLOW_USE_CATEGORIES: frozenset[str] = frozenset(
    {"spices", "condiment", "dairy", "pantry", "beverage"}
)

_NON_FOOD_BLOCKLIST: frozenset[str] = frozenset(
    {
        "foil",
        "aluminum foil",
        "plastic wrap",
        "parchment",
        "parchment paper",
        "toothpicks",
        "skewer",
        "skewers",
    }
)

_BEVERAGE_ALLOWLIST: frozenset[str] = frozenset({"water"})

_LEFTOVER_PREFIX_RE = re.compile(r"^leftover[\s_-]+", re.IGNORECASE)


def _get_lookup(lookup: CanonicalPriceLookup | None) -> CanonicalPriceLookup | None:
    if lookup is not None:
        return lookup
    try:
        return get_canonical_price_lookup()
    except Exception:  # pragma: no cover - data file optional
        return None


def resolve_to_canonical(
    raw_label: str,
    lookup: CanonicalPriceLookup | None = None,
) -> str | None:
    """Map a free-form ingredient label to a canonical name (or ``None``)."""

    if not raw_label or not raw_label.strip():
        return None

    runtime_lookup = _get_lookup(lookup)
    if runtime_lookup is None:
        return None
    return runtime_lookup.canonicalize(raw_label)


def is_slow_use(
    canonical: str,
    lookup: CanonicalPriceLookup | None = None,
) -> bool:
    """Return ``True`` if a canonical ingredient is eligible for the pantry."""

    if not canonical:
        return False
    name = canonical.strip().lower()
    if not name:
        return False
    if name in _NON_FOOD_BLOCKLIST:
        return False
    if _LEFTOVER_PREFIX_RE.match(name):
        return False

    runtime_lookup = _get_lookup(lookup)
    if runtime_lookup is None:
        return False
    info = runtime_lookup.lookup(canonical)
    if info is None or info.category is None:
        return False
    category = info.category.strip().lower()
    if category not in _SLOW_USE_CATEGORIES:
        return False
    if category == "beverage" and name not in _BEVERAGE_ALLOWLIST:
        return False
    return True


def merge_detections(
    raw_results: list[DetectedIngredient],
    manual_extras: list[str] | None = None,
    lookup: CanonicalPriceLookup | None = None,
) -> PantryAnalysis:
    """Combine LLM detections + manual entries; canonicalise + filter eligibility."""

    runtime_lookup = _get_lookup(lookup)

    detections: list[DetectedIngredient] = []
    rejected: list[str] = []
    seen_canonicals: set[str] = set()

    def _process(label: str, confidence: float) -> None:
        cleaned = (label or "").strip()
        if not cleaned:
            return
        canonical = resolve_to_canonical(cleaned, runtime_lookup)
        if canonical is None:
            rejected.append(cleaned)
            return
        if not is_slow_use(canonical, runtime_lookup):
            rejected.append(cleaned)
            return
        key = canonical.strip().lower()
        if key in seen_canonicals:
            return
        seen_canonicals.add(key)
        detections.append(
            DetectedIngredient(
                raw_label=cleaned,
                canonical=canonical,
                eligible=True,
                confidence=confidence,
            )
        )

    for entry in raw_results or []:
        _process(entry.raw_label, entry.confidence)

    for extra in manual_extras or []:
        _process(extra, 1.0)

    return PantryAnalysis(detections=detections, rejected=rejected)
