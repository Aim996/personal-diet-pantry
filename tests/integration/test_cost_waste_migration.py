from __future__ import annotations

from pathlib import Path
import shutil

from personal_diet_pantry import database


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migrations_through(tmp_path: Path, version: int) -> Path:
    target = tmp_path / f"migrations-{version}"
    target.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= version:
            shutil.copy2(source, target / source.name)
    return target


def test_017_preserves_legacy_price_as_structurally_unknown(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "diet.sqlite")
    try:
        database.apply_migrations(connection, _migrations_through(tmp_path, 16))
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (
                'txn_legacy_price', 'pantry_add', 'committed',
                '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z',
                'legacy batch', '[]', '[]'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pantry_batches (
                food_name, normalized_name, added_at,
                initial_quantity, remaining_quantity, unit, price,
                status, source, version, transaction_id
            ) VALUES (
                '鸡蛋', 'egg', '2026-07-30T00:00:00Z',
                12, 12, 'piece', 18.5,
                'active', 'manual', 1, 'txn_legacy_price'
            )
            """
        )
        connection.commit()

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")
        row = connection.execute(
            """
            SELECT price, price_minor, currency, remaining_cost_minor
            FROM pantry_batches
            """
        ).fetchone()

        assert row["price"] == 18.5
        assert row["price_minor"] is None
        assert row["currency"] is None
        assert row["remaining_cost_minor"] is None
        expected_version = max(
            int(path.name.split("_", 1)[0])
            for path in (PROJECT_ROOT / "migrations").glob("*.sql")
        )
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == expected_version
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
