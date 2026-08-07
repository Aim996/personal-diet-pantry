from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import snapshot_business_tables


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def _add_batch(
    service: DietService,
    *,
    normalized_name: str,
    expires_at: str,
) -> None:
    result = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": normalized_name,
                "normalized_name": normalized_name,
                "quantity": 3,
                "unit": "piece",
                "added_at": "2026-08-01T08:00:00+08:00",
                "source_text": f"add {normalized_name}",
                "storage_location": "fridge",
                "expires_at": expires_at,
            },
        }
    )
    assert result["ok"] is True


def test_expiring_report_includes_all_expired_remaining_batches_and_is_read_only(
    tmp_path: Path,
) -> None:
    service = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: NOW,
    )
    try:
        _add_batch(
            service,
            normalized_name="boiled-egg",
            expires_at="2026-08-03T23:59:59+08:00",
        )
        _add_batch(
            service,
            normalized_name="tofu-pudding",
            expires_at="2026-08-07T23:59:59+08:00",
        )
        _add_batch(
            service,
            normalized_name="outside-window",
            expires_at="2026-08-20T23:59:59+08:00",
        )
        _add_batch(
            service,
            normalized_name="consumed-test",
            expires_at="2026-08-06T23:59:59+08:00",
        )
        service.connection.execute(
            """
            UPDATE pantry_batches
            SET status = 'consumed', remaining_quantity = 0
            WHERE normalized_name = 'consumed-test'
            """
        )
        service.connection.commit()
        before = snapshot_business_tables(service.connection)

        result = service.dispatch(
            {
                "domain": "report",
                "action": "expiring_inventory",
                "payload": {
                    "report_date": "2026-08-04",
                    "within_days": 7,
                },
            }
        )

        assert result["ok"] is True
        assert snapshot_business_tables(service.connection) == before
        data = result["data"]
        assert [item["normalized_name"] for item in data["batches"]] == [
            "boiled-egg",
            "tofu-pudding",
        ]
        assert data["complete"] is True
        assert data["state_counts"] == {
            "expired": 1,
            "expiring_soon": 0,
            "usable": 1,
        }
        assert data["range"] == {
            "timezone_name": "Asia/Shanghai",
            "expired_lower_bound": None,
            "future_start_local": "2026-08-04T00:00:00+08:00",
            "future_end_local": "2026-08-12T00:00:00+08:00",
            "future_start_utc": "2026-08-03T16:00:00Z",
            "future_end_utc": "2026-08-11T16:00:00Z",
            "end_exclusive": True,
        }
    finally:
        service.close()
