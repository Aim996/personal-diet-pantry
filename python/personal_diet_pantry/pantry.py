"""Journaled pantry batches with deterministic, non-negative deduction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import json
import math
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from . import costs, waste
from .clock import utc_text
from .inventory_order import deduction_order_sql, normalized_deduction_strategy
from .package_semantics import (
    PackageSpec,
    package_spec,
    remaining_display_quantity,
    to_base_quantity,
    validate_package_spec,
)
from .transactions import MutationContext, TransactionManager
from .unit_weights import UnitWeightValidationError, derive_average_unit_weight


ACTIVE = "active"
OPENED = "opened"
FROZEN = "frozen"
THAWED = "thawed"
DISCARDED = "discarded"
EXPIRED = "expired"
CONSUMED = "consumed"
INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"


class PantryValidationError(ValueError):
    """Raised when pantry input or a requested state change is invalid."""


class InsufficientStockError(PantryValidationError):
    """Raised when the complete eligible selection cannot cover a deduction."""

    code = INSUFFICIENT_STOCK


class PantryReferenceStaleError(PantryValidationError):
    """Raised when a persisted batch reference no longer matches its row."""


class BatchSelector(StrEnum):
    """Explicit user choices that replace the normal opened/FEFO/FIFO order."""

    COLD_STORAGE = "cold_storage"
    FROZEN = "frozen"
    NEWEST = "newest"


@dataclass(frozen=True)
class PantryBatch:
    """A user-facing batch record; persistence and journal identifiers stay private."""

    food_name: str
    normalized_name: str
    batch_code: str | None
    storage_location: str | None
    purchase_date: str | None
    added_at: datetime
    opened_at: datetime | None
    expires_at: datetime | None
    initial_quantity: Decimal
    remaining_quantity: Decimal
    unit: str
    initial_display_quantity: Decimal | None
    display_unit: str | None
    base_quantity_per_display_unit: Decimal | None
    package_hierarchy: tuple[Mapping[str, str], ...] | None
    price: Decimal | None
    price_minor: int | None
    currency: str | None
    remaining_cost_minor: int | None
    status: str
    source: str
    notes: str | None
    total_weight_g: Decimal | None
    average_unit_weight_g: Decimal | None
    weight_basis: str | None
    weight_source: str | None
    weight_confidence: str | None
    version: int

    @property
    def remaining_display_quantity(self) -> Decimal | None:
        return remaining_display_quantity(
            self.remaining_quantity,
            spec=self.package_spec,
        )

    @property
    def package_spec(self) -> PackageSpec | None:
        if self.initial_display_quantity is None:
            return None
        assert self.display_unit is not None
        assert self.base_quantity_per_display_unit is not None
        return PackageSpec(
            initial_display_quantity=self.initial_display_quantity,
            display_unit=self.display_unit,
            base_quantity_per_display_unit=self.base_quantity_per_display_unit,
            package_hierarchy=self.package_hierarchy or (),
        )


@dataclass(frozen=True)
class BatchSelectionLine:
    """One public line in a deterministic selection or completed deduction."""

    batch_code: str | None
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class BatchSelection:
    normalized_name: str
    required_quantity: Decimal
    unit: str
    lines: tuple[BatchSelectionLine, ...]


def add_batch(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    food_name: str,
    normalized_name: str | None = None,
    quantity: Decimal,
    unit: str,
    added_at: datetime,
    source_text: str,
    batch_code: str | None = None,
    storage_location: str | None = None,
    purchase_date: str | None = None,
    expires_at: datetime | None = None,
    price: Decimal | None = None,
    price_minor: int | None = None,
    currency: str | None = None,
    source: str = "manual",
    notes: str | None = None,
    total_weight_g: Decimal | None = None,
    average_unit_weight_g: Decimal | None = None,
    weight_basis: str | None = None,
    weight_source: str | None = None,
    weight_confidence: str | None = None,
    initial_display_quantity: Decimal | None = None,
    display_unit: str | None = None,
    base_quantity_per_display_unit: Decimal | None = None,
    package_hierarchy: Sequence[Mapping[str, Any]] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> PantryBatch:
    """Add one positive batch and its matching ``add`` movement atomically."""

    result = manager.execute(
        "pantry_add",
        _source_text(source_text),
        lambda context: _add_batch_in_context(
            context,
            food_name=food_name,
            normalized_name=normalized_name,
            quantity=quantity,
            unit=unit,
            added_at=added_at,
            source_text=source_text,
            batch_code=batch_code,
            storage_location=storage_location,
            purchase_date=purchase_date,
            expires_at=expires_at,
            price=price,
            price_minor=price_minor,
            currency=currency,
            source=source,
            notes=notes,
            total_weight_g=total_weight_g,
            average_unit_weight_g=average_unit_weight_g,
            weight_basis=weight_basis,
            weight_source=weight_source,
            weight_confidence=weight_confidence,
            initial_display_quantity=initial_display_quantity,
            display_unit=display_unit,
            base_quantity_per_display_unit=base_quantity_per_display_unit,
            package_hierarchy=package_hierarchy,
            aliases=aliases,
        ),
    )
    return result.value


def _add_batch_in_context(
    context: MutationContext,
    **kwargs: object,
) -> PantryBatch:
    """Compatibility wrapper returning only the public batch."""

    return _add_batch_record_in_context(context, **kwargs)[1]


def _add_batch_record_in_context(
    context: MutationContext,
    *,
    food_name: str,
    normalized_name: str | None = None,
    quantity: Decimal,
    unit: str,
    added_at: datetime,
    source_text: str,
    batch_code: str | None = None,
    storage_location: str | None = None,
    purchase_date: str | None = None,
    expires_at: datetime | None = None,
    price: Decimal | None = None,
    price_minor: int | None = None,
    currency: str | None = None,
    source: str = "manual",
    notes: str | None = None,
    total_weight_g: Decimal | None = None,
    average_unit_weight_g: Decimal | None = None,
    weight_basis: str | None = None,
    weight_source: str | None = None,
    weight_confidence: str | None = None,
    initial_display_quantity: Decimal | None = None,
    display_unit: str | None = None,
    base_quantity_per_display_unit: Decimal | None = None,
    package_hierarchy: Sequence[Mapping[str, Any]] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> tuple[int, PantryBatch]:
    """Validate and add one batch, retaining its identity inside the domain layer."""

    if expires_at is None:
        raise PantryValidationError("expires_at is required")
    normalized_added_at = _timestamp(added_at)
    normalized_expires_at = _validated_expiry_timestamp(expires_at, added_at)
    name = _canonical_name(food_name, normalized_name, aliases)
    amount = _positive_quantity(quantity)
    _sqlite_real(amount, "quantity")
    normalized_unit = _unit(unit)
    normalized_package = validate_package_spec(
        base_quantity=amount,
        spec=package_spec(
            display_quantity=initial_display_quantity,
            display_unit=display_unit,
            base_quantity_per_display_unit=base_quantity_per_display_unit,
            package_hierarchy=package_hierarchy,
        ),
    )
    (
        normalized_total_weight,
        normalized_average_weight,
        normalized_weight_basis,
        normalized_weight_source,
        normalized_weight_confidence,
    ) = _weight_metadata(
        quantity=amount,
        unit=normalized_unit,
        total_weight_g=total_weight_g,
        average_unit_weight_g=average_unit_weight_g,
        weight_basis=weight_basis,
        weight_source=weight_source,
        weight_confidence=weight_confidence,
        source_text=source_text,
    )
    (
        normalized_price_minor,
        normalized_currency,
        normalized_remaining_cost,
    ) = costs.structured_price(price_minor, currency)
    row = _add_batch(
        context,
        food_name=food_name.strip(),
        normalized_name=name,
        quantity=amount,
        unit=normalized_unit,
        added_at=normalized_added_at,
        source_text=_source_text(source_text),
        batch_code=_optional_text(batch_code),
        storage_location=_optional_text(storage_location),
        purchase_date=_optional_text(purchase_date),
        expires_at=normalized_expires_at,
        price=_optional_decimal(price, "price"),
        price_minor=normalized_price_minor,
        currency=normalized_currency,
        remaining_cost_minor=normalized_remaining_cost,
        source=_required_text(source, "source"),
        notes=_optional_text(notes),
        total_weight_g=(
            _sqlite_real(normalized_total_weight, "total_weight_g")
            if normalized_total_weight is not None
            else None
        ),
        average_unit_weight_g=(
            _sqlite_real(normalized_average_weight, "average_unit_weight_g")
            if normalized_average_weight is not None
            else None
        ),
        weight_basis=normalized_weight_basis,
        weight_source=normalized_weight_source,
        weight_confidence=normalized_weight_confidence,
        initial_display_quantity=(
            _sqlite_real(
                normalized_package.initial_display_quantity,
                "initial_display_quantity",
            )
            if normalized_package is not None
            else None
        ),
        display_unit=(
            normalized_package.display_unit
            if normalized_package is not None
            else None
        ),
        base_quantity_per_display_unit=(
            _sqlite_real(
                normalized_package.base_quantity_per_display_unit,
                "base_quantity_per_display_unit",
            )
            if normalized_package is not None
            else None
        ),
        package_hierarchy_json=(
            json.dumps(
                [dict(item) for item in normalized_package.package_hierarchy],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if normalized_package is not None
            and normalized_package.package_hierarchy
            else None
        ),
    )
    return int(row["id"]), _batch(row)


def query_batches(
    connection: sqlite3.Connection,
    *,
    normalized_name: str | None = None,
    statuses: tuple[str, ...] | None = None,
    missing_expiry_only: bool = False,
) -> tuple[PantryBatch, ...]:
    """Return public batch data in stable added-time order."""

    return tuple(
        batch
        for _, batch in _query_batch_targets(
            connection,
            normalized_name=normalized_name,
            statuses=statuses,
            missing_expiry_only=missing_expiry_only,
        )
    )


def _query_batch_targets(
    connection: sqlite3.Connection,
    *,
    normalized_name: str | None = None,
    statuses: tuple[str, ...] | None = None,
    missing_expiry_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[tuple[int, PantryBatch], ...]:
    """Return internal row targets paired with their public batch views."""

    clauses: list[str] = []
    values: list[object] = []
    if normalized_name is not None:
        clauses.append("normalized_name = ?")
        values.append(_required_text(normalized_name, "normalized_name").lower())
    if statuses is not None:
        if not statuses:
            return ()
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        values.extend(statuses)
    if missing_expiry_only:
        clauses.append("expires_at IS NULL")
        eligible = (ACTIVE, OPENED, FROZEN, THAWED)
        clauses.append(f"status IN ({', '.join('?' for _ in eligible)})")
        values.extend(eligible)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    pagination = ""
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise PantryValidationError("limit must be a positive integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PantryValidationError("offset must be a non-negative integer")
        pagination = " LIMIT ? OFFSET ?"
        values.extend((limit, offset))
    rows = connection.execute(
        f"SELECT * FROM pantry_batches {where} "
        f"ORDER BY added_at, id{pagination}",
        values,
    ).fetchall()
    return tuple((int(row["id"]), _batch(row)) for row in rows)


def select_batches(
    connection: sqlite3.Connection,
    normalized_name: str,
    required_quantity: Decimal,
    *,
    selector: BatchSelector | str | None = None,
    unit: str | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    """Preview a full selection while holding the immediate transaction lock."""

    name = _required_text(normalized_name, "normalized_name").lower()
    amount = _positive_quantity(required_quantity)
    _sqlite_real(amount, "required_quantity")
    expected_unit = _unit(unit) if unit is not None else None
    strategy = _strategy(deduction_strategy)
    if connection.in_transaction:
        return _selection(
            connection, name, amount, _selector(selector), expected_unit, strategy
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        selection = _selection(
            connection, name, amount, _selector(selector), expected_unit, strategy
        )
    finally:
        connection.rollback()
    return selection


def deduct_inventory(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    normalized_name: str,
    quantity: Decimal,
    unit: str,
    source_text: str,
    selector: BatchSelector | str | None = None,
    reason: str | None = None,
    linked_meal_id: int | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    """Deduct all selected batches, or atomically leave stock untouched."""

    return manager.execute(
        "pantry_deduct",
        _source_text(source_text),
        lambda context: _deduct_inventory_in_context(
            connection,
            context,
            normalized_name=normalized_name,
            quantity=quantity,
            unit=unit,
            source_text=source_text,
            selector=selector,
            reason=reason,
            linked_meal_id=linked_meal_id,
            deduction_strategy=deduction_strategy,
        ),
    ).value


def _deduct_inventory_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    normalized_name: str,
    quantity: Decimal,
    unit: str,
    source_text: str,
    selector: BatchSelector | str | None = None,
    reason: str | None = None,
    linked_meal_id: int | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    """Validate and deduct inventory inside a caller-owned journal transaction."""

    return _reduce_inventory_in_context(
        connection,
        context,
        normalized_name=normalized_name,
        quantity=quantity,
        unit=unit,
        movement_type="consume",
        source_text=source_text,
        reason=reason,
        linked_meal_id=linked_meal_id,
        selector=selector,
        deduction_strategy=deduction_strategy,
    )


def reduce_inventory(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    normalized_name: str,
    quantity: Decimal,
    unit: str,
    movement_type: Literal["consume", "discard"],
    source_text: str,
    reason: str | None = None,
    waste_category: str | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    """Reduce one product across batches in one journal transaction."""

    transaction_type = (
        "pantry_adjust" if movement_type == "discard" else "pantry_deduct"
    )
    return manager.execute(
        transaction_type,
        _source_text(source_text),
        lambda context: _reduce_inventory_in_context(
            connection,
            context,
            normalized_name=normalized_name,
            quantity=quantity,
            unit=unit,
            movement_type=movement_type,
            source_text=source_text,
            reason=reason,
            waste_category=waste_category,
            deduction_strategy=deduction_strategy,
        ),
    ).value


def _reduce_inventory_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    normalized_name: str,
    quantity: Decimal,
    unit: str,
    movement_type: Literal["consume", "discard"],
    source_text: str,
    reason: str | None,
    linked_meal_id: int | None = None,
    waste_category: str | None = None,
    selector: BatchSelector | str | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    """Apply a complete cross-batch reduction inside a caller transaction."""

    if movement_type not in {"consume", "discard"}:
        raise PantryValidationError("inventory movement type is invalid")

    name = _required_text(normalized_name, "normalized_name").lower()
    amount = _positive_quantity(quantity)
    _sqlite_real(amount, "quantity")
    wanted_unit = _unit(unit)
    chosen_selector = _selector(selector)
    strategy = _strategy(deduction_strategy)
    source = _source_text(source_text)
    selection = _selection(
        connection,
        name,
        amount,
        chosen_selector,
        expected_unit=wanted_unit,
        deduction_strategy=strategy,
    )
    for batch_id, line in _selected_rows(
        connection, name, amount, chosen_selector, wanted_unit, strategy
    ):
        row = connection.execute(
            "SELECT * FROM pantry_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:  # Defensive: selection and update share the caller transaction.
            raise PantryValidationError("selected batch no longer exists")
        remaining = _decimal(row["remaining_quantity"]) - line.quantity
        terminal_status = (
            DISCARDED if movement_type == "discard" else CONSUMED
        )
        status = terminal_status if remaining == Decimal("0") else row["status"]
        allocation = costs.allocation_for_reduction(row, line.quantity)
        changes: dict[str, object] = {
            "remaining_quantity": _sqlite_real(
                remaining, "remaining_quantity"
            ),
            "status": status,
            "version": row["version"] + 1,
        }
        if allocation is not None:
            changes["remaining_cost_minor"] = allocation.remaining_cost_minor
        context.update(
            "pantry_batches",
            batch_id,
            changes,
        )
        movement = _movement(
            context,
            batch_id,
            movement_type,
            line.quantity,
            wanted_unit,
            reason or source,
            linked_meal_id if movement_type == "consume" else None,
            (
                waste.normalize_category(waste_category)
                if movement_type == "discard"
                else None
            ),
        )
        costs.record_allocation(
            context,
            batch_id=batch_id,
            movement_id=int(movement["id"]),
            allocation_kind=(
                "waste" if movement_type == "discard" else "consume"
            ),
            quantity=line.quantity,
            unit=wanted_unit,
            allocation=allocation,
            allocated_at=str(movement["created_at"]),
        )
    return selection


def adjust_batch(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    batch_code: str | None = None,
    quantity: Decimal,
    source_text: str,
    reason: str | None = None,
    _batch_id: int | None = None,
    _expected_version: int | None = None,
) -> PantryBatch:
    """Set a batch's remaining stock to a non-negative measured quantity."""

    desired = _nonnegative_quantity(quantity)
    _sqlite_real(desired, "quantity")

    def mutate(context: MutationContext) -> sqlite3.Row:
        row = _batch_target(
            connection, batch_code, _batch_id, _expected_version
        )
        new_status = (
            CONSUMED if desired == Decimal("0") else _restored_status(row["status"])
        )
        current_quantity = _decimal(row["remaining_quantity"])
        reduction = max(current_quantity - desired, Decimal("0"))
        allocation = (
            costs.allocation_for_reduction(row, reduction)
            if reduction > 0
            else None
        )
        changes: dict[str, object] = {
            "remaining_quantity": _sqlite_real(
                desired, "remaining_quantity"
            ),
            "status": new_status,
            "version": row["version"] + 1,
        }
        if allocation is not None:
            changes["remaining_cost_minor"] = allocation.remaining_cost_minor
        updated = context.update(
            "pantry_batches",
            row["id"],
            changes,
        )
        movement = _movement(
            context,
            row["id"],
            "adjust",
            abs(desired - current_quantity),
            row["unit"],
            reason or source_text,
            None,
        )
        if reduction > 0:
            costs.record_allocation(
                context,
                batch_id=int(row["id"]),
                movement_id=int(movement["id"]),
                allocation_kind="adjustment",
                quantity=reduction,
                unit=str(row["unit"]),
                allocation=allocation,
                allocated_at=str(movement["created_at"]),
            )
        return updated

    return _batch(
        manager.execute("pantry_adjust", _source_text(source_text), mutate).value
    )


