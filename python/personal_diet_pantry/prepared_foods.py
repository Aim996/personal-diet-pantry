"""Atomic cooked-leftover storage with immutable portion-total nutrition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import sqlite3
from typing import Mapping

from .nutrition import NutritionResult
from .pantry import PantryBatch, _add_batch_in_context
from .transactions import MutationContext


_NUTRIENT_FIELDS = (
    "calories_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "hydration_ml",
)
_RESULT_FIELDS = {
    "calories_kcal": "calories",
    "protein_g": "protein",
    "fat_g": "fat",
    "carbohydrate_g": "carbohydrate",
    "fiber_g": "fiber",
    "sodium_mg": "sodium",
    "hydration_ml": "hydration_ml",
}
_SOURCE_GRADES = frozenset({"A", "B", "C", "D", "unknown"})


class PreparedFoodValidationError(ValueError):
    """Raised when a cooked leftover cannot be stored or scaled safely."""


@dataclass(frozen=True)
class LeftoverDraft:
    food_name: str
    normalized_name: str
    quantity: Decimal
    unit: str
    storage_location: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class PreparedFood:
    batch: PantryBatch
    nutrition_basis: str
    nutrition: Mapping[str, str | None]
    source_grade: str


@dataclass(frozen=True)
class PreparedFoodReference:
    """An exact prepared batch selected through an opaque workflow handle."""

    batch_id: int
    profile_id: int
    version: int
    food_name: str
    normalized_name: str
    unit: str
    initial_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True)
class InventoryEffect:
    food_name: str
    direction: str
    quantity: Decimal
    unit: str
    remaining_quantity: Decimal | None
    cleared: bool
    storage_location: str | None
    prepared: bool


def load_prepared_food_reference(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    profile_id: int,
    expected_version: int,
) -> PreparedFoodReference:
    """Load one still-current prepared batch without fuzzy product matching."""

    row = connection.execute(
        """
        SELECT pb.id AS batch_id, pb.version, pb.food_name,
               pb.normalized_name, pb.unit, pb.initial_quantity,
               pb.remaining_quantity, pb.status, pfp.id AS profile_id,
               pfp.nutrition_basis
        FROM pantry_batches AS pb
        JOIN prepared_food_profiles AS pfp ON pfp.pantry_batch_id = pb.id
        WHERE pb.id = ? AND pfp.id = ?
        """,
        (batch_id, profile_id),
    ).fetchone()
    if (
        row is None
        or int(row["version"]) != expected_version
        or row["status"] not in {"active", "opened", "thawed"}
        or Decimal(str(row["remaining_quantity"])) <= 0
        or row["nutrition_basis"] != "portion_total"
    ):
        raise PreparedFoodValidationError(
            "prepared food reference is stale"
        )
    return PreparedFoodReference(
        batch_id=int(row["batch_id"]),
        profile_id=int(row["profile_id"]),
        version=int(row["version"]),
        food_name=str(row["food_name"]),
        normalized_name=str(row["normalized_name"]),
        unit=str(row["unit"]),
        initial_quantity=Decimal(str(row["initial_quantity"])),
        remaining_quantity=Decimal(str(row["remaining_quantity"])),
    )


def create_leftover_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    source_meal_id: int,
    draft: LeftoverDraft,
    consumed_nutrition: Mapping[str, Decimal | None],
    consumed_quantity: Decimal,
    committed_at: datetime,
    source_text: str,
    aliases: Mapping[str, str],
    source_grade: str = "B",
) -> PreparedFood:
    """Add one leftover batch and its nutrition in the caller's transaction."""

    if not isinstance(source_meal_id, int) or isinstance(source_meal_id, bool):
        raise PreparedFoodValidationError("source_meal_id must be an integer")
    if not isinstance(draft, LeftoverDraft):
        raise PreparedFoodValidationError("draft must be a LeftoverDraft")
    validate_leftover_expiry(draft, committed_at)
    consumed = _positive_decimal(consumed_quantity, "consumed_quantity")
    leftover = _positive_decimal(draft.quantity, "leftover quantity")
    food_name = _required_text(draft.food_name, "food_name")
    normalized_name = _required_text(
        draft.normalized_name, "normalized_name"
    ).lower()
    unit = _required_text(draft.unit, "unit")
    storage = _required_text(draft.storage_location, "storage_location")
    grade = _required_text(source_grade, "source_grade")
    if grade not in _SOURCE_GRADES:
        raise PreparedFoodValidationError("unsupported source_grade")
    ratio = leftover / consumed
    nutrition = {
        field: _scaled_text(consumed_nutrition.get(field), ratio, field)
        for field in _NUTRIENT_FIELDS
    }
    batch = _add_batch_in_context(
        context,
        food_name=food_name,
        normalized_name=normalized_name,
        quantity=leftover,
        unit=unit,
        added_at=committed_at,
        source_text=source_text,
        storage_location=storage,
        expires_at=draft.expires_at,
        source="prepared_leftover",
        aliases=aliases,
    )
    movement = connection.execute(
        "SELECT pantry_batch_id FROM pantry_movements WHERE id = last_insert_rowid()"
    ).fetchone()
    if movement is None:
        raise PreparedFoodValidationError("leftover batch movement is missing")
    batch_id = int(movement["pantry_batch_id"])
    context.update(
        "pantry_batches",
        batch_id,
        {"source_meal_id": source_meal_id},
    )
    context.insert(
        "prepared_food_profiles",
        {
            "pantry_batch_id": batch_id,
            "source_meal_id": source_meal_id,
            "nutrition_basis": "portion_total",
            "nutrition_json": _canonical_json(nutrition),
            "initial_quantity": float(leftover),
            "unit": unit,
            "source_grade": grade,
            "created_at": _utc_text(committed_at),
        },
    )
    return PreparedFood(batch, "portion_total", nutrition, grade)


