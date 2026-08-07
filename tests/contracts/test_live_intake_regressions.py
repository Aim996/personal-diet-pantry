from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from personal_diet_pantry import service as service_module
from personal_diet_pantry.service import DietService
from personal_diet_pantry.transactions import TransactionManager

from tests.contracts.helpers import recent_operation_handle, recorded_meal


def _facts(
    *,
    calories: str,
    protein: str,
    fat: str,
    carbohydrate: str,
    fiber: str,
    sodium: str,
    hydration_ml: str | None = None,
    source: str = "contract fixture",
) -> dict[str, str]:
    result = {
        "calories": calories,
        "protein": protein,
        "fat": fat,
        "carbohydrate": carbohydrate,
        "fiber": fiber,
        "sodium": sodium,
        "source": source,
        "source_grade": "A",
    }
    if hydration_ml is not None:
        result["hydration_ml"] = hydration_ml
    return result


def _add_small_milk_inventory(service: DietService) -> None:
    for name, quantity in (
        ("川象小瓶牛奶200ml", "400"),
        ("小象小瓶牛奶250ml", "500"),
    ):
        result = service.dispatch(
            {
                "domain": "pantry",
                "action": "add",
                "payload": {
                    "food_name": name,
                    "normalized_name": name,
                    "quantity": quantity,
                    "unit": "ml",
                    "added_at": "2026-07-30T07:00:00+08:00",
                    "source_text": f"家里有{name}",
                    "storage_location": "fridge",
                    "expires_at": "2099-08-05T00:00:00+08:00",
                },
            }
        )
        assert result["ok"] is True


def _small_milk_meal(*, raw_name: str) -> dict[str, object]:
    return {
        "occurred_at": "2026-07-30T08:00:00+08:00",
        "meal_type": "breakfast",
        "source_text": "我刚喝了一瓶小瓶牛奶",
        "location_type": "home",
        "items": [
            {
                "raw_name": raw_name,
                "normalized_name": "川象小瓶牛奶200ml",
                "amount": "200",
                "unit": "ml",
                "consumed_volume_ml": "200",
                "nutrition_basis": "per_100ml",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="60",
                    protein="3.2",
                    fat="3.3",
                    carbohydrate="4.8",
                    fiber="0",
                    sodium="45",
                    hydration_ml="88",
                ),
            }
        ],
    }


def test_ambiguous_raw_milk_cannot_be_hidden_by_exact_normalized_name(
    service: DietService,
) -> None:
    _add_small_milk_inventory(service)

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": _small_milk_meal(raw_name="小瓶牛奶"),
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "AMBIGUOUS_TARGET"
    assert result["requires_confirmation"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 0
    remaining = {
        row["normalized_name"]: str(row["remaining_quantity"])
        for row in service.connection.execute(
            "SELECT normalized_name, remaining_quantity FROM pantry_batches"
        )
    }
    assert remaining == {
        "川象小瓶牛奶200ml": "400.0",
        "小象小瓶牛奶250ml": "500.0",
    }


def test_explicit_raw_product_name_deducts_only_that_product(
    service: DietService,
) -> None:
    _add_small_milk_inventory(service)

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": _small_milk_meal(raw_name="川象小瓶牛奶200ml"),
        }
    )

    assert result["ok"] is True
    remaining = {
        row["normalized_name"]: str(row["remaining_quantity"])
        for row in service.connection.execute(
            "SELECT normalized_name, remaining_quantity FROM pantry_batches"
        )
    }
    assert remaining == {
        "川象小瓶牛奶200ml": "200.0",
        "小象小瓶牛奶250ml": "500.0",
    }


def test_nested_selected_handle_uses_one_operation_time(
    service: DietService,
    monkeypatch,
) -> None:
    _add_small_milk_inventory(service)
    base = datetime(2026, 7, 30, tzinfo=timezone.utc)
    ttl = timedelta(
        minutes=service.settings.behavior.inventory.preview_expiration_minutes
    )
    near_expiry = base + ttl - timedelta(seconds=1)
    expired = base + ttl + timedelta(seconds=1)
    operation_times = iter((base, near_expiry))

    def changing_now() -> datetime:
        return next(operation_times, expired)

    monkeypatch.setattr(service_module, "utc_now", changing_now)
    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {"search_text": "牛奶", "unit": "ml"},
        }
    )
    chosen = next(
        item
        for item in search["data"]["candidates"]
        if item["normalized_name"] == "小象小瓶牛奶250ml"
    )
    ingredient = _small_milk_meal(raw_name="小瓶牛奶")["items"][0]
    ingredient.update(
        {
            "normalized_name": chosen["normalized_name"],
            "amount": "250",
            "consumed_volume_ml": "250",
            "inventory_match_handle": chosen["workflow"][
                "inventory_match_handle"
            ],
        }
    )

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T08:00:00+08:00",
                "meal_type": "breakfast",
                "source_text": "早餐喝了一瓶小瓶牛奶",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "牛奶早餐",
                        "normalized_name": "milk breakfast",
                        "ingredients": [ingredient],
                    }
                ],
            },
        }
    )

    assert result["ok"] is True, result
    remaining = {
        row["normalized_name"]: str(row["remaining_quantity"])
        for row in service.connection.execute(
            "SELECT normalized_name, remaining_quantity FROM pantry_batches"
        )
    }
    assert remaining == {
        "川象小瓶牛奶200ml": "400.0",
        "小象小瓶牛奶250ml": "250.0",
    }


def test_cooking_selected_handle_uses_one_operation_time(
    service: DietService,
    monkeypatch,
) -> None:
    _add_small_milk_inventory(service)
    base = datetime(2026, 7, 30, tzinfo=timezone.utc)
    ttl = timedelta(
        minutes=service.settings.behavior.inventory.preview_expiration_minutes
    )
    near_expiry = base + ttl - timedelta(seconds=1)
    expired = base + ttl + timedelta(seconds=1)
    operation_times = iter((base, near_expiry))

    def changing_now() -> datetime:
        return next(operation_times, expired)

    monkeypatch.setattr(service_module, "utc_now", changing_now)
    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {"search_text": "牛奶", "unit": "ml"},
        }
    )
    chosen = next(
        item
        for item in search["data"]["candidates"]
        if item["normalized_name"] == "小象小瓶牛奶250ml"
    )
    ingredient = _small_milk_meal(raw_name="小瓶牛奶")["items"][0]
    ingredient.update(
        {
            "normalized_name": chosen["normalized_name"],
            "amount": "250",
            "consumed_volume_ml": "250",
            "inventory_match_handle": chosen["workflow"][
                "inventory_match_handle"
            ],
        }
    )

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-30T08:00:00+08:00",
                "meal_type": "breakfast",
                "source_text": "用一瓶小瓶牛奶做早餐饮品",
                "dish": {
                    "raw_name": "早餐饮品",
                    "normalized_name": "breakfast drink",
                    "unit": "portion",
                    "consumed_quantity": "1",
                    "ingredients": [ingredient],
                },
            },
        }
    )

    assert result["ok"] is True, result
    remaining = {
        row["normalized_name"]: str(row["remaining_quantity"])
        for row in service.connection.execute(
            "SELECT normalized_name, remaining_quantity FROM pantry_batches"
        )
    }
    assert remaining == {
        "川象小瓶牛奶200ml": "400.0",
        "小象小瓶牛奶250ml": "250.0",
    }


def test_nested_handle_mismatches_report_recursive_item_fields(
    service: DietService,
) -> None:
    _add_small_milk_inventory(service)
    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {"search_text": "牛奶", "unit": "ml"},
        }
    )
    chosen = next(
        item
        for item in search["data"]["candidates"]
        if item["normalized_name"] == "小象小瓶牛奶250ml"
    )
    mismatched = {
        "raw_name": "小瓶牛奶",
        "normalized_name": "川象小瓶牛奶200ml",
        "amount": "250",
        "unit": "ml",
        "inventory_match_handle": chosen["workflow"]["inventory_match_handle"],
    }
    ordinary = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T08:00:00+08:00",
                "meal_type": "breakfast",
                "source_text": "早餐喝了一瓶小瓶牛奶",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "牛奶早餐",
                        "normalized_name": "milk breakfast",
                        "ingredients": [mismatched],
                    }
                ],
            },
        }
    )
    cooking = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-30T08:00:00+08:00",
                "meal_type": "breakfast",
                "source_text": "用一瓶小瓶牛奶做早餐饮品",
                "dish": {
                    "raw_name": "早餐饮品",
                    "normalized_name": "breakfast drink",
                    "unit": "portion",
                    "consumed_quantity": "1",
                    "ingredients": [mismatched],
                },
            },
        }
    )

    assert ordinary["error"]["field"] == (
        "items[0].ingredients[0].inventory_match_handle"
    )
    assert cooking["error"]["field"] == (
        "dish.ingredients[0].inventory_match_handle"
    )
    for result in (ordinary, cooking):
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert result["error"]["reason"] == "identity_mismatch"
        assert result["error"]["retryable"] is True


