from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

from personal_diet_pantry import database
from personal_diet_pantry.transactions import TransactionManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-07-30T00:00:00Z"


def _migration_subset(tmp_path: Path, *, through: int) -> Path:
    target = tmp_path / f"migrations-{through}"
    target.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, target / source.name)
    return target


def _legacy_meal(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, status, created_at, source_text
        ) VALUES ('tx-legacy', 'meal_record', 'pending', ?, 'legacy meal')
        """,
        (NOW,),
    )
    connection.execute(
        """
        INSERT INTO meals (
            occurred_at, meal_type, source_text, location_type,
            total_calories, total_protein, total_fat,
            total_carbohydrate, total_fiber, total_sodium,
            total_hydration_ml, confidence, created_at, updated_at,
            transaction_id, nutrition_status,
            nutrition_missing_fields_json
        ) VALUES (
            ?, 'breakfast', 'legacy meal', 'home',
            '371.4', '21.34', '9.24', '58.24', '7.2', '382',
            '475', '0.417', ?, ?, 'tx-legacy', 'complete', '[]'
        )
        """,
        (NOW, NOW, NOW),
    )
    meal_id = int(
        connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO meal_items (
            meal_id, parent_item_id, item_role, display_order,
            raw_name, normalized_name, amount, unit,
            consumed_weight_g, calories, protein, fat,
            carbohydrate, fiber, sodium, hydration_ml,
            source_grade, nutrition_source, confidence, transaction_id
        ) VALUES (
            ?, NULL, 'food', 0,
            'legacy item', 'legacy item', '1', 'serving',
            '100', '371.4', '21.34', '9.24',
            '58.24', '7.2', '382', '475',
            'C', 'legacy fixture', '0.417', 'tx-legacy'
        )
        """,
        (meal_id,),
    )
    item_id = int(
        connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    )
    meal = dict(
        connection.execute(
            "SELECT * FROM meals WHERE id = ?", (meal_id,)
        ).fetchone()
    )
    item = dict(
        connection.execute(
            "SELECT * FROM meal_items WHERE id = ?", (item_id,)
        ).fetchone()
    )
    before = [
        {"table": "meals", "row_id": meal_id, "row": None},
        {"table": "meal_items", "row_id": item_id, "row": None},
    ]
    after = [
        {"table": "meals", "row_id": meal_id, "row": meal},
        {"table": "meal_items", "row_id": item_id, "row": item},
    ]
    connection.execute(
        """
        UPDATE transactions
        SET status = 'committed', committed_at = ?,
            before_snapshot = ?, after_snapshot = ?
        WHERE id = 'tx-legacy'
        """,
        (
            NOW,
            json.dumps(before, ensure_ascii=False, separators=(",", ":")),
            json.dumps(after, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    connection.commit()


def test_013_adds_intake_evidence_without_losing_legacy_meals(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "legacy.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=12),
        )
        _legacy_meal(connection)

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        meal = connection.execute(
            """
            SELECT source_text, event_timezone, local_date,
                   nutrition_calculation_status,
                   nutrition_provenance_status
            FROM meals
            """
        ).fetchone()
        assert meal is not None
        assert meal["source_text"] == "legacy meal"
        assert meal["event_timezone"] is None
        assert meal["local_date"] is None
        assert meal["nutrition_calculation_status"] == "unverified"
        assert meal["nutrition_provenance_status"] == "untraceable"

        evidence_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(meal_item_nutrition_evidence)"
            )
        }
        assert {
            "id",
            "meal_item_id",
            "basis",
            "input_facts_json",
            "scale_factor",
            "calculation_status",
            "provenance_status",
            "transaction_id",
        } <= evidence_columns

        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        expected = {
            int(path.name.split("_", 1)[0]): path.name
            for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
        }
        observed = {
            int(row["version"]): str(row["name"])
            for row in rows
        }
        assert observed == expected
    finally:
        connection.close()


def test_013_adds_dimension_and_active_fingerprint_constraints(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "current.sqlite")
    try:
        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        item_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(meal_items)")
        }
        assert {"consumed_volume_ml", "consumed_servings"} <= item_columns

        indexes = {
            row["name"]
            for row in connection.execute("PRAGMA index_list(meals)")
        }
        assert "idx_meals_active_intake_fingerprint" in indexes
    finally:
        connection.close()


def test_014_scrubs_legacy_session_keys_from_rows_and_snapshots(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "legacy-private.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=12),
        )
        _legacy_meal(connection)
        connection.execute(
            """
            UPDATE meals
            SET source_session_key = 'agent:u2:main:private-value'
            """
        )
        after = json.loads(
            connection.execute(
                """
                SELECT after_snapshot FROM transactions
                WHERE id = 'tx-legacy'
                """
            ).fetchone()[0]
        )
        after[0]["row"]["source_session_key"] = (
            "agent:u2:main:private-value"
        )
        connection.execute(
            """
            UPDATE transactions
            SET after_snapshot = ?
            WHERE id = 'tx-legacy'
            """,
            (
                json.dumps(
                    after,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )
        connection.commit()

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        assert connection.execute(
            "SELECT source_session_key FROM meals"
        ).fetchone()[0] is None
        snapshots = connection.execute(
            """
            SELECT before_snapshot, after_snapshot FROM transactions
            WHERE id = 'tx-legacy'
            """
        ).fetchone()
        assert "agent:u2:main:private-value" not in snapshots[0]
        assert "agent:u2:main:private-value" not in snapshots[1]
    finally:
        connection.close()


def test_014_upgrades_database_with_already_applied_013(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "applied-013.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=13),
        )
        assert connection.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0] == 13

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        rows = connection.execute(
            """
            SELECT version, name
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
        expected = {
            int(path.name.split("_", 1)[0]): path.name
            for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
        }
        observed = {
            int(row["version"]): str(row["name"])
            for row in rows
        }
        assert observed == expected
        assert observed[14] == "014_session_key_minimization.sql"
    finally:
        connection.close()


def test_pre_013_meal_transaction_can_undo_and_redo_after_upgrade(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "legacy-undo.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=12),
        )
        _legacy_meal(connection)
        database.apply_migrations(connection, PROJECT_ROOT / "migrations")
        manager = TransactionManager(connection)

        manager.undo("tx-legacy")

        assert connection.execute(
            "SELECT count(*) FROM meals"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM meal_items"
        ).fetchone()[0] == 0

        manager.redo("tx-legacy")

        item = connection.execute(
            """
            SELECT consumed_volume_ml, consumed_servings
            FROM meal_items
            """
        ).fetchone()
        assert item is not None
        assert item["consumed_volume_ml"] is None
        assert item["consumed_servings"] is None
    finally:
        connection.close()
