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


def test_goal_preview_then_one_confirmation_commits_without_a_second_prompt(
    service: DietService,
) -> None:
    """A goal proposal must produce a real handle that a bare confirmation can use."""

    before = service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )
    preview = service.dispatch(
        {
            "domain": "system",
            "action": "preview_update_goals",
            "payload": {
                "calories_kcal": 1900,
                "protein_g": 170,
                "fat_g": 55,
                "carbohydrate_g": 150,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 3000,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认我的每日目标",
            },
        }
    )

    assert before["ok"] is True
    assert preview["ok"] is True, preview
    assert preview["outcome"] == "preview_ready"
    assert preview["requires_confirmation"] is True
    assert service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )["data"] == before["data"]

    commit_handle = preview["data"]["preview"]["workflow"]["commit_handle"]
    committed = service.dispatch(
        {
            "domain": "system",
            "action": "commit_update_goals",
            "payload": {"commit_handle": commit_handle},
        }
    )
    replayed = service.dispatch(
        {
            "domain": "system",
            "action": "commit_update_goals",
            "payload": {"commit_handle": commit_handle},
        }
    )

    assert committed["ok"] is True, committed
    assert committed["outcome"] == "write_committed"
    assert replayed == committed
    assert committed["data"]["goal_profile"]["goals"]["calories_kcal"] == 1900


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