def test_explicit_fat_trim_reduces_nutrition_and_preserves_raw_deduction(
    service: DietService,
) -> None:
    stocked = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "鸭腿",
                "normalized_name": "duck leg",
                "quantity": "120",
                "unit": "g",
                "added_at": "2026-07-30T09:00:00+08:00",
                "source_text": "库存有一只120克带骨鸭腿",
                "storage_location": "fridge",
                "expires_at": "2026-08-02T00:00:00+08:00",
            },
        }
    )
    assert stocked["ok"] is True

    result = recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T12:00:00+08:00",
            "meal_type": "lunch",
            "source_text": "吃了库存鸭腿，原重120克，可食78克，剔除4克脂肪",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "库存鸭腿120克带骨",
                    "normalized_name": "duck leg",
                    "amount": "120",
                    "unit": "g",
                    "consumed_weight_g": "78",
                    "inventory_deduction_weight_g": "120",
                    "nutrition_basis": "per_100g",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="200",
                        protein="18",
                        fat="15",
                        carbohydrate="0",
                        fiber="0",
                        sodium="100",
                    ),
                    "preparation_losses": [
                        {
                            "kind": "fat",
                            "quantity": "4",
                            "unit": "g",
                            "nutrition_facts": _facts(
                                calories="36",
                                protein="0",
                                fat="4",
                                carbohydrate="0",
                                fiber="0",
                                sodium="0",
                            ),
                        }
                    ],
                }
            ],
        },
    )

    assert result["data"]["meal"]["total_calories"] == "120"
    assert result["data"]["meal"]["total_fat"] == "7.7"
    batch = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches WHERE normalized_name = 'duck leg'"
    ).fetchone()
    evidence = service.connection.execute(
        "SELECT portion_evidence_json FROM meal_item_nutrition_evidence"
    ).fetchone()
    assert str(batch["remaining_quantity"]) == "0.0"
    portion = json.loads(evidence["portion_evidence_json"])
    assert portion["preparation_losses"] == [
        {
            "kind": "fat",
            "quantity": "4",
            "unit": "g",
            "nutrition_facts": {
                "calories": "36",
                "protein": "0",
                "fat": "4",
                "carbohydrate": "0",
                "fiber": "0",
                "sodium": "0",
                "hydration_ml": None,
                "source": "contract fixture",
                "source_grade": "A",
                "uncertainty": None,
            },
        }
    ]


def test_500ml_soy_uses_per_100ml_basis_once(
    service: DietService,
) -> None:
    recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "喝了500ml豆浆",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "豆浆",
                    "normalized_name": "soy milk",
                    "amount": "500",
                    "unit": "ml",
                    "consumed_volume_ml": "500",
                    "nutrition_basis": "per_100ml",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="33",
                        protein="3.5",
                        fat="1.8",
                        carbohydrate="2",
                        fiber="0",
                        sodium="50",
                        hydration_ml="95",
                    ),
                }
            ],
        },
    )

    meal = service.connection.execute(
        """
        SELECT total_calories, total_protein, total_hydration_ml,
               nutrition_calculation_status,
               nutrition_provenance_status
        FROM meals
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    item = service.connection.execute(
        """
        SELECT consumed_weight_g, consumed_volume_ml, hydration_ml
        FROM meal_items
        """
    ).fetchone()
    evidence = service.connection.execute(
        """
        SELECT basis, scale_factor, dataset_version,
               calculation_status, provenance_status
        FROM meal_item_nutrition_evidence
        """
    ).fetchone()

    assert meal is not None
    assert meal["total_calories"] == "165"
    assert meal["total_protein"] == "17.5"
    assert meal["total_hydration_ml"] == "475"
    assert meal["nutrition_calculation_status"] == "valid"
    assert meal["nutrition_provenance_status"] == "traceable"
    assert item["consumed_weight_g"] is None
    assert item["consumed_volume_ml"] == "500"
    assert item["hydration_ml"] == "475"
    assert evidence["basis"] == "per_100ml"
    assert evidence["scale_factor"] == "5"
    assert evidence["dataset_version"] == "fixture-1"
    assert evidence["calculation_status"] == "valid"
    assert evidence["provenance_status"] == "traceable"


def _add_and_search_boxed_soy(
    service: DietService,
) -> tuple[str, dict[str, object]]:
    product_name = "小象无糖豆浆"
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": product_name,
                "normalized_name": product_name,
                "quantity": "500",
                "unit": "ml",
                "display_quantity": "2",
                "display_unit": "盒",
                "base_quantity_per_display_unit": "250",
                "added_at": "2026-08-03T00:00:00Z",
                "expires_at": "2026-08-10T00:00:00Z",
                "source_text": "买了两盒小象无糖豆浆，每盒250毫升",
                "storage_location": "fridge",
                "nutrition_profile": {
                    "normalized_name": product_name,
                    "serving_basis": "per_100ml",
                    "nutrition": {
                        "calories_kcal": "33",
                        "protein_g": "3.5",
                        "fat_g": "1.8",
                        "carbohydrate_g": "2",
                        "fiber_g": None,
                        "sodium_mg": None,
                        "hydration_ml": "95",
                    },
                    "source_text": "包装营养标签",
                    "source_grade": "A",
                },
            },
        }
    )
    assert added["ok"] is True, added

    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {
                "search_text": "豆浆",
                "unit": "ml",
                "nutrition_mode": "summary",
            },
        }
    )
    assert search["ok"] is True, search
    assert len(search["data"]["candidates"]) == 1
    candidate = search["data"]["candidates"][0]
    assert candidate["nutrition"]["serving_basis"] == "per_100ml"
    return product_name, candidate


def _record_boxed_soy(
    service: DietService,
    *,
    product_name: str,
    candidate: dict[str, object],
    amount: str,
    unit: str,
    **item_overrides: object,
) -> dict[str, object]:
    item = {
        "raw_name": f"{amount}{unit}{product_name}",
        "normalized_name": product_name,
        "amount": amount,
        "unit": unit,
        "inventory_match_handle": candidate["workflow"][
            "inventory_match_handle"
        ],
    } | item_overrides
    return service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "meal_type": "breakfast",
                "source_text": f"喝了{amount}{unit}{product_name}",
                "location_type": "home",
                "items": [item],
            },
        }
    )


def test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(
        2026, 8, 3, 1, 2, 3, tzinfo=timezone.utc
    )
    product_name, candidate = _add_and_search_boxed_soy(service)

    meal = recorded_meal(
        service,
        payload={
            "meal_type": "breakfast",
            "source_text": "早上喝了一盒小象无糖豆浆",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "一盒小象无糖豆浆",
                    "normalized_name": product_name,
                    "amount": "1",
                    "unit": "盒",
                    "portion_expression": "1盒",
                    "inventory_match_handle": candidate["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    stored_meal = service.connection.execute(
        """
        SELECT total_calories, total_protein, total_fiber, total_sodium,
               total_hydration_ml, nutrition_status,
               nutrition_missing_fields_json, nutrition_calculation_status,
               nutrition_provenance_status
        FROM meals
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    stored_item = service.connection.execute(
        """
        SELECT consumed_weight_g, consumed_volume_ml, fiber, sodium,
               hydration_ml, nutrition_source
        FROM meal_items
        """
    ).fetchone()
    evidence = service.connection.execute(
        """
        SELECT basis, scale_factor, input_facts_json,
               calculation_status, provenance_status
        FROM meal_item_nutrition_evidence
        """
    ).fetchone()
    assert meal["data"]["meal"]["occurred_at"] == "2026-08-03T01:02:03Z"
    assert stored_meal["total_calories"] == "82.5"
    assert stored_meal["total_protein"] == "8.75"
    assert stored_meal["total_fiber"] is None
    assert stored_meal["total_sodium"] is None
    assert stored_meal["total_hydration_ml"] == "237.5"
    assert stored_meal["nutrition_status"] == "partial"
    assert json.loads(stored_meal["nutrition_missing_fields_json"]) == [
        "fiber",
        "sodium",
    ]
    assert stored_meal["nutrition_calculation_status"] == "valid"
    assert stored_meal["nutrition_provenance_status"] == "partial"
    assert stored_item["consumed_weight_g"] is None
    assert stored_item["consumed_volume_ml"] == "250"
    assert stored_item["fiber"] is None
    assert stored_item["sodium"] is None
    assert stored_item["hydration_ml"] == "237.5"
    assert "estimate" not in stored_item["nutrition_source"]
    assert evidence["basis"] == "consumed_total"
    assert evidence["scale_factor"] == "1"
    input_facts = json.loads(evidence["input_facts_json"])
    assert input_facts["fiber"] is None
    assert input_facts["sodium"] is None
    assert evidence["calculation_status"] == "valid"
    assert evidence["provenance_status"] == "partial"

    remaining = service.dispatch(
        {
            "domain": "pantry",
            "action": "query",
            "payload": {
                "normalized_name": product_name,
                "include_details": True,
            },
        }
    )
    assert remaining["ok"] is True, remaining
    batch = remaining["data"]["batches"][0]
    assert batch["remaining_quantity"] == "250.0"
    assert batch["remaining_display_quantity"] == "1.0"
    assert batch["display_unit"] == "盒"

    undone = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": recent_operation_handle(
                    service,
                    operation="undo",
                    operation_type="meal_record",
                )
            },
        }
    )
    assert undone["ok"] is True, undone
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT count(*) FROM meal_item_nutrition_evidence"
    ).fetchone()[0] == 0
    restored = service.dispatch(
        {
            "domain": "pantry",
            "action": "query",
            "payload": {
                "normalized_name": product_name,
                "include_details": True,
            },
        }
    )
    restored_batch = restored["data"]["batches"][0]
    assert restored_batch["remaining_quantity"] == "500.0"
    assert restored_batch["remaining_display_quantity"] == "2.0"


