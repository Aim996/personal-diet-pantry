from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 30, 0, 30, tzinfo=timezone.utc)


@pytest.fixture
def weight_service(tmp_path: Path):
    current = [NOW]
    service = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: current[0],
    )
    try:
        yield service, current
    finally:
        service.close()


def _record(
    service: DietService,
    *,
    weight: str = "105",
    unit: str | None = None,
    status_note: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"weight": weight}
    if unit is not None:
        payload["unit"] = unit
    if status_note is not None:
        payload["status_note"] = status_note
    result = service.dispatch(
        {
            "domain": "weight",
            "action": "record",
            "payload": payload,
        }
    )
    assert result["ok"] is True
    return result


def _query(service: DietService, *, limit: int = 20) -> dict[str, object]:
    result = service.dispatch(
        {
            "domain": "weight",
            "action": "query",
            "payload": {"limit": limit},
        }
    )
    assert result["ok"] is True
    return result["data"]["summary"]


def test_record_uses_system_time_default_kg_and_returns_summary(
    weight_service,
) -> None:
    service, _current = weight_service

    result = _record(service, status_note="空腹")

    record = result["data"]["record"]
    assert record["weight_kg"] == "105"
    assert "weight_g" not in record
    assert record["status_note"] == "空腹"
    assert record["measured_at"] == "2026-07-30T00:30:00Z"
    assert record["workflow"]["record_handle"].startswith("wfh_")
    assert result["data"]["summary"] == {
        "seven_day_average_kg": "105.0",
    }
    row = service.connection.execute(
        "SELECT measured_at, weight_g, status_note FROM body_weight_logs"
    ).fetchone()
    assert dict(row) == {
        "measured_at": "2026-07-30T00:30:00Z",
        "weight_g": 105_000,
        "status_note": "空腹",
    }


def test_record_returns_seven_day_downward_trend(weight_service) -> None:
    service, current = weight_service
    current[0] = NOW - timedelta(days=8)
    _record(service, weight="106")
    current[0] = NOW

    result = _record(service, weight="105")

    assert result["data"]["summary"] == {
        "seven_day_average_kg": "105.0",
        "trend": {
            "direction": "down",
            "change_kg": "1.0",
            "current_average_kg": "105.0",
            "previous_average_kg": "106.0",
        },
    }


def test_record_normalizes_blank_status_to_absent(weight_service) -> None:
    service, _current = weight_service

    result = _record(service, status_note="   ")

    assert result["data"]["record"]["status_note"] is None


def test_weight_record_is_semantically_idempotent(weight_service) -> None:
    service, _current = weight_service
    request = {
        "domain": "weight",
        "action": "record",
        "payload": {
            "weight": "105",
            "unit": "kg",
            "status_note": "空腹",
        },
    }
    first = service.dispatch(
        request
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000006"
                ),
                "request_fingerprint": "e" * 64,
                "semantic_fingerprint": "f" * 64,
            }
        }
    )
    second = service.dispatch(
        request
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000007"
                ),
                "request_fingerprint": "e" * 64,
                "semantic_fingerprint": "f" * 64,
            }
        }
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["data"]["status"] == "committed"
    assert service.connection.execute(
        """
        SELECT count(*) FROM body_weight_logs
        WHERE deleted_at IS NULL
        """
    ).fetchone()[0] == 1


