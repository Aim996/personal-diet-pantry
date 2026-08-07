from __future__ import annotations

import ctypes
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from personal_diet_pantry.data_erasure import _cleanup_transactions
from personal_diet_pantry import database, file_io, maintenance_control
from personal_diet_pantry import trusted_workflows as trusted_workflow_module
from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dispatch(
    service: DietService,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": "system", "action": action, "payload": payload}
    )


def _seed(service: DietService) -> None:
    assert service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": "300",
                "unit": "ml",
                "occurred_at": "2026-07-30T01:00:00Z",
                "source_text": "早上喝水",
            },
        }
    )["ok"] is True
    assert service.dispatch(
        {
            "domain": "weight",
            "action": "record",
            "payload": {"weight": 105, "status_note": "空腹"},
        }
    )["ok"] is True
    assert service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "苹果",
                "normalized_name": "apple",
                "quantity": "4",
                "unit": "piece",
                "added_at": "2026-07-30T00:00:00Z",
                "expires_at": "2026-08-10T00:00:00Z",
                "source_text": "买了苹果",
            },
        }
    )["ok"] is True
    assert service.dispatch(
        {
            "domain": "system",
            "action": "update_preferences",
            "payload": {
                "rule_type": "water_unit",
                "subject": "cup",
                "outcome": {"milliliters": 350},
                "source_text": "一杯按350毫升",
            },
        }
    )["ok"] is True


def _preview_and_commit(
    service: DietService,
    payload: dict[str, object],
    key: str,
) -> tuple[dict[str, object], dict[str, object]]:
    preview = _dispatch(service, "preview_delete_data", payload)
    assert preview["ok"] is True
    committed = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": key,
        },
    )
    assert committed["ok"] is True
    assert (
        committed["data"]["deletion"]["affected_counts"]
        == preview["data"]["preview"]["affected_counts"]
    )
    return preview, committed


def test_source_text_erasure_redacts_audit_copies_and_is_exact(
    service: DietService,
) -> None:
    _seed(service)
    _preview, committed = _preview_and_commit(
        service,
        {"scope": "raw_source_text"},
        "erase-source-text",
    )
    assert committed["data"]["deletion"]["scope"] == "raw_source_text"
    for table in ("water_logs", "pantry_batches"):
        columns = {
            row["name"]
            for row in service.connection.execute(f"PRAGMA table_info({table})")
        }
        if "source_text" in columns:
            assert {
                row[0]
                for row in service.connection.execute(
                    f"SELECT DISTINCT source_text FROM {table}"
                )
            } <= {"[removed]"}
    transactions = service.connection.execute(
        "SELECT source_text, before_snapshot, after_snapshot FROM transactions"
    ).fetchall()
    assert all(row["source_text"] == "[removed]" for row in transactions)
    assert all("早上喝水" not in (row["after_snapshot"] or "") for row in transactions)


def test_privacy_cleanup_marks_retained_transaction_non_undoable(
    service: DietService,
) -> None:
    _seed(service)
    target = service.connection.execute(
        """
        SELECT transactions.id
        FROM transactions
        JOIN water_logs
          ON water_logs.transaction_id = transactions.id
        WHERE transactions.before_snapshot != '[]'
          AND transactions.after_snapshot != '[]'
        LIMIT 1
        """
    ).fetchone()
    assert target is not None

    _cleanup_transactions(
        service.connection,
        (target["id"],),
    )
    rows = service.connection.execute(
        """
        SELECT before_snapshot, after_snapshot, undo_policy, effect_count
        FROM transactions
        WHERE id = ?
        """,
        (target["id"],),
    ).fetchall()
    assert rows
    assert all(
        row["before_snapshot"] == "[]"
        and row["after_snapshot"] == "[]"
        and row["undo_policy"] == "none"
        and row["effect_count"] == 0
        for row in rows
    )


def test_all_business_erasure_keeps_backups_and_replay_is_idempotent(
    service: DietService,
) -> None:
    _seed(service)
    backup = _dispatch(
        service,
        "backup",
        {"label": "before-erasure", "operation_key": "before-erasure-backup"},
    )
    assert backup["ok"] is True
    backup_names = {path.name for path in service.data_paths.backups.iterdir()}

    preview = _dispatch(
        service,
        "preview_delete_data",
        {"scope": "all_business"},
    )
    handle = preview["data"]["workflow"]["commit_handle"]
    payload = {
        "commit_handle": handle,
        "confirmed": True,
        "operation_key": "erase-all-business",
    }
    committed = _dispatch(service, "commit_delete_data", payload)
    replayed = _dispatch(service, "commit_delete_data", payload)
    assert committed["ok"] is True
    assert replayed == committed
    assert backup_names <= {
        path.name for path in service.data_paths.backups.iterdir()
    }
    assert service.connection.execute(
        "SELECT count(*) FROM water_logs"
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT count(*) FROM pantry_batches"
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT count(*) FROM personal_rules"
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT count(*) FROM privacy_erasure_tombstones"
    ).fetchone()[0] == 1
    goal = service.connection.execute(
        "SELECT goal_source, confirmed_at FROM nutrition_goal_profiles WHERE id = 1"
    ).fetchone()
    assert goal["goal_source"] == "configuration_default"
    assert goal["confirmed_at"] is None