def test_unlabeled_packaged_inventory_requires_confirmation_and_stays_unknown(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)
    goals = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 1900,
                "protein_g": 170,
                "fat_g": 55,
                "carbohydrate_g": 150,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 3000,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认测试目标",
            },
        }
    )
    assert goals["ok"] is True
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "原味燕麦奶",
                "normalized_name": "plain oat milk",
                "quantity": "500",
                "unit": "ml",
                "display_quantity": "2",
                "display_unit": "盒",
                "base_quantity_per_display_unit": "250",
                "added_at": "2026-08-03T00:00:00Z",
                "expires_at": "2026-08-10T00:00:00Z",
                "source_text": "两盒原味燕麦奶，每盒250ml",
            },
        }
    )
    assert added["ok"] is True
    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {
                "search_text": "plain oat milk",
                "unit": "ml",
                "nutrition_mode": "full",
            },
        }
    )
    candidate = search["data"]["candidates"][0]
    assert candidate["nutrition_available"] is False

    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "meal_type": "snack",
                "source_text": "喝了一盒原味燕麦奶",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "一盒原味燕麦奶",
                        "normalized_name": "plain oat milk",
                        "amount": "1",
                        "unit": "盒",
                        "inventory_match_handle": candidate["workflow"][
                            "inventory_match_handle"
                        ],
                    }
                ],
            },
        }
    )

    assert preview["ok"] is True
    assert preview["outcome"] == "preview_ready"
    assert preview["requires_confirmation"] is True
    assert "nutrition unknown" in preview["warnings"][0]
    assert preview["data"]["preview"]["items"][0]["consumed_volume_ml"] == "250"
    assert preview["data"]["preview"]["items"][0]["calories"] is None
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 0

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
    assert committed["outcome"] == "write_committed"
    assert committed["data"]["meal"]["total_calories"] is None
    assert "营养待补充" in committed["data"]["rendered_receipt"]
    assert "🔥 热量 ░░░░░░░░░░ 未知" in committed["data"]["rendered_receipt"]
    remaining = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches WHERE normalized_name = 'plain oat milk'"
    ).fetchone()[0]
    assert Decimal(str(remaining)) == Decimal("250")


def test_partial_label_for_unlabeled_inventory_scales_known_fields_and_undoes_atomically(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc)
    goals = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 1900,
                "protein_g": 170,
                "fat_g": 55,
                "carbohydrate_g": 150,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 3000,
                "timezone_name": "Asia/Shanghai",
                "source_text": "UAT26部分标签测试目标",
            },
        }
    )
    assert goals["ok"] is True, goals
    product_name = "UAT26标签豆奶"
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": product_name,
                "normalized_name": product_name,
                "quantity": "360",
                "unit": "ml",
                "display_quantity": "2",
                "display_unit": "盒",
                "base_quantity_per_display_unit": "180",
                "added_at": "2026-08-07T08:00:00+08:00",
                "expires_at": "2026-08-20T00:00:00+08:00",
                "source_text": "两盒UAT26标签豆奶，每盒180毫升",
                "storage_location": "fridge",
            },
        }
    )
    assert added["ok"] is True, added
    search = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {
                "search_text": product_name,
                "unit": "ml",
                "nutrition_mode": "full",
            },
        }
    )
    assert search["ok"] is True, search
    candidate = search["data"]["candidates"][0]
    assert candidate["nutrition_available"] is False
    inventory_name = candidate["normalized_name"]
    assert isinstance(inventory_name, str)

    recorded = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "meal_type": "snack",
                "source_text": "喝了一盒UAT26标签豆奶，标签按每100毫升提供",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "一盒UAT26标签豆奶",
                        "normalized_name": product_name,
                        "amount": "1",
                        "unit": "盒",
                        "inventory_match_handle": candidate["workflow"][
                            "inventory_match_handle"
                        ],
                        "nutrition_basis": "per_100ml",
                        "nutrition_facts": {
                            "calories": "70",
                            "protein": "3",
                            "fat": "2",
                            "carbohydrate": "10",
                            "source": "用户提供的包装标签",
                            "source_grade": "A",
                        },
                    }
                ],
            },
        }
    )

    assert recorded["ok"] is True, recorded
    assert recorded["outcome"] == "write_committed"
    assert recorded["requires_confirmation"] is False
    meal = recorded["data"]["meal"]
    assert meal["total_calories"] == "126"
    assert meal["total_protein"] == "5.4"
    assert meal["total_fat"] == "3.6"
    assert meal["total_carbohydrate"] == "18"
    assert meal["total_fiber"] is None
    assert meal["total_sodium"] is None
    assert meal["items"][0]["consumed_volume_ml"] == "180"
    assert "🥬 纤维 ░░░░░░░░░░ 未知" in recorded["data"]["rendered_receipt"]
    stored = service.connection.execute(
        """
        SELECT nutrition_status, nutrition_missing_fields_json,
               total_fiber, total_sodium
        FROM meals WHERE deleted_at IS NULL
        """
    ).fetchone()
    assert stored["nutrition_status"] == "partial"
    assert json.loads(stored["nutrition_missing_fields_json"]) == ["fiber", "sodium"]
    assert stored["total_fiber"] is None
    assert stored["total_sodium"] is None
    remaining = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches WHERE normalized_name = ?",
        (inventory_name,),
    ).fetchone()[0]
    assert Decimal(str(remaining)) == Decimal("180")

    undone = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": recent_operation_handle(
                    service,
                    operation="undo",
                    operation_type="meal_record",
                )
            },
        }
    )
    assert undone["ok"] is True, undone
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 0
    restored = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches WHERE normalized_name = ?",
        (inventory_name,),
    ).fetchone()[0]
    assert Decimal(str(restored)) == Decimal("360")


def test_inventory_handle_accepts_an_equivalent_base_unit(service):
    product_name, candidate = _add_and_search_boxed_soy(service)

    result = _record_boxed_soy(
        service,
        product_name=product_name,
        candidate=candidate,
        amount="250",
        unit="ml",
    )

    assert result["ok"] is True, result
    stored = service.connection.execute(
        "SELECT amount, unit, consumed_volume_ml FROM meal_items"
    ).fetchone()
    assert tuple(stored) == ("250", "ml", "250")