def discard_batch(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    batch_code: str | None = None,
    discarded_at: datetime,
    source_text: str,
    reason: str | None = None,
    waste_category: str | None = None,
    _batch_id: int | None = None,
    _expected_version: int | None = None,
) -> PantryBatch:
    """Discard all remaining stock and journal the discarded quantity."""

    _timestamp(discarded_at)  # Validate caller timestamp even though the schema has no discarded_at column.
    return _change_state(
        connection,
        manager,
        batch_code,
        DISCARDED,
        "discard",
        source_text,
        reason,
        batch_id=_batch_id,
        expected_version=_expected_version,
        waste_category=waste.normalize_category(waste_category),
    )


def mark_opened(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    batch_code: str | None = None,
    opened_at: datetime,
    source_text: str,
    _batch_id: int | None = None,
    _expected_version: int | None = None,
) -> PantryBatch:
    """Mark an eligible batch opened, preserving an existing earliest open time."""

    opened = _timestamp(opened_at)

    def mutate(context: MutationContext) -> sqlite3.Row:
        row = _batch_target(
            connection, batch_code, _batch_id, _expected_version
        )
        _require_state(row, {ACTIVE, THAWED, OPENED}, "open")
        updated = context.update(
            "pantry_batches",
            row["id"],
            {
                "status": OPENED,
                "opened_at": row["opened_at"] or opened,
                "version": row["version"] + 1,
            },
        )
        _movement(
            context,
            row["id"],
            "open",
            Decimal("0"),
            row["unit"],
            source_text,
            None,
        )
        return updated

    return _batch(
        manager.execute("pantry_adjust", _source_text(source_text), mutate).value
    )


