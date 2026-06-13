"""Canonical-ingredient price lookup backed by Phase 1 parquet artifacts.

The shopping-list node previously relied on a 22-entry hard-coded EUR price map
which dropped most ingredients to ``0.00``. This module loads the offline
artifacts produced by ``scripts/feature_engineering/build_canonical_table.py``
and ``build_price_lookup.py`` once at process start and exposes:

* :func:`CanonicalPriceLookup.canonicalize` to map a raw ingredient string
  (``"2 tablespoons (Optional) extra-virgin olive oil"``) to a canonical name
  (``"olive oil"``) so the shopping list can dedupe correctly.
* :func:`CanonicalPriceLookup.estimate_price_eur` to convert a quantity + unit
  pair into a EUR estimate using the canonical retail price (kg / L / each).

The lookup is intentionally lazy and memoised so multiple agent calls don't
pay the parquet load cost more than once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRICES_PARQUET = PROJECT_ROOT / "data" / "lookups" / "canonical_prices.parquet"
DEFAULT_PARSED_TO_CANONICAL_PARQUET = (
    PROJECT_ROOT / "data" / "lookups" / "parsed_name_to_canonical.parquet"
)

# Volume / weight conversions to the canonical base unit (ml or g respectively).
# Values are approximate but consistent with the rest of the pipeline.
_ML_PER_UNIT: dict[str, float] = {
    "ml": 1.0,
    "milliliter": 1.0,
    "milliliters": 1.0,
    "millilitre": 1.0,
    "millilitres": 1.0,
    "cl": 10.0,
    "centiliter": 10.0,
    "centiliters": 10.0,
    "dl": 100.0,
    "deciliter": 100.0,
    "deciliters": 100.0,
    "l": 1000.0,
    "liter": 1000.0,
    "liters": 1000.0,
    "litre": 1000.0,
    "litres": 1000.0,
    "tsp": 5.0,
    "teaspoon": 5.0,
    "teaspoons": 5.0,
    "tbsp": 15.0,
    "tablespoon": 15.0,
    "tablespoons": 15.0,
    "cup": 240.0,
    "cups": 240.0,
    "fl oz": 30.0,
    "fluid ounce": 30.0,
    "fluid ounces": 30.0,
    "floz": 30.0,
    "pint": 473.0,
    "pints": 473.0,
    "quart": 946.0,
    "quarts": 946.0,
    "gallon": 3785.0,
    "gallons": 3785.0,
    "dash": 0.6,
    "dashes": 0.6,
    "pinch": 0.3,
    "pinches": 0.3,
    "drop": 0.05,
    "drops": 0.05,
    "splash": 5.0,
    "splashes": 5.0,
}

_G_PER_UNIT: dict[str, float] = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "gr": 1.0,
    "kg": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.59,
    "lbs": 453.59,
    "pound": 453.59,
    "pounds": 453.59,
    "mg": 0.001,
    "milligram": 0.001,
    "milligrams": 0.001,
}

# Tokens that map cleanly onto the "each" canonical unit.
_EACH_UNIT_TOKENS: set[str] = {
    "each",
    "piece",
    "pieces",
    "clove",
    "cloves",
    "head",
    "heads",
    "bunch",
    "bunches",
    "stalk",
    "stalks",
    "sprig",
    "sprigs",
    "leaf",
    "leaves",
    "slice",
    "slices",
    "loaf",
    "loaves",
    "can",
    "cans",
    "jar",
    "jars",
    "package",
    "packages",
    "packet",
    "packets",
    "container",
    "containers",
    "stick",
    "sticks",
    "egg",
    "eggs",
    "fillet",
    "fillets",
    "breast",
    "breasts",
    "thigh",
    "thighs",
}

# Light noise tokens we strip before lookup. Conservative on purpose so we don't
# accidentally collapse meaningful adjectives ("smoked salmon" must stay).
_MODIFIER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\(.*?\)",  # parenthesised qualifiers, e.g. "(Optional)"
        r"\boptional\b",
        r"\bto taste\b",
        r"\bfor garnish\b",
        r"\bfor serving\b",
        r"\bfor frying\b",
        r"\bas needed\b",
        r"\bif desired\b",
        r"\bdivided\b",
        r"\bplus more\b.*",
        r"\bor more\b",
        r"\bor as needed\b",
    )
)

_TRAILING_PUNCT_RE = re.compile(r"[\s,;:.!\-]+$")
_LEADING_PUNCT_RE = re.compile(r"^[\s,;:.!\-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_QTY_PREFIX_RE = re.compile(
    r"^\s*(?:[\d¼½¾⅓⅔⅛⅜⅝⅞]+(?:[\s\-/][\d¼½¾⅓⅔⅛⅜⅝⅞]+)?(?:\.\d+)?)\s*"
)


@dataclass(frozen=True)
class PriceInfo:
    """Canonical retail price metadata for a single canonical ingredient."""

    canonical: str
    price_eur: float | None
    unit: str | None  # one of "kg", "L", "each", or None
    category: str | None


def normalize_ingredient_name(raw: str) -> str:
    """Strip quantities, units, and modifiers from a raw ingredient string.

    The output is lowercase, single-spaced, and ready to look up against the
    ``parsed_name_to_canonical`` table or the canonical name index. Returns an
    empty string when the input is empty after stripping.
    """

    if not raw:
        return ""

    text = raw.lower().strip()

    for pattern in _MODIFIER_PATTERNS:
        text = pattern.sub(" ", text)

    text = _QTY_PREFIX_RE.sub("", text)

    tokens = [tok for tok in re.split(r"\s+", text) if tok]

    # Only strip volume / weight units up front — "each"-class tokens like
    # ``eggs`` or ``fillet`` are ambiguous (they may be the actual ingredient)
    # so we leave those for the canonicalisation lookup to disambiguate.
    cleaned: list[str] = []
    skip_next_of = False
    saw_meaningful_token = False
    for token in tokens:
        if skip_next_of and token == "of":
            skip_next_of = False
            continue
        skip_next_of = False
        bare = token.strip(",.;:!()")
        if not bare:
            continue
        is_volume_or_weight = bare in _ML_PER_UNIT or bare in _G_PER_UNIT
        if is_volume_or_weight and not saw_meaningful_token:
            skip_next_of = True
            continue
        if not is_volume_or_weight:
            saw_meaningful_token = True
        cleaned.append(bare)

    text = " ".join(cleaned)
    text = _TRAILING_PUNCT_RE.sub("", text)
    text = _LEADING_PUNCT_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def _to_grams(quantity: float, unit: str | None) -> float | None:
    if quantity <= 0:
        return None
    if unit is None:
        return None
    factor = _G_PER_UNIT.get(unit.lower())
    return quantity * factor if factor is not None else None


def _to_milliliters(quantity: float, unit: str | None) -> float | None:
    if quantity <= 0:
        return None
    if unit is None:
        return None
    factor = _ML_PER_UNIT.get(unit.lower())
    return quantity * factor if factor is not None else None


def _is_each_unit(unit: str | None) -> bool:
    if unit is None:
        return False
    return unit.lower() in _EACH_UNIT_TOKENS


class CanonicalPriceLookup:
    """In-memory canonical ingredient pricing index.

    The lookup is intentionally read-only after construction so it can be
    shared safely between agent threads.
    """

    def __init__(
        self,
        prices: dict[str, PriceInfo],
        parsed_to_canonical: dict[str, str],
    ) -> None:
        self._prices: dict[str, PriceInfo] = prices
        self._parsed_to_canonical: dict[str, str] = parsed_to_canonical
        self._canonical_lower_index: dict[str, str] = {
            canonical.lower(): canonical for canonical in prices
        }

    @classmethod
    def from_default_paths(cls) -> "CanonicalPriceLookup":
        """Load the canonical pricing tables from the standard ``data/lookups``."""

        return cls.from_paths(
            prices_parquet=DEFAULT_PRICES_PARQUET,
            parsed_to_canonical_parquet=DEFAULT_PARSED_TO_CANONICAL_PARQUET,
        )

    @classmethod
    def from_paths(
        cls,
        *,
        prices_parquet: Path,
        parsed_to_canonical_parquet: Path,
    ) -> "CanonicalPriceLookup":
        prices = _load_prices(prices_parquet) if prices_parquet.exists() else {}
        parsed_to_canonical = (
            _load_parsed_to_canonical(parsed_to_canonical_parquet)
            if parsed_to_canonical_parquet.exists()
            else {}
        )
        return cls(prices=prices, parsed_to_canonical=parsed_to_canonical)

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` when any pricing data was loaded successfully."""

        return bool(self._prices)

    @property
    def known_canonicals(self) -> Iterable[str]:
        """Iterable view of the canonical names with at least metadata loaded."""

        return self._prices.keys()

    def canonicalize(self, raw_name: str) -> str | None:
        """Map a raw ingredient string to its canonical name (or ``None``)."""

        normalized = normalize_ingredient_name(raw_name)
        if not normalized:
            return None
        return self._canonicalize_normalized(normalized)

    def lookup(self, canonical: str) -> PriceInfo | None:
        """Return the :class:`PriceInfo` for ``canonical`` if known."""

        if not canonical:
            return None
        info = self._prices.get(canonical)
        if info is not None:
            return info
        resolved = self._canonical_lower_index.get(canonical.lower())
        return self._prices.get(resolved) if resolved else None

    def estimate_price_eur(
        self,
        canonical: str,
        quantity: float | None,
        unit: str | None,
    ) -> float | None:
        """Estimate the EUR cost for ``quantity`` of ``canonical`` in ``unit``.

        Heuristics:
        * If the canonical unit is ``each``, multiply quantity (or 1) by base.
        * For ``kg`` / ``L`` canonicals, convert quantity to the base unit and
          scale; missing/unknown units fall back to roughly half a base pack so
          the user sees a realistic, non-zero estimate.
        * Returns ``None`` when there is no canonical price at all.
        """

        info = self.lookup(canonical)
        if info is None or info.price_eur is None:
            return None

        base_unit = (info.unit or "").lower()
        qty = quantity if quantity and quantity > 0 else None

        if base_unit == "each":
            count = qty if qty is not None else 1.0
            return round(info.price_eur * count, 2)

        if base_unit == "kg":
            grams = _to_grams(qty, unit) if qty is not None else None
            if grams is None:
                # Fall back to a half-pack heuristic so display stays informative.
                return round(info.price_eur * 0.5, 2)
            kilograms = max(grams / 1000.0, 0.05)
            return round(info.price_eur * kilograms, 2)

        if base_unit == "l":
            milliliters = _to_milliliters(qty, unit) if qty is not None else None
            if milliliters is None:
                return round(info.price_eur * 0.25, 2)
            liters = max(milliliters / 1000.0, 0.05)
            return round(info.price_eur * liters, 2)

        # Unknown/missing canonical unit: fall back to a single-pack heuristic.
        return round(info.price_eur, 2)

    def _canonicalize_normalized(self, normalized: str) -> str | None:
        """Lookup helpers shared by :meth:`canonicalize`. ``normalized`` must be
        the output of :func:`normalize_ingredient_name`.
        """

        if not normalized:
            return None

        direct = self._parsed_to_canonical.get(normalized)
        if direct:
            return direct
        canonical_hit = self._canonical_lower_index.get(normalized)
        if canonical_hit:
            return canonical_hit

        # Try simple plural / singular folds: "tomatoes" -> "tomato".
        for variant in _stem_variants(normalized):
            mapped = self._parsed_to_canonical.get(variant)
            if mapped:
                return mapped
            canonical = self._canonical_lower_index.get(variant)
            if canonical:
                return canonical

        # Drop a single leading adjective at a time and retry.
        # This catches "fresh tomatoes" -> "tomatoes" -> "tomato".
        tokens = normalized.split()
        for start in range(1, min(len(tokens), 4)):
            candidate = " ".join(tokens[start:])
            if not candidate:
                continue
            mapped = self._parsed_to_canonical.get(candidate)
            if mapped:
                return mapped
            canonical = self._canonical_lower_index.get(candidate)
            if canonical:
                return canonical
            for variant in _stem_variants(candidate):
                mapped = self._parsed_to_canonical.get(variant)
                if mapped:
                    return mapped
                canonical = self._canonical_lower_index.get(variant)
                if canonical:
                    return canonical

        # Last-mile suffix match: try each token as a candidate canonical.
        for token in reversed(tokens):
            if len(token) < 3:
                continue
            canonical = self._canonical_lower_index.get(token)
            if canonical:
                return canonical
            for variant in _stem_variants(token):
                canonical = self._canonical_lower_index.get(variant)
                if canonical:
                    return canonical

        return None