def test_inventory_handle_rejects_an_unbound_display_unit(service):
    product_name, candidate = _add_and_search_boxed_soy(service)

    result = _record_boxed_soy(
        service,
        product_name=product_name,
        candidate=candidate,
        amount="1",
        unit="瓶",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "items[0].unit"


def test_inventory_handle_rejects_a_conflicting_consumed_measure(service):
    product_name, candidate = _add_and_search_boxed_soy(service)

    result = _record_boxed_soy(
        service,
        product_name=product_name,
        candidate=candidate,
        amount="1",
        unit="盒",
        consumed_volume_ml="200",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "items[0].consumed_volume_ml"
    assert result["error"]["reason"] == "incompatible"


def test_unknown_restaurant_nutrition_records_without_invented_estimates(
    service,
):
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-08-03T12:00:00+08:00",
                "meal_type": "lunch",
                "source_text": "午餐吃了一份店里的时令拼盘",
                "location_type": "restaurant",
                "items": [
                    {
                        "raw_name": "一份时令拼盘",
                        "normalized_name": "未收录时令拼盘",
                        "amount": "1",
                        "unit": "portion",
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
            },
        }
    )

    assert result["ok"] is True, result
    meal = service.connection.execute(
        """
        SELECT total_calories, total_protein, total_fat,
               total_carbohydrate, total_fiber, total_sodium,
               nutrition_status, nutrition_missing_fields_json,
               nutrition_calculation_status, nutrition_provenance_status
        FROM meals
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    assert tuple(meal[:6]) == (None, None, None, None, None, None)
    assert meal["nutrition_status"] == "incomplete"
    assert json.loads(meal["nutrition_missing_fields_json"]) == [
        "calories",
        "protein",
        "fat",
        "carbohydrate",
        "fiber",
        "sodium",
    ]
    assert meal["nutrition_calculation_status"] == "unverified"
    assert meal["nutrition_provenance_status"] == "untraceable"
    item = service.connection.execute(
        """
        SELECT calories, protein, fat, carbohydrate, fiber, sodium,
               nutrition_source
        FROM meal_items
        """
    ).fetchone()
    assert tuple(item[:6]) == (None, None, None, None, None, None)
    assert item["nutrition_source"] is None
    assert service.connection.execute(
        "SELECT count(*) FROM meal_item_nutrition_evidence"
    ).fetchone()[0] == 0


def test_two_liquids_keep_independent_volume_and_basis(
    service: DietService,
) -> None:
    result = recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T08:05:00+08:00",
            "meal_type": "breakfast",
            "source_text": "早餐喝了250ml牛奶和300ml黑咖啡",
            "location_type": "restaurant",
            "items": [
                {
                    "raw_name": "250ml牛奶",
                    "normalized_name": "milk",
                    "amount": "250",
                    "unit": "ml",
                    "consumed_volume_ml": "250",
                    "nutrition_basis": "per_100ml",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="60",
                        protein="3.2",
                        fat="3.3",
                        carbohydrate="4.8",
                        fiber="0",
                        sodium="45",
                        hydration_ml="88",
                    ),
                },
                {
                    "raw_name": "300ml黑咖啡",
                    "normalized_name": "black coffee",
                    "amount": "300",
                    "unit": "ml",
                    "consumed_volume_ml": "300",
                    "nutrition_basis": "per_100ml",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="2",
                        protein="0.1",
                        fat="0",
                        carbohydrate="0.3",
                        fiber="0",
                        sodium="1",
                        hydration_ml="99",
                    ),
                },
            ],
        },
    )

    meal = result["data"]["meal"]
    assert meal["total_calories"] == "156"
    assert meal["total_protein"] == "8.3"
    assert meal["total_fat"] == "8.25"
    assert meal["total_carbohydrate"] == "12.9"
    assert meal["total_hydration_ml"] == "517"
    assert Decimal(meal["total_hydration_ml"]) <= Decimal("550")
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1

    assert service.connection.execute(
        "SELECT count(*) FROM meal_items"
    ).fetchone()[0] == 2
    evidence = service.connection.execute(
        """
        SELECT basis, scale_factor
        FROM meal_item_nutrition_evidence
        ORDER BY meal_item_id
        """
    ).fetchall()
    assert [(row["basis"], row["scale_factor"]) for row in evidence] == [
        ("per_100ml", "2.5"),
        ("per_100ml", "3"),
    ]


def test_two_small_sweet_potatoes_and_500ml_soy_milk_total(
    service: DietService,
) -> None:
    result = recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "早餐吃了两个小小的红薯，然后喝了500ml豆浆",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "两个小小的红薯",
                    "normalized_name": "red sweet potato",
                    "amount": "2",
                    "unit": "piece",
                    "consumed_weight_g": "240",
                    "nutrition_basis": "consumed_total",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="206.4",
                        protein="3.84",
                        fat="0.24",
                        carbohydrate="48.24",
                        fiber="7.2",
                        sodium="132",
                    ),
                },
                {
                    "raw_name": "500ml豆浆",
                    "normalized_name": "soy milk",
                    "amount": "500",
                    "unit": "ml",
                    "consumed_volume_ml": "500",
                    "nutrition_basis": "per_100ml",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="33",
                        protein="3.5",
                        fat="1.8",
                        carbohydrate="2",
                        fiber="0",
                        sodium="50",
                        hydration_ml="95",
                    ),
                },
            ],
        },
    )

    meal = result["data"]["meal"]
    assert meal["total_calories"] == "371.4"
    assert meal["total_hydration_ml"] is None
    assert meal["items"][1]["hydration_ml"] == "475"
    assert meal["total_calories"] not in {"1494.4", "1594"}
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_consumed_total_sweet_potato_is_not_scaled_again(
    service: DietService,
) -> None:
    recorded_meal(
        service,
        payload={
            "occurred_at": "2026-07-30T08:05:00+08:00",
            "meal_type": "breakfast",
            "source_text": "吃了两个小红薯",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "两个小红薯",
                    "normalized_name": "red sweet potato",
                    "amount": "2",
                    "unit": "piece",
                    "portion_expression": "两个小小的",
                    "consumed_weight_g": "240",
                    "nutrition_basis": "consumed_total",
                    "nutrition_dataset_version": "fixture-1",
                    "nutrition_facts": _facts(
                        calories="206.4",
                        protein="3.84",
                        fat="0.24",
                        carbohydrate="48.24",
                        fiber="7.2",
                        sodium="132",
                    ),
                }
            ],
        },
    )

    row = service.connection.execute(
        """
        SELECT total_calories, total_protein
        FROM meals
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    evidence = service.connection.execute(
        """
        SELECT basis, scale_factor, portion_evidence_json
        FROM meal_item_nutrition_evidence
        """
    ).fetchone()

    assert row["total_calories"] == "206.4"
    assert row["total_protein"] == "3.84"
    assert evidence["basis"] == "consumed_total"
    assert evidence["scale_factor"] == "1"
    assert "两个小小的" in evidence["portion_evidence_json"]


def test_direct_nutrition_without_basis_is_rejected(
    service: DietService,
) -> None:
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T08:10:00+08:00",
                "meal_type": "breakfast",
                "source_text": "喝了500ml豆浆",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "豆浆",
                        "normalized_name": "soy milk",
                        "amount": "500",
                        "unit": "ml",
                        "consumed_volume_ml": "500",
                        "nutrition_facts": _facts(
                            calories="165",
                            protein="17.5",
                            fat="9",
                            carbohydrate="10",
                            fiber="0",
                            sodium="250",
                            hydration_ml="475",
                        ),
                    }
                ],
            },
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == "items[0].nutrition_basis"
    assert result["error"]["reason"] == "required"
    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("item", "expected_field", "expected_reason"),
    [
        (
            {"nutrition_basis": "per_100g", "nutrition_facts": "facts"},
            "items[0].consumed_weight_g",
            "required",
        ),
        (
            {"nutrition_basis": "per_100ml", "nutrition_facts": "facts"},
            "items[0].consumed_volume_ml",
            "required",
        ),
        (
            {"nutrition_basis": "per_serving", "nutrition_facts": "facts"},
            "items[0].consumed_servings",
            "required",
        ),
        (
            {"nutrition_basis": "per_100ml"},
            "items[0].nutrition_basis",
            "incompatible",
        ),
        (
            {
                "nutrition_basis": "per_100ml",
                "consumed_volume_ml": "250",
                "nutrition_facts": "facts",
                "nutrition_estimate": "estimate",
            },
            "items[0].nutrition_estimate",
            "incompatible",
        ),
    ],
)
def test_nutrition_basis_contract_errors_are_structured(
    service: DietService,
    item: dict[str, str],
    expected_field: str,
    expected_reason: str,
) -> None:
    nutrition_facts = _facts(
        calories="33",
        protein="3.5",
        fat="1.8",
        carbohydrate="2",
        fiber="0",
        sodium="50",
        hydration_ml="95",
    )
    nutrition_estimate = {
        **nutrition_facts,
        "source": "contract estimate",
        "source_grade": "C",
    }
    resolved_item: dict[str, object] = {
        "raw_name": "豆浆",
        "normalized_name": "soy milk",
    }
    for key, value in item.items():
        resolved_item[key] = (
            nutrition_facts
            if value == "facts"
            else nutrition_estimate
            if value == "estimate"
            else value
        )

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T08:10:00+08:00",
                "meal_type": "breakfast",
                "source_text": "豆浆营养关系校验",
                "location_type": "home",
                "items": [resolved_item],
            },
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["field"] == expected_field
    assert result["error"]["reason"] == expected_reason
    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 0