def freeze_batch(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    batch_code: str | None = None,
    frozen_at: datetime,
    source_text: str,
    _batch_id: int | None = None,
    _expected_version: int | None = None,
) -> PantryBatch:
    """Freeze an active, opened, or thawed batch."""

    _timestamp(frozen_at)
    return _change_state(
        connection,
        manager,
        batch_code,
        FROZEN,
        "freeze",
        source_text,
        None,
        batch_id=_batch_id,
        expected_version=_expected_version,
    )


def thaw_batch(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    batch_code: str | None = None,
    thawed_at: datetime,
    source_text: str,
    _batch_id: int | None = None,
    _expected_version: int | None = None,
) -> PantryBatch:
    """Thaw a frozen batch."""

    _timestamp(thawed_at)
    return _change_state(
        connection,
        manager,
        batch_code,
        THAWED,
        "thaw",
        source_text,
        None,
        batch_id=_batch_id,
        expected_version=_expected_version,
    )


def _add_batch(context: MutationContext, **values: object) -> sqlite3.Row:
    quantity = values.pop("quantity")
    unit = values.pop("unit")
    added_at = values.pop("added_at")
    source_text = values.pop("source_text")
    row = context.insert(
        "pantry_batches",
        {**values, "added_at": added_at, "initial_quantity": _sqlite_real(quantity, "initial_quantity"), "remaining_quantity": _sqlite_real(quantity, "remaining_quantity"), "unit": unit, "status": ACTIVE, "version": 1},
    )
    _movement(context, row["id"], "add", quantity, unit, source_text, None)
    return row


