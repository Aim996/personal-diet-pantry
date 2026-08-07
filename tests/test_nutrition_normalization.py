from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from personal_diet_pantry.nutrition import NutritionFacts
from personal_diet_pantry.nutrition_normalization import (
    ConsumptionMeasure,
    NutritionBasis,
    NutritionEvidence,
    NutritionNormalizationError,
    normalize_nutrition,
)


def _soy() -> NutritionFacts:
    return NutritionFacts(
        calories=Decimal("33"),
        protein=Decimal("3.5"),
        fat=Decimal("1.8"),
        carbohydrate=Decimal("2"),
        fiber=Decimal("0"),
        sodium=Decimal("50"),
        hydration_ml=Decimal("95"),
        source="Chinese food composition table",
        source_grade="A",
    )


def _evidence(
    basis: NutritionBasis,
    *,
    facts: NutritionFacts | None = None,
) -> NutritionEvidence:
    return NutritionEvidence(
        facts=facts or _soy(),
        basis=basis,
        dataset_version="fixture-1",
        rules_version="0.6.1",
    )


def test_per_100ml_soy_scales_once_for_500ml() -> None:
    normalized = normalize_nutrition(
        _evidence(NutritionBasis.PER_100ML),
        ConsumptionMeasure(volume_ml=Decimal("500")),
    )

    assert normalized.scale_factor == Decimal("5")
    assert normalized.result.calories == Decimal("165")
    assert normalized.result.protein == Decimal("17.5")
    assert normalized.result.hydration_ml == Decimal("475")
    assert normalized.calculation_status == "valid"
    assert normalized.provenance_status == "traceable"


def test_partial_packaging_label_scales_known_fields_without_zero_filling() -> None:
    label = NutritionFacts(
        calories=Decimal("70"),
        protein=Decimal("3"),
        fat=Decimal("2"),
        carbohydrate=Decimal("10"),
        fiber=None,
        sodium=None,
        hydration_ml=None,
        source="packaging label",
        source_grade="A",
    )

    normalized = normalize_nutrition(
        _evidence(NutritionBasis.PER_100ML, facts=label),
        ConsumptionMeasure(volume_ml=Decimal("180")),
    )

    assert normalized.scale_factor == Decimal("1.8")
    assert normalized.result.calories == Decimal("126")
    assert normalized.result.protein == Decimal("5.4")
    assert normalized.result.fat == Decimal("3.6")
    assert normalized.result.carbohydrate == Decimal("18")
    assert normalized.result.fiber is None
    assert normalized.result.sodium is None
    assert normalized.result.hydration_ml is None


def test_nutrition_facts_reject_all_unknown_values() -> None:
    with pytest.raises(ValueError, match="at least one known nutrient"):
        NutritionFacts(
            calories=None,
            protein=None,
            fat=None,
            carbohydrate=None,
            fiber=None,
            sodium=None,
            hydration_ml=None,
            source="empty label",
            source_grade="A",
        )


def test_consumed_total_is_not_scaled_again() -> None:
    normalized = normalize_nutrition(
        _evidence(NutritionBasis.CONSUMED_TOTAL),
        ConsumptionMeasure(volume_ml=Decimal("500")),
    )

    assert normalized.scale_factor == Decimal("1")
    assert normalized.result.calories == Decimal("33")
    assert normalized.result.hydration_ml == Decimal("95")


def test_per_100g_scales_from_weight_only() -> None:
    normalized = normalize_nutrition(
        _evidence(NutritionBasis.PER_100G),
        ConsumptionMeasure(weight_g=Decimal("240")),
    )

    assert normalized.scale_factor == Decimal("2.4")
    assert normalized.result.calories == Decimal("79.2")


def test_per_serving_scales_from_servings_only() -> None:
    normalized = normalize_nutrition(
        _evidence(NutritionBasis.PER_SERVING),
        ConsumptionMeasure(servings=Decimal("2")),
    )

    assert normalized.scale_factor == Decimal("2")
    assert normalized.result.calories == Decimal("66")


@pytest.mark.parametrize(
    ("basis", "measure", "message"),
    [
        (
            NutritionBasis.PER_100ML,
            ConsumptionMeasure(weight_g=Decimal("500")),
            "per_100ml requires volume_ml",
        ),
        (
            NutritionBasis.PER_100G,
            ConsumptionMeasure(volume_ml=Decimal("500")),
            "per_100g requires weight_g",
        ),
        (
            NutritionBasis.PER_SERVING,
            ConsumptionMeasure(weight_g=Decimal("100")),
            "per_serving requires servings",
        ),
    ],
)
def test_basis_rejects_mismatched_measure(
    basis: NutritionBasis,
    measure: ConsumptionMeasure,
    message: str,
) -> None:
    with pytest.raises(NutritionNormalizationError, match=message):
        normalize_nutrition(_evidence(basis), measure)


@pytest.mark.parametrize(
    ("basis", "measure"),
    [
        (
            NutritionBasis.PER_100ML,
            ConsumptionMeasure(volume_ml=Decimal("0")),
        ),
        (
            NutritionBasis.PER_100G,
            ConsumptionMeasure(weight_g=Decimal("0")),
        ),
        (
            NutritionBasis.PER_SERVING,
            ConsumptionMeasure(servings=Decimal("0")),
        ),
    ],
)
def test_scaling_basis_requires_a_positive_matching_measure(
    basis: NutritionBasis,
    measure: ConsumptionMeasure,
) -> None:
    with pytest.raises(
        NutritionNormalizationError,
        match="requires",
    ):
        normalize_nutrition(_evidence(basis), measure)


def test_matching_basis_may_preserve_an_independent_physical_measure() -> None:
    normalized = normalize_nutrition(
        _evidence(NutritionBasis.PER_100ML),
        ConsumptionMeasure(
            volume_ml=Decimal("500"),
            weight_g=Decimal("510"),
        ),
    )

    assert normalized.scale_factor == Decimal("5")
    assert normalized.result.hydration_ml == Decimal("475")


def test_hydration_cannot_exceed_consumed_volume() -> None:
    impossible = replace(_soy(), hydration_ml=Decimal("120"))

    with pytest.raises(
        NutritionNormalizationError,
        match="hydration exceeds consumed volume",
    ):
        normalize_nutrition(
            _evidence(NutritionBasis.PER_100ML, facts=impossible),
            ConsumptionMeasure(volume_ml=Decimal("500")),
        )


def test_hydration_cannot_exceed_consumed_mass_for_per_100g() -> None:
    impossible = replace(_soy(), hydration_ml=Decimal("110"))

    with pytest.raises(
        NutritionNormalizationError,
        match="hydration exceeds consumed mass",
    ):
        normalize_nutrition(
            _evidence(NutritionBasis.PER_100G, facts=impossible),
            ConsumptionMeasure(weight_g=Decimal("100")),
        )


def test_missing_dataset_version_is_partial_provenance() -> None:
    evidence = replace(
        _evidence(NutritionBasis.PER_100ML),
        dataset_version=None,
    )

    normalized = normalize_nutrition(
        evidence,
        ConsumptionMeasure(volume_ml=Decimal("500")),
    )

    assert normalized.provenance_status == "partial"