def test_nutrition_evidence_follows_meal_undo_and_redo(
    service: DietService,
) -> None:
    recorded_meal(service)
    transaction_id = service.connection.execute(
        "SELECT transaction_id FROM meals"
    ).fetchone()[0]
    manager = TransactionManager(service.connection)

    manager.undo(transaction_id)

    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT count(*) FROM meal_item_nutrition_evidence"
    ).fetchone()[0] == 0

    manager.redo(transaction_id)

    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 1
    assert service.connection.execute(
        "SELECT count(*) FROM meal_item_nutrition_evidence"
    ).fetchone()[0] == 1


def test_clear_completed_external_meal_records_without_confirmation(
    service: DietService,
) -> None:
    payload = {
        "occurred_at": "2026-07-30T12:00:00+08:00",
        "meal_type": "lunch",
        "source_text": "中午在公司吃了两个煎蛋",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "两个煎蛋",
                "normalized_name": "fried egg",
                "amount": "2",
                "unit": "piece",
                "consumed_weight_g": "100",
                "nutrition_basis": "consumed_total",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="180",
                    protein="12.6",
                    fat="13.6",
                    carbohydrate="0.8",
                    fiber="0",
                    sodium="180",
                ),
            }
        ],
    }

    result = service.dispatch(
        {"domain": "meal", "action": "record", "payload": payload}
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is False
    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 1


def test_clear_count_estimate_records_directly_and_supports_one_turn_correction(
    service: DietService,
) -> None:
    standard_estimate = _facts(
        calories="169.6",
        protein="8",
        fat="12.8",
        carbohydrate="4.8",
        fiber="0",
        sodium="600",
        source="常见火腿肠每100克估算",
    )
    standard_estimate["source_grade"] = "C"
    standard_estimate["uncertainty"] = "品牌和配方会有差异"
    initial = {
        "occurred_at": "2026-08-06T08:00:00+08:00",
        "meal_type": "snack",
        "source_text": "刚吃了根火腿肠",
        "location_type": "home",
        "items": [
            {
                "raw_name": "火腿肠",
                "normalized_name": "sausage",
                "amount": "1",
                "unit": "根",
                "portion_expression": "1根 50克（估算）",
                "consumed_weight_g": "50",
                "quantity_estimate": {
                    "suggested": "50",
                    "lower": "40",
                    "upper": "65",
                    "unit": "g",
                    "evidence_type": "standard_portion",
                    "policy_key": "portion.standard_count_weight",
                },
                "nutrition_basis": "per_100g",
                "nutrition_estimate": standard_estimate,
            }
        ],
    }

    recorded = service.dispatch(
        {"domain": "meal", "action": "record", "payload": initial}
    )

    assert recorded["ok"] is True, recorded
    assert recorded["requires_confirmation"] is False
    assert recorded["data"]["meal"]["workflow"]["meal_handle"].startswith("wfh_")
    assert recorded["data"]["meal"]["total_calories"] == "84.8"
    assert recorded["data"]["rendered_receipt"].startswith(
        "已记录！火腿肠 1根 50克（估算）｜84.8 kcal"
    )

    corrected = deepcopy(initial)
    corrected.pop("occurred_at")
    corrected["source_text"] = "刚才那根火腿肠其实是80克"
    corrected_item = corrected["items"][0]
    corrected_item["consumed_weight_g"] = "80"
    corrected_item["portion_expression"] = "1根 80克"
    corrected_item.pop("quantity_estimate")
    updated = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": recorded["data"]["meal"]["workflow"][
                    "meal_handle"
                ],
                "draft": corrected,
            },
        }
    )

    assert updated["ok"] is True, updated
    assert updated["requires_confirmation"] is False
    assert updated["data"]["meal"]["workflow"]["meal_handle"].startswith("wfh_")
    assert updated["data"]["meal"]["total_calories"] == "135.68"
    assert updated["data"]["rendered_receipt"].startswith(
        "已更新！火腿肠 1根 80克｜135.68 kcal"
    )
    active = service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0]
    assert active == 1


def test_real_corn_trace_keeps_edible_estimate_then_accepts_exact_weight(
    service: DietService,
) -> None:
    goals = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 1900,
                "protein_g": 170,
                "fat_g": 55,
                "carbohydrate_g": 150,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 3000,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认回执测试目标",
            },
        }
    )
    assert goals["ok"] is True, goals
    corn_estimate = _facts(
        calories="100",
        protein="3.4",
        fat="1.5",
        carbohydrate="20.5",
        fiber="2.4",
        sodium="3",
        source="中国食物成分表常见估算：煮甜玉米可食部",
    )
    corn_estimate["source_grade"] = "C"
    corn_estimate["uncertainty"] = "可食部（玉米粒）约90克，为估算值"
    initial = {
        "occurred_at": "2026-08-06T10:38:00+08:00",
        "meal_type": "snack",
        "source_text": "吃了个玉米",
        "location_type": "unknown",
        "items": [
            {
                "raw_name": "玉米",
                "normalized_name": "玉米",
                "amount": "1",
                "unit": "个",
                "portion_expression": "1个",
                "consumed_weight_g": "90",
                "nutrition_basis": "per_100g",
                "nutrition_estimate": corn_estimate,
            }
        ],
    }

    recorded = service.dispatch(
        {"domain": "meal", "action": "record", "payload": initial}
    )

    assert recorded["ok"] is True, recorded
    assert recorded["requires_confirmation"] is False
    assert recorded["data"]["rendered_receipt"].startswith(
        "已记录！玉米 1个｜可食部（玉米粒）约90克（估算）｜90 kcal"
    )
    assert recorded["data"]["meal"]["items"][0]["portion_expression"] == (
        "1个｜可食部（玉米粒）约90克（估算）"
    )

    corrected = deepcopy(initial)
    corrected.pop("occurred_at")
    corrected["source_text"] = "其实是80克"
    corrected_item = corrected["items"][0]
    corrected_item["consumed_weight_g"] = "80"
    # Match the real OpenClaw trace: the model retained amount/unit but
    # narrowed the user-visible expression to the new weight and did not
    # repeat the old edible-part uncertainty.  The service, not the model,
    # must preserve the last committed measurement object.
    corrected_item["portion_expression"] = "80克"
    corrected_item.pop("quantity_estimate", None)
    corrected_item["nutrition_estimate"] = dict(corn_estimate)
    corrected_item["nutrition_estimate"].pop("uncertainty", None)

    updated = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": recorded["data"]["meal"]["workflow"]["meal_handle"],
                "draft": corrected,
            },
        }
    )

    assert updated["ok"] is True, updated
    assert updated["requires_confirmation"] is False
    assert updated["data"]["rendered_receipt"].startswith(
        "已更新！玉米 1个｜可食部（玉米粒）80克｜80 kcal"
    )
    assert "80克（估算）" not in updated["data"]["rendered_receipt"]
    assert updated["data"]["meal"]["items"][0]["portion_expression"] == (
        "1个｜可食部（玉米粒）80克"
    )
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1

    deleted = service.dispatch(
        {
            "domain": "meal",
            "action": "delete",
            "payload": {
                "meal_handle": updated["data"]["meal"]["workflow"]["meal_handle"],
                "source_text": "把整条玉米记录删掉",
            },
        }
    )
    assert deleted["ok"] is True, deleted
    assert deleted["outcome"] == "write_committed"
    assert deleted["data"]["rendered_receipt"].startswith(
        "已删除！玉米 1个｜可食部（玉米粒）80克｜80 kcal"
    )
    delete_receipt = deleted["data"]["rendered_receipt"]
    for label in ("🔥 热量", "🥩 蛋白", "🧈 脂肪", "🌾 碳水", "🥬 纤维", "💧 饮水"):
        assert delete_receipt.count(label) == 1
    assert "-80kcal" in delete_receipt
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 0


