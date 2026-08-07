from __future__ import annotations

import json

from personal_diet_pantry.service import DietService


PROVENANCE_KEYS = {"goals_confirmed", "goal_source", "confirmed_at"}


def _assert_default_provenance(value: dict[str, object]) -> None:
    assert PROVENANCE_KEYS <= value.keys()
    assert value["goals_confirmed"] is False
    assert value["goal_source"] == "configuration_default"
    assert value["confirmed_at"] is None


def _assert_confirmed_provenance(value: dict[str, object]) -> None:
    assert PROVENANCE_KEYS <= value.keys()
    assert value["goals_confirmed"] is True
    assert value["goal_source"] == "user_confirmed"
    assert isinstance(value["confirmed_at"], str)
    assert value["confirmed_at"].endswith("Z")


def _update_goals(service: DietService) -> dict[str, object]:
    result = service.dispatch(
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
    assert result["ok"] is True
    return result


def _operation_handle(
    service: DietService,
    operation: str,
) -> str:
    recent = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {
                "operation": operation,
                "operation_type": "profile_update",
                "limit": 1,
            },
        }
    )
    assert recent["ok"] is True
    return recent["data"]["candidates"][0]["workflow"][
        "operation_handle"
    ]


def test_progress_and_insights_publish_default_provenance(
    service: DietService,
) -> None:
    for action in ("progress", "insights"):
        result = service.dispatch(
            {
                "domain": "report",
                "action": action,
                "payload": {"report_date": "2026-07-29"},
            }
        )
        assert result["ok"] is True
        _assert_default_provenance(result["data"])


def test_system_query_and_update_publish_provenance(
    service: DietService,
) -> None:
    query = service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )
    assert query["ok"] is True
    _assert_default_provenance(query["data"]["goal_profile"])

    update = _update_goals(service)
    _assert_confirmed_provenance(update["data"]["goal_profile"])
    assert "transaction_id" not in json.dumps(update)


def test_structured_goals_cannot_be_duplicated_as_learned_preferences(
    service: DietService,
) -> None:
    update = _update_goals(service)
    assert update["ok"] is True

    duplicate = service.dispatch(
        {
            "domain": "system",
            "action": "update_preferences",
            "payload": {
                "rule_type": "reply_style",
                "subject": "nutrition_goals",
                "outcome": {"value": "duplicate"},
                "source_text": "把营养目标再记成偏好",
            },
        }
    )

    assert duplicate["ok"] is False
    assert service.connection.execute(
        "SELECT count(*) FROM nutrition_goal_profiles"
    ).fetchone()[0] == 1
    assert service.connection.execute(
        """
        SELECT count(*) FROM personal_rules
        WHERE subject IN ('diet_goals', 'nutrition_goals', 'water_goal')
        """
    ).fetchone()[0] == 0


def test_meal_and_water_commits_publish_default_provenance(
    service: DietService,
) -> None:
    water = service.dispatch(
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
    assert water["ok"] is True
    _assert_default_provenance(water["data"])

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
                        "nutrition_dataset_version": "contract-fixture-1",
                        "nutrition_facts": {
                            "calories": 165,
                            "protein": 31,
                            "fat": 3.6,
                            "carbohydrate": 0,
                            "fiber": 0,
                            "sodium": 74,
                            "source": "contract fixture",
                            "source_grade": "A",
                        },
                    }
                ],
            },
        }
    )
    assert preview["requires_confirmation"] is False
    meal = preview
    assert meal["ok"] is True
    _assert_default_provenance(meal["data"])


def test_update_undo_redo_restores_provenance(
    service: DietService,
) -> None:
    _update_goals(service)
    query = service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )
    _assert_confirmed_provenance(query["data"]["goal_profile"])

    undo = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": _operation_handle(service, "undo")
            },
        }
    )
    assert undo["ok"] is True
    query = service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )
    _assert_default_provenance(query["data"]["goal_profile"])

    redo = service.dispatch(
        {
            "domain": "transaction",
            "action": "redo",
            "payload": {
                "operation_handle": _operation_handle(service, "redo")
            },
        }
    )
    assert redo["ok"] is True
    query = service.dispatch(
        {"domain": "system", "action": "query_goals", "payload": {}}
    )
    _assert_confirmed_provenance(query["data"]["goal_profile"])
