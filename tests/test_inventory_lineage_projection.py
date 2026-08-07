from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import nutrition_estimate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _add(
    service: DietService,
    *,
    name: str,
    quantity: str,
    unit: str,
) -> None:
    result = service.dispatch(
        {
            "domain": "pantry",
            "action": "add",
            "payload": {
                "food_name": name,
                "normalized_name": name,
                "quantity": quantity,
                "unit": unit,
                "added_at": "2026-07-29T08:00:00+08:00",
                "source_text": f"add {name}",
                "storage_location": "fridge",
                "expires_at": "2026-08-10T00:00:00+08:00",
            },
        }
    )
    assert result["ok"] is True


def _search(service: DietService, text: str) -> dict[str, object]:
    result = service.dispatch(
        {
            "domain": "pantry",
            "action": "search",
            "payload": {"search_text": text, "limit": 5},
        }
    )
    assert result["ok"] is True
    assert result["data"]["candidates"]
    return result["data"]["candidates"][0]


def test_prepared_food_search_projects_only_formal_cooking_relation(
    tmp_path: Path,
) -> None:
    service = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: NOW,
    )
    try:
        _add(service, name="rice", quantity="300", unit="g")
        cooked = service.dispatch(
            {
                "domain": "meal",
                "action": "record_cooking",
                "payload": {
                    "occurred_at": "2026-07-29T18:00:00+08:00",
                    "meal_type": "dinner",
                    "source_text": "cook two portions of fried rice",
                    "dish": {
                        "raw_name": "fried rice",
                        "normalized_name": "fried-rice",
                        "unit": "portion",
                        "consumed_quantity": "1",
                        "ingredients": [
                            {
                                "raw_name": "rice",
                                "normalized_name": "rice",
                                "amount": "300",
                                "unit": "g",
                                "consumed_weight_g": "300",
                                "nutrition_basis": "per_100g",
                                "nutrition_dataset_version": "lineage-test-1",
                                "nutrition_facts": nutrition_estimate(),
                            }
                        ],
                        "leftover": {
                            "food_name": "fried rice",
                            "normalized_name": "fried-rice",
                            "quantity": "1",
                            "unit": "portion",
                            "storage_location": "fridge",
                            "expires_at": "2026-07-31T00:00:00+08:00",
                        },
                    },
                },
            }
        )
        assert cooked["ok"] is True

        candidate = _search(service, "fried rice")

        assert candidate["inventory_kind"] == "prepared_food"
        assert candidate["relations"] == [
            {
                "relation_type": "prepared_from_cooking",
                "evidence_type": "committed_transaction",
                "summary": "由已提交的烹饪事务生成",
            }
        ]
        assert "meal_id" not in str(candidate)
        assert "batch_id" not in str(candidate)
        assert "transaction_id" not in str(candidate)
    finally:
        service.close()


def test_unlinked_raw_and_processed_names_do_not_infer_containment(
    tmp_path: Path,
) -> None:
    service = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: NOW,
    )
    try:
        _add(service, name="egg", quantity="22", unit="piece")
        _add(service, name="boiled-egg", quantity="3", unit="piece")

        raw = _search(service, "egg")
        named_processed = _search(service, "boiled-egg")

        assert raw["available_quantity"] == "22.0"
        assert named_processed["available_quantity"] == "3.0"
        assert raw["inventory_kind"] == "raw_food"
        assert named_processed["inventory_kind"] == "raw_food"
        assert raw["relations"] == []
        assert named_processed["relations"] == []
    finally:
        service.close()
