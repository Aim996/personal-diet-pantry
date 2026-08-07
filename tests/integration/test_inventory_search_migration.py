from pathlib import Path

from personal_diet_pantry.database import apply_migrations, connect_database


ROOT = Path(__file__).resolve().parents[2]


def test_inventory_search_migration_adds_index_and_product_reference(tmp_path):
    connection = connect_database(tmp_path / "diet.sqlite")
    apply_migrations(connection, ROOT / "migrations")

    indexes = {
        row["name"]
        for row in connection.execute("PRAGMA index_list('pantry_batches')")
    }
    assert "idx_pantry_batches_search" in indexes
    assert "idx_pantry_batches_match_key" in indexes

    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='operation_previews'"
    ).fetchone()["sql"]
    assert "pantry_product_reference" in sql
