"""Journaled body-weight records with exact unit conversion and trend summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import sqlite3
from typing import Literal

from .transactions import TransactionManager


_GRAMS_PER_UNIT = {
    "kg": Decimal("1000"),
    "斤": Decimal("500"),
    "jin": Decimal("500"),
    "lb": Decimal("453.59237"),
}
_MIN_WEIGHT_G = 5_000
_MAX_WEIGHT_G = 500_000
_ONE_DECIMAL = Decimal("0.1")


class BodyWeightValidationError(ValueError):
    """Raised when a body-weight value, status, reference, or time is invalid."""


class BodyWeightReferenceStaleError(BodyWeightValidationError):
    """Raised when a selected body-weight record changed before mutation."""


@dataclass(frozen=True)
class BodyWeightRecord:
    """One body-weight measurement safe for service-layer projection."""

    id: int
    measured_at: datetime
    weight_g: int
    weight_kg: Decimal
    status_note: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class BodyWeightTrend:
    """Comparison between the current and preceding seven-day windows."""

    direction: Literal["up", "down", "stable"]
    change_kg: Decimal
    current_average_kg: Decimal
    previous_average_kg: Decimal


@dataclass(frozen=True)
class BodyWeightSummary:
    """Recent measurements plus the current seven-day average and trend."""

    records: tuple[BodyWeightRecord, ...]
    seven_day_average_kg: Decimal | None
    trend: BodyWeightTrend | None


def record_body_weight(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    weight: Decimal,
    unit: str,
    measured_at: datetime,
    status_note: str | None,
) -> BodyWeightRecord:
    """Record a measurement at the trusted system time."""

    weight_g = _to_grams(weight, unit)
    measured_text = _timestamp(measured_at)
    note = _status_note(status_note)
    result = manager.execute(
        "record_correction",
        "body weight record",
        lambda context: context.insert(
            "body_weight_logs",
            {
                "measured_at": measured_text,
                "weight_g": weight_g,
                "status_note": note,
            },
        ),
    )
    return _body_weight_record(result.value)


def query_body_weight(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    limit: int = 20,
) -> BodyWeightSummary:
    """Return recent active records and two exact seven-day windows."""

    now_text = _timestamp(now)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise BodyWeightValidationError("limit must be an integer from 1 to 100")
    has_range = start_utc is not None or end_utc is not None
    if has_range and (start_utc is None or end_utc is None):
        raise BodyWeightValidationError(
            "start_utc and end_utc must be supplied together"
        )
    clauses = ["deleted_at IS NULL", "measured_at <= ?"]
    parameters: list[str | int] = [now_text]
    if start_utc is not None and end_utc is not None:
        start_text = _timestamp(start_utc)
        end_text = _timestamp(end_utc)
        if end_text <= start_text:
            raise BodyWeightValidationError("end_utc must be after start_utc")
        clauses.extend(("measured_at >= ?", "measured_at < ?"))
        parameters.extend((start_text, end_text))
    parameters.append(limit)
    rows = connection.execute(
        f"""
        SELECT * FROM body_weight_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY measured_at DESC, id DESC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    current_start = _timestamp(now - timedelta(days=7))
    previous_start = _timestamp(now - timedelta(days=14))
    current_weights = _window_weights(
        connection,
        start_exclusive=current_start,
        end_inclusive=now_text,
    )
    previous_weights = _window_weights(
        connection,
        start_exclusive=previous_start,
        end_inclusive=current_start,
    )
    current_average = _average_kg(current_weights)
    previous_average = _average_kg(previous_weights)
    return BodyWeightSummary(
        records=tuple(_body_weight_record(row) for row in rows),
        seven_day_average_kg=_rounded_kg(current_average),
        trend=_trend(current_average, previous_average),
    )


def get_body_weight(
    connection: sqlite3.Connection,
    *,
    weight_id: int,
) -> BodyWeightRecord:
    """Return one active record selected by an already validated handle."""

    return _body_weight_record(_active_record(connection, weight_id))


def update_body_weight(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    weight_id: int,
    weight: Decimal,
    unit: str,
    status_note: str | None,
    changed_at: datetime,
    _expected_version: int | None = None,
) -> BodyWeightRecord:
    """Correct weight or status without rewriting the measurement time."""

    changes = {
        "weight_g": _to_grams(weight, unit),
        "status_note": _status_note(status_note),
        "updated_at": _timestamp(changed_at),
    }
    result = manager.execute(
        "record_correction",
        "body weight correction",
        lambda context: _update_active_weight(
            connection,
            context,
            weight_id,
            changes,
            expected_version=_expected_version,
        ),
    )
    return _body_weight_record(result.value)


