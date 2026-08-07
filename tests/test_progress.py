from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from personal_diet_pantry import goal_profiles, progress
from personal_diet_pantry.service import DietService

from tests.contracts.helpers import recorded_meal


def _record_progress_fixture(service: DietService) -> None:
    recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T12:00:00+08:00",
            "meal_type": "lunch",
            "source_text": "午餐合计脂肪77.25克",
            "location_type": "restaurant",
            "items": [
                {
                    "raw_name": "午餐",
                    "normalized_name": "progress fixture meal",
                    "amount": "1",
                    "unit": "portion",
                    "consumed_servings": "1",
                    "nutrition_basis": "consumed_total",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": {
                        "calories": "1000",
                        "protein": "20",
                        "fat": "77.25",
                        "carbohydrate": "50",
                        "fiber": "5",
                        "sodium": "500",
                        "source": "contract fixture",
                        "source_grade": "A",
                    },
                }
            ],
        },
    )


def _snapshot(service: DietService) -> progress.ProgressSnapshot:
    return progress.daily_progress_snapshot(
        service.connection,
        occurred_at=datetime(2026, 7, 30, 4, tzinfo=timezone.utc),
        goal_profile=goal_profiles.load_goal_profile(service.connection),
        occurred_on=date(2026, 7, 30),
    )


def test_maximum_goal_returns_exact_over_by(service: DietService) -> None:
    _record_progress_fixture(service)

    unconfirmed = _snapshot(service)
    assert all(metric.over_by is None for metric in unconfirmed.metrics)

    updated = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 2100,
                "protein_g": 90,
                "fat_g": 55,
                "carbohydrate_g": 260,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 2200,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认测试目标",
            },
        }
    )
    assert updated["ok"] is True

    metrics = _snapshot(service).metrics
    fat = next(metric for metric in metrics if metric.key == "fat")
    protein = next(metric for metric in metrics if metric.key == "protein")
    assert fat.current == Decimal("77.25")
    assert fat.target == Decimal("55")
    assert fat.over_by == Decimal("22.25")
    assert fat.percent == 140
    assert protein.goal_type == "minimum"
    assert protein.over_by is None

    report = service.dispatch(
        {
            "domain": "report",
            "action": "progress",
            "payload": {"report_date": "2026-07-30"},
        }
    )
    assert report["ok"] is True
    public_fat = next(
        metric
        for metric in report["data"]["metrics"]
        if metric["key"] == "fat"
    )
    assert public_fat["over_by"] == "22.25"