def _change_state(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    batch_code: str | None,
    status: str,
    movement_type: str,
    source_text: str,
    reason: str | None,
    *,
    batch_id: int | None = None,
    expected_version: int | None = None,
    waste_category: str | None = None,
) -> PantryBatch:
    def mutate(context: MutationContext) -> sqlite3.Row:
        row = _batch_target(
            connection, batch_code, batch_id, expected_version
        )
        if status == FROZEN:
            _require_state(row, {ACTIVE, OPENED, THAWED}, "freeze")
        elif status == THAWED:
            _require_state(row, {FROZEN}, "thaw")
        elif status == DISCARDED:
            _require_state(row, {ACTIVE, OPENED, FROZEN, THAWED}, "discard")
        changes: dict[str, object] = {
            "status": status,
            "version": row["version"] + 1,
        }
        movement_quantity = Decimal("0")
        allocation = None
        if status == DISCARDED:
            movement_quantity = _decimal(row["remaining_quantity"])
            changes["remaining_quantity"] = _sqlite_real(
                Decimal("0"), "remaining_quantity"
            )
            allocation = costs.allocation_for_reduction(
                row,
                movement_quantity,
            )
            if allocation is not None:
                changes["remaining_cost_minor"] = (
                    allocation.remaining_cost_minor
                )
        updated = context.update("pantry_batches", row["id"], changes)
        movement = _movement(
            context,
            row["id"],
            movement_type,
            movement_quantity,
            row["unit"],
            reason or source_text,
            None,
            (
                waste.normalize_category(waste_category)
                if status == DISCARDED
                else None
            ),
        )
        if status == DISCARDED:
            costs.record_allocation(
                context,
                batch_id=int(row["id"]),
                movement_id=int(movement["id"]),
                allocation_kind="waste",
                quantity=movement_quantity,
                unit=str(row["unit"]),
                allocation=allocation,
                allocated_at=str(movement["created_at"]),
            )
        return updated

    return _batch(manager.execute("pantry_adjust", _source_text(source_text), mutate).value)