def test_all_business_removes_canary_from_database_and_derived_files(
    service: DietService,
) -> None:
    marker = "ERASE-CANARY-070-UNIQUE"
    _seed(service)
    transaction_id = service.connection.execute(
        "SELECT id FROM transactions ORDER BY created_at LIMIT 1"
    ).fetchone()[0]
    snapshot = json.dumps(
        [
            {
                "row": {"id": 1, "source_text": marker},
                "row_id": 1,
                "table": "water_logs",
            }
        ],
        ensure_ascii=False,
    )
    service.connection.execute(
        """
        UPDATE transactions
        SET source_text = ?, before_snapshot = ?, after_snapshot = ?
        WHERE id = ?
        """,
        (marker, snapshot, snapshot, transaction_id),
    )
    service.connection.execute(
        """
        INSERT INTO operation_previews (
            token_hash, operation_type, request_json, result_json,
            resource_versions_json, created_at, expires_at
        ) VALUES (?, 'export_reference', ?, ?, '{}', ?, ?)
        """,
        (
            "a" * 64,
            json.dumps({"source_text": marker}),
            json.dumps({"source_text": marker}),
            "2026-07-30T00:00:00Z",
            "2099-07-30T00:00:00Z",
        ),
    )
    service.connection.commit()

    for directory, name in (
        (service.data_paths.cache, "canary.cache"),
        (service.data_paths.exports, "canary.json"),
        (service.data_paths.reports, "canary.md"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(marker, encoding="utf-8")

    _preview, committed = _preview_and_commit(
        service,
        {"scope": "all_business"},
        "erase-all-canary",
    )

    database_text = "\n".join(service.connection.iterdump())
    derived_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for directory in (
            service.data_paths.cache,
            service.data_paths.exports,
            service.data_paths.reports,
        )
        for path in directory.rglob("*")
        if path.is_file()
    )
    assert marker not in database_text
    assert marker not in derived_text
    assert committed["data"]["deletion"]["residue_count"] == 0
    assert committed["data"]["deletion"]["effect_count"] > 0


def test_delete_digest_is_rebuilt_after_begin_immediate(
    service: DietService,
) -> None:
    preview = _dispatch(
        service,
        "preview_delete_data",
        {"scope": "all_business"},
    )
    second = DietService(
        source_root=Path(__file__).resolve().parents[2],
        plugin_config={"dataDir": str(service.data_paths.root)},
        env={},
    )
    injected = False

    def inject(statement: str) -> None:
        nonlocal injected
        if statement.strip().upper() == "BEGIN IMMEDIATE" and not injected:
            injected = True
            written = second.dispatch(
                {
                    "domain": "water",
                    "action": "record",
                    "payload": {
                        "amount": "111",
                        "unit": "ml",
                        "occurred_at": "2026-07-30T02:00:00Z",
                        "source_text": "late concurrent water",
                    },
                }
            )
            assert written["ok"] is True

    try:
        service.connection.set_trace_callback(inject)
        committed = _dispatch(
            service,
            "commit_delete_data",
            {
                "commit_handle": preview["data"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
                "operation_key": "delete-lock-race",
            },
        )
    finally:
        service.connection.set_trace_callback(None)
        second.close()

    assert committed["ok"] is False
    assert committed["error"]["code"] == "STALE_PREVIEW"
    assert service.connection.execute(
        "SELECT count(*) FROM water_logs"
    ).fetchone()[0] >= 1


def test_file_created_after_manifest_blocks_database_commit(
    service: DietService,
    monkeypatch,
) -> None:
    _seed(service)
    preview = _dispatch(
        service,
        "preview_delete_data",
        {"scope": "all_business"},
    )

    def inject(checkpoint: str) -> None:
        if checkpoint == "before_second_derived_scan":
            path = service.data_paths.reports / "late.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("late private report", encoding="utf-8")

    monkeypatch.setattr(service.trusted_workflows, "_crash_probe", inject)
    result = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": "late-derived-before-commit",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "DERIVED_FILES_CHANGED"
    assert service.connection.execute(
        "SELECT count(*) FROM water_logs"
    ).fetchone()[0] > 0
    assert (service.data_paths.reports / "late.md").is_file()


def test_post_commit_derived_roots_record_zero_residue_evidence(
    service: DietService,
) -> None:
    _seed(service)
    _preview, result = _preview_and_commit(
        service,
        {"scope": "all_business"},
        "derived-zero-evidence",
    )
    assert result["ok"] is True
    evidence = service.maintenance_controller.connection.execute(
        """
        SELECT expected_json, observed_json, outcome
        FROM maintenance_checks
        WHERE check_code = 'derived_roots_zero_residue'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert json.loads(evidence["expected_json"]) == {"file_count": 0}
    assert json.loads(evidence["observed_json"]) == {"file_count": 0}
    assert evidence["outcome"] == "pass"


def test_post_commit_late_file_records_nonzero_and_never_reports_success(
    service: DietService,
    monkeypatch,
) -> None:
    _seed(service)
    preview = _dispatch(service, "preview_delete_data", {"scope": "all_business"})
    late = service.data_paths.reports / "post-commit-late.md"

    def inject(checkpoint: str) -> None:
        if checkpoint == "after_database_commit_before_residue_evidence":
            late.write_text("late private output", encoding="utf-8")

    monkeypatch.setattr(service.trusted_workflows, "_crash_probe", inject)
    result = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": "post-commit-late",
        },
    )
    assert result["ok"] is False
    evidence = service.maintenance_controller.connection.execute(
        """
        SELECT observed_json, outcome FROM maintenance_checks
        WHERE check_code = 'derived_roots_zero_residue'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert json.loads(evidence["observed_json"]) == {"file_count": 1}
    assert evidence["outcome"] == "fail"
    latest = service.maintenance_controller.history(1)[0]
    assert latest.status == "failed"
    assert late.is_file()


def test_file_created_during_purge_blocks_terminal_success(
    service: DietService,
    monkeypatch,
) -> None:
    private = service.data_paths.reports / "private.md"
    private.write_text("private", encoding="utf-8")
    preview = _dispatch(service, "preview_delete_data", {"scope": "all_business"})
    late = service.data_paths.exports / "late-during-purge.json"

    def inject(checkpoint: str) -> None:
        if checkpoint == "first_item_purged":
            late.write_text("late private export", encoding="utf-8")

    monkeypatch.setattr(service.trusted_workflows, "_crash_probe", inject)
    result = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": "late-during-purge",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VERIFICATION_REQUIRED"
    evidence = service.maintenance_controller.connection.execute(
        """
        SELECT outcome, observed_json FROM maintenance_checks
        WHERE check_code = 'erasure_quarantine_terminal'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert evidence["outcome"] == "fail"
    assert json.loads(evidence["observed_json"])["root_file_count"] == 1
    assert late.is_file()


def test_committed_recovery_requires_fresh_zero_root_scan(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"),
            str(tmp_path),
            "database_commit_complete",
        ],
        check=False,
    )
    assert completed.returncode == 91
    late = tmp_path / "reports" / "late-after-crash.md"
    late.write_text("late private report", encoding="utf-8")

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as reopened:
        latest = reopened.maintenance_controller.history(1)[0]
        assert latest.status == "failed"
        decision = reopened.maintenance_controller.connection.execute(
            """
            SELECT reconciliation_decision FROM maintenance_operations
            WHERE operation_handle = ?
            """,
            (latest.handle,),
        ).fetchone()[0]
        assert decision == "verification_required"
        evidence = reopened.maintenance_controller.connection.execute(
            """
            SELECT outcome, observed_json FROM maintenance_checks
            WHERE operation_id = (
                SELECT id FROM maintenance_operations
                WHERE operation_handle = ?
            )
              AND check_code = 'erasure_quarantine_terminal'
            """,
            (latest.handle,),
        ).fetchone()
        assert evidence["outcome"] == "fail"
        assert json.loads(evidence["observed_json"])["root_file_count"] == 1
        assert late.is_file()


@pytest.mark.parametrize(
    "checkpoint",
    ["database_commit_complete", "stage_replace_complete"],
    ids=["committed", "absent"],
)
def test_recovery_rejects_unmanifested_operation_quarantine_entry(
    tmp_path: Path,
    checkpoint: str,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"),
            str(tmp_path),
            checkpoint,
        ],
        check=False,
    )
    assert completed.returncode == 91
    quarantine_root = tmp_path / "control" / "erasure-quarantine"
    operation_dirs = [path for path in quarantine_root.iterdir() if path.is_dir()]
    assert len(operation_dirs) == 1
    residue = operation_dirs[0] / "unmanifested-private.bin"
    residue.write_bytes(b"unmanifested private residue")

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as reopened:
        latest = reopened.maintenance_controller.history(1)[0]
        operation = reopened.maintenance_controller.connection.execute(
            """
            SELECT id, status, reconciliation_decision
            FROM maintenance_operations
            WHERE operation_handle = ?
            """,
            (latest.handle,),
        ).fetchone()
        assert operation["status"] == "failed"
        assert operation["reconciliation_decision"] == "verification_required"
        evidence = reopened.maintenance_controller.connection.execute(
            """
            SELECT outcome, observed_json
            FROM maintenance_checks
            WHERE operation_id = ?
              AND check_code = 'erasure_quarantine_terminal'
            """,
            (operation["id"],),
        ).fetchone()
        assert evidence["outcome"] == "fail"
        observed = json.loads(evidence["observed_json"])
        assert observed["quarantine_entry_count"] == 1
        assert observed["quarantine_entries_valid"] is False
        assert residue.read_bytes() == b"unmanifested private residue"


def test_quarantine_item_state_transition_requires_exact_row(
    service: DietService,
) -> None:
    with pytest.raises(
        trusted_workflow_module.ErasureVerificationRequired,
        match="Quarantine item transition",
    ):
        service.trusted_workflows._set_item_state(
            987654321,
            "0" * 64,
            "staged",
        )


@pytest.mark.parametrize(
    ("checkpoint", "fault_target"),
    [
        ("database_commit_complete", "durable_unlink"),
        ("first_item_purged", "durable_rmdir"),
    ],
)
def test_startup_recovery_durability_fault_is_recorded_and_does_not_block_service(
    tmp_path: Path,
    monkeypatch,
    checkpoint: str,
    fault_target: str,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"),
            str(tmp_path),
            checkpoint,
        ],
        check=False,
    )
    assert completed.returncode == 91
    original = getattr(trusted_workflow_module, fault_target)
    injected = False

    def fail_once(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            raise OSError(f"injected startup {fault_target}")
        return original(*args, **kwargs)

    monkeypatch.setattr(trusted_workflow_module, fault_target, fail_once)
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as reopened:
        assert injected is True
        latest = reopened.maintenance_controller.history(1)[0]
        assert latest.status == "failed"
        row = reopened.maintenance_controller.connection.execute(
            """
            SELECT id, reconciliation_decision FROM maintenance_operations
            WHERE operation_handle = ?
            """,
            (latest.handle,),
        ).fetchone()
        assert row["reconciliation_decision"] == "verification_required"
        evidence = reopened.maintenance_controller.connection.execute(
            """
            SELECT outcome FROM maintenance_checks
            WHERE operation_id = ? AND check_code = 'erasure_recovery_failure'
            """,
            (row["id"],),
        ).fetchone()
        assert evidence["outcome"] == "fail"
        assert reopened.maintenance_controller.connection.execute(
            """
            SELECT count(*) FROM maintenance_quarantine_items
            WHERE operation_id = ?
            """,
            (row["id"],),
        ).fetchone()[0] > 0
        subsequent = reopened.dispatch(
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": "250",
                    "unit": "ml",
                    "occurred_at": "2026-07-30T04:00:00Z",
                    "source_text": "post recovery operation",
                },
            }
        )
        assert subsequent["ok"] is True


def test_service_closes_both_connections_when_startup_recovery_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    opened_business = []
    opened_controllers = []
    real_connect = database.connect_database
    real_controller = maintenance_control.MaintenanceController

    def capture_business(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened_business.append(connection)
        return connection

    def capture_controller(*args, **kwargs):
        controller = real_controller(*args, **kwargs)
        opened_controllers.append(controller)
        return controller

    def fail_recovery(self):
        raise OSError("injected startup recovery failure")

    monkeypatch.setattr(database, "connect_database", capture_business)
    monkeypatch.setattr(
        maintenance_control,
        "MaintenanceController",
        capture_controller,
    )
    monkeypatch.setattr(
        trusted_workflow_module.TrustedWorkflowModule,
        "recover_startup",
        fail_recovery,
    )

    with pytest.raises(OSError, match="injected startup recovery failure"):
        DietService(
            source_root=PROJECT_ROOT,
            plugin_config={"dataDir": str(tmp_path)},
            env={},
        )

    assert len(opened_business) == 1
    assert len(opened_controllers) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened_business[0].execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened_controllers[0].connection.execute("SELECT 1")


def _create_directory_link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            raise symlink_error
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr or completed.stdout)


def _require_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "symlink-capability-target"
    link = tmp_path / "symlink-capability-link"
    target.mkdir()
    try:
        _create_directory_link(target, link)
    except OSError as error:
        pytest.skip(f"directory links/reparse points are unavailable: {error}")
    else:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)


def test_durable_replace_is_bound_to_validated_parent_during_real_swap(
    service: DietService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _require_directory_symlink(tmp_path)
    source_parent = service.data_paths.cache / "swappable"
    displaced_parent = service.data_paths.cache / "displaced"
    outside_parent = tmp_path / "outside-replace"
    source_parent.mkdir()
    outside_parent.mkdir()
    source = source_parent / "private.bin"
    outside = outside_parent / "private.bin"
    source.write_bytes(b"inside-private")
    outside.write_bytes(b"outside-must-survive")
    destination = (
        service.data_paths.control
        / "erasure-quarantine"
        / ("d" * 64)
        / ("e" * 64 + ".bin")
    )
    destination.parent.mkdir(parents=True)
    swapped = False

    def swap_parent(boundary: str) -> None:
        nonlocal swapped
        if boundary != "replace_source_file_flush":
            return
        try:
            os.replace(source_parent, displaced_parent)
            _create_directory_link(
                outside_parent,
                source_parent,
            )
            swapped = True
        except OSError:
            # Windows rejects the rename while the validated directory handle is held.
            return

    monkeypatch.setattr(file_io, "_flush_probe", swap_parent)
    file_io.durable_replace(
        source,
        destination,
        data_paths=service.data_paths,
    )

    assert destination.read_bytes() == b"inside-private"
    assert outside.read_bytes() == b"outside-must-survive"
    if swapped:
        assert not (displaced_parent / source.name).exists()
    else:
        assert not source.exists()


def test_durable_unlink_is_bound_to_validated_parent_during_real_swap(
    service: DietService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _require_directory_symlink(tmp_path)
    target_parent = (
        service.data_paths.control / "erasure-quarantine" / ("a" * 64)
    )
    displaced_parent = target_parent.parent / ("b" * 64)
    outside_parent = tmp_path / "outside-unlink"
    target_parent.mkdir(parents=True)
    outside_parent.mkdir()
    target = target_parent / "private.bin"
    outside = outside_parent / "private.bin"
    target.write_bytes(b"quarantined-private")
    outside.write_bytes(b"outside-must-survive")
    swapped = False

    def swap_parent(boundary: str) -> None:
        nonlocal swapped
        if boundary != "unlink_target_file_flush":
            return
        try:
            os.replace(target_parent, displaced_parent)
            _create_directory_link(
                outside_parent,
                target_parent,
            )
            swapped = True
        except OSError:
            return

    monkeypatch.setattr(file_io, "_flush_probe", swap_parent)
    file_io.durable_unlink(target, data_paths=service.data_paths)

    assert outside.read_bytes() == b"outside-must-survive"
    if swapped:
        assert not (displaced_parent / target.name).exists()
    else:
        assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative API")
def test_windows_create_and_rename_bind_destination_parent_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hasattr(file_io, "_open_windows_relative")
    captured_create: dict[str, object] = {}

    def fake_nt_create(
        output_handle,
        _access,
        object_attributes,
        _io_status,
        _allocation,
        _attributes,
        _sharing,
        _disposition,
        _options,
        _ea_buffer,
        _ea_length,
    ) -> int:
        attributes = ctypes.cast(
            object_attributes,
            ctypes.POINTER(file_io._OBJECT_ATTRIBUTES),
        ).contents
        name = attributes.ObjectName.contents
        captured_create["root"] = attributes.RootDirectory
        captured_create["name"] = ctypes.wstring_at(
            name.Buffer,
            name.Length // ctypes.sizeof(ctypes.c_wchar),
        )
        output_handle._obj.value = 303
        return 0

    monkeypatch.setattr(file_io._ntdll, "NtCreateFile", fake_nt_create)
    assert file_io._open_windows_relative(
        202,
        "temporary.bin",
        desired_access=file_io._GENERIC_READ,
        disposition=file_io._FILE_CREATE,
        directory=False,
    ) == 303
    assert captured_create == {"root": 202, "name": "temporary.bin"}

    captured_rename: dict[str, object] = {}

    def fake_set_information(handle, _io_status, buffer, size, info_class) -> int:
        raw = ctypes.string_at(buffer, size)
        information = file_io._FILE_RENAME_INFO.from_buffer_copy(raw)
        offset = file_io._FILE_RENAME_INFO.FileName.offset
        captured_rename["source"] = handle.value
        captured_rename["class"] = info_class
        captured_rename["root"] = information.RootDirectory
        captured_rename["name"] = raw[
            offset : offset + information.FileNameLength
        ].decode("utf-16-le")
        return 0

    monkeypatch.setattr(
        file_io._ntdll,
        "NtSetInformationFile",
        fake_set_information,
    )
    file_io._rename_windows_handle(101, 202, "destination.bin")
    assert captured_rename == {
        "source": 101,
        "class": file_io._FILE_RENAME_INFORMATION_CLASS,
        "root": 202,
        "name": "destination.bin",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative API")
def test_windows_handle_relative_publish_supports_long_path_and_overwrite(
    service: DietService,
) -> None:
    parent = (
        service.data_paths.cache
        / ("a" * 80)
        / ("b" * 80)
        / ("c" * 80)
    )
    destination = parent / "published.txt"
    source = parent / "replacement.txt"
    assert len(str(destination)) > 260

    file_io.atomic_write_bytes(
        destination,
        b"first",
        data_paths=service.data_paths,
    )
    file_io.atomic_write_bytes(
        destination,
        b"second",
        data_paths=service.data_paths,
    )
    file_io.atomic_write_text(
        source,
        "replacement",
        data_paths=service.data_paths,
    )
    file_io.durable_replace(
        source,
        destination,
        data_paths=service.data_paths,
        expected_sha256=hashlib.sha256(b"replacement").hexdigest(),
    )

    assert file_io.sha256_regular_file(
        destination,
        data_paths=service.data_paths,
    ) == hashlib.sha256(b"replacement").hexdigest()
    with pytest.raises(FileNotFoundError):
        file_io.sha256_regular_file(source, data_paths=service.data_paths)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup")
def test_bound_windows_parent_closes_all_handles_without_masking_primary_error(
    service: DietService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = service.data_paths.cache / "one" / "two" / "private.bin"
    target.parent.mkdir(parents=True)
    original_close = file_io._close_windows_handle
    opened: list[int] = []
    close_calls: list[int] = []
    injected = False

    def close_with_first_fault(handle: int) -> None:
        nonlocal injected
        close_calls.append(handle)
        original_close(handle)
        if not injected:
            injected = True
            raise OSError("injected parent close failure")

    monkeypatch.setattr(file_io, "_close_windows_handle", close_with_first_fault)
    try:
        with pytest.raises(RuntimeError, match="primary operation failure"):
            with file_io._bound_windows_parent(target, service.data_paths) as bound:
                opened[:] = bound.handles
                raise RuntimeError("primary operation failure")
        assert set(close_calls) == set(opened)
    finally:
        for handle in opened:
            if handle not in close_calls:
                original_close(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup")
def test_durable_mkdir_closes_every_parent_handle_after_close_fault(
    service: DietService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = file_io._open_windows_directory
    original_close = file_io._close_windows_handle
    opened: list[int] = []
    close_calls: list[int] = []
    injected = False

    def recording_open(path: Path) -> int:
        handle = original_open(path)
        opened.append(handle)
        return handle

    def close_with_first_fault(handle: int) -> None:
        nonlocal injected
        close_calls.append(handle)
        original_close(handle)
        if not injected:
            injected = True
            raise OSError("injected mkdir close failure")

    monkeypatch.setattr(file_io, "_open_windows_directory", recording_open)
    monkeypatch.setattr(file_io, "_close_windows_handle", close_with_first_fault)
    try:
        with pytest.raises(OSError, match="injected mkdir close failure"):
            file_io._durable_mkdir_windows(
                service.data_paths.root,
                ("close-fault-a", "close-fault-b"),
            )
        assert set(close_calls) == set(opened)
    finally:
        for handle in opened:
            if handle not in close_calls:
                original_close(handle)


def test_durable_replace_flushes_same_parent_only_once(
    service: DietService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = service.data_paths.cache / "same-parent-source.bin"
    destination = service.data_paths.cache / "same-parent-destination.bin"
    source.write_bytes(b"same-parent")
    probes: list[str] = []
    monkeypatch.setattr(file_io, "_flush_probe", probes.append)

    file_io.durable_replace(source, destination, data_paths=service.data_paths)

    assert destination.read_bytes() == b"same-parent"
    assert probes.count("replace_source_parent_flush") == 1
    assert probes.count("replace_destination_parent_flush") == 0


def test_all_business_rejects_real_derived_reparse_without_mutation(
    service: DietService,
    tmp_path: Path,
) -> None:
    _require_directory_symlink(tmp_path)
    _seed(service)
    outside = tmp_path / "outside-derived"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"outside-secret")
    linked = service.data_paths.reports / "linked"
    _create_directory_link(outside, linked)
    preview = _dispatch(service, "preview_delete_data", {"scope": "all_business"})

    result = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": "reject-derived-reparse",
        },
    )

    assert result["ok"] is False
    assert secret.read_bytes() == b"outside-secret"
    assert service.connection.execute(
        "SELECT count(*) FROM water_logs"
    ).fetchone()[0] == 1
    if linked.is_symlink():
        linked.unlink()
    else:
        os.rmdir(linked)


def test_quarantine_commit_preflights_all_items_before_first_move(
    service: DietService,
    monkeypatch,
) -> None:
    first = service.data_paths.cache / "first.bin"
    second = service.data_paths.reports / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    preview = _dispatch(service, "preview_delete_data", {"scope": "all_business"})

    def inject(checkpoint: str) -> None:
        if checkpoint == "manifest_persisted":
            second.write_bytes(b"changed")

    monkeypatch.setattr(service.trusted_workflows, "_crash_probe", inject)
    result = _dispatch(
        service,
        "commit_delete_data",
        {
            "commit_handle": preview["data"]["workflow"]["commit_handle"],
            "confirmed": True,
            "operation_key": "preflight-all-before-move",
        },
    )
    assert result["ok"] is False
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"changed"
    states = service.maintenance_controller.connection.execute(
        "SELECT state FROM maintenance_quarantine_items ORDER BY item_key"
    ).fetchall()
    assert {row["state"] for row in states} == {"planned"}


def _derived_files(service: DietService) -> list[Path]:
    return sorted(
        path
        for root in (
            service.data_paths.cache,
            service.data_paths.exports,
            service.data_paths.reports,
        )
        for path in root.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize(
    ("checkpoint", "terminal_state"),
    [
        ("manifest_persisted", "restored"),
        ("stage_replace_complete", "restored"),
        ("database_commit_complete", "purged"),
        ("purge_pending_persisted", "purged"),
        ("purge_unlink_fsynced", "purged"),
        ("first_item_purged", "purged"),
    ],
)
def test_erasure_quarantine_reconciles_every_crash_boundary(
    tmp_path: Path,
    checkpoint: str,
    terminal_state: str,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"),
            str(tmp_path),
            checkpoint,
        ],
        check=False,
    )
    assert completed.returncode == 91

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as reopened:
        rows = reopened.maintenance_controller.connection.execute(
            "SELECT state FROM maintenance_quarantine_items ORDER BY id"
        ).fetchall()
        assert rows
        assert {row["state"] for row in rows} == {terminal_state}
        unreleased = reopened.maintenance_controller.connection.execute(
            """
            SELECT count(*) FROM maintenance_operations
            WHERE status IN ('accepted', 'running', 'interrupted', 'reconciling')
              AND exclusive_released_at IS NULL
            """
        ).fetchone()[0]
        assert unreleased == 0
        if terminal_state == "restored":
            assert _derived_files(reopened)
        else:
            assert _derived_files(reopened) == []
        if checkpoint == "stage_replace_complete":
            assert {path.read_bytes() for path in _derived_files(reopened)} == {
                b"cache-content",
                b"report-content",
            }
            quarantine_root = (
                reopened.data_paths.control / "erasure-quarantine"
            )
            assert not list(quarantine_root.iterdir())


def test_quarantine_item_key_uses_domain_and_path_not_content(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"),
            str(tmp_path),
            "manifest_persisted",
        ],
        check=False,
    )
    assert completed.returncode == 91
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as reopened:
        rows = reopened.maintenance_controller.connection.execute(
            """
            SELECT item_key, original_relative_name, sha256, state
            FROM maintenance_quarantine_items ORDER BY original_relative_name
            """
        ).fetchall()
        assert len(rows) == 2
        assert len({row["sha256"] for row in rows}) == 2
        assert {row["item_key"] for row in rows} == {
            hashlib.sha256(b"cache\0same.bin").hexdigest(),
            hashlib.sha256(b"reports\0nested/same.bin").hexdigest(),
        }
        assert {row["state"] for row in rows} == {"restored"}


def test_quarantine_reconciliation_preflights_all_items_before_mutation(
    service: DietService,
) -> None:
    record, _ = service.maintenance_controller.accept(
        "commit_delete_data",
        {"commit_handle": "wfh_ambiguous", "confirmed": True},
        operation_key="ambiguous-recovery",
        exclusive=True,
    )
    service.maintenance_controller.mark_running(record.handle)
    operation_id = service.maintenance_controller.connection.execute(
        "SELECT id FROM maintenance_operations WHERE operation_handle = ?",
        (record.handle,),
    ).fetchone()[0]
    preview_hash = "b" * 64
    quarantine = (
        service.data_paths.control / "erasure-quarantine" / preview_hash
    )
    quarantine.mkdir(parents=True)
    first = service.data_paths.cache / "safe.bin"
    second = service.data_paths.reports / "ambiguous.bin"
    first.write_bytes(b"safe")
    second.write_bytes(b"ambiguous")
    staged_second = quarantine / f"{'2' * 64}.bin"
    staged_second.write_bytes(b"ambiguous")
    now = "2026-07-30T00:00:00Z"
    control = service.maintenance_controller.connection
    control.execute(
        """
        INSERT INTO maintenance_checks (
            operation_id, stage_code, check_code, outcome, checked_at,
            expected_json, observed_json
        ) VALUES (?, 'manifest', 'erasure_manifest', 'pass', ?, ?, ?)
        """,
        (
            operation_id,
            now,
            json.dumps(
                {
                    "preview_token_hash": preview_hash,
                    "target_digest": "c" * 64,
                }
            ),
            json.dumps({"file_count": 2}),
        ),
    )
    control.executemany(
        """
        INSERT INTO maintenance_quarantine_items (
            operation_id, item_key, original_relative_name,
            staged_relative_name, sha256, state, recorded_at
        ) VALUES (?, ?, ?, ?, ?, 'planned', ?)
        """,
        [
            (
                operation_id,
                "1" * 64,
                "cache/safe.bin",
                f"control/erasure-quarantine/{preview_hash}/{'1' * 64}.bin",
                hashlib.sha256(b"safe").hexdigest(),
                now,
            ),
            (
                operation_id,
                "2" * 64,
                "reports/ambiguous.bin",
                f"control/erasure-quarantine/{preview_hash}/{'2' * 64}.bin",
                hashlib.sha256(b"ambiguous").hexdigest(),
                now,
            ),
        ],
    )
    control.commit()
    before_rows = [
        tuple(row)
        for row in control.execute(
            """
            SELECT item_key, state, resolved_at
            FROM maintenance_quarantine_items ORDER BY item_key
            """
        )
    ]

    report = service.trusted_workflows.recover_startup()

    assert report.verification_required == 1
    assert first.read_bytes() == b"safe"
    assert second.read_bytes() == b"ambiguous"
    assert staged_second.read_bytes() == b"ambiguous"
    after_rows = [
        tuple(row)
        for row in control.execute(
            """
            SELECT item_key, state, resolved_at
            FROM maintenance_quarantine_items ORDER BY item_key
            """
        )
    ]
    assert after_rows == before_rows


@pytest.mark.parametrize(
    "corruption",
    [
        "escaping_preview_hash",
        "uppercase_preview_hash",
        "manifest_outcome",
        "manifest_count",
        "forged_item_key",
        "cross_operation_staged_path",
    ],
)
def test_quarantine_recovery_rejects_unbound_manifest_without_mutation(
    service: DietService,
    corruption: str,
) -> None:
    record, _ = service.maintenance_controller.accept(
        "commit_delete_data",
        {"commit_handle": "wfh_manifest_binding", "confirmed": True},
        operation_key=f"manifest-binding-{corruption}",
        exclusive=True,
    )
    service.maintenance_controller.mark_running(record.handle)
    control = service.maintenance_controller.connection
    operation_id = control.execute(
        "SELECT id FROM maintenance_operations WHERE operation_handle = ?",
        (record.handle,),
    ).fetchone()[0]
    safe_preview_hash = "a" * 64
    preview_hash = safe_preview_hash
    if corruption == "escaping_preview_hash":
        preview_hash = "../escape"
    elif corruption == "uppercase_preview_hash":
        preview_hash = "A" * 64
    domain = "cache"
    relative_name = "binding.bin"
    expected_key = hashlib.sha256(
        f"{domain}\0{relative_name}".encode("utf-8")
    ).hexdigest()
    item_key = "f" * 64 if corruption == "forged_item_key" else expected_key
    staged_preview_hash = (
        "b" * 64
        if corruption == "cross_operation_staged_path"
        else safe_preview_hash
    )
    original = service.data_paths.cache / relative_name
    staged = (
        service.data_paths.control
        / "erasure-quarantine"
        / staged_preview_hash
        / f"{item_key}.bin"
    )
    original.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    if corruption == "cross_operation_staged_path":
        staged.write_bytes(b"bound-private")
    else:
        original.write_bytes(b"bound-private")
    now = "2026-07-30T00:00:00Z"
    control.execute(
        """
        INSERT INTO maintenance_checks (
            operation_id, stage_code, check_code, outcome, checked_at,
            expected_json, observed_json
        ) VALUES (?, 'manifest', 'erasure_manifest', ?, ?, ?, ?)
        """,
        (
            operation_id,
            "fail" if corruption == "manifest_outcome" else "pass",
            now,
            json.dumps(
                {
                    "preview_token_hash": preview_hash,
                    "target_digest": "c" * 64,
                }
            ),
            json.dumps(
                {"file_count": 2 if corruption == "manifest_count" else 1}
            ),
        ),
    )
    control.execute(
        """
        INSERT INTO maintenance_quarantine_items (
            operation_id, item_key, original_relative_name,
            staged_relative_name, sha256, state, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            operation_id,
            item_key,
            f"{domain}/{relative_name}",
            staged.relative_to(service.data_paths.root).as_posix(),
            hashlib.sha256(b"bound-private").hexdigest(),
            "staged" if corruption == "cross_operation_staged_path" else "planned",
            now,
        ),
    )
    control.commit()
    before_files = {
        "original": original.read_bytes() if original.exists() else None,
        "staged": staged.read_bytes() if staged.exists() else None,
    }
    before_item = tuple(
        control.execute(
            """
            SELECT state, resolved_at FROM maintenance_quarantine_items
            WHERE operation_id = ? AND item_key = ?
            """,
            (operation_id, item_key),
        ).fetchone()
    )

    report = service.trusted_workflows.recover_startup()

    assert report.verification_required == 1
    assert {
        "original": original.read_bytes() if original.exists() else None,
        "staged": staged.read_bytes() if staged.exists() else None,
    } == before_files
    assert tuple(
        control.execute(
            """
            SELECT state, resolved_at FROM maintenance_quarantine_items
            WHERE operation_id = ? AND item_key = ?
            """,
            (operation_id, item_key),
        ).fetchone()
    ) == before_item


@pytest.mark.parametrize(
    "boundary",
    [
        "mkdir_new_directory_flush",
        "mkdir_parent_flush",
        "replace_source_file_flush",
        "replace_destination_file_flush",
        "replace_source_parent_flush",
        "replace_destination_parent_flush",
        "unlink_target_file_flush",
        "unlink_parent_flush",
        "rmdir_quarantine_directory_flush",
        "rmdir_parent_flush",
    ],
)
def test_quarantine_flush_failure_never_reports_success_or_purged(
    tmp_path: Path,
    monkeypatch,
    boundary: str,
) -> None:
    data_dir = tmp_path / boundary
    injected = False

    def fail_once(observed: str) -> None:
        nonlocal injected
        if observed == boundary and not injected:
            injected = True
            raise OSError(f"injected {boundary}")

    monkeypatch.setattr(file_io, "_flush_probe", fail_once)
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    ) as service:
        private = service.data_paths.reports / "private.md"
        private.write_bytes(b"private")
        preview = _dispatch(
            service,
            "preview_delete_data",
            {"scope": "all_business"},
        )
        result = _dispatch(
            service,
            "commit_delete_data",
            {
                "commit_handle": preview["data"]["workflow"]["commit_handle"],
                "confirmed": True,
                "operation_key": f"flush-{boundary}",
            },
        )
        assert injected is True
        assert result["ok"] is False

    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    ) as reopened:
        operation = reopened.maintenance_controller.connection.execute(
            """
            SELECT id, operation_handle, status, reconciliation_decision
            FROM maintenance_operations
            WHERE operation_key = ?
            """,
            (f"flush-{boundary}",),
        ).fetchone()
        rows = reopened.maintenance_controller.connection.execute(
            """
            SELECT state, resolved_at FROM maintenance_quarantine_items
            WHERE operation_id = ? ORDER BY item_key
            """,
            (operation["id"],),
        ).fetchall()
        tombstone = reopened.connection.execute(
            """
            SELECT count(*) FROM privacy_erasure_tombstones
            WHERE control_operation_handle = ?
            """,
            (operation["operation_handle"],),
        ).fetchone()[0]
        if boundary.startswith("mkdir_"):
            assert rows == []
            assert tombstone == 0
            assert (reopened.data_paths.reports / "private.md").read_bytes() == b"private"
        elif boundary.startswith("replace_"):
            assert {row["state"] for row in rows} == {"restored"}
            assert all(row["resolved_at"] is not None for row in rows)
            assert tombstone == 0
            assert (reopened.data_paths.reports / "private.md").read_bytes() == b"private"
        else:
            assert {row["state"] for row in rows} == {"purged"}
            assert all(row["resolved_at"] is not None for row in rows)
            assert tombstone == 1
            assert _derived_files(reopened) == []
            assert operation["status"] == "committed"
            assert operation["reconciliation_decision"] == "committed"
        quarantine_root = reopened.data_paths.control / "erasure-quarantine"
        assert not [path for path in quarantine_root.glob("*") if path.is_dir()]
        assert not [
            row
            for row in reopened.maintenance_controller.history(20)
            if row.status == "running"
        ]


def test_quarantine_recovery_matches_only_its_control_operation_tombstone(
    tmp_path: Path,
) -> None:
    worker = PROJECT_ROOT / "tests/helpers/erasure_crash_worker.py"
    first = subprocess.run(
        [
            sys.executable,
            str(worker),
            str(tmp_path),
            "stage_replace_complete",
            "with-unrelated",
        ],
        check=False,
    )
    assert first.returncode == 91
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as restored:
        latest_id = restored.maintenance_controller.connection.execute(
            "SELECT max(id) FROM maintenance_operations"
        ).fetchone()[0]
        states = restored.maintenance_controller.connection.execute(
            """
            SELECT state FROM maintenance_quarantine_items
            WHERE operation_id = ?
            """,
            (latest_id,),
        ).fetchall()
        assert {row["state"] for row in states} == {"restored"}
        assert restored.connection.execute(
            "SELECT count(*) FROM privacy_erasure_tombstones"
        ).fetchone()[0] == 1

    second = subprocess.run(
        [
            sys.executable,
            str(worker),
            str(tmp_path),
            "database_commit_complete",
            "with-unrelated",
        ],
        check=False,
    )
    assert second.returncode == 91
    with DietService(
        source_root=PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path)},
        env={},
    ) as purged:
        latest_id = purged.maintenance_controller.connection.execute(
            "SELECT max(id) FROM maintenance_operations"
        ).fetchone()[0]
        states = purged.maintenance_controller.connection.execute(
            """
            SELECT state FROM maintenance_quarantine_items
            WHERE operation_id = ?
            """,
            (latest_id,),
        ).fetchall()
        assert {row["state"] for row in states} == {"purged"}
        handles = {
            row[0]
            for row in purged.connection.execute(
                """
                SELECT control_operation_handle
                FROM privacy_erasure_tombstones
                ORDER BY control_operation_handle
                """
            )
        }
        assert "mop_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in handles
        assert len(handles) == 2


