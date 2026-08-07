from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry import service as service_module
from personal_diet_pantry.service import DietService
from scripts.behavior_contract import load_behavior_contract

from tests.contracts.helpers import (
    PROVENANCE_KEYS,
    assert_write_envelope,
    complete_meal_payload,
    nutrition_estimate,
    query_meals,
    query_water,
    recorded_meal,
    recorded_water,
    remaining_quantity,
    snapshot_business_tables,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_meal_water_actions_bind_exact_contract_tests() -> None:
    contract = load_behavior_contract(PROJECT_ROOT)

    for domain in ("meal", "water"):
        for item in contract[domain].values():
            assert item.python_test.startswith(
                    (
                        "tests/contracts/test_meal_water_contracts.py::test_",
                        "tests/contracts/test_live_intake_regressions.py::test_",
                        "tests/contracts/test_recipe_shopping_contracts.py::test_",
                        "tests/contracts/test_prepared_food_direct_contracts.py::test_",
                    )
            )


def test_meal_query_empty_and_after_record(
    service: DietService,
) -> None:
    assert query_meals(service) == []

    result = recorded_meal(service)

    assert len(query_meals(service)) == 1
    assert "inventory_effects" in result["data"]
    assert_write_envelope(result["data"])


def test_public_meal_correction_previews_then_commits_once(
    service: DietService,
) -> None:
    recorded = recorded_meal(service)
    original = query_meals(service)[0]
    draft = complete_meal_payload()
    draft.pop("occurred_at")
    draft["source_text"] = "修正：午餐只吃了50克米饭"
    draft["items"][0].update(
        {
            "raw_name": "50克米饭",
            "amount": "50",
            "consumed_weight_g": "50",
        }
    )
    before = snapshot_business_tables(service.connection)

    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "_preview_only": True,
                "meal_handle": original["workflow"]["meal_handle"],
                "draft": draft,
            },
        }
    )

    assert preview["ok"] is True, preview
    assert preview["outcome"] == "preview_ready"
    assert preview["requires_confirmation"] is True
    assert snapshot_business_tables(service.connection) == before
    assert preview["data"]["preview"]["occurred_at"] == original["occurred_at"]

    committed = service.dispatch(
        {
            "domain": "meal",
            "action": "commit_record",
            "payload": {
                "commit_handle": preview["data"]["preview"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
            },
        }
    )
    replayed = service.dispatch(
        {
            "domain": "meal",
            "action": "commit_record",
            "payload": {
                "commit_handle": preview["data"]["preview"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
            },
        }
    )

    assert committed["ok"] is True, committed
    assert committed["outcome"] == "write_committed"
    assert replayed == committed
    assert committed["data"]["meal"]["occurred_at"] == original["occurred_at"]
    assert committed["data"]["meal"]["total_calories"] == "90"
    assert committed["data"]["rendered_receipt"].startswith("已更新！")
    assert len(query_meals(service)) == 1


def test_meal_correction_receipt_reports_net_inventory_change(
    service: DietService,
) -> None:
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "花生",
                "normalized_name": "peanut",
                "quantity": "100",
                "unit": "g",
                "added_at": "2026-07-29T06:00:00Z",
                "source_text": "买了100克花生",
                "storage_location": "pantry",
                "expires_at": "2026-09-01T00:00:00Z",
            },
        }
    )
    assert added["ok"] is True, added

    def inventory_handle() -> str:
        searched = service.dispatch(
            {
                "domain": "pantry",
                "action": "search",
                "payload": {"search_text": "花生", "unit": "g"},
            }
        )
        assert searched["ok"] is True, searched
        return searched["data"]["candidates"][0]["workflow"][
            "inventory_match_handle"
        ]

    meal_payload = {
        "occurred_at": "2026-07-29T15:00:00+08:00",
        "meal_type": "snack",
        "source_text": "吃了10克花生",
        "location_type": "home",
        "items": [
            {
                "raw_name": "花生10克",
                "normalized_name": "peanut",
                "amount": "10",
                "unit": "g",
                "consumed_weight_g": "10",
                "inventory_deduction_weight_g": "10",
                "inventory_match_handle": inventory_handle(),
                "nutrition_basis": "per_100g",
                "nutrition_facts": nutrition_estimate(),
            }
        ],
    }
    recorded_meal(service, payload=meal_payload)
    assert remaining_quantity(service, "peanut") == 90
    original = query_meals(service)[0]

    corrected = deepcopy(meal_payload)
    corrected.pop("occurred_at")
    corrected["source_text"] = "修正：其实只吃了5克花生"
    corrected["items"][0].update(
        {
            "raw_name": "花生5克",
            "amount": "5",
            "consumed_weight_g": "5",
            "inventory_deduction_weight_g": "5",
            "inventory_match_handle": inventory_handle(),
        }
    )
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "_preview_only": True,
                "meal_handle": original["workflow"]["meal_handle"],
                "draft": corrected,
            },
        }
    )
    assert preview["ok"] is True, preview
    committed = service.dispatch(
        {
            "domain": "meal",
            "action": "commit_record",
            "payload": {
                "commit_handle": preview["data"]["preview"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
            },
        }
    )

    assert committed["ok"] is True, committed
    assert remaining_quantity(service, "peanut") == 95
    assert committed["data"]["inventory_effects"] == [
        {
            "food_name": "花生",
            "direction": "increase",
            "quantity": "5",
            "unit": "g",
            "remaining_quantity": "95",
            "cleared": False,
            "storage_location": "pantry",
            "prepared": False,
        }
    ]
    assert "剩 95g（+5g）" in committed["data"]["rendered_receipt"]


def test_meal_record_uses_trusted_system_time_when_omitted(
    service: DietService,
) -> None:
    trusted_now = datetime(2026, 8, 3, 1, 2, 3, tzinfo=timezone.utc)
    service._clock = lambda: trusted_now
    payload = complete_meal_payload()
    payload.pop("occurred_at")

    result = recorded_meal(service, payload=payload)

    assert result["data"]["meal"]["occurred_at"] == "2026-08-03T01:02:03Z"


def test_meal_record_preserves_explicit_time(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(
        2026, 8, 3, 1, 2, 3, tzinfo=timezone.utc
    )
    payload = complete_meal_payload()
    payload["occurred_at"] = "2026-08-03T08:30:00+08:00"

    result = recorded_meal(service, payload=payload)

    assert result["data"]["meal"]["occurred_at"] == "2026-08-03T00:30:00Z"


def test_meal_record_cooking_uses_trusted_system_time_when_omitted(
    service: DietService,
) -> None:
    trusted_now = datetime(2026, 8, 3, 1, 2, 3, tzinfo=timezone.utc)
    service._clock = lambda: trusted_now
    ingredient = deepcopy(complete_meal_payload()["items"][0])
    stocked = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "rice",
                "normalized_name": "rice",
                "quantity": "150",
                "unit": "g",
                "added_at": "2026-08-03T00:00:00Z",
                "source_text": "added 150 grams of rice",
                "storage_location": "fridge",
                "expires_at": "2026-08-05T00:00:00Z",
            },
        }
    )
    assert stocked["ok"] is True

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "meal_type": "dinner",
                "source_text": "cooked one portion of fried rice",
                "dish": {
                    "raw_name": "fried rice",
                    "normalized_name": "fried rice",
                    "unit": "portion",
                    "consumed_quantity": "1",
                    "ingredients": [ingredient],
                },
            },
        }
    )

    assert result["ok"] is True, result
    assert result["data"]["meal"]["occurred_at"] == "2026-08-03T01:02:03Z"


