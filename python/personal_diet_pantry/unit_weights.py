"""Decimal-safe conversion between counted inventory and nutrition weight."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class UnitWeightValidationError(ValueError):
    """Raised when count-to-weight metadata cannot be used safely."""


_OUTPUT_QUANTUM = Decimal("0.001")


def derive_average_unit_weight(
    quantity: Decimal,
    total_weight_g: Decimal,
) -> Decimal:
    """Return grams per counted unit from a positive quantity and total weight."""

    count = _positive_decimal(quantity, "quantity")
    total = _positive_decimal(total_weight_g, "total_weight_g")
    return _rounded(total / count)


def consumed_weight_g(
    count: Decimal,
    average_unit_weight_g: Decimal,
    edible_ratio: Decimal | None = None,
) -> Decimal:
    """Convert a counted portion to grams, optionally applying an edible ratio."""

    weight = _positive_decimal(count, "count") * _positive_decimal(
        average_unit_weight_g, "average_unit_weight_g"
    )
    if edible_ratio is not None:
        weight *= _ratio(edible_ratio, "edible_ratio")
    return _rounded(weight)


def _positive_decimal(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise UnitWeightValidationError(f"{field} must be a positive finite Decimal")
    return value


def _ratio(value: Decimal, field: str) -> Decimal:
    ratio = _positive_decimal(value, field)
    if ratio > 1:
        raise UnitWeightValidationError(f"{field} must be no greater than 1")
    return ratio


def _rounded(value: Decimal) -> Decimal:
    try:
        return value.quantize(_OUTPUT_QUANTUM, rounding=ROUND_HALF_UP).normalize()
    except InvalidOperation as error:
        raise UnitWeightValidationError(
            "calculated weight is outside the supported decimal range"
        ) from error
