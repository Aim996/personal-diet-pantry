from __future__ import annotations

from pathlib import Path

from personal_diet_pantry.database import apply_migrations, connect_database
from personal_diet_pantry.workflow_lineage import index_transaction_snapshots


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_transaction_snapshot_index_records_before_and_after_only(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "diet.sqlite")
    try:
        apply_migrations(connection, PROJECT_ROOT / "migrations")
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, source_text
            ) VALUES (
                'txn_lineage', 'pantry_add', 'pending',
                '2026-07-30T00:00:00Z', 'private food text'
            )
            """
        )
        index_transaction_snapshots(
            connection,
            transaction_id="txn_lineage",
            before_snapshot=(
                '[{"row":null,"row_id":9,"table":"pantry_batches"}]'
            ),
            after_snapshot=(
                '[{"row":{"id":9},"row_id":9,'
                '"table":"pantry_batches"}]'
            ),
            created_at="2026-07-30T00:00:00Z",
        )

        rows = connection.execute(
            """
            SELECT workflow_kind, workflow_key, entity_kind,
                   entity_key, relation
            FROM workflow_entity_links
            ORDER BY relation
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "transaction",
                "txn_lineage",
                "pantry_batches",
                "9",
                "after",
            ),
            (
                "transaction",
                "txn_lineage",
                "pantry_batches",
                "9",
                "before",
            ),
        ]
        assert "private food text" not in repr(rows)
    finally:
        connection.close()