def validate_leftover_expiry(
    draft: LeftoverDraft, committed_at: datetime
) -> None:
    if committed_at.tzinfo is None or committed_at.utcoffset() is None:
        raise PreparedFoodValidationError("committed_at must include a timezone")
    expires_at = draft.expires_at
    if expires_at is None:
        raise PreparedFoodValidationError("leftover expires_at is required")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise PreparedFoodValidationError(
            "leftover expires_at must include a timezone"
        )
    if expires_at.astimezone(timezone.utc) <= committed_at.astimezone(
        timezone.utc
    ):
        raise PreparedFoodValidationError(
            "leftover expires_at must be later than committed_at"
        )


def leftover_expiry_payload(value: datetime | None) -> str:
    if value is None:
        raise PreparedFoodValidationError("leftover expires_at is required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PreparedFoodValidationError(
            "leftover expires_at must include a timezone"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def calculate_prepared_nutrition(
    snapshot: Mapping[str, str | None],
    *,
    selected_quantity: Decimal,
    initial_quantity: Decimal,
    source_grade: str,
) -> NutritionResult:
    """Scale a whole-leftover snapshot by the selected inventory fraction."""

    selected = _nonnegative_decimal(selected_quantity, "selected_quantity")
    initial = _positive_decimal(initial_quantity, "initial_quantity")
    if selected > initial:
        raise PreparedFoodValidationError(
            "selected_quantity cannot exceed initial_quantity"
        )
    grade = _required_text(source_grade, "source_grade")
    if grade not in _SOURCE_GRADES:
        raise PreparedFoodValidationError("unsupported source_grade")
    ratio = selected / initial
    values = {
        _RESULT_FIELDS[field]: _scaled_decimal(snapshot.get(field), ratio, field)
        for field in _NUTRIENT_FIELDS
    }
    return NutritionResult(
        **values,
        source="prepared_leftover",
        source_grade=grade,
        uncertainty=(
            "Some nutrients in the prepared food are unknown"
            if any(snapshot.get(field) is None for field in _NUTRIENT_FIELDS)
            else None
        ),
    )


def inventory_effects_for_meal(
    connection: sqlite3.Connection, meal_id: int
) -> tuple[InventoryEffect, ...]:
    """Return only deductions and prepared additions caused by one meal."""

    rows = connection.execute(
        """
        SELECT pb.food_name, pm.quantity, pm.unit, pb.remaining_quantity,
               pb.status, pb.storage_location
        FROM pantry_movements AS pm
        JOIN pantry_batches AS pb ON pb.id = pm.pantry_batch_id
        WHERE pm.linked_meal_id = ? AND pm.movement_type = 'consume'
        ORDER BY pm.id
        """,
        (meal_id,),
    ).fetchall()
    effects = [
        InventoryEffect(
            food_name=row["food_name"],
            direction="decrease",
            quantity=_display_decimal(row["quantity"]),
            unit=row["unit"],
            remaining_quantity=_display_decimal(row["remaining_quantity"]),
            cleared=row["status"] == "consumed",
            storage_location=None,
            prepared=False,
        )
        for row in rows
    ]
    leftovers = connection.execute(
        """
        SELECT food_name, initial_quantity, unit, remaining_quantity,
               status, storage_location
        FROM pantry_batches
        WHERE source_meal_id = ?
        ORDER BY id
        """,
        (meal_id,),
    ).fetchall()
    effects.extend(
        InventoryEffect(
            food_name=row["food_name"],
            direction="increase",
            quantity=_display_decimal(row["initial_quantity"]),
            unit=row["unit"],
            remaining_quantity=_display_decimal(row["remaining_quantity"]),
            cleared=False,
            storage_location=row["storage_location"],
            prepared=True,
        )
        for row in leftovers
    )
    return tuple(effects)


def inventory_effects_for_transaction(
    connection: sqlite3.Connection, transaction_id: str
) -> tuple[InventoryEffect, ...]:
    """Return the net pantry effect of one committed meal correction.

    A correction may restore the old deduction and consume the corrected
    amount in the same transaction. Reporting only the new meal's deduction
    would therefore describe the opposite of the real stock change.
    """

    rows = connection.execute(
        """
        SELECT pm.pantry_batch_id, pm.movement_type, pm.quantity, pm.unit,
               pb.food_name, pb.remaining_quantity, pb.status,
               pb.storage_location,
               EXISTS(
                   SELECT 1 FROM prepared_food_profiles AS pfp
                   WHERE pfp.pantry_batch_id = pb.id
               ) AS prepared
        FROM pantry_movements AS pm
        JOIN pantry_batches AS pb ON pb.id = pm.pantry_batch_id
        WHERE pm.transaction_id = ?
          AND pm.movement_type IN ('add', 'consume', 'restore', 'adjust')
        ORDER BY pm.id
        """,
        (transaction_id,),
    ).fetchall()
    by_batch: dict[int, dict[str, object]] = {}
    for row in rows:
        batch_id = int(row["pantry_batch_id"])
        entry = by_batch.setdefault(
            batch_id,
            {
                "food_name": row["food_name"],
                "unit": row["unit"],
                "remaining_quantity": row["remaining_quantity"],
                "status": row["status"],
                "storage_location": row["storage_location"],
                "prepared": bool(row["prepared"]),
                "net": Decimal("0"),
            },
        )
        quantity = _display_decimal(row["quantity"])
        if row["movement_type"] in {"add", "restore"}:
            entry["net"] = Decimal(str(entry["net"])) + quantity
        else:
            entry["net"] = Decimal(str(entry["net"])) - quantity

    effects: list[InventoryEffect] = []
    for entry in by_batch.values():
        net = Decimal(str(entry["net"]))
        if net == 0:
            continue
        effects.append(
            InventoryEffect(
                food_name=str(entry["food_name"]),
                direction="increase" if net > 0 else "decrease",
                quantity=abs(net).normalize(),
                unit=str(entry["unit"]),
                remaining_quantity=_display_decimal(
                    entry["remaining_quantity"]
                ),
                cleared=entry["status"] == "consumed",
                storage_location=(
                    str(entry["storage_location"])
                    if entry["storage_location"] is not None
                    else None
                ),
                prepared=bool(entry["prepared"]),
            )
        )
    return tuple(effects)


def _scaled_text(
    value: Decimal | None, ratio: Decimal, field: str
) -> str | None:
    scaled = _scaled_decimal(value, ratio, field)
    if scaled is None:
        return None
    return format(scaled.normalize(), "f")


def _display_decimal(value: object) -> Decimal:
    number = _nonnegative_decimal(value, "stored quantity")
    return number.normalize() if number != 0 else Decimal("0")


def _scaled_decimal(
    value: Decimal | str | None, ratio: Decimal, field: str
) -> Decimal | None:
    if value is None:
        return None
    number = _nonnegative_decimal(value, field)
    return (number * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _positive_decimal(value: object, field: str) -> Decimal:
    number = _nonnegative_decimal(value, field)
    if number <= 0:
        raise PreparedFoodValidationError(f"{field} must be positive")
    return number


def _nonnegative_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PreparedFoodValidationError(f"{field} must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PreparedFoodValidationError(f"{field} must be numeric") from error
    if not number.is_finite() or number < 0:
        raise PreparedFoodValidationError(
            f"{field} must be finite and non-negative"
        )
    return number


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreparedFoodValidationError(f"{field} must be non-empty text")
    return value.strip()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(value: Mapping[str, str | None]) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
