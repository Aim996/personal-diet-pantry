from __future__ import annotations

from personal_diet_pantry import goal_profiles
from personal_diet_pantry.service import DietService


def test_bootstrap_goal_profile_is_not_user_confirmed(
    service: DietService,
) -> None:
    result = service.dispatch({"domain": "report", "action": "progress"})

    assert result["ok"] is True
    assert result["data"]["goals_confirmed"] is False
    profile = goal_profiles.load_goal_profile(service.connection)
    assert profile.goal_source == "configuration_default"
    assert profile.confirmed_at is None
    assert profile.confirmed is False


def test_formal_goal_update_marks_profile_confirmed(
    service: DietService,
) -> None:
    update = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 2100,
                "protein_g": 90,
                "fat_g": 65,
                "carbohydrate_g": 260,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 2200,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认我的每日目标",
            },
        }
    )

    assert update["ok"] is True
    result = service.dispatch({"domain": "report", "action": "progress"})
    assert result["ok"] is True
    assert result["data"]["goals_confirmed"] is True
    profile = goal_profiles.load_goal_profile(service.connection)
    assert profile.goal_source == "user_confirmed"
    assert profile.confirmed_at is not None
    assert profile.confirmed_at.tzinfo is not None
    assert profile.confirmed is True


def test_water_commit_exposes_goal_confirmation_state(
    service: DietService,
) -> None:
    result = service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": 300,
                "unit": "ml",
                "occurred_at": "2026-07-29T08:00:00+08:00",
                "source_text": "喝了 300ml 水",
            },
        }
    )

    assert result["ok"] is True
    assert result["data"]["goals_confirmed"] is False


def test_meal_commit_exposes_goal_confirmation_state(
    service: DietService,
) -> None:
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-29T12:00:00+08:00",
                "meal_type": "lunch",
                "source_text": "午餐吃了 100 克鸡胸肉",
                "location_type": "restaurant",
                "items": [
                    {
                        "raw_name": "鸡胸肉",
                        "normalized_name": "chicken breast",
                        "amount": 100,
                        "unit": "g",
                        "consumed_weight_g": 100,
                        "nutrition_basis": "per_100g",
                        "nutrition_dataset_version": "test-fixture-1",
                        "nutrition_facts": {
                            "calories": 165,
                            "protein": 31,
                            "fat": 3.6,
                            "carbohydrate": 0,
                            "fiber": 0,
                            "sodium": 74,
                            "source": "test fixture",
                            "source_grade": "A",
                        },
                    }
                ],
            },
        }
    )
    assert preview["requires_confirmation"] is False
    result = preview

    assert result["ok"] is True
    assert result["data"]["goals_confirmed"] is False