def test_delete_scopes_are_distinct_and_intake_range_requires_dates(
    service: DietService,
) -> None:
    missing_dates = _dispatch(
        service,
        "preview_delete_data",
        {"scope": "intake_range"},
    )
    assert missing_dates["ok"] is False
    assert missing_dates["error"]["code"] == "INVALID_INPUT"

    _seed(service)
    pref_preview = _dispatch(
        service,
        "preview_delete_data",
        {"scope": "preferences"},
    )
    assert pref_preview["ok"] is True
    counts = pref_preview["data"]["preview"]["affected_counts"]
    assert counts["personal_rules"] == 1
    assert counts.get("water_logs", 0) == 0

    intake_preview = _dispatch(
        service,
        "preview_delete_data",
        {
            "scope": "intake_range",
            "date_start": "2026-07-30",
            "date_end": "2026-07-30",
        },
    )
    assert intake_preview["ok"] is True
    intake_counts = intake_preview["data"]["preview"]["affected_counts"]
    assert intake_counts["water_logs"] == 1
    assert intake_counts.get("personal_rules", 0) == 0


def test_all_five_delete_scopes_commit_exact_preview_counts(
    tmp_path: Path,
) -> None:
    cases = (
        ("raw_source_text", {}),
        ("preferences", {}),
        (
            "intake_range",
            {"date_start": "2026-07-30", "date_end": "2026-07-30"},
        ),
        ("business_facts_keep_config", {}),
        ("all_business", {}),
    )
    for index, (scope, extra) in enumerate(cases):
        with DietService(
            source_root=Path(__file__).resolve().parents[2],
            plugin_config={"dataDir": str(tmp_path / f"scope-{index}")},
        ) as scoped:
            _seed(scoped)
            artifacts = {
                scoped.data_paths.cache / "preserve.cache": b"cache-private-bytes",
                scoped.data_paths.exports / "preserve.json": b"export-private-bytes",
                scoped.data_paths.reports / "preserve.md": b"report-private-bytes",
            }
            for path, contents in artifacts.items():
                path.write_bytes(contents)
            preview, committed = _preview_and_commit(
                scoped,
                {"scope": scope, **extra},
                f"erase-scope-{index}",
            )
            assert committed["data"]["deletion"]["affected_counts"] == preview[
                "data"
            ]["preview"]["affected_counts"]
            if scope == "preferences":
                assert scoped.connection.execute(
                    "SELECT count(*) FROM water_logs"
                ).fetchone()[0] == 1
            if scope == "intake_range":
                assert scoped.connection.execute(
                    "SELECT count(*) FROM pantry_batches"
                ).fetchone()[0] == 1
            if scope == "business_facts_keep_config":
                assert scoped.connection.execute(
                    "SELECT count(*) FROM personal_rules"
                ).fetchone()[0] == 1
            if scope == "all_business":
                assert not any(path.exists() for path in artifacts)
            else:
                assert {
                    path: path.read_bytes() for path in artifacts
                } == artifacts
                assert "derived_files_removed" not in committed["data"][
                    "deletion"
                ]
