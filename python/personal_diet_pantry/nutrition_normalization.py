"""Explicit serving-basis normalization for one consumed food portion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .nutrition import NutritionFacts, NutritionResult, scale_nutrition


class NutritionNormalizationError(ValueError):
    """Raised when nutrition evidence cannot be matched to a consumed measure."""


class NutritionBasis(str, Enum):
    """The quantity represented by the supplied nutrition facts."""

    PER_100G = "per_100g"
    PER_100ML = "per_100ml"
    PER_SERVING = "per_serving"
    CONSUMED_TOTAL = "consumed_total"


@dataclass(frozen=True)
class ConsumptionMeasure:
    """Independent consumed dimensions; callers must not conflate mass and volume."""

    weight_g: Decimal | None = None
    volume_ml: Decimal | None = None
    servings: Decimal | None = None


@dataclass(frozen=True)
class NutritionEvidence:
    """Nutrition inputs and their reproducibility metadata."""

    facts: NutritionFacts
    basis: NutritionBasis
    dataset_version: str | None
    rules_version: str
    portion_evidence: dict[str, object] | None = None


@dataclass(frozen=True)
class NormalizedNutrition:
    """One consumed portion plus the evidence needed to reproduce it."""

    result: NutritionResult
    scale_factor: Decimal
    calculation_status: str
    provenance_status: str
    warnings: tuple[str, ...]


def normalize_nutrition(
    evidence: NutritionEvidence,
    measure: ConsumptionMeasure,
) -> NormalizedNutrition:
    """Normalize explicit evidence into one consumed portion."""

    if not isinstance(evidence, NutritionEvidence):
        raise NutritionNormalizationError(
            "evidence must be NutritionEvidence"
        )
    if not isinstance(measure, ConsumptionMeasure):
        raise NutritionNormalizationError(
            "measure must be ConsumptionMeasure"
    )
    scale = _scale_for(evidence.basis, measure)
    result = scale_nutrition(evidence.facts, scale)
    validate_consumed_hydration(result, measure)
    return NormalizedNutrition(
        result=result,
        scale_factor=scale,
        calculation_status="valid",
        provenance_status=(
            "traceable"
            if evidence.dataset_version is not None
            else "partial"
        ),
        warnings=(),
    )


def validate_consumed_hydration(
    result: NutritionResult,
    measure: ConsumptionMeasure,
) -> None:
    """Reject impossible water totals after all nutrition sources resolve."""

    if not isinstance(result, NutritionResult):
        raise NutritionNormalizationError(
            "result must be NutritionResult"
        )
    if not isinstance(measure, ConsumptionMeasure):
        raise NutritionNormalizationError(
            "measure must be ConsumptionMeasure"
        )
    if (
        result.hydration_ml is not None
        and measure.volume_ml is not None
        and result.hydration_ml > measure.volume_ml
    ):
        raise NutritionNormalizationError(
            "hydration exceeds consumed volume"
        )
    if (
        result.hydration_ml is not None
        and measure.weight_g is not None
        and result.hydration_ml > measure.weight_g
    ):
        raise NutritionNormalizationError(
            "hydration exceeds consumed mass"
        )


def _scale_for(
    basis: NutritionBasis,
    measure: ConsumptionMeasure,
) -> Decimal:
    if basis is NutritionBasis.CONSUMED_TOTAL:
        return Decimal("1")
    if basis is NutritionBasis.PER_100G:
        return _required_positive(
            measure.weight_g,
            "per_100g requires weight_g",
        ) / Decimal("100")
    if basis is NutritionBasis.PER_100ML:
        return _required_positive(
            measure.volume_ml,
            "per_100ml requires volume_ml",
        ) / Decimal("100")
    if basis is NutritionBasis.PER_SERVING:
        return _required_positive(
            measure.servings,
            "per_serving requires servings",
        )
    raise NutritionNormalizationError(
        f"unsupported nutrition basis: {basis!r}"
    )


def _required_positive(
    value: Decimal | None,
    message: str,
) -> Decimal:
    if value is None:
        raise NutritionNormalizationError(message)
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise NutritionNormalizationError(message)
    return value