def _stem_variants(token: str) -> list[str]:
    """Return cheap singular/plural variants for ``token``.

    We keep this intentionally small — the parsed-to-canonical table already
    handles most morphological variation; this just patches the long tail.
    """

    variants: list[str] = []
    if token.endswith("ies") and len(token) > 4:
        variants.append(token[:-3] + "y")
    if token.endswith("es") and len(token) > 3:
        variants.append(token[:-2])
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        variants.append(token[:-1])
    if not token.endswith("s"):
        variants.append(token + "s")
    return variants


def _load_prices(path: Path) -> dict[str, PriceInfo]:
    df = pd.read_parquet(path)
    out: dict[str, PriceInfo] = {}
    for row in df.itertuples(index=False):
        canonical = getattr(row, "canonical", None)
        if not canonical:
            continue
        price_raw = getattr(row, "price_eur", None)
        try:
            price_eur = (
                float(price_raw)
                if price_raw is not None and not pd.isna(price_raw)
                else None
            )
        except (TypeError, ValueError):
            price_eur = None
        unit_raw = getattr(row, "unit", None)
        unit = (
            str(unit_raw).strip()
            if unit_raw is not None and not pd.isna(unit_raw)
            else None
        )
        if unit and unit.lower() == "l":
            unit = "L"
        category_raw = getattr(row, "category", None)
        category = (
            str(category_raw).strip()
            if category_raw is not None and not pd.isna(category_raw)
            else None
        )
        out[str(canonical)] = PriceInfo(
            canonical=str(canonical),
            price_eur=price_eur,
            unit=unit,
            category=category,
        )
    return out


def _load_parsed_to_canonical(path: Path) -> dict[str, str]:
    df = pd.read_parquet(path)
    out: dict[str, str] = {}
    for row in df.itertuples(index=False):
        parsed = getattr(row, "parsed_name_norm", None)
        canonical = getattr(row, "canonical", None)
        if parsed is None or canonical is None:
            continue
        try:
            if pd.isna(parsed) or pd.isna(canonical):
                continue
        except TypeError:
            pass
        parsed_str = str(parsed).strip().lower()
        canonical_str = str(canonical).strip()
        if not parsed_str or not canonical_str:
            continue
        out[parsed_str] = canonical_str
    return out


@lru_cache(maxsize=1)
def get_canonical_price_lookup() -> CanonicalPriceLookup:
    """Process-wide singleton initialised from the default parquet paths.

    Wrapped in :func:`functools.lru_cache` so the parquet files are only read
    once per process. Tests can use :class:`CanonicalPriceLookup` directly with
    custom data instead of going through this accessor.
    """

    return CanonicalPriceLookup.from_default_paths()
