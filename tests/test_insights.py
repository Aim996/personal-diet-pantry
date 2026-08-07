from __future__ import annotations

import pytest

from personal_diet_pantry.service import DietService


def _confirm_goals(service: DietService) -> None:
    result = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 2000,
                "protein_g": 75,
                "fat_g": 60,
                "carbohydrate_g": 250,
                "fiber_g": 25,
                "sodium_mg": 2300,
                "water_ml": 2000,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认每日营养目标",
            },
        }
    )
    assert result["ok"] is True


def _add_expiring_batch(
    service: DietService, *, name: str, expiry_day: int
) -> None:
    result = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": name,
                "normalized_name": name.lower(),
                "quantity": 1,
                "unit": "piece",
                "added_at": "2026-07-28T08:00:00+08:00",
                "expires_at": f"2026-07-{expiry_day:02d}T20:00:00+08:00",
                "source_text": f"添加{name}",
                "storage_location": "fridge",
                "source": "manual",
            },
        }
    )
    assert result["ok"] is True


def test_unconfirmed_defaults_never_create_goal_gap_priority(
    service: DietService,
) -> None:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "period": "daily",
                "report_date": "2026-07-29",
            },
        }
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["goals_confirmed"] is False
    assert all(metric["target"] is None for metric in data["metrics"])
    assert "goal_gap" not in {item["code"] for item in data["priorities"]}


def test_expiring_inventory_is_bounded_but_preserves_total(
    service: DietService,
) -> None:
    _add_expiring_batch(service, name="豆腐", expiry_day=30)
    _add_expiring_batch(service, name="牛奶", expiry_day=31)
    _add_expiring_batch(service, name="酸奶", expiry_day=31)

    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "period": "daily",
                "report_date": "2026-07-29",
                "within_days": 7,
                "limit": 2,
            },
        }
    )

    assert result["ok"] is True
    inventory = result["data"]["expiring_inventory"]
    assert inventory["total_count"] == 3
    assert len(inventory["items"]) == 2
    assert inventory["truncated"] is True
    assert result["data"]["priorities"][0]["code"] == "expiring_inventory"


def test_confirmed_goals_enable_bounded_goal_gap_evidence(
    service: DietService,
) -> None:
    _confirm_goals(service)

    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "period": "weekly",
                "report_date": "2026-07-29",
            },
        }
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["goals_confirmed"] is True
    assert all(metric["target"] is not None for metric in data["metrics"])
    goal_gap = next(
        item for item in data["priorities"] if item["code"] == "goal_gap"
    )
    assert goal_gap["metric_key"] in {"protein", "fiber", "water"}
    assert 0 <= goal_gap["deviation_percent"] <= 100
    assert len(data["priorities"]) <= 3


@pytest.mark.parametrize(
    ("period", "expected_start", "expected_day_count", "protein_target"),
    [
        ("weekly", "2026-07-27", 3, "225"),
        ("monthly", "2026-07-01", 29, "2175"),
    ],
)
def test_current_period_targets_stop_at_the_anchor_date(
    service: DietService,
    period: str,
    expected_start: str,
    expected_day_count: int,
    protein_target: str,
) -> None:
    _confirm_goals(service)

    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "period": period,
                "report_date": "2026-07-29",
            },
        }
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["period"] == {
        "kind": period,
        "start_date": expected_start,
        "end_date": "2026-07-29",
        "day_count": expected_day_count,
    }
    protein = next(
        metric for metric in data["metrics"] if metric["key"] == "protein"
    )
    assert protein["target"] == protein_target