def test_home_meal_without_matching_batch_has_no_inventory_fact(
    service: DietService,
) -> None:
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": complete_meal_payload(),
        }
    )

    assert result["ok"] is True
    assert result["data"]["inventory_effects"] == []
    item = service.connection.execute(
        """
        SELECT inventory_deduction_weight_g
        FROM meal_items
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    movements = service.connection.execute(
        "SELECT count(*) FROM pantry_movements"
    ).fetchone()[0]
    assert item["inventory_deduction_weight_g"] is None
    assert movements == 0


def test_meal_record_is_semantically_idempotent(
    service: DietService,
) -> None:
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "preview_record",
            "payload": complete_meal_payload(),
        }
    )
    assert preview["ok"] is True
    request = {
        "domain": "meal",
        "action": "commit_record",
        "payload": {
            "commit_handle": preview["data"]["preview"]["workflow"][
                "commit_handle"
            ],
            "confirmed": True,
        },
    }

    first_request = deepcopy(request) | {
        "_internal": {
            "operation_id": "op_00000000-0000-4000-8000-000000000001",
            "request_fingerprint": "a" * 64,
            "semantic_fingerprint": "b" * 64,
        }
    }
    second_request = deepcopy(request) | {
        "_internal": {
            "operation_id": "op_00000000-0000-4000-8000-000000000002",
            "request_fingerprint": "a" * 64,
            "semantic_fingerprint": "b" * 64,
        }
    }
    first = service.dispatch(first_request)
    second = service.dispatch(second_request)

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(query_meals(service)) == 1
    assert second["data"]["status"] == "committed"
    assert "daily_progress" in first["data"]
    assert PROVENANCE_KEYS <= first["data"].keys()


def test_meal_preview_commit_requires_real_handle(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "preview_record",
            "payload": complete_meal_payload(),
        }
    )
    after = snapshot_business_tables(service.connection)

    assert preview["ok"] is True
    assert before == after
    fake = service.dispatch(
        {
            "domain": "meal",
            "action": "commit_record",
            "payload": {
                "commit_handle": "not-a-real-handle",
                "confirmed": True,
            },
        }
    )
    assert fake["ok"] is False
    assert query_meals(service) == []

    committed = service.dispatch(
        {
            "domain": "meal",
            "action": "commit_record",
            "payload": {
                "commit_handle": preview["data"]["preview"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
            },
        }
    )
    assert committed["ok"] is True
    assert len(query_meals(service)) == 1


def _egg_meal_payload(count: int) -> dict[str, object]:
    return {
        "occurred_at": "2026-08-03T03:46:50Z",
        "meal_type": "breakfast",
        "source_text": f"早餐吃了{count}个鸡蛋",
        "location_type": "home",
        "items": [
            {
                "raw_name": f"{count}个鸡蛋",
                "normalized_name": "egg",
                "amount": str(count),
                "unit": "piece",
                "nutrition_basis": "consumed_total",
                "nutrition_facts": {
                    "calories": str(70 * count),
                    "protein": str(6 * count),
                    "fat": str(5 * count),
                    "carbohydrate": "0",
                    "fiber": "0",
                    "sodium": str(70 * count),
                    "source": "egg package label",
                    "source_grade": "A",
                },
                "confidence_signals": {
                    "source_confidence": "1",
                    "name_match_confidence": "1",
                    "quantity_confidence": "1",
                    "batch_uniqueness": "1",
                    "context_consistency": "1",
                    "personal_rule_confidence": "1",
                },
            }
        ],
    }


def test_meal_update_without_occurred_at_preserves_original_time_and_stock(
    service: DietService,
) -> None:
    stocked = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "鸡蛋",
                "normalized_name": "egg",
                "quantity": "4",
                "unit": "piece",
                "added_at": "2026-08-03T00:00:00Z",
                "expires_at": "2026-08-10T00:00:00Z",
                "source_text": "买了4个鸡蛋",
            },
        }
    )
    assert stocked["ok"] is True, stocked
    recorded = recorded_meal(service, payload=_egg_meal_payload(2))
    assert recorded["data"]["inventory_effects"]
    handle = query_meals(service, "2026-08-03")[0]["workflow"][
        "meal_handle"
    ]
    draft = _egg_meal_payload(3)
    draft.pop("occurred_at")
    draft["source_text"] = "修正：早餐实际吃了3个鸡蛋"

    updated = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {"meal_handle": handle, "draft": draft},
        }
    )

    assert updated["ok"] is True, updated
    active = query_meals(service, "2026-08-03")
    assert len(active) == 1
    assert active[0]["occurred_at"] == "2026-08-03T03:46:50Z"
    remaining = service.connection.execute(
        """
        SELECT remaining_quantity
        FROM pantry_batches
        WHERE normalized_name = 'egg'
        """
    ).fetchone()
    assert str(remaining["remaining_quantity"]) == "1.0"


def test_meal_update_and_delete_use_issued_handle(
    service: DietService,
) -> None:
    recorded_meal(service)
    selected = query_meals(service)[0]
    handle = selected["workflow"]["meal_handle"]
    draft = complete_meal_payload()
    draft["source_text"] = "午餐改为160克米饭"
    draft["location_type"] = "restaurant"
    draft["items"][0]["amount"] = "160"
    draft["items"][0]["consumed_weight_g"] = "160"
    draft["items"][0]["nutrition_facts"]["source_grade"] = "A"
    draft["items"][0]["nutrition_facts"].pop("uncertainty")
    draft["items"][0]["confidence_signals"] = {
        "source_confidence": "1",
        "name_match_confidence": "1",
        "quantity_confidence": "1",
        "batch_uniqueness": "1",
        "context_consistency": "1",
        "personal_rule_confidence": "1",
    }

    rejected = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": "not-a-real-handle",
                "draft": draft,
            },
        }
    )
    assert rejected["ok"] is False

    updated = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {"meal_handle": handle, "draft": draft},
        }
    )
    assert updated["ok"] is True
    assert query_meals(service)[0]["source_text"] == draft["source_text"]

    fresh_handle = query_meals(service)[0]["workflow"]["meal_handle"]
    deleted = service.dispatch(
        {
            "domain": "meal",
            "action": "delete",
            "payload": {
                "meal_handle": fresh_handle,
                "source_text": "删除测试餐次",
            },
        }
    )
    assert deleted["ok"] is True
    assert query_meals(service) == []


def test_meal_delete_with_unique_handle_derives_audit_text(
    service: DietService,
) -> None:
    first = complete_meal_payload()
    first["occurred_at"] = "2026-08-03T11:00:00+08:00"
    first["source_text"] = "吃了第一份测试餐"
    recorded_meal(service, payload=first)
    second = complete_meal_payload()
    second["occurred_at"] = "2026-08-03T12:00:00+08:00"
    second["source_text"] = "吃了第二份测试餐"
    recorded_meal(service, payload=second)

    selected = next(
        meal
        for meal in query_meals(service, "2026-08-03")
        if meal["source_text"] == first["source_text"]
    )
    deleted = service.dispatch(
        {
            "domain": "meal",
            "action": "delete",
            "payload": {"meal_handle": selected["workflow"]["meal_handle"]},
        }
    )

    assert deleted["ok"] is True, deleted
    remaining = query_meals(service, "2026-08-03")
    assert [meal["source_text"] for meal in remaining] == [second["source_text"]]


def test_meal_nutrition_estimate_is_read_only(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "nutrition_estimate",
            "payload": {
                "normalized_name": "contract rice",
                "consumed_weight_g": "150",
                "estimate": nutrition_estimate(),
            },
        }
    )

    assert result["ok"] is True
    assert result["data"]["nutrition"]["calories"] == "270.00"
    assert snapshot_business_tables(service.connection) == before


def test_record_cooking_commits_meal_and_inventory_atomically(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
    stocked = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "米饭",
                "normalized_name": "rice",
                "quantity": "300",
                "unit": "g",
                "added_at": "2026-07-29T08:00:00+08:00",
                "source_text": "准备300克米饭",
                "storage_location": "fridge",
                "expires_at": "2026-07-30T00:00:00+08:00",
            },
        }
    )
    assert stocked["ok"] is True
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-29T18:00:00+08:00",
                "meal_type": "dinner",
                "source_text": "做了两份炒饭，吃一份留一份",
                "dish": {
                    "raw_name": "炒饭",
                    "normalized_name": "fried rice",
                    "unit": "piece",
                    "consumed_quantity": "1",
                    "ingredients": [
                        {
                            "raw_name": "米饭",
                            "normalized_name": "rice",
                            "amount": "300",
                            "unit": "g",
                            "consumed_weight_g": "300",
                            "nutrition_basis": "per_100g",
                            "nutrition_dataset_version": "contract-fixture-1",
                            "nutrition_facts": nutrition_estimate(),
                        }
                    ],
                    "leftover": {
                        "food_name": "炒饭",
                        "normalized_name": "fried rice",
                        "quantity": "1",
                        "unit": "piece",
                        "storage_location": "fridge",
                        "expires_at": "2026-07-31T00:00:00+08:00",
                    },
                },
            },
        }
    )

    assert result["ok"] is True
    assert len(query_meals(service)) == 1
    assert "inventory_effects" in result["data"]
    assert_write_envelope(result["data"])
    meal_transaction = service.connection.execute(
        "SELECT transaction_id FROM meals"
    ).fetchone()[0]
    batch_transaction = service.connection.execute(
        """
        SELECT transaction_id
        FROM pantry_batches
        WHERE normalized_name = 'fried rice'
        """
    ).fetchone()[0]
    assert meal_transaction == batch_transaction


def _record_fried_rice_with_leftover(service: DietService) -> str:
    service._clock = lambda: datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
    stocked = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "米饭",
                "normalized_name": "rice",
                "quantity": "300",
                "unit": "g",
                "added_at": "2026-07-29T08:00:00+08:00",
                "source_text": "准备300克米饭",
                "storage_location": "fridge",
                "expires_at": "2026-07-30T00:00:00+08:00",
            },
        }
    )
    assert stocked["ok"] is True
    cooked = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-29T18:00:00+08:00",
                "meal_type": "dinner",
                "source_text": "做了两份炒饭，吃一份留一份",
                "dish": {
                    "raw_name": "炒饭",
                    "normalized_name": "fried rice",
                    "unit": "piece",
                    "consumed_quantity": "1",
                    "ingredients": [
                        {
                            "raw_name": "300克米饭",
                            "normalized_name": "rice",
                            "amount": "300",
                            "unit": "g",
                            "consumed_weight_g": "300",
                            "nutrition_basis": "per_100g",
                            "nutrition_dataset_version": "contract-fixture-1",
                            "nutrition_facts": nutrition_estimate(),
                        }
                    ],
                    "leftover": {
                        "food_name": "炒饭",
                        "normalized_name": "fried rice",
                        "quantity": "1",
                        "unit": "piece",
                        "storage_location": "fridge",
                        "expires_at": "2026-07-31T00:00:00+08:00",
                    },
                },
            },
        }
    )
    assert cooked["ok"] is True
    return query_meals(service)[0]["workflow"]["meal_handle"]


def _corrected_fried_rice_without_leftover() -> dict[str, object]:
    return {
        "occurred_at": "2026-07-29T18:00:00+08:00",
        "meal_type": "dinner",
        "source_text": "修正：只用了150克米饭，没有剩菜",
        "dish": {
            "raw_name": "炒饭",
            "normalized_name": "fried rice",
            "unit": "piece",
            "consumed_quantity": "1",
            "ingredients": [
                {
                    "raw_name": "150克米饭",
                    "normalized_name": "rice",
                    "amount": "150",
                    "unit": "g",
                    "consumed_weight_g": "150",
                    "nutrition_basis": "per_100g",
                    "nutrition_dataset_version": "contract-fixture-1",
                    "nutrition_facts": nutrition_estimate(),
                }
            ],
        },
    }


def test_cooking_correction_retires_untouched_old_leftover(
    service: DietService,
) -> None:
    handle = _record_fried_rice_with_leftover(service)
    service._clock = lambda: datetime(2026, 7, 29, 9, 5, tzinfo=timezone.utc)

    corrected = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": handle,
                "draft": _corrected_fried_rice_without_leftover(),
            },
        }
    )

    assert corrected["ok"] is True, corrected
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1
    rice = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches WHERE normalized_name = 'rice'"
    ).fetchone()
    old_leftover = service.connection.execute(
        """
        SELECT remaining_quantity, status
        FROM pantry_batches
        WHERE normalized_name = 'fried rice'
        """
    ).fetchone()
    assert str(rice["remaining_quantity"]) == "150.0"
    assert str(old_leftover["remaining_quantity"]) == "0.0"
    assert old_leftover["status"] == "consumed"


def test_cooking_update_with_insufficient_stock_rolls_back_everything(
    service: DietService,
) -> None:
    handle = _record_fried_rice_with_leftover(service)
    draft = _corrected_fried_rice_without_leftover()
    draft.pop("occurred_at")
    dish = draft["dish"]
    ingredient = dish["ingredients"][0]
    ingredient.update(
        {
            "raw_name": "1000克米饭",
            "amount": "1000",
            "consumed_weight_g": "1000",
        }
    )
    before = snapshot_business_tables(service.connection)
    service._clock = lambda: datetime(
        2026, 7, 29, 9, 5, tzinfo=timezone.utc
    )

    corrected = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {"meal_handle": handle, "draft": draft},
        }
    )

    assert corrected["ok"] is False
    assert corrected["error"]["code"] == "INSUFFICIENT_STOCK"
    assert snapshot_business_tables(service.connection) == before


def test_cooking_correction_rejects_consumed_old_leftover(
    service: DietService,
) -> None:
    handle = _record_fried_rice_with_leftover(service)
    eaten = recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-29T20:00:00+08:00",
            "meal_type": "dinner",
            "source_text": "后来又吃了半份炒饭",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "半份炒饭",
                    "normalized_name": "fried rice",
                    "amount": "0.5",
                    "unit": "piece",
                }
            ],
        },
    )
    assert eaten["ok"] is True
    before = snapshot_business_tables(service.connection)
    service._clock = lambda: datetime(2026, 7, 29, 9, 5, tzinfo=timezone.utc)

    corrected = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": handle,
                "draft": _corrected_fried_rice_without_leftover(),
            },
        }
    )

    assert corrected["ok"] is False
    assert corrected["error"]["code"] == "STALE_PREVIEW"
    assert snapshot_business_tables(service.connection) == before


def test_water_query_empty_and_after_record(
    service: DietService,
) -> None:
    assert query_water(service)["records"] == []

    result = recorded_water(service)

    summary = query_water(service)
    assert len(summary["records"]) == 1
    assert summary["total_ml"] == 300
    assert_write_envelope(result["data"])


def test_water_record_without_timestamp_uses_trusted_system_time(
    service: DietService,
    monkeypatch,
) -> None:
    trusted_now = datetime(2026, 8, 5, 12, 34, tzinfo=timezone.utc)
    monkeypatch.setattr(service_module, "utc_now", lambda: trusted_now)

    result = service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": "300",
                "unit": "ml",
                "source_text": "刚喝了300毫升水",
            },
        }
    )

    assert result["ok"] is True
    stored = service.connection.execute(
        "SELECT occurred_at FROM water_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert stored is not None
    assert stored["occurred_at"] == "2026-08-05T12:34:00Z"


def test_water_record_is_semantically_idempotent(
    service: DietService,
) -> None:
    request = {
        "domain": "water",
        "action": "record",
        "payload": {
            "amount": "300",
            "unit": "ml",
            "occurred_at": "2026-07-29T08:00:00+08:00",
            "source_text": "喝了300ml水",
        },
    }
    first = service.dispatch(
        deepcopy(request)
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000003"
                ),
                "request_fingerprint": "c" * 64,
                "semantic_fingerprint": "d" * 64,
            }
        }
    )
    second = service.dispatch(
        deepcopy(request)
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000004"
                ),
                "request_fingerprint": "c" * 64,
                "semantic_fingerprint": "d" * 64,
            }
        }
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(query_water(service)["records"]) == 1
    assert second["data"]["status"] == "committed"
    assert PROVENANCE_KEYS <= first["data"].keys()


def test_water_update_and_delete_use_issued_handle(
    service: DietService,
) -> None:
    recorded = recorded_water(service)
    handle = recorded["data"]["workflow"]["record_handle"]

    rejected = service.dispatch(
        {
            "domain": "water",
            "action": "update",
            "payload": {
                "record_handle": "not-a-real-handle",
                "amount": "350",
                "unit": "ml",
                "occurred_at": "2026-07-29T08:00:00+08:00",
                "source_text": "改为350ml",
            },
        }
    )
    assert rejected["ok"] is False

    updated = service.dispatch(
        {
            "domain": "water",
            "action": "update",
            "payload": {
                "record_handle": handle,
                "amount": "350",
                "unit": "ml",
                "occurred_at": "2026-07-29T08:00:00+08:00",
                "source_text": "改为350ml",
            },
        }
    )
    assert updated["ok"] is True
    assert_write_envelope(updated["data"])

    deleted = service.dispatch(
        {
            "domain": "water",
            "action": "delete",
            "payload": {
                "record_handle": updated["data"]["workflow"][
                    "record_handle"
                ],
                "source_text": "删除测试饮水",
            },
        }
    )
    assert deleted["ok"] is True
    assert_write_envelope(deleted["data"])
    assert query_water(service)["records"] == []
