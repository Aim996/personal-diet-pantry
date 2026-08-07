from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import shutil

import pytest

from personal_diet_pantry import database
from personal_diet_pantry.package_semantics import (
    PackageSemanticError,
    PackageSpec,
    remaining_display_quantity,
    to_base_quantity,
    validate_package_spec,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration_subset(tmp_path: Path, *, through: int) -> Path:
    target = tmp_path / f"migrations-{through}"
    target.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= through:
            shutil.copy2(source, target / source.name)
    return target


def test_021_preserves_legacy_batch_with_unknown_package_facts(
    tmp_path: Path,
) -> None:
    connection = database.connect_database(tmp_path / "upgrade.sqlite")
    try:
        database.apply_migrations(
            connection,
            _migration_subset(tmp_path, through=20),
        )
        transaction_id = "00000000-0000-4000-8000-000000000021"
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
                initial_quantity, remaining_quantity, unit, status,
                source, version, transaction_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Legacy Egg",
                "legacy egg",
                "2026-08-01T00:00:00Z",
                "2026-08-31T00:00:00Z",
                12,
                12,
                "piece",
                "active",
                "manual",
                1,
                transaction_id,
            ),
        )
        connection.commit()

        database.apply_migrations(connection, PROJECT_ROOT / "migrations")

        row = connection.execute(
            """
            SELECT initial_display_quantity, display_unit,
                   base_quantity_per_display_unit, package_hierarchy_json
            FROM pantry_batches
            WHERE normalized_name = 'legacy egg'
            """
        ).fetchone()
        assert tuple(row) == (None, None, None, None)
    finally:
        connection.close()


def test_package_spec_validates_exact_base_quantity_and_converts_units() -> None:
    spec = PackageSpec(
        initial_display_quantity=Decimal("3"),
        display_unit="盒",
        base_quantity_per_display_unit=Decimal("180"),
        package_hierarchy=({"unit": "箱", "contains": "6", "child_unit": "盒"},),
    )

    assert validate_package_spec(base_quantity=Decimal("540"), spec=spec) == spec
    assert to_base_quantity(
        Decimal("2"), "盒", base_unit="g", spec=spec
    ) == (Decimal("360"), "g")
    assert remaining_display_quantity(Decimal("270"), spec=spec) == Decimal(
        "1.5"
    )

    with pytest.raises(PackageSemanticError, match="conflicts"):
        validate_package_spec(base_quantity=Decimal("500"), spec=spec)

