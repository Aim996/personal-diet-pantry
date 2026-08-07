"""Deterministic, local nutrition lookup and calculation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import yaml


_OUTPUT_PRECISION = Decimal("0.01")
_CALCULATION_PRECISION = 50
_GRADES = frozenset({"A", "B", "C", "D"})


class NutritionValidationError(ValueError):
    """Raised when nutrition data cannot safely be calculated or persisted."""


@dataclass(frozen=True)
class NutritionFacts:
    """Known nutrient quantities for one declared evidence basis."""

    calories: Decimal | None
    protein: Decimal | None
    fat: Decimal | None
    carbohydrate: Decimal | None
    fiber: Decimal | None
    sodium: Decimal | None
    source: str
    source_grade: str
    uncertainty: str | None = None
    hydration_ml: Decimal | None = None

    def __post_init__(self) -> None:
        if all(
            getattr(self, field) is None
            for field in (
                "calories",
                "protein",
                "fat",
                "carbohydrate",
                "fiber",
                "sodium",
                "hydration_ml",
            )
        ):
            raise NutritionValidationError(
                "nutrition facts must include at least one known nutrient"
            )
        _validate_nutrients(self)
        _validate_provenance(self.source, self.source_grade, self.uncertainty)


@dataclass(frozen=True)
class NutritionResult:
    """Rounded nutrients for one consumed portion with its source provenance."""

    calories: Decimal | None
    protein: Decimal | None
    fat: Decimal | None
    carbohydrate: Decimal | None
    fiber: Decimal | None
    sodium: Decimal | None
    source: str
    source_grade: str
    uncertainty: str | None = None
    hydration_ml: Decimal | None = None

    def __post_init__(self) -> None:
        _validate_nutrients(self)
        _validate_provenance(self.source, self.source_grade, self.uncertainty)


class NutritionRepository:
    """Resolve nutrition facts locally from personal cache, seeds, or an estimate."""

    def __init__(
        self,
        rules_root: Path,
        connection: sqlite3.Connection | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        self._rules_root = Path(rules_root)
        self._connection = connection
        self._now = _utc_timestamp(now or datetime.now(timezone.utc))
        self._foods = _load_foods(self._rules_root / "nutrition-foods.yaml")

    def lookup(
        self,
        normalized_name: str,
        *,
        brand: str | None = None,
        estimate: NutritionFacts | None = None,
    ) -> NutritionFacts:
        """Return facts using the documented local-only precedence order."""

        name = _normalized_name(normalized_name)
        cache = self._cache_facts(name, brand)
        if cache is not None:
            return cache
        shipped = self._foods.get(name)
        if shipped is not None:
            return shipped
        if estimate is None:
            raise KeyError(f"No local nutrition facts for {name!r}; supply a C or D estimate")
        if not isinstance(estimate, NutritionFacts):
            raise NutritionValidationError("estimate must be NutritionFacts")
        if estimate.source_grade not in {"C", "D"}:
            raise NutritionValidationError("caller-supplied estimates must have a C or D source grade")
        return estimate

    def resolution_sources(
        self,
        normalized_name: str,
        *,
        brand: str | None = None,
        estimate: NutritionFacts | None = None,
    ) -> tuple[NutritionFacts | None, NutritionFacts | None, NutritionFacts | None]:
        """Return independent cache, shipped, and caller-estimate sources."""

        name = _normalized_name(normalized_name)
        if estimate is not None:
            if not isinstance(estimate, NutritionFacts):
                raise NutritionValidationError("estimate must be NutritionFacts")
            if estimate.source_grade not in {"C", "D"}:
                raise NutritionValidationError(
                    "caller-supplied estimates must have a C or D source grade"
                )
        return self._cache_facts(name, brand), self._foods.get(name), estimate

    def _cache_facts(self, normalized_name: str, brand: str | None) -> NutritionFacts | None:
        if self._connection is None:
            return None
        if brand is not None and brand.strip():
            row = self._active_cache_row(normalized_name, brand.strip())
            if row is not None:
                return _facts_from_cache(row)
        row = self._active_cache_row(normalized_name, "")
        return _facts_from_cache(row) if row is not None else None

    def _active_cache_row(self, normalized_name: str, brand: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT nutrition_json, source, source_grade
            FROM nutrition_cache
            WHERE normalized_name = ?
              AND brand = ?
              AND serving_basis = 'per_100g'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_name, brand, self._now),
        ).fetchone()


def calculate_nutrition(
    facts_per_100g: NutritionFacts,
    consumed_weight_g: Decimal,
) -> NutritionResult:
    """Calculate a consumed portion without binary floating-point arithmetic."""

    if not isinstance(facts_per_100g, NutritionFacts):
        raise NutritionValidationError("facts_per_100g must be NutritionFacts")
    weight = _nonnegative_decimal(consumed_weight_g, "consumed_weight_g")
    return scale_nutrition(facts_per_100g, weight / Decimal("100"))


def scale_nutrition(
    facts: NutritionFacts | NutritionResult,
    multiplier: Decimal,
) -> NutritionResult:
    """Scale nutrition facts by one explicit, non-negative multiplier."""

    if not isinstance(facts, (NutritionFacts, NutritionResult)):
        raise NutritionValidationError(
            "facts must be NutritionFacts or NutritionResult"
        )
    scale = _nonnegative_decimal(multiplier, "multiplier")

    def scaled(value: Decimal | None) -> Decimal | None:
        return _round_output(value * scale) if value is not None else None

    with localcontext() as calculation_context:
        calculation_context.prec = _CALCULATION_PRECISION
        calculation_context.rounding = ROUND_HALF_UP
        return NutritionResult(
            calories=scaled(facts.calories),
            protein=scaled(facts.protein),
            fat=scaled(facts.fat),
            carbohydrate=scaled(facts.carbohydrate),
            fiber=scaled(facts.fiber),
            sodium=scaled(facts.sodium),
            source=facts.source,
            source_grade=facts.source_grade,
            uncertainty=facts.uncertainty,
            hydration_ml=(
                scaled(facts.hydration_ml)
                if facts.hydration_ml is not None
                else None
            ),
        )


def calculate_inventory_deduction(
    consumed_weight_g: Decimal,
    edible_ratio: Decimal,
    cooking_yield: Decimal,
) -> Decimal:
    """Convert cooked edible consumption to the raw inventory weight to deduct."""

    consumed = _nonnegative_decimal(consumed_weight_g, "consumed_weight_g")
    edible = _ratio(edible_ratio, "edible_ratio")
    yield_ratio = _positive_ratio(cooking_yield, "cooking_yield")
    with localcontext() as calculation_context:
        calculation_context.prec = _CALCULATION_PRECISION
        calculation_context.rounding = ROUND_HALF_UP
        return _round_output(consumed / edible / yield_ratio)


def weakest_grade(*grades: str) -> str:
    """Return the least reliable material grade, with A better than B/C/D."""

    if not grades or any(grade not in _GRADES for grade in grades):
        raise NutritionValidationError("grades must contain only A, B, C, or D")
    return max(grades, key=("A", "B", "C", "D").index)


def encode_decimal_text(value: Decimal, field: str) -> str:
    """Encode a non-negative Decimal as canonical, non-exponent persistence text."""

    number = _nonnegative_decimal(value, field)
    if number.is_zero():
        return "0"
    encoded = format(number, "f")
    if "." in encoded:
        encoded = encoded.rstrip("0").rstrip(".")
    return encoded


def decode_decimal_text(value: str, field: str) -> Decimal:
    """Decode only canonical Decimal text emitted by :func:`encode_decimal_text`."""

    if not isinstance(value, str) or not value:
        raise NutritionValidationError(f"{field} must be canonical decimal text")
    try:
        number = Decimal(value)
    except Exception as error:
        raise NutritionValidationError(f"{field} must be canonical decimal text") from error
    if encode_decimal_text(number, field) != value:
        raise NutritionValidationError(f"{field} must be canonical decimal text")
    return number


def _validate_nutrients(facts: NutritionFacts | NutritionResult) -> None:
    for field in (
        "calories",
        "protein",
        "fat",
        "carbohydrate",
        "fiber",
        "sodium",
        "hydration_ml",
    ):
        value = getattr(facts, field)
        if value is None:
            continue
        _nonnegative_decimal(value, field)


def _validate_provenance(source: str, source_grade: str, uncertainty: str | None) -> None:
    if not isinstance(source, str) or not source.strip():
        raise NutritionValidationError("source must be a non-empty string")
    if source_grade not in _GRADES:
        raise NutritionValidationError("source_grade must be one of A, B, C, or D")
    if uncertainty is not None and (not isinstance(uncertainty, str) or not uncertainty.strip()):
        raise NutritionValidationError("uncertainty must be a non-empty string when provided")


def _nonnegative_decimal(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise NutritionValidationError(f"{field} must be a finite, non-negative Decimal")
    return value


def _ratio(value: Decimal, field: str) -> Decimal:
    number = _nonnegative_decimal(value, field)
    if number <= 0 or number > 1:
        raise NutritionValidationError(f"{field} must be a Decimal greater than zero through one")
    return number


def _positive_ratio(value: Decimal, field: str) -> Decimal:
    number = _nonnegative_decimal(value, field)
    if number <= 0:
        raise NutritionValidationError(f"{field} must be a positive Decimal")
    return number


def _round_output(value: Decimal) -> Decimal:
    return value.quantize(_OUTPUT_PRECISION, rounding=ROUND_HALF_UP)


def _load_foods(path: Path) -> dict[str, NutritionFacts]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise NutritionValidationError(f"Unable to load nutrition food data: {path}") from error
    if not isinstance(document, Mapping) or not isinstance(document.get("foods"), Mapping):
        raise NutritionValidationError("nutrition-foods.yaml must contain a foods mapping")
    return {
        _normalized_name(name): _facts_from_mapping(value)
        for name, value in document["foods"].items()
    }


def _facts_from_cache(row: sqlite3.Row) -> NutritionFacts:
    try:
        payload = json.loads(row["nutrition_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise NutritionValidationError("nutrition cache contains invalid JSON") from error
    if not isinstance(payload, Mapping):
        raise NutritionValidationError("nutrition cache payload must be a JSON object")
    return _facts_from_mapping(payload, source=row["source"], source_grade=row["source_grade"])


def _facts_from_mapping(
    values: Mapping[str, Any],
    *,
    source: str | None = None,
    source_grade: str | None = None,
) -> NutritionFacts:
    if source is None:
        source = values.get("source")
    if source_grade is None:
        source_grade = values.get("source_grade")
    nutrients = {field: _data_decimal(values.get(field), field) for field in _NUTRIENT_FIELDS}
    uncertainty = values.get("uncertainty")
    return NutritionFacts(
        **nutrients,
        source=source,
        source_grade=source_grade,
        uncertainty=uncertainty,
        hydration_ml=_optional_data_decimal(
            values.get("hydration_ml"), "hydration_ml"
        ),
    )


_NUTRIENT_FIELDS = ("calories", "protein", "fat", "carbohydrate", "fiber", "sodium")


def _data_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise NutritionValidationError(f"{field} must be a string or integer Decimal value")
    try:
        return Decimal(str(value))
    except Exception as error:
        raise NutritionValidationError(f"{field} must be a finite Decimal value") from error


def _optional_data_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _data_decimal(value, field)


def _normalized_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NutritionValidationError("normalized_name must be a non-empty string")
    return value.strip().lower()


def _utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NutritionValidationError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
