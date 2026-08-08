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


def test_022_marks_legacy_pantry_defaults_without_changing_inventory(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "upgrade.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=21),
        )
        transaction_id = "00000000-0000-4000-8000-000000000022"
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, source_text
            ) VALUES (?, 'pantry_add', 'pending', ?, ?)
            """,
            (transaction_id, "2026-08-01T00:00:00Z", "legacy row"),
        )
        connection.execute(
            """
            INSERT INTO pantry_batches (
                food_name, normalized_name, added_at, expires_at,
                initial_quantity, remaining_quantity, unit, storage_location,
                status, source, version, transaction_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "旧版酸奶",
                "旧版酸奶",
                "2026-08-01T00:00:00Z",
                "2026-08-08T00:00:00Z",
                2,
                2,
                "盒",
                "冷藏",
                "active",
                "manual",
                1,
                transaction_id,
            ),
        )
        connection.commit()

        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=22),
        )

        row = connection.execute(
            """
            SELECT remaining_quantity, storage_location, expires_at,
                   storage_location_source, expiry_source
            FROM pantry_batches
            WHERE normalized_name = '旧版酸奶'
            """
        ).fetchone()
        assert tuple(row) == (
            2,
            "冷藏",
            "2026-08-08T00:00:00Z",
            "legacy_unknown",
            "legacy_unknown",
        )
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == 22
    finally:
        connection.close()
