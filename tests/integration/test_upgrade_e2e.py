from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from personal_diet_pantry import database
from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ROOT = (
    PROJECT_ROOT.parents[2]
    / "0.5.0"
    / "source"
    / "personal-diet-pantry"
)


def _legacy_request(
    data_dir: Path,
    request: dict[str, object],
) -> dict[str, object]:
    assert LEGACY_ROOT.is_dir(), (
        "immutable 0.5.0 source is required for the upgrade rehearsal"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LEGACY_ROOT / "python")
    environment["PERSONAL_DIET_PANTRY_DATA_DIR"] = str(data_dir)
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-m", "personal_diet_pantry.cli"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        check=True,
        cwd=LEGACY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    response = json.loads(completed.stdout)
    assert isinstance(response, dict)
    return response


def _create_legacy_water_database(data_dir: Path) -> None:
    if LEGACY_ROOT.is_dir():
        result = _legacy_request(
            data_dir,
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": 300,
                    "unit": "ml",
                    "occurred_at": "2026-07-29T08:00:00+08:00",
                    "source_text": "0.5.0 升级夹具饮水",
                },
            },
        )
        assert result["ok"] is True
        return

    data_dir.mkdir(parents=True)
    migration_dir = data_dir / "legacy-migrations"
    migration_dir.mkdir()
    for source in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 11:
            shutil.copy2(source, migration_dir / source.name)
    connection = database.connect_database(data_dir / "diet.sqlite")
    try:
        database.apply_migrations(connection, migration_dir)
        connection.execute(
            """
            INSERT INTO nutrition_goal_profiles (
                id, calories_kcal, protein_g, fat_g, carbohydrate_g,
                fiber_g, sodium_mg, water_ml, timezone_name, updated_at
            ) VALUES (
                1, 2000, 75, 60, 250, 25, 2300, 2000,
                'Asia/Shanghai', '2026-07-29T00:00:00Z'
            )
            """
        )
        transaction_id = "txn_legacy_water_fixture"
        before = json.dumps(
            [{"table": "water_logs", "row_id": 1, "row": None}],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        water_row = {
            "id": 1,
            "occurred_at": "2026-07-29T00:00:00Z",
            "amount_ml": 300,
            "source_text": "0.5.0 升级夹具饮水",
            "created_at": "2026-07-29T00:00:00Z",
            "updated_at": "2026-07-29T00:00:00Z",
            "deleted_at": None,
            "transaction_id": transaction_id,
        }
        after = json.dumps(
            [{"table": "water_logs", "row_id": 1, "row": water_row}],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, committed_at,
                source_text, before_snapshot, after_snapshot
            ) VALUES (?, 'water_record', 'committed', ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                "2026-07-29T00:00:00Z",
                "2026-07-29T00:00:00Z",
                "portable 0.5.0 schema fixture",
                before,
                after,
            ),
        )
        connection.execute(
            """
            INSERT INTO water_logs (
                id, occurred_at, amount_ml, source_text, created_at,
                updated_at, deleted_at, transaction_id
            ) VALUES (1, ?, 300, ?, ?, ?, NULL, ?)
            """,
            (
                "2026-07-29T00:00:00Z",
                "0.5.0 升级夹具饮水",
                "2026-07-29T00:00:00Z",
                "2026-07-29T00:00:00Z",
                transaction_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _schema_snapshot(database_path: Path) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(database_path)
    try:
        return tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )
    finally:
        connection.close()


def _broken_source_root(tmp_path: Path) -> Path:
    root = tmp_path / "broken-source"
    root.mkdir()
    for name in ("config", "rules", "templates", "migrations"):
        shutil.copytree(PROJECT_ROOT / name, root / name)
    migration = root / "migrations" / "012_goal_provenance.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8")
        + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    return root


def test_immutable_v050_database_upgrades_without_data_loss(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "upgrade-data"
    _create_legacy_water_database(data_dir)

    service = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    )
    try:
        versions = [
            row[0]
            for row in service.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        self_check = service.dispatch(
            {
                "domain": "system",
                "action": "self_check",
                "payload": {},
            }
        )
        water = service.dispatch(
            {
                "domain": "water",
                "action": "query",
                "payload": {"occurred_on": "2026-07-29"},
            }
        )
        goals = service.dispatch(
            {
                "domain": "system",
                "action": "query_goals",
                "payload": {},
            }
        )
        weight = service.dispatch(
            {
                "domain": "weight",
                "action": "record",
                "payload": {
                    "weight": 105,
                    "unit": "kg",
                    "status_note": "升级验证",
                },
            }
        )
    finally:
        service.close()

    expected_versions = [
        int(path.name.split("_", 1)[0])
        for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    ]
    assert versions == expected_versions
    assert not [
        check
        for check in self_check["data"]["checks"]
        if check["level"] == "FAIL"
    ]
    assert water["data"]["summary"]["total_ml"] == 300
    assert weight["data"]["record"]["weight_kg"] == "105"
    assert (
        weight["data"]["summary"]["seven_day_average_kg"]
        == "105.0"
    )
    assert goals["data"]["goal_profile"]["goal_source"] in {
        "configuration_default",
        "user_confirmed",
    }


def test_failed_012_upgrade_rolls_back_legacy_schema(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "rollback-data"
    _create_legacy_water_database(data_dir)
    database_path = data_dir / "diet.sqlite"
    before = _schema_snapshot(database_path)

    service = DietService(
        _broken_source_root(tmp_path),
        plugin_config={"dataDir": str(data_dir)},
        env={},
    )
    try:
        degraded = service.dispatch(
            {
                "domain": "system",
                "action": "query_goals",
                "payload": {},
            }
        )
    finally:
        service.close()

    assert degraded["ok"] is False
    assert degraded["error"]["code"] == "DATABASE_INTEGRITY_ERROR"
    assert _schema_snapshot(database_path) == before
    connection = sqlite3.connect(database_path)
    try:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(nutrition_goal_profiles)"
            )
        }
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    finally:
        connection.close()
    assert "goal_source" not in columns
    assert "confirmed_at" not in columns
    assert versions == list(range(1, 12))