def test_query_update_and_delete_use_opaque_record_handle(
    weight_service,
) -> None:
    service, _current = weight_service
    recorded = _record(service, weight="105", status_note="空腹")
    handle = recorded["data"]["workflow"]["record_handle"]

    rejected = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": "not-a-real-handle",
                "weight": "104.8",
            },
        }
    )
    assert rejected["ok"] is False

    updated = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": handle,
                "weight": "104.8",
                "status_note": None,
            },
        }
    )
    assert updated["ok"] is True
    assert updated["data"]["record"]["weight_kg"] == "104.8"
    assert updated["data"]["record"]["status_note"] is None
    assert updated["data"]["record"]["measured_at"] == (
        "2026-07-30T00:30:00Z"
    )

    selected = _query(service)["records"][0]
    assert "id" not in selected
    assert "weight_g" not in selected
    previewed = service.dispatch(
        {
            "domain": "weight",
            "action": "delete",
            "payload": {
                "record_handle": selected["workflow"]["record_handle"],
            },
        }
    )
    assert previewed["ok"] is True
    assert previewed["outcome"] == "preview_ready"
    assert previewed["requires_confirmation"] is True
    preview = previewed["data"]["preview"]
    assert preview["weight_kg"] == "104.8"
    assert preview["measured_at"] == "2026-07-30T00:30:00Z"
    assert preview["measured_at_local"] == "2026-07-30T08:30:00+08:00"
    assert preview["timezone_name"] == "Asia/Shanghai"
    assert preview["status_note"] is None
    assert _query(service)["records"][0]["weight_kg"] == "104.8"

    deleted = service.dispatch(
        {
            "domain": "weight",
            "action": "delete",
            "payload": {
                "commit_handle": previewed["data"]["workflow"][
                    "commit_handle"
                ],
            },
        }
    )
    assert deleted["ok"] is True
    assert deleted["outcome"] == "write_committed"
    assert _query(service)["records"] == []
    assert "seven_day_average_kg" not in _query(service)
    assert "trend" not in _query(service)

    repeated = service.dispatch(
        {
            "domain": "weight",
            "action": "delete",
            "payload": {
                "commit_handle": previewed["data"]["workflow"][
                    "commit_handle"
                ],
            },
        }
    )
    assert repeated["ok"] is False
    assert repeated["error"]["code"] == "STALE_PREVIEW"


def test_weight_update_is_a_patch_and_old_handle_stales_immediately(
    weight_service,
) -> None:
    service, _current = weight_service
    recorded = _record(service, weight="105", status_note="空腹")
    old_handle = recorded["data"]["workflow"]["record_handle"]

    status_only = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": old_handle,
                "status_note": "睡前",
            },
        }
    )
    assert status_only["ok"] is True
    assert status_only["data"]["record"]["weight_kg"] == "105"
    assert status_only["data"]["record"]["status_note"] == "睡前"

    reused = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": old_handle,
                "weight": "104.8",
            },
        }
    )
    assert reused["ok"] is False
    assert reused["error"]["code"] == "STALE_PREVIEW"

    new_handle = status_only["data"]["workflow"]["record_handle"]
    weight_only = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": new_handle,
                "weight": "104.8",
            },
        }
    )
    assert weight_only["ok"] is True
    assert weight_only["data"]["record"]["weight_kg"] == "104.8"
    assert weight_only["data"]["record"]["status_note"] == "睡前"


def test_undone_record_handle_cannot_target_a_later_insert(
    weight_service,
) -> None:
    service, _current = weight_service
    recorded = _record(service, weight="105")
    old_handle = recorded["data"]["workflow"]["record_handle"]
    recent = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {"operation": "undo", "limit": 1},
        }
    )
    undone = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": recent["data"]["candidates"][0][
                    "workflow"
                ]["operation_handle"],
            },
        }
    )
    assert undone["ok"] is True
    _record(service, weight="106")

    stale = service.dispatch(
        {
            "domain": "weight",
            "action": "update",
            "payload": {
                "record_handle": old_handle,
                "weight": "104",
            },
        }
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "STALE_PREVIEW"
    assert _query(service)["records"][0]["weight_kg"] == "106"


def test_body_weight_record_can_be_undone_and_redone(weight_service) -> None:
    service, _current = weight_service
    _record(service, weight="105", status_note="睡前")

    recent = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {"operation": "undo", "limit": 1},
        }
    )
    assert recent["ok"] is True
    candidate = recent["data"]["candidates"][0]
    assert "105" in candidate["summary"]
    undone = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": candidate["workflow"][
                    "operation_handle"
                ],
            },
        }
    )
    assert undone["ok"] is True
    assert _query(service)["records"] == []

    recent_redo = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {"operation": "redo", "limit": 1},
        }
    )
    assert recent_redo["ok"] is True
    redone = service.dispatch(
        {
            "domain": "transaction",
            "action": "redo",
            "payload": {
                "operation_handle": recent_redo["data"]["candidates"][0][
                    "workflow"
                ]["operation_handle"],
            },
        }
    )
    assert redone["ok"] is True
    assert _query(service)["records"][0]["weight_kg"] == "105"
