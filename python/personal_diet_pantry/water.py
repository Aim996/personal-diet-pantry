"""Journaled water-record operations and deterministic unit conversion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import sqlite3

from .models import Settings
from .transactions import TransactionManager
from .timezones import local_day_utc_bounds


class WaterValidationError(ValueError):
    """Raised when a water amount, unit, or timestamp is not usable."""


class WaterReferenceStaleError(WaterValidationError):
    """Raised when a persisted water reference no longer matches its row."""


@dataclass(frozen=True)
class WaterRecord:
    """A persisted water log safe to return from normal water operations."""

    id: int
    amount_ml: int
    occurred_at: datetime
    source_text: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class WaterSummary:
    """Active water records and their aggregate for one requested window."""

    occurred_on: date | None
    total_ml: int
    records: tuple[WaterRecord, ...]


def record_water(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    amount: Decimal,
    unit: str,
    occurred_at: datetime,
    source_text: str,
    settings: Settings,
    learned_unit_ml: Decimal | None = None,
) -> WaterRecord:
    """Record one validated water entry through the atomic transaction journal."""

    amount_ml = _to_milliliters(
        amount, unit, settings, learned_unit_ml=learned_unit_ml
    )
    occurred_text = _timestamp(occurred_at)
    _source_text(source_text)
    result = manager.execute(
        "water_record",
        source_text,
        lambda context: context.insert(
            "water_logs",
            {"occurred_at": occurred_text, "amount_ml": amount_ml, "source_text": source_text},
        ),
    )
    return _water_record(result.value)


def query_water(
    connection: sqlite3.Connection,
    *,
    occurred_on: date | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    timezone_name: str = "UTC",
) -> WaterSummary:
    """Return active records and totals for a day or half-open UTC range."""

    has_range = start_utc is not None or end_utc is not None
    if occurred_on is not None and has_range:
        raise WaterValidationError(
            "occurred_on cannot be combined with start_utc and end_utc"
        )
    if occurred_on is not None:
        if not isinstance(occurred_on, date) or isinstance(occurred_on, datetime):
            raise WaterValidationError("occurred_on must be a date")
        start, end = local_day_utc_bounds(occurred_on, timezone_name)
    else:
        if start_utc is None or end_utc is None:
            raise WaterValidationError(
                "one occurred_on date or a complete UTC range is required"
            )
        start = _timestamp(start_utc)
        end = _timestamp(end_utc)
        if end <= start:
            raise WaterValidationError("end_utc must be after start_utc")
    rows = connection.execute(
        """
        SELECT * FROM water_logs
        WHERE occurred_at >= ? AND occurred_at < ? AND deleted_at IS NULL
        ORDER BY occurred_at, id
        """,
        (start, end),
    ).fetchall()
    records = tuple(_water_record(row) for row in rows)
    return WaterSummary(
        occurred_on=occurred_on,
        total_ml=sum(record.amount_ml for record in records),
        records=records,
    )


def update_water(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    water_id: int,
    amount: Decimal,
    unit: str,
    occurred_at: datetime,
    source_text: str,
    settings: Settings,
    learned_unit_ml: Decimal | None = None,
    _expected_state: tuple[str, str | None] | None = None,
) -> WaterRecord:
    """Replace an active record's values through one journaled correction."""

    amount_ml = _to_milliliters(
        amount, unit, settings, learned_unit_ml=learned_unit_ml
    )
    occurred_text = _timestamp(occurred_at)
    _source_text(source_text)
    result = manager.execute(
        "record_correction",
        source_text,
        lambda context: _update_active_water(
            connection,
            context,
            water_id,
            {
                "amount_ml": amount_ml,
                "occurred_at": occurred_text,
                "source_text": source_text,
                "updated_at": _timestamp(datetime.now(timezone.utc)),
            },
            expected_state=_expected_state,
        ),
    )
    return _water_record(result.value)


