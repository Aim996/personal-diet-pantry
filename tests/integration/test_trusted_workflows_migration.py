from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from personal_diet_pantry.database import apply_migrations, connect_database
from personal_diet_pantry.maintenance_control import MaintenanceController


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration_subset(tmp_path: Path, through: int) -> Path:
    destination = tmp_path / f"migrations-through-{through}"
    destination.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, destination / source.name)
    return destination


def _control_migration_subset(tmp_path: Path, through: int) -> Path:
    destination = tmp_path / f"control-migrations-through-{through}"
    destination.mkdir()
    for source in sorted(
        (PROJECT_ROOT / "control-migrations").glob("*.sql")
    ):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, destination / source.name)
    return destination


def _unsafe_snapshot_migration_subset(tmp_path: Path) -> Path:
    destination = tmp_path / "unsafe-snapshot-migrations"
    destination.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 18:
            shutil.copy2(source, destination / source.name)
    (destination / "019_unsafe_snapshot_backfill.sql").write_text(
        """
        ALTER TABLE transactions
        ADD COLUMN undo_policy TEXT NOT NULL DEFAULT 'snapshot'
        CHECK (undo_policy IN ('snapshot', 'none'));

        ALTER TABLE transactions
        ADD COLUMN effect_count INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(effect_count) = 'integer' AND effect_count >= 0);

        UPDATE transactions
        SET effect_count = MAX(
            COALESCE(json_array_length(before_snapshot), 0),
            COALESCE(json_array_length(after_snapshot), 0)
        );
        """,
        encoding="utf-8",
    )
    return destination


def _insert_tombstone(
    connection: sqlite3.Connection,
    *,
    suffix: str,
    control_handle: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO privacy_erasure_tombstones (
            erasure_handle,
            preview_token_hash,
            scope,
            affected_counts_json,
            summary_sha256,
            committed_at,
            control_operation_handle
        ) VALUES (?, ?, 'raw_source_text', '{}', ?, ?, ?)
        """,
        (
            "erase_" + suffix * 24,
            suffix * 64,
            suffix * 64,
            f"2026-07-30T00:0{ord(suffix) - ord('a')}:00Z",
            control_handle,
        ),
    )


def test_019_retries_after_unsafe_backfill_and_tolerates_legacy_snapshots(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "legacy-snapshots.sqlite")
    try:
        apply_migrations(connection, _migration_subset(tmp_path, 18))
        connection.executemany(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, source_text,
                before_snapshot, after_snapshot
            ) VALUES (?, 'record_correction', ?, ?, ?, ?, ?)
            """,
            [
                (
                    "txn_pending_malformed",
                    "pending",
                    "2026-07-30T00:00:00Z",
                    "pending malformed",
                    "not-json",
                    '{"table":"pantry_batches"}',
                ),
                (
                    "txn_failed_malformed",
                    "failed",
                    "2026-07-30T00:00:01Z",
                    "failed malformed",
                    "[",
                    "",
                ),
                (
                    "txn_pending_primitives",
                    "pending",
                    "2026-07-30T00:00:02Z",
                    "pending primitive array",
                    '["plain",7,null]',
                    '[true,{"table":"pantry_batches","row_id":71}]',
                ),
            ],
        )
        connection.commit()

        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            apply_migrations(
                connection,
                _unsafe_snapshot_migration_subset(tmp_path),
            )

        columns_after_failure = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(transactions)")
        }
        assert "undo_policy" not in columns_after_failure
        assert "effect_count" not in columns_after_failure
        assert connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 19"
        ).fetchone() is None

        apply_migrations(connection, PROJECT_ROOT / "migrations")

        policies = {
            row["id"]: (row["undo_policy"], row["effect_count"])
            for row in connection.execute(
                """
                SELECT id, undo_policy, effect_count
                FROM transactions
                WHERE id LIKE 'txn_%_malformed'
                   OR id = 'txn_pending_primitives'
                """
            )
        }
        assert policies == {
            "txn_pending_malformed": ("none", 0),
            "txn_failed_malformed": ("none", 0),
            "txn_pending_primitives": ("snapshot", 3),
        }
        links = {
            (
                row["workflow_key"],
                row["relation"],
                row["entity_kind"],
                row["entity_key"],
            )
            for row in connection.execute(
                """
                SELECT
                    workflow_key,
                    relation,
                    entity_kind,
                    entity_key
                FROM workflow_entity_links
                WHERE workflow_key IN (
                    'txn_pending_malformed',
                    'txn_failed_malformed',
                    'txn_pending_primitives'
                )
                """
            )
        }
        assert links == {
            (
                "txn_pending_primitives",
                "after",
                "pantry_batches",
                "71",
            )
        }
    finally:
        connection.close()


