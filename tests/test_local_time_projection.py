from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import complete_meal_payload, recorded_meal


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def _service(
    tmp_path: Path,
    clock: MutableClock,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> DietService:
    data_dir = tmp_path / "data"
    if timezone_name != "Asia/Shanghai":
        config_dir = data_dir / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "profile.yaml").write_text(
            f"timezone: {timezone_name}\n",
            encoding="utf-8",
        )
    return DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
        _clock=clock,
    )


def test_public_meal_water_and_weight_project_shanghai_local_time(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc))
    service = _service(tmp_path, clock)
    try:
        meal_payload = deepcopy(complete_meal_payload())
        meal_payload.update(
            occurred_at="2026-08-03T14:37:00Z",
            source_text="22:37 蛋白粉",
            meal_type="snack",
        )
        meal_result = recorded_meal(service, payload=meal_payload)
        meal = meal_result["data"]["meal"]

        water_result = service.dispatch(
            {
                "domain": "water",
                "action": "record",
                "payload": {
                    "amount": 300,
                    "unit": "ml",
                    "occurred_at": "2026-08-03T14:37:00Z",
                    "source_text": "喝水",
                },
            }
        )
        assert water_result["ok"] is True
        water = water_result["data"]["record"]

        clock.current = datetime(2026, 8, 3, 17, 25, tzinfo=timezone.utc)
        weight_result = service.dispatch(
            {
                "domain": "weight",
                "action": "record",
                "payload": {"weight": 80, "unit": "kg"},
            }
        )
        assert weight_result["ok"] is True
        weight = weight_result["data"]["record"]

        assert meal["occurred_at"] == "2026-08-03T14:37:00Z"
        assert meal["occurred_at_local"] == "2026-08-03T22:37:00+08:00"
        assert water["occurred_at"] == "2026-08-03T14:37:00Z"
        assert water["occurred_at_local"] == "2026-08-03T22:37:00+08:00"
        assert weight["measured_at"] == "2026-08-03T17:25:00Z"
        assert weight["measured_at_local"] == "2026-08-04T01:25:00+08:00"
        assert {
            meal["timezone_name"],
            water["timezone_name"],
            weight["timezone_name"],
        } == {"Asia/Shanghai"}

        stored_meal = service.connection.execute(
            "SELECT occurred_at FROM meals ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        stored_water = service.connection.execute(
            "SELECT occurred_at FROM water_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        stored_weight = service.connection.execute(
            "SELECT measured_at FROM body_weight_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert stored_meal == "2026-08-03T14:37:00Z"
        assert stored_water == "2026-08-03T14:37:00Z"
        assert stored_weight == "2026-08-03T17:25:00Z"
    finally:
        service.close()


def test_public_projection_uses_iana_dst_offsets(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    service = _service(tmp_path, clock, timezone_name="America/New_York")
    try:
        projections = []
        for occurred_at, source_text in (
            ("2026-01-15T12:00:00Z", "winter meal"),
            ("2026-07-15T12:00:00Z", "summer meal"),
        ):
            payload = deepcopy(complete_meal_payload())
            payload.update(occurred_at=occurred_at, source_text=source_text)
            result = recorded_meal(service, payload=payload)
            projections.append(result["data"]["meal"])

        assert projections[0]["occurred_at_local"] == "2026-01-15T07:00:00-05:00"
        assert projections[1]["occurred_at_local"] == "2026-07-15T08:00:00-04:00"
        assert all(
            item["timezone_name"] == "America/New_York"
            for item in projections
        )
    finally:
        service.close()
