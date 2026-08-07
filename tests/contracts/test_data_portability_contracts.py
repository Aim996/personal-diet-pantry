from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from personal_diet_pantry.service import DietService
from personal_diet_pantry.transactions import (
    TransactionManager,
    TransactionNotUndoable,
)
from tests.contracts.helpers import recorded_meal


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dispatch(
    service: DietService,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": "system", "action": action, "payload": payload}
    )


def _seed_portable_facts(service: DietService) -> None:
    assert service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": "300",
                "unit": "ml",
                "occurred_at": "2026-07-30T01:00:00Z",
                "source_text": "=WEBSERVICE(\"https://example.invalid\")",
            },
        }
    )["ok"] is True
    assert service.dispatch(
        {
            "domain": "weight",
            "action": "record",
            "payload": {"weight": 105, "unit": "kg", "status_note": "空腹"},
        }
    )["ok"] is True
    assert service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "鸡蛋",
                "normalized_name": "egg",
                "quantity": "12",
                "unit": "piece",
                "added_at": "2026-07-30T00:00:00Z",
                "expires_at": "2026-08-30T00:00:00Z",
                "price_minor": 1200,
                "currency": "CNY",
                "notes": "temporary sk-abcdefghijklmnopqrst test token",
                "source_text": "买了12个鸡蛋",
            },
        }
    )["ok"] is True
    recorded_meal(service)


def _artifact_path(service: DietService, result: dict[str, object]) -> Path:
    name = result["data"]["export"]["name"]
    assert Path(name).name == name
    return service.data_paths.exports / name


def _assert_no_private_keys(value: object) -> None:
    forbidden = {
        "id",
        "transaction_id",
        "source_session_key",
        "source_session_hash",
        "source_model",
        "test_run_id",
        "preview_token",
        "token",
        "absolute_path",
    }
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for key, child in value.items():
            assert not key.endswith("_id")
            _assert_no_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_private_keys(child)


def test_json_export_is_versioned_atomic_private_and_handle_stable(
    service: DietService,
) -> None:
    _seed_portable_facts(service)
    first = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "export-json-1"},
    )
    second = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "export-json-2"},
    )
    assert first["ok"] is True
    assert second["ok"] is True
    first_path = _artifact_path(service, first)
    second_path = _artifact_path(service, second)
    assert first_path.is_file()
    assert second_path.is_file()
    assert not list(service.data_paths.exports.glob("*.tmp"))

    first_bundle = json.loads(first_path.read_text(encoding="utf-8"))
    second_bundle = json.loads(second_path.read_text(encoding="utf-8"))
    manifest = first_bundle["manifest"]
    assert manifest["export_schema_version"] == 1
    assert manifest["contract_version"] == 1
    assert manifest["record_counts"]["meals"] == 1
    assert manifest["record_counts"]["water_logs"] == 1
    records_text = json.dumps(
        first_bundle["records"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert manifest["records_sha256"] == hashlib.sha256(
        records_text.encode("utf-8")
    ).hexdigest()
    assert (
        first_bundle["records"]["meals"][0]["external_handle"]
        == second_bundle["records"]["meals"][0]["external_handle"]
    )
    _assert_no_private_keys(first_bundle)
    exported_text = first_path.read_text(encoding="utf-8")
    assert str(service.data_paths.root) not in exported_text
    assert "sk-abcdefghijklmnopqrst" not in exported_text
    assert manifest["quality_summary"]["redacted_value_count"] >= 1


def test_csv_export_cells_are_formula_safe(
    service: DietService,
    tmp_path: Path,
) -> None:
    _seed_portable_facts(service)
    result = _dispatch(
        service,
        "export_data",
        {"format": "csv", "operation_key": "export-csv-1"},
    )
    assert result["ok"] is True
    path = _artifact_path(service, result)
    assert path.suffix == ".zip"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert all(
            not name.startswith(("/", "\\"))
            and ".." not in Path(name).parts
            for name in names
        )
        for name in names:
            if not name.endswith(".csv"):
                continue
            rows = csv.reader(
                archive.read(name).decode("utf-8-sig").splitlines()
            )
            for row in rows:
                for cell in row:
                    assert not cell.startswith(("=", "+", "-", "@"))
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "csv-target")},
    ) as target:
        name = "portable.zip"
        shutil.copy2(path, target.data_paths.imports / name)
        validated = _dispatch(
            target,
            "validate_import",
            {"import_name": name},
        )
        assert validated["ok"] is True
        committed = _dispatch(
            target,
            "import_data",
            {
                "commit_handle": validated["data"]["workflow"]["commit_handle"],
                "confirmed": True,
                "operation_key": "csv-import",
            },
        )
        assert committed["ok"] is True
        assert target.connection.execute(
            "SELECT count(*) FROM meals"
        ).fetchone()[0] == 1


