from __future__ import annotations

from pathlib import Path
import shutil

from personal_diet_pantry import database


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration_subset(tmp_path: Path, *, through: int) -> Path:
    target = tmp_path / f"migrations-{through}"
    target.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, target / source.name)
    return target


def test_015_adds_body_weight_storage_without_changing_existing_data(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "upgrade.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=14),
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
                expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "a" * 64,
                "meal_preview",
                '{"meal":"existing"}',
                '{"preview":"existing"}',
                "{}",
                "2026-07-30T00:00:00Z",
                "2026-07-30T00:30:00Z",
            ),
        )
        connection.commit()
        before_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        after_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert before_tables <= after_tables
        assert "body_weight_logs" in after_tables
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(body_weight_logs)"
            )
        }
        assert {
            "id",
            "measured_at",
            "weight_g",
            "status_note",
            "version",
            "created_at",
            "updated_at",
            "deleted_at",
            "transaction_id",
        } == columns
        table_sql = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'body_weight_logs'
            """
        ).fetchone()["sql"]
        assert "AUTOINCREMENT" in table_sql
        indexes = {
            row["name"]
            for row in connection.execute(
                "PRAGMA index_list(body_weight_logs)"
            )
        }
        assert "idx_body_weight_logs_active_measured_at" in indexes
        preserved_preview = connection.execute(
            """
            SELECT operation_type, request_json, result_json
            FROM operation_previews
            WHERE token_hash = ?
            """,
            ("a" * 64,),
        ).fetchone()
        assert dict(preserved_preview) == {
            "operation_type": "meal_preview",
            "request_json": '{"meal":"existing"}',
            "result_json": '{"preview":"existing"}',
        }
        migrations = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        expected = {
            int(path.name.split("_", 1)[0]): path.name
            for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
        }
        observed = {
            int(row["version"]): str(row["name"])
            for row in migrations
        }
        assert observed == expected
    finally:
        connection.close()
