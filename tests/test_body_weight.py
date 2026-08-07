from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from personal_diet_pantry import body_weight, database
from personal_diet_pantry.transactions import TransactionManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 30, 0, 30, tzinfo=timezone.utc)


@pytest.fixture
def weight_store(tmp_path: Path):
    connection = database.connect_database(tmp_path / "weight.sqlite")
    database.apply_migrations(connection, PROJECT_ROOT / "migrations")
    try:
        yield connection, TransactionManager(connection)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("amount", "unit", "expected_g"),
    [
        (Decimal("105"), "kg", 105_000),
        (Decimal("105"), "斤", 52_500),
        (Decimal("105"), "lb", 47_627),
        (Decimal("70.25"), "kg", 70_250),
    ],
)
def test_record_normalizes_supported_units_to_integer_grams(
    weight_store,
    amount: Decimal,
    unit: str,
    expected_g: int,
) -> None:
    connection, manager = weight_store

    record = body_weight.record_body_weight(
        connection,
        manager,
        weight=amount,
        unit=unit,
        measured_at=NOW,
        status_note=" 空腹 ",
    )

    assert record.weight_g == expected_g
    assert record.weight_kg == Decimal(expected_g) / Decimal(1000)
    assert record.measured_at == NOW
    assert record.status_note == "空腹"


@pytest.mark.parametrize(
    ("amount", "unit"),
    [
        (Decimal("0"), "kg"),
        (Decimal("-1"), "kg"),
        (Decimal("NaN"), "kg"),
        (Decimal("Infinity"), "kg"),
        (Decimal("4.999"), "kg"),
        (Decimal("500.001"), "kg"),
        (Decimal("70"), "stone"),
    ],
)
def test_record_rejects_invalid_or_out_of_range_weight(
    weight_store,
    amount: Decimal,
    unit: str,
) -> None:
    connection, manager = weight_store

    with pytest.raises(body_weight.BodyWeightValidationError):
        body_weight.record_body_weight(
            connection,
            manager,
            weight=amount,
            unit=unit,
            measured_at=NOW,
            status_note=None,
        )


def test_record_requires_system_timezone_and_bounded_status(weight_store) -> None:
    connection, manager = weight_store

    with pytest.raises(body_weight.BodyWeightValidationError):
        body_weight.record_body_weight(
            connection,
            manager,
            weight=Decimal("70"),
            unit="kg",
            measured_at=NOW.replace(tzinfo=None),
            status_note=None,
        )

    with pytest.raises(body_weight.BodyWeightValidationError):
        body_weight.record_body_weight(
            connection,
            manager,
            weight=Decimal("70"),
            unit="kg",
            measured_at=NOW,
            status_note="x" * 81,
        )


def test_blank_status_is_stored_as_none(weight_store) -> None:
    connection, manager = weight_store

    record = body_weight.record_body_weight(
        connection,
        manager,
        weight=Decimal("70"),
        unit="kg",
        measured_at=NOW,
        status_note="   ",
    )

    assert record.status_note is None


def test_query_returns_seven_day_average_and_downward_trend(weight_store) -> None:
    connection, manager = weight_store
    for days_ago, kilograms in [
        (13, "72"),
        (8, "71"),
        (6, "70"),
        (1, "69"),
        (0, "68"),
    ]:
        body_weight.record_body_weight(
            connection,
            manager,
            weight=Decimal(kilograms),
            unit="kg",
            measured_at=NOW - timedelta(days=days_ago),
            status_note=None,
        )

    summary = body_weight.query_body_weight(
        connection,
        now=NOW,
        limit=20,
    )

    assert [record.weight_kg for record in summary.records] == [
        Decimal("68"),
        Decimal("69"),
        Decimal("70"),
        Decimal("71"),
        Decimal("72"),
    ]
    assert summary.seven_day_average_kg == Decimal("69.0")
    assert summary.trend is not None
    assert summary.trend.direction == "down"
    assert summary.trend.change_kg == Decimal("2.5")
    assert summary.trend.current_average_kg == Decimal("69.0")
    assert summary.trend.previous_average_kg == Decimal("71.5")


def test_query_omits_trend_without_previous_window_data(weight_store) -> None:
    connection, manager = weight_store
    body_weight.record_body_weight(
        connection,
        manager,
        weight=Decimal("70.04"),
        unit="kg",
        measured_at=NOW,
        status_note="睡前",
    )

    summary = body_weight.query_body_weight(connection, now=NOW, limit=20)

    assert summary.seven_day_average_kg == Decimal("70.0")
    assert summary.trend is None


def test_query_reports_stable_when_rounded_difference_is_zero(weight_store) -> None:
    connection, manager = weight_store
    body_weight.record_body_weight(
        connection,
        manager,
        weight=Decimal("70.04"),
        unit="kg",
        measured_at=NOW - timedelta(days=8),
        status_note=None,
    )
    body_weight.record_body_weight(
        connection,
        manager,
        weight=Decimal("70.08"),
        unit="kg",
        measured_at=NOW,
        status_note=None,
    )

    summary = body_weight.query_body_weight(connection, now=NOW, limit=20)

    assert summary.trend is not None
    assert summary.trend.direction == "stable"
    assert summary.trend.change_kg == Decimal("0.0")


def test_update_preserves_measurement_time_and_delete_excludes_record(
    weight_store,
) -> None:
    connection, manager = weight_store
    original = body_weight.record_body_weight(
        connection,
        manager,
        weight=Decimal("70"),
        unit="kg",
        measured_at=NOW - timedelta(hours=1),
        status_note="空腹",
    )

    updated = body_weight.update_body_weight(
        connection,
        manager,
        weight_id=original.id,
        weight=Decimal("69.8"),
        unit="kg",
        status_note=None,
        changed_at=NOW,
    )
    assert updated.measured_at == NOW - timedelta(hours=1)
    assert updated.weight_kg == Decimal("69.8")
    assert updated.status_note is None

    deleted = body_weight.delete_body_weight(
        connection,
        manager,
        weight_id=original.id,
        deleted_at=NOW,
    )
    assert deleted.deleted_at == NOW

    summary = body_weight.query_body_weight(connection, now=NOW, limit=20)
    assert summary.records == ()
    assert summary.seven_day_average_kg is None
    assert summary.trend is None