def test_direct_corn_uses_local_nutrition_when_model_omits_nutrients(
    service: DietService,
) -> None:
    recorded = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-08-06T23:09:00+08:00",
                "meal_type": "other",
                "source_text": "吃了个玉米",
                "location_type": "unknown",
                "items": [
                    {
                        "raw_name": "玉米",
                        "normalized_name": "玉米",
                        "amount": "1",
                        "unit": "个",
                        "portion_expression": "1个｜可食部（玉米粒）约90克（估算）",
                        "consumed_weight_g": "90",
                    }
                ],
            },
        }
    )

    assert recorded["ok"] is True, recorded
    meal = recorded["data"]["meal"]
    assert meal["total_calories"] == "90"
    assert meal["total_protein"] == "3.06"
    assert meal["total_fat"] == "1.35"
    assert meal["total_carbohydrate"] == "18.45"
    assert meal["total_fiber"] == "2.16"
    assert meal["items"][0]["source_grade"] == "C"
    receipt = recorded["data"]["rendered_receipt"]
    assert receipt.startswith(
        "已记录！玉米 1个｜可食部（玉米粒）约90克（估算）｜90 kcal"
    )
    assert "营养待补充" not in receipt
    assert "（部分未知）" not in receipt


def test_handle_bound_correction_hydrates_unchanged_meal_fields(
    service: DietService,
) -> None:
    facts = _facts(
        calories="112",
        protein="4",
        fat="1.2",
        carbohydrate="22.8",
        fiber="2.9",
        sodium="2",
        source="中国食物成分表/USDA（甜玉米，熟，可食部）",
    )
    initial = {
        "occurred_at": "2026-08-06T13:53:00+08:00",
        "meal_type": "snack",
        "source_text": "吃了个玉米",
        "location_type": "unknown",
        "items": [
            {
                "raw_name": "玉米",
                "normalized_name": "玉米",
                "amount": "1",
                "unit": "个",
                "portion_expression": "1个｜可食部（玉米粒）约90克（估算）",
                "consumed_weight_g": "90",
                "nutrition_basis": "per_100g",
                "nutrition_facts": facts,
            }
        ],
    }
    recorded = service.dispatch(
        {"domain": "meal", "action": "record", "payload": initial}
    )
    assert recorded["ok"] is True, recorded

    corrected_item = deepcopy(initial["items"][0])
    corrected_item["consumed_weight_g"] = "80"
    corrected_item["portion_expression"] = "1个｜可食部（玉米粒）80克"
    updated = service.dispatch(
        {
            "domain": "meal",
            "action": "update",
            "payload": {
                "meal_handle": recorded["data"]["meal"]["workflow"][
                    "meal_handle"
                ],
                "draft": {"items": [corrected_item]},
            },
        }
    )

    assert updated["ok"] is True, updated
    meal = updated["data"]["meal"]
    assert meal["meal_type"] == "snack"
    assert meal["source_text"] == "吃了个玉米"
    assert meal["location_type"] == "unknown"
    assert meal["occurred_at_local"] == "2026-08-06T13:53:00+08:00"
    assert meal["items"][0]["consumed_weight_g"] == "80"
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_direct_nutrition_estimate_keeps_missing_sodium_unknown(
    service: DietService,
) -> None:
    corn_estimate = _facts(
        calories="112",
        protein="4",
        fat="1.5",
        carbohydrate="22.8",
        fiber="2.9",
        sodium="3",
        source="中国食物成分表常见估算：鲜玉米可食部",
    )
    corn_estimate.pop("sodium")
    corn_estimate["source_grade"] = "C"
    corn_estimate["uncertainty"] = "钠数据未知"

    recorded = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-08-06T13:01:00+08:00",
                "meal_type": "snack",
                "source_text": "吃了个玉米",
                "location_type": "unknown",
                "items": [
                    {
                        "raw_name": "玉米",
                        "normalized_name": "玉米（鲜）",
                        "amount": "1",
                        "unit": "个",
                        "portion_expression": "1个",
                        "consumed_weight_g": "90",
                        "nutrition_basis": "per_100g",
                        "nutrition_estimate": corn_estimate,
                    }
                ],
            },
        }
    )

    assert recorded["ok"] is True, recorded
    assert recorded["data"]["meal"]["total_sodium"] is None
    meal = service.connection.execute(
        """
        SELECT total_sodium, nutrition_status, nutrition_missing_fields_json
        FROM meals WHERE deleted_at IS NULL
        """
    ).fetchone()
    assert meal["total_sodium"] is None
    assert meal["nutrition_status"] == "partial"
    assert json.loads(meal["nutrition_missing_fields_json"]) == ["sodium"]
    stored = service.connection.execute(
        "SELECT sodium FROM meal_items"
    ).fetchone()
    assert stored["sodium"] is None


def test_external_meal_low_inventory_signals_do_not_block_recording(
    service: DietService,
) -> None:
    payload = {
        "occurred_at": "2026-07-30T12:02:00+08:00",
        "meal_type": "lunch",
        "source_text": "中午在公司吃了两个煎蛋",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "两个煎蛋",
                "normalized_name": "fried egg",
                "amount": "2",
                "unit": "piece",
                "consumed_weight_g": "100",
                "nutrition_basis": "consumed_total",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="180",
                    protein="12.6",
                    fat="13.6",
                    carbohydrate="0.8",
                    fiber="0",
                    sodium="180",
                ),
                "confidence_signals": {
                    "source_confidence": "0.1",
                    "name_match_confidence": "0.1",
                    "batch_uniqueness": "0.1",
                },
            }
        ],
    }

    result = service.dispatch(
        {"domain": "meal", "action": "record", "payload": payload}
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is False
    assert result["data"]["inventory_effects"] == []


def test_two_fried_eggs_and_300ml_milk_are_scaled_once(
    service: DietService,
) -> None:
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T12:04:00+08:00",
                "meal_type": "lunch",
                "source_text": "中午在公司吃了两个煎蛋，还喝了一瓶300ml牛奶",
                "location_type": "restaurant",
                "items": [
                    {
                        "raw_name": "两个煎蛋",
                        "normalized_name": "fried egg",
                        "amount": "2",
                        "unit": "piece",
                        "consumed_weight_g": "100",
                        "nutrition_basis": "consumed_total",
                        "nutrition_dataset_version": "fixture-1",
                        "nutrition_facts": _facts(
                            calories="180",
                            protein="12.6",
                            fat="13.6",
                            carbohydrate="0.8",
                            fiber="0",
                            sodium="180",
                        ),
                    },
                    {
                        "raw_name": "一瓶300ml牛奶",
                        "normalized_name": "milk",
                        "amount": "300",
                        "unit": "ml",
                        "consumed_volume_ml": "300",
                        "nutrition_basis": "per_100ml",
                        "nutrition_dataset_version": "fixture-1",
                        "nutrition_facts": _facts(
                            calories="60",
                            protein="3.2",
                            fat="3.3",
                            carbohydrate="4.8",
                            fiber="0",
                            sodium="45",
                            hydration_ml="88",
                        ),
                    },
                ],
            },
        }
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is False
    meal = result["data"]["meal"]
    assert meal["total_calories"] == "360"
    assert meal["total_hydration_ml"] is None
    assert meal["items"][1]["hydration_ml"] == "264"
    assert result["data"]["inventory_effects"] == []