def test_019_backfills_policy_effect_count_and_before_after_lineage(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "diet.sqlite")
    try:
        apply_migrations(connection, _migration_subset(tmp_path, 18))
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (?, 'pantry_add', 'committed', ?, ?, ?, ?, ?)
            """,
            (
                "txn_with_effect",
                "2026-07-30T00:00:00Z",
                "2026-07-30T00:00:00Z",
                "seed",
                '[{"row":null,"row_id":41,"table":"pantry_batches"}]',
                '[{"row":{"id":41},"row_id":41,"table":"pantry_batches"}]',
            ),
        )
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (
                'txn_empty_import', 'record_correction', 'committed',
                '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z',
                'portable data import', '[]', '[]'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (
                'txn_nonempty_import', 'record_correction', 'committed',
                '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z',
                'content-independent import label',
                '[{"row":null,"row_id":51,"table":"pantry_batches"}]',
                '[{"row":{"id":51},"row_id":51,"table":"pantry_batches"}]'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO operation_previews (
                token_hash,
                operation_type,
                request_json,
                result_json,
                resource_versions_json,
                created_at,
                expires_at,
                consumed_at,
                transaction_id
            ) VALUES (
                ?,
                'import_preview',
                '{}',
                '{}',
                '{}',
                '2026-07-30T00:00:00Z',
                '2026-07-30T01:00:00Z',
                '2026-07-30T00:01:00Z',
                'txn_nonempty_import'
            )
            """,
            ("1" * 64,),
        )
        connection.commit()

        apply_migrations(connection, PROJECT_ROOT / "migrations")

        normal = connection.execute(
            """
            SELECT undo_policy, effect_count
            FROM transactions
            WHERE id = 'txn_with_effect'
            """
        ).fetchone()
        empty = connection.execute(
            """
            SELECT undo_policy, effect_count
            FROM transactions
            WHERE id = 'txn_empty_import'
            """
        ).fetchone()
        nonempty_import = connection.execute(
            """
            SELECT undo_policy, effect_count
            FROM transactions
            WHERE id = 'txn_nonempty_import'
            """
        ).fetchone()
        links = {
            (row["relation"], row["entity_kind"], row["entity_key"])
            for row in connection.execute(
                """
                SELECT relation, entity_kind, entity_key
                FROM workflow_entity_links
                WHERE workflow_key = 'txn_with_effect'
                """
            )
        }

        assert tuple(normal) == ("snapshot", 1)
        assert tuple(empty) == ("none", 0)
        assert tuple(nonempty_import) == ("none", 1)
        assert links == {
            ("before", "pantry_batches", "41"),
            ("after", "pantry_batches", "41"),
        }
    finally:
        connection.close()


