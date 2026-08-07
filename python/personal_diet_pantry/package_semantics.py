"""Deterministic package facts layered over canonical pantry quantities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any


class PackageSemanticError(ValueError):
    """Raised when package facts are incomplete or internally inconsistent."""


@dataclass(frozen=True)
class PackageSpec:
    """Persisted package facts for one pantry batch."""

    initial_display_quantity: Decimal
    display_unit: str
    base_quantity_per_display_unit: Decimal
    package_hierarchy: tuple[Mapping[str, str], ...] = ()


def package_spec(
    *,
    display_quantity: Decimal | str | int | float | None,
    display_unit: str | None,
    base_quantity_per_display_unit: Decimal | str | int | float | None,
    package_hierarchy: Sequence[Mapping[str, Any]] | None = None,
) -> PackageSpec | None:
    """Build a normalized spec, rejecting partial package facts."""

    supplied = (
        display_quantity is not None,
        display_unit is not None,
        base_quantity_per_display_unit is not None,
        package_hierarchy is not None,
    )
    if not any(supplied):
        return None
    if not all(supplied[:3]):
        raise PackageSemanticError(
            "display_quantity, display_unit, and "
            "base_quantity_per_display_unit must be supplied together"
        )
    normalized_unit = _text(display_unit, "display_unit")
    if len(normalized_unit) > 40:
        raise PackageSemanticError("display_unit must contain at most 40 characters")
    return PackageSpec(
        initial_display_quantity=_positive_decimal(
            display_quantity, "display_quantity"
        ),
        display_unit=normalized_unit,
        base_quantity_per_display_unit=_positive_decimal(
            base_quantity_per_display_unit,
            "base_quantity_per_display_unit",
        ),
        package_hierarchy=_hierarchy(package_hierarchy),
    )


def validate_package_spec(
    *,
    base_quantity: Decimal,
    spec: PackageSpec | None,
) -> PackageSpec | None:
    """Require the package product to equal the canonical initial quantity."""

    if spec is None:
        return None
    expected = (
        spec.initial_display_quantity
        * spec.base_quantity_per_display_unit
    )
    if expected != _finite_decimal(base_quantity, "base_quantity"):
        raise PackageSemanticError(
            "base quantity conflicts with package specification"
        )
    return spec


def to_base_quantity(
    quantity: Decimal,
    unit: str,
    *,
    base_unit: str,
    spec: PackageSpec | None,
) -> tuple[Decimal, str]:
    """Convert either the canonical unit or the stored display unit."""

    amount = _positive_decimal(quantity, "quantity")
    normalized_unit = _text(unit, "unit")
    normalized_base_unit = _text(base_unit, "base_unit")
    if normalized_unit.casefold() == normalized_base_unit.casefold():
        return amount, normalized_base_unit
    if (
        spec is not None
        and normalized_unit.casefold() == spec.display_unit.casefold()
    ):
        return (
            amount * spec.base_quantity_per_display_unit,
            normalized_base_unit,
        )
    raise PackageSemanticError("inventory unit cannot be converted")


def remaining_display_quantity(
    remaining_quantity: Decimal,
    *,
    spec: PackageSpec | None,
) -> Decimal | None:
    """Derive display quantity so only one remaining amount is persisted."""

    if spec is None:
        return None
    amount = _finite_decimal(remaining_quantity, "remaining_quantity")
    if amount < 0:
        raise PackageSemanticError("remaining_quantity cannot be negative")
    result = amount / spec.base_quantity_per_display_unit
    display_quantum = Decimal(1).scaleb(
        spec.initial_display_quantity.as_tuple().exponent
    )
    quantized = result.quantize(display_quantum)
    return quantized if quantized == result else result


def _hierarchy(
    value: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, str], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value, Sequence
    ):
        raise PackageSemanticError("package_hierarchy must be an array")
    normalized: list[Mapping[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or not item:
            raise PackageSemanticError(
                f"package_hierarchy[{index}] must be a non-empty object"
            )
        row: dict[str, str] = {}
        for key, raw in item.items():
            normalized_key = _text(key, "package_hierarchy key")
            if len(normalized_key) > 40:
                raise PackageSemanticError(
                    "package_hierarchy keys must contain at most 40 characters"
                )
            normalized_value = _text(raw, f"package_hierarchy[{index}]")
            if len(normalized_value) > 80:
                raise PackageSemanticError(
                    "package_hierarchy values must contain at most 80 characters"
                )
            row[normalized_key] = normalized_value
        normalized.append(MappingProxyType(row))
    return tuple(normalized)


def _positive_decimal(value: Any, field: str) -> Decimal:
    number = _finite_decimal(value, field)
    if number <= 0:
        raise PackageSemanticError(f"{field} must be positive")
    return number


def _finite_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PackageSemanticError(f"{field} must be a finite number")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PackageSemanticError(f"{field} must be a finite number") from error
    if not number.is_finite():
        raise PackageSemanticError(f"{field} must be a finite number")
    return number


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageSemanticError(f"{field} must be non-empty text")
    return value.strip()
