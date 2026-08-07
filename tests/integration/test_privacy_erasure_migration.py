from __future__ import annotations

from pathlib import Path

from personal_diet_pantry import database


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_privacy_erasure_migration_is_present_and_idempotent(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "diet.sqlite")
    try:
        database.apply_migrations(connection, PROJECT_ROOT / "migrations")
        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "portable_entity_handles",
            "privacy_erasure_tombstones",
        } <= tables
        expected_version = max(
            int(path.name.split("_", 1)[0])
            for path in (PROJECT_ROOT / "migrations").glob("*.sql")
        )
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == expected_version
        preview_sql = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'operation_previews'
            """
        ).fetchone()[0]
        assert "import_preview" in preview_sql
        assert "delete_data_preview" in preview_sql
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