def delete_body_weight(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    weight_id: int,
    deleted_at: datetime,
    _expected_version: int | None = None,
) -> BodyWeightRecord:
    """Logically delete a measurement at the trusted system time."""

    deleted_text = _timestamp(deleted_at)
    result = manager.execute(
        "record_correction",
        "body weight deletion",
        lambda context: _update_active_weight(
            connection,
            context,
            weight_id,
            {"deleted_at": deleted_text, "updated_at": deleted_text},
            expected_version=_expected_version,
        ),
    )
    return _body_weight_record(result.value)


def _to_grams(weight: Decimal, unit: str) -> int:
    if not isinstance(weight, Decimal):
        raise BodyWeightValidationError("weight must be a Decimal")
    if not weight.is_finite() or weight <= 0:
        raise BodyWeightValidationError("weight must be positive and finite")
    if not isinstance(unit, str):
        raise BodyWeightValidationError("unit must be a string")
    multiplier = _GRAMS_PER_UNIT.get(unit.strip().lower())
    if multiplier is None:
        raise BodyWeightValidationError(f"unknown body-weight unit: {unit!r}")
    try:
        grams = (weight * multiplier).to_integral_value(rounding=ROUND_HALF_UP)
    except DecimalException as error:
        raise BodyWeightValidationError("weight cannot be converted") from error
    if grams < _MIN_WEIGHT_G or grams > _MAX_WEIGHT_G:
        raise BodyWeightValidationError("weight must be between 5 kg and 500 kg")
    return int(grams)


def _status_note(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BodyWeightValidationError("status_note must be text")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 80:
        raise BodyWeightValidationError("status_note must not exceed 80 characters")
    return normalized


def _window_weights(
    connection: sqlite3.Connection,
    *,
    start_exclusive: str,
    end_inclusive: str,
) -> tuple[int, ...]:
    return tuple(
        int(row["weight_g"])
        for row in connection.execute(
            """
            SELECT weight_g FROM body_weight_logs
            WHERE deleted_at IS NULL
              AND measured_at > ?
              AND measured_at <= ?
            ORDER BY measured_at, id
            """,
            (start_exclusive, end_inclusive),
        )
    )


def _average_kg(weights_g: tuple[int, ...]) -> Decimal | None:
    if not weights_g:
        return None
    average_g = Decimal(sum(weights_g)) / Decimal(len(weights_g))
    return average_g / Decimal(1000)


def _rounded_kg(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)


def _trend(
    current_average: Decimal | None,
    previous_average: Decimal | None,
) -> BodyWeightTrend | None:
    if current_average is None or previous_average is None:
        return None
    signed_change = (current_average - previous_average).quantize(
        _ONE_DECIMAL,
        rounding=ROUND_HALF_UP,
    )
    direction: Literal["up", "down", "stable"]
    if signed_change > 0:
        direction = "up"
    elif signed_change < 0:
        direction = "down"
    else:
        direction = "stable"
    return BodyWeightTrend(
        direction=direction,
        change_kg=abs(signed_change),
        current_average_kg=_rounded_kg(current_average),
        previous_average_kg=_rounded_kg(previous_average),
    )


def _active_record(connection: sqlite3.Connection, weight_id: int) -> sqlite3.Row:
    if isinstance(weight_id, bool) or not isinstance(weight_id, int):
        raise BodyWeightValidationError("weight_id must be an integer")
    row = connection.execute(
        """
        SELECT * FROM body_weight_logs
        WHERE id = ? AND deleted_at IS NULL
        """,
        (weight_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No active body-weight record with id {weight_id!r}")
    return row


def _update_active_weight(
    connection: sqlite3.Connection,
    context,
    weight_id: int,
    changes: dict[str, int | str | None],
    *,
    expected_version: int | None,
) -> sqlite3.Row:
    try:
        row = _active_record(connection, weight_id)
    except KeyError as error:
        if expected_version is not None:
            raise BodyWeightReferenceStaleError(
                "selected body-weight record is stale"
            ) from error
        raise
    if expected_version is not None and row["version"] != expected_version:
        raise BodyWeightReferenceStaleError(
            "selected body-weight record is stale"
        )
    versioned_changes = dict(changes)
    versioned_changes["version"] = int(row["version"]) + 1
    return context.update("body_weight_logs", weight_id, versioned_changes)


def _body_weight_record(row: sqlite3.Row) -> BodyWeightRecord:
    weight_g = int(row["weight_g"])
    return BodyWeightRecord(
        id=int(row["id"]),
        measured_at=_parse_timestamp(row["measured_at"]),
        weight_g=weight_g,
        weight_kg=Decimal(weight_g) / Decimal(1000),
        status_note=row["status_note"],
        version=int(row["version"]),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        deleted_at=(
            _parse_timestamp(row["deleted_at"])
            if row["deleted_at"] is not None
            else None
        ),
    )


def _timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise BodyWeightValidationError(
            "timestamps must be timezone-aware datetimes"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
