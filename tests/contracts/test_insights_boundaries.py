from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from personal_diet_pantry.insights import classify_nutrition_state
from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixed_service(
    tmp_path: Path,
    timestamp: str,
    *,
    name: str,
) -> DietService:
    now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / name)},
        env={},
        _clock=lambda: now,
    )


def _insights(
    service: DietService,
    report_date: str,
    *,
    period: str = "daily",
    **payload: int,
) -> dict[str, object]:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "report_date": report_date,
                "period": period,
                **payload,
            },
        }
    )
    assert result["ok"] is True
    return result["data"]


def _confirm_goals(
    service: DietService,
    *,
    timezone_name: str,
    sodium_mg: int = 2300,
) -> None:
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
                "sodium_mg": sodium_mg,
                "water_ml": 2000,
                "timezone_name": timezone_name,
                "source_text": "确认每日营养目标",
            },
        }
    )
    assert result["ok"] is True


def _record_known_meal(service: DietService) -> None:
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-29T12:00:00+08:00",
                "meal_type": "lunch",
                "source_text": "午餐测试记录",
                "location_type": "restaurant",
                "items": [
                    {
                        "raw_name": "测试食物",
                        "normalized_name": "test food",
                        "amount": 100,
                        "unit": "g",
                        "consumed_weight_g": 100,
                        "nutrition_basis": "per_100g",
                        "nutrition_dataset_version": "contract-fixture-1",
                        "nutrition_facts": {
                            "calories": 100,
                            "protein": 10,
                            "fat": 5,
                            "carbohydrate": 12,
                            "fiber": 2,
                            "sodium": 10,
                            "source": "contract fixture",
                            "source_grade": "A",
                        },
                    }
                ],
            },
        }
    )
    assert preview["requires_confirmation"] is False
    committed = preview
    assert committed["ok"] is True


def test_insights_contains_sodium_metric(
    service: DietService,
) -> None:
    data = _insights(service, "2026-07-29")

    assert [item["key"] for item in data["metrics"]] == [
        "calories",
        "protein",
        "fat",
        "carbohydrate",
        "fiber",
        "sodium",
        "water",
    ]


@pytest.mark.parametrize(
    ("missing_fields", "meal_count", "incomplete_count", "expected"),
    [
        (set(), 0, 0, "no_records"),
        (
            {
                "calories",
                "protein",
                "fat",
                "carbohydrate",
                "fiber",
                "sodium",
            },
            1,
            1,
            "fully_unknown",
        ),
        ({"protein", "fiber"}, 1, 1, "partially_known"),
        (set(), 1, 1, "known_minimum"),
        (set(), 1, 0, "known"),
    ],
)
def test_nutrition_state_is_explicit(
    missing_fields: set[str],
    meal_count: int,
    incomplete_count: int,
    expected: str,
) -> None:
    assert classify_nutrition_state(
        unknown_fields=frozenset(missing_fields),
        meal_count=meal_count,
        incomplete_meal_count=incomplete_count,
    ) == expected


def test_empty_insights_has_explicit_no_records_state(
    service: DietService,
) -> None:
    data = _insights(service, "2026-07-29")

    assert data["data_quality"]["nutrition_data_state"] == "no_records"
    assert data["expiring_inventory"] == {
        "total_count": 0,
        "items": [],
        "truncated": False,
    }


def test_maximum_goal_reports_over_target_status(
    service: DietService,
) -> None:
    _confirm_goals(
        service,
        timezone_name="Asia/Shanghai",
        sodium_mg=1,
    )
    _record_known_meal(service)

    data = _insights(service, "2026-07-29")
    sodium = next(
        item for item in data["metrics"] if item["key"] == "sodium"
    )

    assert sodium["goal_type"] == "maximum"
    assert sodium["status"] == "over"
    assert sodium["current"] == "10"


def test_future_insights_anchor_is_rejected(
    tmp_path: Path,
) -> None:
    service = _fixed_service(
        tmp_path,
        "2026-07-29T12:00:00Z",
        name="future",
    )
    try:
        result = service.dispatch(
            {
                "domain": "report",
                "action": "insights",
                "payload": {"report_date": "2026-07-30"},
            }
        )
    finally:
        service.close()

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "report_date"
    assert result["error"]["reason"] == "future_date"


def test_week_to_date_crosses_dst_by_calendar_date(
    tmp_path: Path,
) -> None:
    service = _fixed_service(
        tmp_path,
        "2026-03-08T12:00:00Z",
        name="dst",
    )
    try:
        _confirm_goals(service, timezone_name="America/New_York")
        data = _insights(service, "2026-03-08", period="weekly")
    finally:
        service.close()

    assert data["period"]["start_date"] == "2026-03-02"
    assert data["period"]["end_date"] == "2026-03-08"
    assert data["period"]["day_count"] == 7


@pytest.mark.parametrize(("field", "value"), [("limit", 0), ("limit", 11)])
def test_insights_rejects_out_of_range_limits(
    service: DietService,
    field: str,
    value: int,
) -> None:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {"report_date": "2026-07-29", field: value},
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("value", [0, 31])
def test_insights_rejects_out_of_range_windows(
    service: DietService,
    value: int,
) -> None:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "insights",
            "payload": {
                "report_date": "2026-07-29",
                "within_days": value,
            },
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("limit", [1, 10])
def test_insights_accepts_boundary_limits(
    service: DietService,
    limit: int,
) -> None:
    data = _insights(
        service,
        "2026-07-29",
        limit=limit,
        within_days=1,
    )

    assert len(data["expiring_inventory"]["items"]) <= limit