def test_same_intake_retry_does_not_create_a_second_meal(
    service: DietService,
) -> None:
    payload = {
        "occurred_at": "2026-07-30T12:05:00+08:00",
        "meal_type": "lunch",
        "source_text": "中午喝了一瓶300ml牛奶",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "一瓶牛奶",
                "normalized_name": "milk",
                "amount": "300",
                "unit": "ml",
                "consumed_volume_ml": "300",
                "nutrition_basis": "per_100ml",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="60",
                    protein="3.2",
                    fat="3.3",
                    carbohydrate="4.8",
                    fiber="0",
                    sodium="45",
                    hydration_ml="88",
                ),
            }
        ],
    }

    first = recorded_meal(service, payload=payload)
    second = recorded_meal(service, payload=payload)

    assert first["ok"] is True
    assert second["ok"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_semantically_equivalent_intake_retry_replays_existing_meal(
    service: DietService,
) -> None:
    first_payload = {
        "occurred_at": "2026-07-30T12:06:05+08:00",
        "meal_type": "lunch",
        "source_text": "中午喝了300ml牛奶，又吃了一个鸡蛋",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "牛奶",
                "normalized_name": "milk",
                "amount": "300",
                "unit": "ml",
                "consumed_volume_ml": "300",
                "nutrition_basis": "per_100ml",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="60",
                    protein="3.2",
                    fat="3.3",
                    carbohydrate="4.8",
                    fiber="0",
                    sodium="45",
                    hydration_ml="88",
                ),
            },
            {
                "raw_name": "鸡蛋",
                "normalized_name": "egg",
                "amount": "1",
                "unit": "piece",
                "consumed_weight_g": "50",
                "nutrition_basis": "consumed_total",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="72",
                    protein="6.3",
                    fat="4.8",
                    carbohydrate="0.4",
                    fiber="0",
                    sodium="71",
                ),
            },
        ],
    }
    second_payload = {
        **first_payload,
        "occurred_at": "2026-07-30T12:06:45+08:00",
        "source_text": "换一种说法：午饭是鸡蛋和牛奶",
        "items": list(reversed(first_payload["items"])),
    }

    first = recorded_meal(service, payload=first_payload)
    second = recorded_meal(service, payload=second_payload)

    assert first["ok"] is True
    assert second["ok"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_preview_commit_then_direct_retry_replays_existing_meal(
    service: DietService,
) -> None:
    payload = {
        "occurred_at": "2026-07-30T12:08:05+08:00",
        "meal_type": "lunch",
        "source_text": "中午喝了300ml牛奶",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "牛奶",
                "normalized_name": "milk",
                "amount": "300",
                "unit": "ml",
                "consumed_volume_ml": "300",
                "nutrition_basis": "per_100ml",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="60",
                    protein="3.2",
                    fat="3.3",
                    carbohydrate="4.8",
                    fiber="0",
                    sodium="45",
                    hydration_ml="88",
                ),
            }
        ],
    }
    preview = service.dispatch(
        {
            "domain": "meal",
            "action": "preview_record",
            "payload": payload,
        }
    )
    assert preview["ok"] is True
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

    retry_payload = deepcopy(payload)
    retry_payload["occurred_at"] = "2026-07-30T12:08:45+08:00"
    retry_payload["source_text"] = "换种说法：午饭喝的是牛奶"
    replayed = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": retry_payload,
        }
    )

    assert replayed["ok"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1


def test_explicit_quantity_uncertainty_still_requires_confirmation(
    service: DietService,
) -> None:
    payload = {
        "occurred_at": "2026-07-30T12:10:00+08:00",
        "meal_type": "lunch",
        "source_text": "中午大概吃了点鸡肉",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "大概一点鸡肉",
                "normalized_name": "chicken",
                "amount": "100",
                "unit": "g",
                "consumed_weight_g": "100",
                "nutrition_basis": "per_100g",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="165",
                    protein="31",
                    fat="3.6",
                    carbohydrate="0",
                    fiber="0",
                    sodium="74",
                ),
                "confidence_signals": {
                    "quantity_confidence": "0.1",
                },
            }
        ],
    }

    result = service.dispatch(
        {"domain": "meal", "action": "record", "payload": payload}
    )

    assert result["ok"] is True
    assert result["requires_confirmation"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 0


def test_discard_one_bad_apple_records_one_movement(
    service: DietService,
) -> None:
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "苹果",
                "normalized_name": "apple",
                "quantity": "5",
                "unit": "piece",
                "added_at": "2026-07-30T09:00:00+08:00",
                "source_text": "买了5个苹果",
                "storage_location": "fridge",
                "expires_at": "2026-08-05T00:00:00+08:00",
            },
        }
    )
    assert added["ok"] is True
    queried = service.dispatch(
        {
            "domain": "pantry",
            "action": "query",
            "payload": {
                "normalized_name": "apple",
                "include_details": True,
            },
        }
    )
    handle = queried["data"]["batches"][0]["workflow"]["batch_handle"]

    adjusted = service.dispatch(
        {
            "domain": "pantry",
            "action": "adjust",
            "payload": {
                "batch_handle": handle,
                "quantity": "4",
                "source_text": "苹果里面有一个坏了，我刚扔了",
                "reason": "discarded one spoiled apple",
            },
        }
    )

    assert adjusted["ok"] is True
    batch = service.connection.execute(
        """
        SELECT remaining_quantity
        FROM pantry_batches
        WHERE normalized_name = 'apple'
        """
    ).fetchone()
    movement = service.connection.execute(
        """
        SELECT movement_type, quantity
        FROM pantry_movements
        WHERE reason = 'discarded one spoiled apple'
        """
    ).fetchone()
    assert str(batch["remaining_quantity"]) == "4.0"
    assert movement["movement_type"] == "adjust"
    assert str(movement["quantity"]) == "1.0"