def test_validate_import_accepts_v067_portability_bundle(
    service: DietService,
    tmp_path: Path,
) -> None:
    _seed_portable_facts(service)
    exported = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "legacy-export"},
    )
    bundle = json.loads(
        _artifact_path(service, exported).read_text(encoding="utf-8")
    )
    bundle["manifest"]["product_version"] = "0.6.7"

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "legacy-target")},
        env={},
    ) as target:
        import_name = "legacy-v067.json"
        (target.data_paths.imports / import_name).write_text(
            json.dumps(
                bundle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        validated = _dispatch(
            target,
            "validate_import",
            {"import_name": import_name},
        )

    assert validated["ok"] is True


def test_validate_then_import_round_trip_is_atomic_and_retry_safe(
    service: DietService,
    tmp_path: Path,
) -> None:
    _seed_portable_facts(service)
    exported = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "round-trip-export"},
    )
    source = _artifact_path(service, exported)
    target_root = tmp_path / "target"
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(target_root)},
    ) as target:
        import_name = "portable.json"
        shutil.copy2(source, target.data_paths.imports / import_name)
        before = {
            table: target.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in ("meals", "water_logs", "body_weight_logs", "pantry_batches")
        }
        validated = _dispatch(
            target,
            "validate_import",
            {"import_name": import_name},
        )
        assert validated["ok"] is True
        assert validated["data"]["validation"]["valid"] is True
        assert before == {
            table: target.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in before
        }
        handle = validated["data"]["workflow"]["commit_handle"]
        committed = _dispatch(
            target,
            "import_data",
            {
                "commit_handle": handle,
                "confirmed": True,
                "operation_key": "round-trip-import",
            },
        )
        replayed = _dispatch(
            target,
            "import_data",
            {
                "commit_handle": handle,
                "confirmed": True,
                "operation_key": "round-trip-import",
            },
        )
        assert committed["ok"] is True
        assert replayed == committed
        assert committed["data"]["import"]["record_counts"] == exported["data"][
            "export"
        ]["record_counts"]
        for table in ("meals", "water_logs", "body_weight_logs", "pantry_batches"):
            assert target.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == service.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]


def test_validate_import_rejects_corruption_without_writes(
    service: DietService,
    tmp_path: Path,
) -> None:
    _seed_portable_facts(service)
    exported = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "corrupt-export"},
    )
    source = _artifact_path(service, exported)
    bundle = json.loads(source.read_text(encoding="utf-8"))
    bundle["records"]["water_logs"][0]["values"]["amount_ml"] = 999
    target_root = tmp_path / "corrupt-target"
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(target_root)},
    ) as target:
        name = "corrupt.json"
        (target.data_paths.imports / name).write_text(
            json.dumps(bundle, ensure_ascii=False),
            encoding="utf-8",
        )
        result = _dispatch(
            target,
            "validate_import",
            {"import_name": name},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert target.connection.execute(
            "SELECT count(*) FROM water_logs"
        ).fetchone()[0] == 0


def test_import_failure_rolls_back_all_business_rows(
    service: DietService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from personal_diet_pantry import data_import

    _seed_portable_facts(service)
    exported = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "rollback-export"},
    )
    source = _artifact_path(service, exported)
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "rollback-target")},
    ) as target:
        name = "portable.json"
        shutil.copy2(source, target.data_paths.imports / name)
        validated = _dispatch(
            target,
            "validate_import",
            {"import_name": name},
        )
        original = data_import._insert_record
        calls = 0

        def fail_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("injected import failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(
            data_import,
            "_dry_run_validate",
            lambda _connection, _records: None,
        )
        monkeypatch.setattr(data_import, "_insert_record", fail_after_first)
        result = _dispatch(
            target,
            "import_data",
            {
                "commit_handle": validated["data"]["workflow"]["commit_handle"],
                "confirmed": True,
                "operation_key": "rollback-import",
            },
        )
        assert result["ok"] is False
        for table in (
            "meals",
            "water_logs",
            "body_weight_logs",
            "pantry_batches",
        ):
            assert target.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0


def test_import_transaction_is_non_undoable_and_has_real_effect_count(
    service: DietService,
    tmp_path: Path,
) -> None:
    _seed_portable_facts(service)
    exported = _dispatch(
        service,
        "export_data",
        {"format": "json", "operation_key": "undo-policy-export"},
    )
    source = _artifact_path(service, exported)

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "undo-policy-target")},
    ) as target:
        import_name = "portable.json"
        shutil.copy2(source, target.data_paths.imports / import_name)
        validated = _dispatch(
            target,
            "validate_import",
            {"import_name": import_name},
        )
        committed = _dispatch(
            target,
            "import_data",
            {
                "commit_handle": validated["data"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
                "operation_key": "undo-policy-import",
            },
        )
        assert committed["ok"] is True

        row = target.connection.execute(
            """
            SELECT id, undo_policy, effect_count
            FROM transactions
            WHERE source_text = 'portable data import'
            """
        ).fetchone()
        recent = target.dispatch(
            {
                "domain": "transaction",
                "action": "get_recent",
                "payload": {
                    "operation": "undo",
                    "operation_type": "record_correction",
                },
            }
        )

        assert row["undo_policy"] == "none"
        assert row["effect_count"] == sum(
            committed["data"]["import"]["record_counts"].values()
        )
        assert recent["data"]["candidates"] == []
        with pytest.raises(TransactionNotUndoable):
            TransactionManager(target.connection).undo(row["id"])
