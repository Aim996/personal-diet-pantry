from __future__ import annotations

from io import StringIO
import json
import re

from personal_diet_pantry import backup, maintenance
from personal_diet_pantry.service import DietService


_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|tmp|var|Users)/)")


def _system(
    service: DietService,
    action: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return service.dispatch(
        {
            "domain": "system",
            "action": action,
            "payload": payload or {},
        }
    )


def test_backup_operation_key_replays_committed_result_once(
    service: DietService,
) -> None:
    first = _system(
        service,
        "backup",
        {"label": "contract", "operation_key": "backup-contract-1"},
    )
    second = _system(
        service,
        "backup",
        {"label": "contract", "operation_key": "backup-contract-1"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["data"] == second["data"]
    assert len(tuple(service.data_paths.backups.glob("*.sqlite"))) == 1


def test_maintenance_status_and_history_are_bounded_and_path_free(
    service: DietService,
) -> None:
    created = _system(
        service,
        "backup",
        {"label": "status", "operation_key": "backup-status-1"},
    )
    handle = created["data"]["maintenance"]["operation_handle"]

    status = _system(
        service,
        "maintenance_status",
        {"operation_handle": handle},
    )
    history = _system(service, "maintenance_history", {"limit": 20})

    assert status["ok"] is True
    assert status["data"]["maintenance"]["status"] == "committed"
    assert history["ok"] is True
    assert 1 <= len(history["data"]["operations"]) <= 20
    rendered = repr({"status": status, "history": history})
    assert not _ABSOLUTE_PATH.search(rendered)
    assert "operation_key" not in rendered
    assert "parameters_sha256" not in rendered
    assert "maintenance.sqlite" not in rendered


def test_conflicting_operation_key_returns_safe_public_error(
    service: DietService,
) -> None:
    first = _system(
        service,
        "backup",
        {"label": "one", "operation_key": "backup-conflict-1"},
    )
    conflict = _system(
        service,
        "backup",
        {"label": "two", "operation_key": "backup-conflict-1"},
    )

    assert first["ok"] is True
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "MAINTENANCE_KEY_CONFLICT"
    assert "backup-conflict-1" not in repr(conflict)


def test_initialize_creates_verified_initial_backup_and_health_evidence(
    service: DietService,
) -> None:
    initialized = _system(
        service,
        "initialize",
        {"operation_key": "initialize-contract-1"},
    )
    checked = _system(service, "self_check")

    backups = tuple(service.data_paths.backups.glob("*.sqlite"))
    assert initialized["ok"] is True
    assert initialized["data"]["initial_backup_created"] is True
    assert len(backups) == 1
    assert backup.verify_backup(
        backups[0],
        data_paths=service.data_paths,
    )
    levels = {
        item["code"]: item["level"]
        for item in checked["data"]["checks"]
    }
    assert levels["backup_age"] == "PASS"
    assert levels["maintenance_control_integrity_check"] == "PASS"
    assert levels["maintenance_control_foreign_key_check"] == "PASS"
    assert levels["maintenance_latest_operation"] == "PASS"


def test_non_migrating_self_check_supports_inventory_expression_index(
    service: DietService,
) -> None:
    output = StringIO()
    diagnostics = StringIO()

    status = maintenance.main(
        ["self-check"],
        env={"PERSONAL_DIET_PANTRY_DATA_DIR": str(service.data_paths.root)},
        stdout=output,
        stderr=diagnostics,
    )

    assert status == 0, diagnostics.getvalue()
    response = json.loads(output.getvalue())
    levels = {check["code"]: check["level"] for check in response["checks"]}
    assert levels["integrity_check"] == "PASS"