def delete_water(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    water_id: int,
    deleted_at: datetime,
    source_text: str,
    _expected_state: tuple[str, str | None] | None = None,
) -> WaterRecord:
    """Logically delete an active record through one journaled correction."""

    deleted_text = _timestamp(deleted_at)
    _source_text(source_text)
    result = manager.execute(
        "record_correction",
        source_text,
        lambda context: _update_active_water(
            connection,
            context,
            water_id,
            {
                "deleted_at": deleted_text,
                "updated_at": _timestamp(datetime.now(timezone.utc)),
            },
            expected_state=_expected_state,
        ),
    )
    return _water_record(result.value)


def _to_milliliters(
    amount: Decimal,
    unit: str,
    settings: Settings,
    *,
    learned_unit_ml: Decimal | None = None,
) -> int:
    if not isinstance(amount, Decimal):
        raise WaterValidationError("amount must be a Decimal")
    if not amount.is_finite() or amount <= 0:
        raise WaterValidationError("amount must be positive and finite")
    normalized_unit = _normalized_unit(unit)
    multiplier = (
        learned_unit_ml
        if learned_unit_ml is not None and normalized_unit != "ml"
        else _unit_multipliers(settings).get(normalized_unit)
    )
    if multiplier is None:
        raise WaterValidationError(f"unknown water unit: {unit!r}")
    if not multiplier.is_finite() or multiplier <= 0:
        raise WaterValidationError("water-unit multiplier must be positive and finite")
    maximum = Decimal(settings.behavior.water.max_single_entry_ml)
    try:
        if amount >= (maximum + Decimal("0.5")) / multiplier:
            raise WaterValidationError(
                "amount exceeds the configured single-entry maximum"
            )
        rounded_amount_ml = (amount * multiplier).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    except DecimalException as error:
        raise WaterValidationError(
            "amount exceeds the configured single-entry maximum"
        ) from error
    if rounded_amount_ml < 1:
        raise WaterValidationError("amount must round to at least one milliliter")
    if rounded_amount_ml > maximum:
        raise WaterValidationError("amount exceeds the configured single-entry maximum")
    return int(rounded_amount_ml)


def _unit_multipliers(settings: Settings) -> dict[str, Decimal]:
    units = settings.default_water_units
    return {
        "ml": Decimal(1),
        "毫升": Decimal(1),
        "cup": Decimal(units.cup_ml),
        "杯": Decimal(units.cup_ml),
        "glass": Decimal(units.glass_ml),
        "杯子": Decimal(units.glass_ml),
        "bottle": Decimal(units.bottle_ml),
        "瓶": Decimal(units.bottle_ml),
    }


def _normalized_unit(unit: str) -> str:
    if not isinstance(unit, str):
        raise WaterValidationError("unit must be a string")
    return unit.strip().lower()


def _source_text(source_text: str) -> None:
    if not isinstance(source_text, str) or not source_text.strip():
        raise WaterValidationError("source_text must be a non-empty string")


def _active_record(connection: sqlite3.Connection, water_id: int) -> sqlite3.Row:
    if isinstance(water_id, bool) or not isinstance(water_id, int):
        raise WaterValidationError("water_id must be an integer")
    row = connection.execute(
        "SELECT * FROM water_logs WHERE id = ? AND deleted_at IS NULL", (water_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"No active water record with id {water_id!r}")
    return row


def _update_active_water(
    connection: sqlite3.Connection,
    context,
    water_id: int,
    changes: dict[str, int | str],
    *,
    expected_state: tuple[str, str | None] | None,
) -> sqlite3.Row:
    """Check the target after the journal transaction starts, then update it."""

    try:
        row = _active_record(connection, water_id)
    except KeyError as error:
        if expected_state is not None:
            raise WaterReferenceStaleError(
                "selected water record is stale"
            ) from error
        raise
    if expected_state is not None and (
        row["updated_at"], row["deleted_at"]
    ) != expected_state:
        raise WaterReferenceStaleError("selected water record is stale")
    return context.update("water_logs", water_id, changes)


def _water_record(row: sqlite3.Row) -> WaterRecord:
    return WaterRecord(
        id=row["id"],
        amount_ml=row["amount_ml"],
        occurred_at=_parse_timestamp(row["occurred_at"]),
        source_text=row["source_text"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        deleted_at=_parse_timestamp(row["deleted_at"]) if row["deleted_at"] is not None else None,
    )


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WaterValidationError("timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
