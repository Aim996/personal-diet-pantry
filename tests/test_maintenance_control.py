from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personal_diet_pantry.maintenance_control import (
    MaintenanceBusyError,
    MaintenanceController,
    MaintenanceKeyConflict,
    MaintenanceStateError,
)


NOW = datetime(2026, 7, 30, 7, 0, tzinfo=timezone.utc)


def _controller(tmp_path: Path) -> MaintenanceController:
    return MaintenanceController(
        tmp_path / "control" / "maintenance.sqlite",
        Path(__file__).resolve().parents[1] / "control-migrations",
        clock=lambda: NOW,
    )


def test_same_operation_key_and_parameters_replay_one_operation(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path) as controller:
        first, first_replay = controller.accept(
            "backup",
            {"label": "nightly"},
            operation_key="nightly-20260730",
            exclusive=False,
        )
        second, second_replay = controller.accept(
            "backup",
            {"label": "nightly"},
            operation_key="nightly-20260730",
            exclusive=False,
        )

        assert first.handle == second.handle
        assert first_replay is False
        assert second_replay is True
        assert len(controller.history()) == 1


def test_same_operation_key_with_different_parameters_is_rejected(
    tmp_path: Path,
) -> None:
    with _controller(tmp_path) as controller:
        controller.accept(
            "backup",
            {"label": "first"},
            operation_key="same-key",
            exclusive=False,
        )

        with pytest.raises(MaintenanceKeyConflict):
            controller.accept(
                "backup",
                {"label": "second"},
                operation_key="same-key",
                exclusive=False,
            )


def test_only_one_exclusive_operation_can_be_active(tmp_path: Path) -> None:
    with _controller(tmp_path) as controller:
        first, _ = controller.accept(
            "restore",
            {"backup_handle": "wfh_a"},
            operation_key="restore-a",
            exclusive=True,
        )
        controller.mark_running(first.handle)

        with pytest.raises(MaintenanceBusyError):
            controller.accept(
                "migrate",
                {},
                operation_key="migrate-b",
                exclusive=True,
            )


def test_state_machine_rejects_invalid_transition(tmp_path: Path) -> None:
    with _controller(tmp_path) as controller:
        operation, _ = controller.accept(
            "repair",
            {},
            operation_key="repair-a",
            exclusive=True,
        )
        controller.mark_running(operation.handle)
        controller.mark_committed(operation.handle, {"repaired": True})

        with pytest.raises(MaintenanceStateError):
            controller.mark_failed(operation.handle, "LATE_FAILURE")


def test_reconciliation_never_replays_destructive_work(tmp_path: Path) -> None:
    calls: list[str] = []
    with _controller(tmp_path) as controller:
        operation, _ = controller.accept(
            "restore",
            {"backup_handle": "wfh_a"},
            operation_key="restore-interrupted",
            exclusive=True,
        )
        controller.mark_running(operation.handle)

        reconciled = controller.reconcile_interrupted(
            lambda record: calls.append(record.handle) or "failed"
        )

        assert calls == [operation.handle]
        assert reconciled[0].status == "failed"
        events = controller.events(operation.handle)
        assert [event["to_status"] for event in events] == [
            "accepted",
            "running",
            "interrupted",
            "reconciling",
            "failed",
        ]