def test_019_marks_nonempty_history_before_privacy_deletion_nonundoable(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "privacy-history.sqlite")
    try:
        apply_migrations(connection, _migration_subset(tmp_path, 18))
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (
                'txn_before_privacy_delete', 'meal_record', 'committed',
                '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z',
                'ordinary audit text',
                '[{"row":null,"row_id":61,"table":"meals"}]',
                '[{"row":{"id":61},"row_id":61,"table":"meals"}]'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO operation_previews (
                token_hash,
                operation_type,
                request_json,
                result_json,
                resource_versions_json,
                created_at,
                expires_at,
                consumed_at,
                transaction_id
            ) VALUES (
                ?,
                'delete_data_preview',
                '{"scope":"raw_source_text"}',
                '{"deletion":{"scope":"raw_source_text"}}',
                '{}',
                '2026-07-30T00:00:00Z',
                '2026-07-30T01:00:00Z',
                '2026-07-30T00:01:00Z',
                NULL
            )
            """,
            ("2" * 64,),
        )
        connection.execute(
            """
            INSERT INTO privacy_erasure_tombstones (
                erasure_handle,
                preview_token_hash,
                scope,
                affected_counts_json,
                summary_sha256,
                committed_at
            ) VALUES (
                ?,
                ?,
                'raw_source_text',
                '{"transactions":1}',
                ?,
                '2026-07-30T00:01:00Z'
            )
            """,
            ("erase_" + "e" * 24, "2" * 64, "3" * 64),
        )
        connection.commit()

        apply_migrations(connection, PROJECT_ROOT / "migrations")

        row = connection.execute(
            """
            SELECT undo_policy, effect_count
            FROM transactions
            WHERE id = 'txn_before_privacy_delete'
            """
        ).fetchone()
        assert tuple(row) == ("none", 1)
    finally:
        connection.close()


def test_019_adds_unique_control_operation_tombstone_binding(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "tombstones.sqlite")
    try:
        apply_migrations(connection, _migration_subset(tmp_path, 18))
        for suffix in ("a", "b"):
            connection.execute(
                """
                INSERT INTO privacy_erasure_tombstones (
                    erasure_handle,
                    preview_token_hash,
                    scope,
                    affected_counts_json,
                    summary_sha256,
                    committed_at
                ) VALUES (?, ?, 'raw_source_text', '{}', ?, ?)
                """,
                (
                    "erase_" + suffix * 24,
                    suffix * 64,
                    suffix * 64,
                    f"2026-07-30T00:0{ord(suffix) - ord('a')}:00Z",
                ),
            )
        connection.commit()

        apply_migrations(connection, PROJECT_ROOT / "migrations")

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(privacy_erasure_tombstones)"
            )
        }
        index_sql = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index'
              AND name = 'idx_privacy_erasure_tombstones_control_operation'
            """
        ).fetchone()["sql"]

        assert "control_operation_handle" in columns
        assert "WHERE control_operation_handle IS NOT NULL" in index_sql

        assert connection.execute(
            """
            SELECT count(*)
            FROM privacy_erasure_tombstones
            WHERE control_operation_handle IS NULL
            """
        ).fetchone()[0] == 2

        control_handle = "mop_" + "a" * 32
        _insert_tombstone(
            connection,
            suffix="c",
            control_handle=control_handle,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_tombstone(
                connection,
                suffix="d",
                control_handle=control_handle,
            )
    finally:
        connection.close()


def test_control_002_migrates_duplicate_legacy_evidence_and_checks_values(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "maintenance.sqlite"
    legacy = MaintenanceController(
        database_path,
        _control_migration_subset(tmp_path, 1),
    )
    try:
        operation = legacy.connection.execute(
            """
            INSERT INTO maintenance_operations (
                operation_handle,
                action,
                parameters_sha256,
                status,
                accepted_at
            ) VALUES (
                'mtn_legacy',
                'backup',
                ?,
                'committed',
                '2026-07-30T00:00:00Z'
            )
            """,
            ("0" * 64,),
        )
        operation_id = operation.lastrowid
        assert operation_id is not None
        legacy.connection.executemany(
            """
            INSERT INTO maintenance_artifacts (
                operation_id,
                artifact_kind,
                relative_name
            ) VALUES (?, 'backup', 'same.sqlite')
            """,
            [(operation_id,), (operation_id,)],
        )
        legacy.connection.executemany(
            """
            INSERT INTO maintenance_checks (
                operation_id,
                check_code,
                outcome,
                checked_at
            ) VALUES (?, 'integrity', 'pass', '2026-07-30T00:00:00Z')
            """,
            [(operation_id,), (operation_id,)],
        )
        legacy.connection.commit()
    finally:
        legacy.close()

    controller = MaintenanceController(
        database_path,
        PROJECT_ROOT / "control-migrations",
    )
    try:
        operation_columns = {
            row["name"]
            for row in controller.connection.execute(
                "PRAGMA table_info(maintenance_operations)"
            )
        }
        artifact_columns = {
            row["name"]
            for row in controller.connection.execute(
                "PRAGMA table_info(maintenance_artifacts)"
            )
        }
        check_columns = {
            row["name"]
            for row in controller.connection.execute(
                "PRAGMA table_info(maintenance_checks)"
            )
        }
        quarantine_columns = {
            row["name"]
            for row in controller.connection.execute(
                "PRAGMA table_info(maintenance_quarantine_items)"
            )
        }
        indexes = {
            row["name"]
            for row in controller.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

        assert {
            "reconciliation_decision",
            "exclusive_released_at",
        } <= operation_columns
        assert {
            "stage_code",
            "expected_sha256",
            "observed_sha256",
        } <= artifact_columns
        assert {
            "stage_code",
            "expected_json",
            "observed_json",
        } <= check_columns
        assert {
            "operation_id",
            "item_key",
            "original_relative_name",
            "staged_relative_name",
            "sha256",
            "state",
            "recorded_at",
            "resolved_at",
        } <= quarantine_columns
        assert {
            "idx_maintenance_artifact_stage_unique",
            "idx_maintenance_check_stage_unique",
            "idx_maintenance_quarantine_state",
        } <= indexes
        artifact_stages = [
            row["stage_code"]
            for row in controller.connection.execute(
                """
                SELECT stage_code
                FROM maintenance_artifacts
                ORDER BY id
                """
            )
        ]
        check_stages = [
            row["stage_code"]
            for row in controller.connection.execute(
                """
                SELECT stage_code
                FROM maintenance_checks
                ORDER BY id
                """
            )
        ]
        assert artifact_stages == ["legacy:1", "legacy:2"]
        assert check_stages == ["legacy:1", "legacy:2"]

        with pytest.raises(sqlite3.IntegrityError):
            controller.connection.execute(
                """
                INSERT INTO maintenance_artifacts (
                    operation_id,
                    stage_code,
                    artifact_kind,
                    relative_name,
                    expected_sha256
                ) VALUES (?, 'backup-created', 'backup', 'new.sqlite', ?)
                """,
                (operation_id, "A" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError):
            controller.connection.execute(
                """
                INSERT INTO maintenance_checks (
                    operation_id,
                    stage_code,
                    check_code,
                    outcome,
                    checked_at,
                    expected_json
                ) VALUES (
                    ?,
                    'integrity-checked',
                    'integrity',
                    'pass',
                    '2026-07-30T00:00:00Z',
                    'not-json'
                )
                """,
                (operation_id,),
            )

        controller.connection.execute(
            """
            INSERT INTO maintenance_quarantine_items (
                operation_id, item_key, original_relative_name,
                staged_relative_name, sha256, state, recorded_at
            ) VALUES (
                ?, ?, 'cache/a.bin', 'control/q/a.bin', ?,
                'purge_pending', ?
            )
            """,
            (
                operation_id,
                "a" * 64,
                "b" * 64,
                "2026-07-30T00:00:00Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            controller.connection.execute(
                """
                INSERT INTO maintenance_quarantine_items (
                    operation_id, item_key, original_relative_name,
                    staged_relative_name, sha256, state, recorded_at
                ) VALUES (
                    ?, ?, 'cache/b.bin', 'control/q/b.bin', ?,
                    'unknown', ?
                )
                """,
                (
                    operation_id,
                    "c" * 64,
                    "d" * 64,
                    "2026-07-30T00:00:00Z",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            controller.connection.execute(
                """
                INSERT INTO maintenance_quarantine_items (
                    operation_id, item_key, original_relative_name,
                    staged_relative_name, sha256, state, recorded_at
                ) VALUES (
                    ?, ?, 'cache/c.bin', 'control/q/c.bin', ?,
                    'planned', ?
                )
                """,
                (
                    operation_id,
                    "a" * 64,
                    "e" * 64,
                    "2026-07-30T00:00:00Z",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            controller.connection.execute(
                """
                INSERT INTO maintenance_quarantine_items (
                    operation_id, item_key, original_relative_name,
                    staged_relative_name, sha256, state, recorded_at
                ) VALUES (
                    ?, ?, 'cache/a.bin', 'control/q/d.bin', ?,
                    'planned', ?
                )
                """,
                (
                    operation_id,
                    "f" * 64,
                    "0" * 64,
                    "2026-07-30T00:00:00Z",
                ),
            )
    finally:
        controller.close()
