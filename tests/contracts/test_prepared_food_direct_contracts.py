from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from personal_diet_pantry.service import DietService


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": domain, "action": action, "payload": payload}
    )


def _remaining(service: DietService, normalized_name: str) -> Decimal:
    value = service.connection.execute(
        "SELECT sum(remaining_quantity) FROM pantry_batches "
        "WHERE normalized_name = ?",
        (normalized_name,),
    ).fetchone()[0]
    return Decimal(str(value or 0))


def _facts() -> dict[str, object]:
    return {
        "calories": "147.9166666667",
        "protein": "5",
        "fat": "1",
        "carbohydrate": "30",
        "fiber": "2",
        "sodium": "20",
        "hydration_ml": "50",
        "source": "prepared-food contract fixture",
        "source_grade": "A",
    }


def _record_cat_ears_cooking(service: DietService) -> None:
    service._clock = lambda: datetime(
        2026, 8, 2, 10, 0, tzinfo=timezone.utc
    )
    added = _dispatch(
        service,
        "pantry",
        "add",
        {
            "food_name": "猫耳朵面",
            "normalized_name": "猫耳朵面",
            "quantity": "810",
            "unit": "g",
            "added_at": "2026-08-02T08:00:00+08:00",
            "expiry_date": "2026-09-01",
            "source_text": "买了810克猫耳朵面",
        },
    )
    assert added["ok"] is True
    cooked = _dispatch(
        service,
        "meal",
        "record_cooking",
        {
            "occurred_at": "2026-08-02T18:00:00+08:00",
            "meal_type": "dinner",
            "source_text": "煮了360克猫耳朵面，吃了一半",
            "dish": {
                "raw_name": "煮猫耳朵面",
                "normalized_name": "煮猫耳朵面",
                "unit": "g",
                "consumed_quantity": "180",
                "ingredients": [
                    {
                        "raw_name": "360克猫耳朵面",
                        "normalized_name": "猫耳朵面",
                        "amount": "360",
                        "unit": "g",
                        "consumed_weight_g": "360",
                        "nutrition_basis": "per_100g",
                        "nutrition_dataset_version": "fixture-1",
                        "nutrition_facts": _facts(),
                    }
                ],
                "leftover": {
                    "food_name": "煮猫耳朵面",
                    "normalized_name": "煮猫耳朵面",
                    "quantity": "180",
                    "unit": "g",
                    "storage_location": "fridge",
                    "expiry_date": "2026-08-04",
                },
            },
        },
    )
    assert cooked["ok"] is True
    assert _remaining(service, "猫耳朵面") == Decimal("450")
    assert _remaining(service, "煮猫耳朵面") == Decimal("180")


def test_record_prepared_reuses_snapshot_and_only_deducts_leftover(
    service: DietService,
) -> None:
    _record_cat_ears_cooking(service)
    search = _dispatch(
        service,
        "pantry",
        "search",
        {"search_text": "煮猫耳朵面"},
    )
    workflow = search["data"]["candidates"][0]["workflow"]
    assert "prepared_food_handle" in workflow

    result = _dispatch(
        service,
        "meal",
        "record_prepared",
        {
            "prepared_food_handle": workflow["prepared_food_handle"],
            "source_text": "刚把冰箱那盒猫耳朵吃了",
        },
    )

    assert result["ok"] is True
    assert result["data"]["meal"]["total_calories"] == "266.25"
    assert _remaining(service, "煮猫耳朵面") == Decimal("0")
    assert _remaining(service, "猫耳朵面") == Decimal("450")

    recent = _dispatch(
        service,
        "transaction",
        "get_recent",
        {"operation": "undo", "operation_type": "meal_record", "limit": 1},
    )
    operation_handle = recent["data"]["candidates"][0]["workflow"][
        "operation_handle"
    ]
    undone = _dispatch(
        service,
        "transaction",
        "undo",
        {"operation_handle": operation_handle},
    )
    assert undone["ok"] is True, undone
    assert _remaining(service, "煮猫耳朵面") == Decimal("180")
    assert _remaining(service, "猫耳朵面") == Decimal("450")