def _selection(
    connection: sqlite3.Connection,
    name: str,
    amount: Decimal,
    selector: BatchSelector | None,
    expected_unit: str | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    chosen = _selected_rows(
        connection,
        name,
        amount,
        selector,
        expected_unit,
        deduction_strategy,
    )
    if not chosen:
        raise InsufficientStockError(f"{INSUFFICIENT_STOCK}: no eligible stock for {name}")
    unit = chosen[0][1].unit
    if sum((line.quantity for _, line in chosen), Decimal("0")) != amount:
        raise InsufficientStockError(f"{INSUFFICIENT_STOCK}: insufficient eligible stock for {name}")
    return BatchSelection(name, amount, unit, tuple(line for _, line in chosen))


def _selected_rows(
    connection: sqlite3.Connection,
    name: str,
    amount: Decimal,
    selector: BatchSelector | None,
    expected_unit: str | None,
    deduction_strategy: Sequence[str] | None,
) -> list[tuple[int, BatchSelectionLine]]:
    clauses = ["normalized_name = ?", "remaining_quantity > 0"]
    values: list[object] = [name]
    order = deduction_order_sql(deduction_strategy)
    if selector == BatchSelector.FROZEN:
        clauses.append("status = 'frozen'")
    else:
        clauses.append("status IN ('active', 'opened', 'thawed')")
        if selector == BatchSelector.COLD_STORAGE:
            clauses.append("lower(COALESCE(storage_location, '')) IN ('fridge', 'refrigerator', 'refrigerated', 'cold storage')")
        elif selector == BatchSelector.NEWEST:
            order = "added_at DESC, id DESC"
    if expected_unit is not None:
        clauses.append("lower(unit) = ?")
        values.append(expected_unit)
    rows = connection.execute(f"SELECT * FROM pantry_batches WHERE {' AND '.join(clauses)} ORDER BY {order}", values).fetchall()
    remaining = amount
    result: list[tuple[int, BatchSelectionLine]] = []
    for row in rows:
        take = min(remaining, _decimal(row["remaining_quantity"]))
        if take > 0:
            result.append((row["id"], BatchSelectionLine(row["batch_code"], take, row["unit"])))
            remaining -= take
        if remaining == 0:
            break
    if len({line.unit.lower() for _, line in result}) > 1:
        raise PantryValidationError("mixed inventory units require a deterministic conversion")
    return result


def _movement(
    context: MutationContext,
    batch_id: int,
    movement_type: str,
    quantity: Decimal,
    unit: str,
    reason: str | None,
    linked_meal_id: int | None,
    waste_category: str | None = None,
) -> sqlite3.Row:
    return context.insert(
        "pantry_movements",
        {
            "pantry_batch_id": batch_id,
            "movement_type": movement_type,
            "quantity": _sqlite_real(quantity, "movement quantity"),
            "unit": unit,
            "reason": _optional_text(reason),
            "linked_meal_id": linked_meal_id,
            "created_at": _now(),
            "waste_category": waste_category,
        },
    )


def _batch_by_code(connection: sqlite3.Connection, batch_code: str) -> sqlite3.Row:
    return _batch_target(connection, batch_code, None, None)


def _batch_target(
    connection: sqlite3.Connection,
    batch_code: str | None,
    batch_id: int | None,
    expected_version: int | None,
) -> sqlite3.Row:
    if batch_id is not None:
        if isinstance(batch_id, bool) or not isinstance(batch_id, int):
            raise PantryValidationError("internal batch target must be an integer")
        row = connection.execute(
            "SELECT * FROM pantry_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            if expected_version is not None:
                raise PantryReferenceStaleError(
                    "selected pantry batch is stale"
                )
            raise KeyError("No pantry batch matches the selected workflow reference")
        if expected_version is not None:
            if (
                isinstance(expected_version, bool)
                or not isinstance(expected_version, int)
                or row["version"] != expected_version
            ):
                raise PantryReferenceStaleError(
                    "selected pantry batch is stale"
                )
        return row
    code = _required_text(batch_code, "batch_code")
    rows = connection.execute(
        "SELECT * FROM pantry_batches WHERE batch_code = ? ORDER BY id", (code,)
    ).fetchall()
    if len(rows) != 1:
        raise KeyError(f"Expected exactly one batch with code {code!r}")
    return rows[0]


def _require_state(row: sqlite3.Row, allowed: set[str], action: str) -> None:
    if row["status"] not in allowed:
        raise PantryValidationError(f"cannot {action} a {row['status']} batch")


def _restored_status(status: str) -> str:
    return ACTIVE if status == CONSUMED else status


def _batch(row: sqlite3.Row) -> PantryBatch:
    return PantryBatch(
        row["food_name"],
        row["normalized_name"],
        row["batch_code"],
        row["storage_location"],
        row["purchase_date"],
        _parse_timestamp(row["added_at"]),
        _parse_optional_timestamp(row["opened_at"]),
        _parse_optional_timestamp(row["expires_at"]),
        _decimal(row["initial_quantity"]),
        _decimal(row["remaining_quantity"]),
        row["unit"],
        (
            _decimal(row["initial_display_quantity"])
            if row["initial_display_quantity"] is not None
            else None
        ),
        row["display_unit"],
        (
            _decimal(row["base_quantity_per_display_unit"])
            if row["base_quantity_per_display_unit"] is not None
            else None
        ),
        (
            tuple(json.loads(row["package_hierarchy_json"]))
            if row["package_hierarchy_json"] is not None
            else None
        ),
        _decimal(row["price"]) if row["price"] is not None else None,
        int(row["price_minor"]) if row["price_minor"] is not None else None,
        str(row["currency"]) if row["currency"] is not None else None,
        (
            int(row["remaining_cost_minor"])
            if row["remaining_cost_minor"] is not None
            else None
        ),
        row["status"],
        row["source"],
        row["notes"],
        _decimal(row["total_weight_g"]) if row["total_weight_g"] is not None else None,
        (
            _decimal(row["average_unit_weight_g"])
            if row["average_unit_weight_g"] is not None
            else None
        ),
        row["weight_basis"],
        row["weight_source"],
        row["weight_confidence"],
        row["version"],
    )


def _weight_metadata(
    *,
    quantity: Decimal,
    unit: str,
    total_weight_g: Decimal | None,
    average_unit_weight_g: Decimal | None,
    weight_basis: str | None,
    weight_source: str | None,
    weight_confidence: str | None,
    source_text: str,
) -> tuple[Decimal | None, Decimal | None, str | None, str | None, str | None]:
    if (
        total_weight_g is None
        and average_unit_weight_g is None
        and weight_basis is None
        and weight_source is None
        and weight_confidence is None
    ):
        return None, None, None, None, None
    if unit not in {"piece", "portion", "pack"}:
        raise PantryValidationError(
            "count-to-weight metadata requires piece, portion, or pack inventory"
        )
    total = _decimal(total_weight_g) if total_weight_g is not None else None
    average = (
        _decimal(average_unit_weight_g)
        if average_unit_weight_g is not None
        else None
    )
    if total is None and average is None:
        raise PantryValidationError(
            "total_weight_g or average_unit_weight_g is required"
        )
    if total is not None and total <= 0:
        raise PantryValidationError("total_weight_g must be positive")
    if average is not None and average <= 0:
        raise PantryValidationError("average_unit_weight_g must be positive")
    derived: Decimal | None = None
    if total is not None:
        try:
            derived = derive_average_unit_weight(quantity, total)
        except UnitWeightValidationError as error:
            raise PantryValidationError(str(error)) from error
    if average is None:
        average = derived
        confidence = "derived"
    else:
        if derived is not None and abs(average - derived) > Decimal("0.01"):
            raise PantryValidationError(
                "average_unit_weight_g conflicts with quantity and total_weight_g"
            )
        confidence = weight_confidence or "confirmed"
    basis = _optional_text(weight_basis)
    if basis is not None and basis not in {"net", "gross", "shell_on", "edible"}:
        raise PantryValidationError(
            "weight_basis must be net, gross, shell_on, or edible"
        )
    if confidence not in {"confirmed", "derived", "estimated"}:
        raise PantryValidationError(
            "weight_confidence must be confirmed, derived, or estimated"
        )
    return (
        total,
        average,
        basis,
        _optional_text(weight_source) or _source_text(source_text),
        confidence,
    )


def _canonical_name(food_name: str, normalized_name: str | None, aliases: Mapping[str, str] | None) -> str:
    raw = _required_text(normalized_name or food_name, "food_name").lower()
    return _required_text((aliases or {}).get(raw, raw), "normalized_name").lower()


def _selector(value: BatchSelector | str | None) -> BatchSelector | None:
    if value is None:
        return None
    try:
        return BatchSelector(value)
    except ValueError as error:
        raise PantryValidationError(f"unknown batch selector: {value!r}") from error


def _strategy(value: Sequence[str] | None) -> tuple[str, ...]:
    try:
        return normalized_deduction_strategy(value)
    except (TypeError, ValueError) as error:
        raise PantryValidationError(str(error)) from error


def _positive_quantity(value: Decimal) -> Decimal:
    number = _decimal(value)
    if number <= 0:
        raise PantryValidationError("quantity must be positive")
    return number


def _nonnegative_quantity(value: Decimal) -> Decimal:
    number = _decimal(value)
    if number < 0:
        raise PantryValidationError("quantity cannot be negative")
    return number


def _optional_decimal(value: Decimal | None, field: str) -> float | None:
    if value is None:
        return None
    number = _nonnegative_quantity(value)
    return _sqlite_real(number, field)


def _sqlite_real(value: Decimal, field: str) -> float:
    """Convert a Decimal only when SQLite REAL preserves its decimal value."""

    number = _decimal(value)
    try:
        converted = float(number)
    except (OverflowError, ValueError) as error:
        raise PantryValidationError(f"{field} is not representable as a SQLite REAL") from error
    changed_sign_or_underflowed = number != 0 and (
        converted == 0
        or (number > 0 and converted < 0)
        or (number < 0 and converted > 0)
    )
    if (
        not math.isfinite(converted)
        or changed_sign_or_underflowed
        or Decimal(str(converted)) != number
    ):
        raise PantryValidationError(f"{field} is not representable as a SQLite REAL")
    return converted


def _decimal(value: Decimal | float | int | str) -> Decimal:
    if isinstance(value, bool):
        raise PantryValidationError("quantity must be a Decimal")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        raise PantryValidationError("quantity must be a finite Decimal") from error
    if not number.is_finite():
        raise PantryValidationError("quantity must be finite")
    return number


def _unit(value: str) -> str:
    raw = _required_text(value, "unit").lower()
    canonical = {
        "g": "g",
        "gram": "g",
        "grams": "g",
        "ml": "ml",
        "milliliter": "ml",
        "milliliters": "ml",
        "piece": "piece",
        "pieces": "piece",
        "portion": "portion",
        "portions": "portion",
        "pack": "pack",
        "packs": "pack",
    }.get(raw)
    if canonical is None:
        raise PantryValidationError("unit must be g, ml, piece, portion, or pack")
    return canonical


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PantryValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value, "text")


def _source_text(value: str) -> str:
    return _required_text(value, "source_text")


def _timestamp(value: datetime) -> str:
    return (
        _utc_datetime(value)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _expiry_timestamp(value: datetime) -> str:
    normalized = _utc_datetime(value)
    if normalized.microsecond:
        normalized = normalized.replace(microsecond=0) + timedelta(seconds=1)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _validated_expiry_timestamp(expires_at: datetime, added_at: datetime) -> str:
    if _utc_datetime(expires_at) <= _utc_datetime(added_at):
        raise PantryValidationError("expires_at must be later than added_at")
    return _expiry_timestamp(expires_at)


def _utc_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PantryValidationError("timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _optional_timestamp(value: datetime | None) -> str | None:
    return _timestamp(value) if value is not None else None


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    return _parse_timestamp(value) if value is not None else None


def _now() -> str:
    return utc_text()
