from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import shutil
import sqlite3

import pytest

from personal_diet_pantry import database
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATED_AT = "2026-07-29T00:00:00.123456Z"


def _migration_subset(
    tmp_path: Path,
    *,
    through: int,
) -> Path:
    destination = tmp_path / f"migrations-{through}"
    destination.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, destination / source.name)
    return destination


def _legacy_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = database.connect_database(tmp_path / "legacy.sqlite")
    database.apply_migrations(
        connection,
        _migration_subset(tmp_path, through=11),
    )
    connection.execute(
        """
        INSERT INTO nutrition_goal_profiles (
            id, calories_kcal, protein_g, fat_g, carbohydrate_g,
            fiber_g, sodium_mg, water_ml, timezone_name, updated_at
        ) VALUES (1, 2000, 80, 60, 250, 25, 2000, 2000, 'UTC', ?)
        """,
        (UPDATED_AT,),
    )
    connection.commit()
    return connection


@pytest.fixture
def legacy_database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = _legacy_connection(tmp_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def legacy_confirmed_database(
    tmp_path: Path,
) -> Iterator[sqlite3.Connection]:
    connection = _legacy_connection(tmp_path)
    connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, status, created_at, committed_at,
            source_text, before_snapshot, after_snapshot
        ) VALUES (
            'txn_legacy_goals', 'profile_update', 'committed',
            '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z',
            'confirmed legacy goals', '[]', '[]'
        )
        """
    )
    connection.execute(
        """
        UPDATE nutrition_goal_profiles
        SET protein_g = 90,
            updated_at = ?,
            transaction_id = 'txn_legacy_goals'
        WHERE id = 1
        """,
        (UPDATED_AT,),
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def _goal_row(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM nutrition_goal_profiles WHERE id = 1"
    ).fetchone()
    assert row is not None
    return row


def test_default_goal_row_migrates_as_unconfirmed(
    legacy_database: sqlite3.Connection,
) -> None:
    database.apply_migrations(legacy_database, PROJECT_ROOT / "migrations")

    row = _goal_row(legacy_database)
    assert row["goal_source"] == "configuration_default"
    assert row["confirmed_at"] is None


def test_transaction_backed_goal_row_migrates_as_confirmed(
    legacy_confirmed_database: sqlite3.Connection,
) -> None:
    before = _goal_row(legacy_confirmed_database)
    database.apply_migrations(
        legacy_confirmed_database,
        PROJECT_ROOT / "migrations",
    )

    after = _goal_row(legacy_confirmed_database)
    assert after["goal_source"] == "user_confirmed"
    assert after["confirmed_at"] == before["updated_at"]


def test_failed_012_rolls_back_both_columns(
    legacy_database: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    broken_migrations = _migration_subset(tmp_path, through=12)
    migration = broken_migrations / "012_goal_provenance.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8")
        + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.DatabaseError):
        database.apply_migrations(legacy_database, broken_migrations)

    columns = {
        row["name"]
        for row in legacy_database.execute(
            "PRAGMA table_info(nutrition_goal_profiles)"
        )
    }
    assert "goal_source" not in columns
    assert "confirmed_at" not in columns


def test_current_schema_records_twenty_three_migrations(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "current.sqlite")
    try:
        database.apply_migrations(connection, PROJECT_ROOT / "migrations")
        count = connection.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        connection.close()

    assert count == 23