def test_correction_retry_has_one_active_meal(
    service: DietService,
) -> None:
    original = {
        "occurred_at": "2026-07-30T12:15:00+08:00",
        "meal_type": "lunch",
        "source_text": "中午喝了500ml豆浆",
        "location_type": "restaurant",
        "items": [
            {
                "raw_name": "500ml豆浆",
                "normalized_name": "soy milk",
                "amount": "500",
                "unit": "ml",
                "consumed_volume_ml": "500",
                "nutrition_basis": "per_100ml",
                "nutrition_dataset_version": "fixture-1",
                "nutrition_facts": _facts(
                    calories="33",
                    protein="3.5",
                    fat="1.8",
                    carbohydrate="2",
                    fiber="0",
                    sodium="50",
                    hydration_ml="95",
                ),
            }
        ],
    }
    recorded_meal(service, payload=original)
    queried = service.dispatch(
        {
            "domain": "meal",
            "action": "query",
            "payload": {"occurred_on": "2026-07-30"},
        }
    )
    handle = queried["data"]["meals"][0]["workflow"]["meal_handle"]
    corrected = deepcopy(original)
    corrected["source_text"] = "修正一下，豆浆是250ml"
    corrected["items"][0]["raw_name"] = "250ml豆浆"
    corrected["items"][0]["amount"] = "250"
    corrected["items"][0]["consumed_volume_ml"] = "250"
    corrected["items"][0]["confidence_signals"] = {
        "source_confidence": "1",
        "name_match_confidence": "1",
        "quantity_confidence": "1",
        "batch_uniqueness": "1",
        "context_consistency": "1",
        "personal_rule_confidence": "1",
    }
    update = {
        "domain": "meal",
        "action": "update",
        "payload": {"meal_handle": handle, "draft": corrected},
    }
    first = deepcopy(update) | {
        "_internal": {
            "operation_id": "op_00000000-0000-4000-8000-000000000061",
            "request_fingerprint": "1" * 64,
            "semantic_fingerprint": "2" * 64,
        }
    }
    second = deepcopy(update) | {
        "_internal": {
            "operation_id": "op_00000000-0000-4000-8000-000000000062",
            "request_fingerprint": "1" * 64,
            "semantic_fingerprint": "2" * 64,
        }
    }

    first_result = service.dispatch(first)
    second_result = service.dispatch(second)

    assert first_result["ok"] is True, first_result
    assert second_result["ok"] is True
    assert second_result["data"]["status"] == "committed"
    active = service.connection.execute(
        """
        SELECT count(*), min(total_calories), max(total_calories)
        FROM meals
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    assert active[0] == 1
    assert active[1] == active[2] == "82.5"


def test_cooking_fraction_keeps_rounded_hydration_within_volume(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "测试液体",
                "normalized_name": "test liquid",
                "quantity": "0.02",
                "unit": "ml",
                "added_at": "2026-07-30T17:30:00+08:00",
                "source_text": "加入0.02ml测试液体",
                "storage_location": "pantry",
                "expires_at": "2027-01-01T00:00:00+08:00",
            },
        }
    )
    assert added["ok"] is True

    cooked = service.dispatch(
        {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-30T18:30:00+08:00",
                "meal_type": "dinner",
                "source_text": "做了三份测试菜，吃了一份",
                "dish": {
                    "raw_name": "测试菜",
                    "normalized_name": "test dish",
                    "unit": "portion",
                    "consumed_quantity": "1",
                    "ingredients": [
                        {
                            "raw_name": "0.02ml测试液体",
                            "normalized_name": "test liquid",
                            "amount": "0.02",
                            "unit": "ml",
                            "consumed_volume_ml": "0.02",
                            "nutrition_basis": "per_100ml",
                            "nutrition_dataset_version": "fixture-1",
                            "nutrition_facts": _facts(
                                calories="0",
                                protein="0",
                                fat="0",
                                carbohydrate="0",
                                fiber="0",
                                sodium="0",
                                hydration_ml="100",
                            ),
                        }
                    ],
                    "leftover": {
                        "food_name": "剩余测试菜",
                        "normalized_name": "test dish",
                        "quantity": "2",
                        "unit": "portion",
                        "storage_location": "fridge",
                        "expires_at": "2026-08-01T00:00:00+08:00",
                    },
                },
            },
        }
    )

    assert cooked["ok"] is True
    item = service.connection.execute(
        """
        SELECT consumed_volume_ml, hydration_ml
        FROM meal_items
        WHERE normalized_name = 'test liquid'
        """
    ).fetchone()
    assert item["consumed_volume_ml"] == "0.01"
    assert item["hydration_ml"] == "0.01"


def test_cook_six_eggs_eat_two_store_four_is_conservative(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    for payload in (
        {
            "food_name": "鸡蛋",
            "normalized_name": "egg",
            "quantity": "8",
            "unit": "piece",
            "added_at": "2026-07-30T17:00:00+08:00",
            "source_text": "冰箱有8个鸡蛋",
            "storage_location": "fridge",
            "expires_at": "2026-08-06T00:00:00+08:00",
        },
        {
            "food_name": "食用油",
            "normalized_name": "cooking oil",
            "quantity": "990",
            "unit": "ml",
            "added_at": "2026-07-30T17:00:00+08:00",
            "source_text": "有990ml食用油",
            "storage_location": "pantry",
            "expires_at": "2027-01-01T00:00:00+08:00",
        },
    ):
        added = service.dispatch(
            {"domain": "pantry", "action": "add", "payload": payload}
        )
        assert added["ok"] is True

    cooking_request = {
            "domain": "meal",
            "action": "record_cooking",
            "payload": {
                "occurred_at": "2026-07-30T18:00:00+08:00",
                "meal_type": "dinner",
                "source_text": "煎了6个鸡蛋，用了10ml油，吃了2个，剩4个放冰箱",
                "dish": {
                    "raw_name": "煎蛋",
                    "normalized_name": "fried egg",
                    "unit": "piece",
                    "consumed_quantity": "2",
                    "ingredients": [
                        {
                            "raw_name": "6个鸡蛋",
                            "normalized_name": "egg",
                            "amount": "6",
                            "unit": "piece",
                            "consumed_weight_g": "300",
                            "nutrition_basis": "per_100g",
                            "nutrition_dataset_version": "fixture-1",
                            "nutrition_facts": _facts(
                                calories="143",
                                protein="12.6",
                                fat="9.5",
                                carbohydrate="0.7",
                                fiber="0",
                                sodium="142",
                            ),
                        },
                        {
                            "raw_name": "10ml油",
                            "normalized_name": "cooking oil",
                            "amount": "10",
                            "unit": "ml",
                            "consumed_volume_ml": "10",
                            "nutrition_basis": "per_100ml",
                            "nutrition_dataset_version": "fixture-1",
                            "nutrition_facts": _facts(
                                calories="884",
                                protein="0",
                                fat="100",
                                carbohydrate="0",
                                fiber="0",
                                sodium="0",
                            ),
                        },
                    ],
                    "leftover": {
                        "food_name": "熟煎蛋",
                        "normalized_name": "fried egg",
                        "quantity": "4",
                        "unit": "piece",
                        "storage_location": "fridge",
                        "expires_at": "2026-08-01T00:00:00+08:00",
                    },
                },
            },
        }
    cooked = service.dispatch(cooking_request)

    assert cooked["ok"] is True
    cooking_transaction = service.connection.execute(
        "SELECT transaction_id FROM meals"
    ).fetchone()[0]

    def remaining(name: str) -> str:
        return str(
            service.connection.execute(
                """
                SELECT remaining_quantity
                FROM pantry_batches
                WHERE normalized_name = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()[0]
        )

    assert remaining("egg") == "2.0"
    assert remaining("cooking oil") == "980.0"
    assert remaining("fried egg") == "4.0"

    replayed = service.dispatch(cooking_request)
    assert replayed["ok"] is True
    assert service.connection.execute(
        "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
    ).fetchone()[0] == 1
    assert remaining("egg") == "2.0"
    assert remaining("cooking oil") == "980.0"
    assert remaining("fried egg") == "4.0"

    assert service.connection.execute(
        """
        SELECT count(*)
        FROM pantry_movements
        WHERE transaction_id = ? AND movement_type = 'consume'
        """,
        (cooking_transaction,),
    ).fetchone()[0] == 2
    egg_intake = service.connection.execute(
        """
        SELECT items.amount, items.consumed_weight_g, items.calories,
               evidence.scale_factor
        FROM meal_items AS items
        JOIN meal_item_nutrition_evidence AS evidence
          ON evidence.meal_item_id = items.id
        WHERE items.normalized_name = 'egg'
        """
    ).fetchone()
    assert egg_intake["amount"] == "2"
    assert egg_intake["consumed_weight_g"] == "100"
    assert egg_intake["calories"] == "143"
    assert egg_intake["scale_factor"] == "1"

    manager = TransactionManager(service.connection)
    manager.undo(cooking_transaction)
    assert remaining("egg") == "8.0"
    assert remaining("cooking oil") == "990.0"
    assert service.connection.execute(
        """
        SELECT count(*) FROM pantry_batches
        WHERE normalized_name = 'fried egg'
        """
    ).fetchone()[0] == 0

    manager.redo(cooking_transaction)
    assert remaining("egg") == "2.0"
    assert remaining("cooking oil") == "980.0"
    assert remaining("fried egg") == "4.0"


def test_linked_liquid_label_cannot_exceed_consumed_volume(
    service: DietService,
) -> None:
    added = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": "异常豆浆",
                "normalized_name": "impossible soy milk",
                "quantity": "500",
                "unit": "ml",
                "added_at": "2026-07-30T07:00:00+08:00",
                "source_text": "买了500ml异常豆浆",
                "storage_location": "fridge",
                "expires_at": "2026-08-01T00:00:00+08:00",
                "nutrition_profile": {
                    "normalized_name": "impossible soy milk",
                    "serving_basis": "per_100ml",
                    "nutrition": {
                        "calories_kcal": "33",
                        "protein_g": "3.5",
                        "fat_g": "1.8",
                        "carbohydrate_g": "2",
                        "fiber_g": "0",
                        "sodium_mg": "50",
                        "hydration_ml": "120",
                    },
                    "source_text": "异常测试标签",
                    "source_grade": "A",
                },
            },
        }
    )
    assert added["ok"] is True

    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": {
                "occurred_at": "2026-07-30T08:00:00+08:00",
                "meal_type": "breakfast",
                "source_text": "喝了500ml异常豆浆",
                "location_type": "home",
                "items": [
                    {
                        "raw_name": "异常豆浆",
                        "normalized_name": "impossible soy milk",
                        "amount": "500",
                        "unit": "ml",
                        "consumed_volume_ml": "500",
                    }
                ],
            },
        }
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert service.connection.execute(
        "SELECT count(*) FROM meals"
    ).fetchone()[0] == 0
