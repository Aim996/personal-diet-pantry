"""Versioned packaging-label nutrition linked to concrete pantry batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import sqlite3
from typing import Any, Mapping

from .transactions import MutationContext, TransactionManager
from .nutrition import NutritionResult


class NutritionProfileValidationError(ValueError):
    """Raised when packaging-label nutrition cannot be stored safely."""


_SERVING_BASES = frozenset({"per_100g", "per_100ml", "per_serving"})
_SOURCE_GRADES = frozenset({"A", "B", "C", "D", "unknown"})
_NUTRIENT_FIELDS = frozenset(
    {
        "energy_kj",
        "calories_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "sodium_mg",
        "fiber_g",
        "sugar_g",
        "saturated_fat_g",
        "hydration_ml",
    }
)


@dataclass(frozen=True)
class NutritionProfileDraft:
    normalized_name: str
    brand: str
    product_key: str
    serving_basis: str
    nutrition: Mapping[str, Decimal | int | str | None]
    source_text: str
    source_grade: str


@dataclass(frozen=True)
class NutritionProfile:
    normalized_name: str
    brand: str
    product_key: str
    serving_basis: str
    nutrition: Mapping[str, str | None]
    source_text: str
    source_grade: str
    profile_version: int
    created_at: datetime


@dataclass(frozen=True)
class LinkedNutritionProjection:
    status: str
    serving_basis: str | None
    source_grade: str | None
    nutrition: Mapping[str, str | None] | None


def create_and_link_profile(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    pantry_batch_id: int,
    draft: NutritionProfileDraft,
    linked_at: datetime,
) -> NutritionProfile:
    """Create one profile version and atomically link its immutable snapshot."""

    if not isinstance(pantry_batch_id, int) or isinstance(pantry_batch_id, bool):
        raise NutritionProfileValidationError("pantry_batch_id must be an integer")
    return manager.execute(
        "pantry_adjust",
        draft.source_text,
        lambda context: _create_and_link_in_context(
            connection,
            context,
            pantry_batch_id=pantry_batch_id,
            draft=draft,
            linked_at=linked_at,
        ),
    ).value


def _create_and_link_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    pantry_batch_id: int,
    draft: NutritionProfileDraft,
    linked_at: datetime,
) -> NutritionProfile:
    """Internal journal-aware implementation used by service commit workflows."""

    normalized = _validated_draft(draft)
    linked_text = _utc_text(linked_at)
    batch = connection.execute(
        "SELECT id FROM pantry_batches WHERE id = ?", (pantry_batch_id,)
    ).fetchone()
    if batch is None:
        raise KeyError("No pantry batch matches the nutrition profile")
    version = connection.execute(
        """
        SELECT COALESCE(MAX(profile_version), 0) + 1
        FROM nutrition_profiles
        WHERE normalized_name = ? AND brand = ? AND product_key = ?
        """,
        (
            normalized.normalized_name,
            normalized.brand,
            normalized.product_key,
        ),
    ).fetchone()[0]
    nutrition_json = _canonical_json(normalized.nutrition)
    profile_row = context.insert(
        "nutrition_profiles",
        {
            "normalized_name": normalized.normalized_name,
            "brand": normalized.brand,
            "product_key": normalized.product_key,
            "serving_basis": normalized.serving_basis,
            "nutrition_json": nutrition_json,
            "source_text": normalized.source_text,
            "source_grade": normalized.source_grade,
            "profile_version": version,
            "created_at": linked_text,
        },
    )
    existing_link = connection.execute(
        "SELECT id FROM pantry_nutrition_links WHERE pantry_batch_id = ?",
        (pantry_batch_id,),
    ).fetchone()
    link_values = {
        "pantry_batch_id": pantry_batch_id,
        "nutrition_profile_id": profile_row["id"],
        "nutrition_snapshot_json": nutrition_json,
        "linked_at": linked_text,
    }
    if existing_link is None:
        context.insert("pantry_nutrition_links", link_values)
    else:
        context.update("pantry_nutrition_links", existing_link["id"], link_values)
    return NutritionProfile(
        normalized.normalized_name,
        normalized.brand,
        normalized.product_key,
        normalized.serving_basis,
        normalized.nutrition,
        normalized.source_text,
        normalized.source_grade,
        version,
        linked_at.astimezone(timezone.utc),
    )


def linked_snapshot(
    connection: sqlite3.Connection,
    pantry_batch_id: int,
) -> dict[str, str | None] | None:
    """Return a defensive copy of the nutrition snapshot for one batch."""

    row = connection.execute(
        """
        SELECT nutrition_snapshot_json
        FROM pantry_nutrition_links
        WHERE pantry_batch_id = ?
        """,
        (pantry_batch_id,),
    ).fetchone()
    if row is None:
        return None
    value = json.loads(row["nutrition_snapshot_json"])
    if not isinstance(value, dict):
        raise NutritionProfileValidationError(
            "stored nutrition snapshot must be an object"
        )
    return dict(value)


def linked_product_nutrition(
    connection: sqlite3.Connection,
    *,
    normalized_name: str,
    unit: str,
    statuses: tuple[str, ...],
    batch_ids: tuple[int, ...] | None = None,
) -> LinkedNutritionProjection:
    """Classify immutable nutrition labels across eligible product batches."""

    name = _required_text(normalized_name, "normalized_name")
    normalized_unit = _required_text(unit, "unit")
    if not statuses:
        raise NutritionProfileValidationError("statuses must not be empty")
    normalized_statuses = tuple(
        _required_text(status, "statuses") for status in statuses
    )
    placeholders = ", ".join("?" for _ in normalized_statuses)
    batch_clause = ""
    batch_values: tuple[int, ...] = ()
    if batch_ids is not None:
        if not batch_ids:
            return LinkedNutritionProjection("none", None, None, None)
        batch_clause = (
            f" AND batches.id IN ({', '.join('?' for _ in batch_ids)})"
        )
        batch_values = batch_ids
    rows = connection.execute(
        f"""
        SELECT
            links.nutrition_snapshot_json,
            profiles.serving_basis,
            profiles.source_grade
        FROM pantry_batches AS batches
        LEFT JOIN pantry_nutrition_links AS links
          ON links.pantry_batch_id = batches.id
        LEFT JOIN nutrition_profiles AS profiles
          ON profiles.id = links.nutrition_profile_id
        WHERE lower(batches.normalized_name) = lower(?)
          AND lower(batches.unit) = lower(?)
          AND batches.status IN ({placeholders})
          AND batches.remaining_quantity > 0
          {batch_clause}
        ORDER BY batches.id
        """,
        (name, normalized_unit, *normalized_statuses, *batch_values),
    ).fetchall()
    if not rows:
        return LinkedNutritionProjection("none", None, None, None)

    linked = tuple(
        row for row in rows if row["nutrition_snapshot_json"] is not None
    )
    if not linked:
        return LinkedNutritionProjection("none", None, None, None)
    if len(linked) != len(rows):
        return LinkedNutritionProjection("partial", None, None, None)

    canonical: list[tuple[str, str, str, dict[str, str | None]]] = []
    for row in linked:
        try:
            snapshot = json.loads(row["nutrition_snapshot_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise NutritionProfileValidationError(
                "stored nutrition snapshot must be valid JSON"
            ) from error
        if not isinstance(snapshot, dict):
            raise NutritionProfileValidationError(
                "stored nutrition snapshot must be an object"
            )
        canonical.append(
            (
                _canonical_json(snapshot),
                str(row["serving_basis"]),
                str(row["source_grade"]),
                dict(snapshot),
            )
        )

    first = canonical[0]
    if any(item[:3] != first[:3] for item in canonical[1:]):
        return LinkedNutritionProjection("mixed", None, None, None)
    return LinkedNutritionProjection("uniform", first[1], first[2], first[3])


def calculate_snapshot_nutrition(
    snapshot: Mapping[str, str | None],
    consumed_amount: Decimal,
    *,
    source_grade: str,
    serving_basis: str = "per_100g",
) -> NutritionResult:
    """Scale one packaging-label snapshot using its declared serving basis."""

    amount = _nonnegative_decimal(consumed_amount, "consumed_amount")
    if serving_basis not in _SERVING_BASES:
        raise NutritionProfileValidationError("unsupported serving_basis")
    multiplier = (
        amount
        if serving_basis == "per_serving"
        else amount / Decimal("100")
    )

    def scaled(field: str) -> Decimal | None:
        value = snapshot.get(field)
        if value is None:
            return None
        return (Decimal(value) * multiplier).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    return NutritionResult(
        calories=scaled("calories_kcal"),
        protein=scaled("protein_g"),
        fat=scaled("fat_g"),
        carbohydrate=scaled("carbohydrate_g"),
        fiber=scaled("fiber_g"),
        sodium=scaled("sodium_mg"),
        source="packaging_label",
        source_grade=source_grade,
        uncertainty=(
            "Some nutrients were not listed on the packaging label"
            if any(
                snapshot.get(field) is None
                for field in (
                    "calories_kcal",
                    "protein_g",
                    "fat_g",
                    "carbohydrate_g",
                    "fiber_g",
                    "sodium_mg",
                )
            )
            else None
        ),
        hydration_ml=scaled("hydration_ml"),
    )


def _validated_draft(draft: NutritionProfileDraft) -> NutritionProfile:
    if not isinstance(draft, NutritionProfileDraft):
        raise NutritionProfileValidationError(
            "draft must be a NutritionProfileDraft"
        )
    name = _required_text(draft.normalized_name, "normalized_name").lower()
    brand = _optional_text(draft.brand)
    product_key = _optional_text(draft.product_key)
    serving_basis = _required_text(draft.serving_basis, "serving_basis")
    if serving_basis not in _SERVING_BASES:
        raise NutritionProfileValidationError("unsupported serving_basis")
    grade = _required_text(draft.source_grade, "source_grade")
    if grade not in _SOURCE_GRADES:
        raise NutritionProfileValidationError("unsupported source_grade")
    if not isinstance(draft.nutrition, Mapping):
        raise NutritionProfileValidationError("nutrition must be an object")
    unknown = set(draft.nutrition) - _NUTRIENT_FIELDS
    if unknown:
        raise NutritionProfileValidationError(
            f"unknown nutrition field: {sorted(unknown)[0]}"
        )
    nutrition = {
        field: _optional_nutrient(draft.nutrition.get(field), field)
        for field in sorted(_NUTRIENT_FIELDS)
        if field in draft.nutrition
    }
    if not any(value is not None for value in nutrition.values()):
        raise NutritionProfileValidationError(
            "at least one nutrition value is required"
        )
    if nutrition.get("calories_kcal") is None and nutrition.get("energy_kj") is not None:
        calories = (
            Decimal(nutrition["energy_kj"]) / Decimal("4.184")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        nutrition["calories_kcal"] = _decimal_text(calories)
    return NutritionProfile(
        name,
        brand,
        product_key,
        serving_basis,
        nutrition,
        _required_text(draft.source_text, "source_text"),
        grade,
        0,
        datetime.min.replace(tzinfo=timezone.utc),
    )


def _optional_nutrient(
    value: Decimal | int | str | None,
    field: str,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise NutritionProfileValidationError(f"{field} must be numeric or null")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise NutritionProfileValidationError(
            f"{field} must be numeric or null"
        ) from error
    if not number.is_finite() or number < 0:
        raise NutritionProfileValidationError(
            f"{field} must be a non-negative finite number"
        )
    return _decimal_text(number)


def _nonnegative_decimal(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise NutritionProfileValidationError(
            f"{field} must be a non-negative finite Decimal"
        )
    return value


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _canonical_json(value: Mapping[str, str | None]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NutritionProfileValidationError(f"{field} must be non-empty text")
    return value.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NutritionProfileValidationError("optional text fields must be text")
    return value.strip()


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise NutritionProfileValidationError("linked_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
