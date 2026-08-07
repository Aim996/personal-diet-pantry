from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from personal_diet_pantry.service import DietService
from personal_diet_pantry.transactions import (
    TransactionManager,
    TransactionNotUndoable,
)
from scripts.behavior_contract import load_behavior_contract

from tests.contracts.helpers import (
    pantry_add_payload,
    remaining_quantity,
    snapshot_business_tables,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pantry(
    service: DietService,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": "pantry", "action": action, "payload": payload}
    )


def _add(service: DietService) -> dict[str, object]:
    result = _pantry(service, "add", pantry_add_payload())
    assert result["ok"] is True
    return result


def _batches(
    service: DietService,
    normalized_name: str = "egg",
) -> list[dict[str, object]]:
    result = _pantry(
        service,
        "query",
        {
            "normalized_name": normalized_name,
            "include_details": True,
        },
    )
    assert result["ok"] is True
    return result["data"]["batches"]


def _batch_handle(service: DietService) -> str:
    return _batches(service)[0]["workflow"]["batch_handle"]


def _nutrition_profile() -> dict[str, object]:
    return {
        "normalized_name": "egg",
        "serving_basis": "per_serving",
        "nutrition": {
            "calories_kcal": "72",
            "protein_g": "6.3",
            "fat_g": "4.8",
            "carbohydrate_g": "0.4",
            "fiber_g": "0",
            "sodium_mg": "71",
        },
        "source_text": "鸡蛋营养标签",
        "source_grade": "A",
    }


def _add_packaged_tofu(
    service: DietService,
    *,
    boxes: str,
    expires_at: str,
) -> None:
    quantity = Decimal(boxes) * Decimal("180")
    result = _pantry(
        service,
        "add",
        {
            "food_name": "青禾无糖豆花",
            "normalized_name": "青禾无糖豆花",
            "quantity": format(quantity, "f"),
            "unit": "g",
            "display_quantity": boxes,
            "display_unit": "盒",
            "base_quantity_per_display_unit": "180",
            "added_at": "2026-08-02T08:00:00+08:00",
            "expires_at": expires_at,
            "source_text": f"买了{boxes}盒豆花，一盒180克",
        },
    )
    assert result["ok"] is True


def _transaction_handle(
    service: DietService,
    operation: str,
    operation_type: str,
) -> str:
    result = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {
                "operation": operation,
                "operation_type": operation_type,
                "limit": 1,
            },
        }
    )
    assert result["ok"] is True
    return result["data"]["candidates"][0]["workflow"][
        "operation_handle"
    ]


def test_pantry_transaction_actions_bind_exact_contract_tests() -> None:
    contract = load_behavior_contract(PROJECT_ROOT)

    for domain in ("pantry", "transaction"):
        for item in contract[domain].values():
            assert item.python_test.startswith(
                (
                    "tests/contracts/test_pantry_transaction_contracts.py::test_",
                    "tests/contracts/test_recipe_shopping_contracts.py::test_",
                    "tests/contracts/test_inventory_search_contracts.py::test_",
                )
            )


def test_pantry_query_empty_and_after_add(
    service: DietService,
) -> None:
    assert _batches(service) == []

    _add(service)

    batches = _batches(service)
    assert len(batches) == 1
    assert batches[0]["remaining_quantity"] == "12.0"
    assert batches[0]["status"] == "active"


def test_package_fields_survive_add_and_new_service_session(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    first = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    )
    try:
        result = _pantry(
            first,
            "add",
            {
                "food_name": "青禾无糖豆花",
                "normalized_name": "青禾无糖豆花",
                "quantity": "360",
                "unit": "g",
                "display_quantity": "2",
                "display_unit": "盒",
                "base_quantity_per_display_unit": "180",
                "package_hierarchy": [
                    {"unit": "箱", "contains": "6", "child_unit": "盒"}
                ],
                "added_at": "2026-08-02T08:00:00+08:00",
                "expires_at": "2026-08-03T23:59:59+08:00",
                "source_text": "两盒豆花，一盒180克",
            },
        )
        assert result["ok"] is True
    finally:
        first.close()

    second = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(data_dir)},
        env={},
    )
    try:
        row = second.connection.execute(
            """
            SELECT initial_display_quantity, display_unit,
                   base_quantity_per_display_unit, package_hierarchy_json
            FROM pantry_batches
            """
        ).fetchone()
        assert tuple(row[:3]) == (2.0, "盒", 180.0)
        assert json.loads(row[3]) == [
            {"child_unit": "盒", "contains": "6", "unit": "箱"}
        ]

        batches = _batches(second, "青禾无糖豆花")
        assert batches[0]["initial_display_quantity"] == "2.0"
        assert batches[0]["remaining_display_quantity"] == "2.0"
        assert batches[0]["display_unit"] == "盒"
    finally:
        second.close()


def test_three_boxes_discard_uses_product_handle_and_fefo(
    service: DietService,
) -> None:
    _add_packaged_tofu(
        service,
        boxes="2",
        expires_at="2026-08-03T23:59:59+08:00",
    )
    _add_packaged_tofu(
        service,
        boxes="3",
        expires_at="2026-08-07T23:59:59+08:00",
    )
    search = _pantry(service, "search", {"search_text": "豆花"})
    handle = search["data"]["candidates"][0]["workflow"][
        "inventory_match_handle"
    ]

    result = _pantry(
        service,
        "discard",
        {
            "inventory_match_handle": handle,
            "quantity": "3",
            "unit": "盒",
            "source_text": "有三盒豆花鼓包了，刚扔掉",
            "waste_category": "spoilage",
        },
    )

    assert result["ok"] is True
    rows = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = '青禾无糖豆花' ORDER BY expires_at"
    ).fetchall()
    assert [row[0] for row in rows] == [0, 360]
    movements = service.connection.execute(
        "SELECT movement_type, quantity FROM pantry_movements "
        "WHERE movement_type = 'discard' ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in movements] == [
        ("discard", 360),
        ("discard", 180),
    ]


def test_product_discard_is_atomic_when_display_quantity_exceeds_stock(
    service: DietService,
) -> None:
    _add_packaged_tofu(
        service,
        boxes="2",
        expires_at="2026-08-03T23:59:59+08:00",
    )
    search = _pantry(service, "search", {"search_text": "豆花"})
    handle = search["data"]["candidates"][0]["workflow"][
        "inventory_match_handle"
    ]
    before = snapshot_business_tables(service.connection)

    result = _pantry(
        service,
        "discard",
        {
            "inventory_match_handle": handle,
            "quantity": "3",
            "unit": "盒",
            "source_text": "扔了三盒豆花",
            "waste_category": "spoilage",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INSUFFICIENT_STOCK"
    assert snapshot_business_tables(service.connection) == before


def test_pantry_add_is_semantically_idempotent(
    service: DietService,
) -> None:
    request = {
        "domain": "pantry",
        "action": "add",
        "payload": pantry_add_payload(),
    }
    first = service.dispatch(
        deepcopy(request)
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000005"
                ),
                "request_fingerprint": "e" * 64,
                "semantic_fingerprint": "f" * 64,
            }
        }
    )
    second = service.dispatch(
        deepcopy(request)
        | {
            "_internal": {
                "operation_id": (
                    "op_00000000-0000-4000-8000-000000000006"
                ),
                "request_fingerprint": "e" * 64,
                "semantic_fingerprint": "f" * 64,
            }
        }
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["data"]["status"] == "committed"
    assert len(_batches(service)) == 1


def test_preview_add_commit_add_requires_matching_handle(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    preview = _pantry(service, "preview_add", pantry_add_payload())
    after = snapshot_business_tables(service.connection)

    assert preview["ok"] is True
    assert before == after
    rejected = _pantry(
        service,
        "commit_add",
        {"commit_handle": "not-a-real-handle"},
    )
    assert rejected["ok"] is False

    committed = _pantry(
        service,
        "commit_add",
        {
            "commit_handle": preview["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    assert committed["ok"] is True
    assert len(_batches(service)) == 1


def test_metadata_preview_commit_changes_only_metadata(
    service: DietService,
) -> None:
    _add(service)
    quantity_before = remaining_quantity(service, "egg")
    movements_before = service.connection.execute(
        "SELECT count(*) FROM pantry_movements"
    ).fetchone()[0]
    snapshot_before = snapshot_business_tables(service.connection)

    preview = _pantry(
        service,
        "preview_update_metadata",
        {
            "batch_handle": _batch_handle(service),
            "expires_at": "2026-08-10T00:00:00Z",
            "total_weight_g": "720",
            "average_unit_weight_g": "60",
            "weight_basis": "net",
            "weight_source": "称重",
            "weight_confidence": "confirmed",
            "source_text": "补充称重和保质期",
        },
    )
    assert preview["ok"] is True
    assert snapshot_business_tables(service.connection) == snapshot_before

    committed = _pantry(
        service,
        "commit_update_metadata",
        {
            "commit_handle": preview["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    assert committed["ok"] is True
    batch = _batches(service)[0]
    assert batch["expires_at"] == "2026-08-10T00:00:00Z"
    assert batch["total_weight_g"] == "720.0"
    assert remaining_quantity(service, "egg") == quantity_before
    assert (
        service.connection.execute(
            "SELECT count(*) FROM pantry_movements"
        ).fetchone()[0]
        == movements_before
    )


def test_nutrition_link_preview_commit_preserves_quantity(
    service: DietService,
) -> None:
    _add(service)
    quantity_before = remaining_quantity(service, "egg")
    before = snapshot_business_tables(service.connection)

    preview = _pantry(
        service,
        "preview_link_nutrition",
        {
            "batch_handle": _batch_handle(service),
            "linked_at": "2026-07-29T09:00:00Z",
            "nutrition_profile": _nutrition_profile(),
        },
    )
    assert preview["ok"] is True
    assert snapshot_business_tables(service.connection) == before

    committed = _pantry(
        service,
        "commit_link_nutrition",
        {
            "commit_handle": preview["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    assert committed["ok"] is True
    assert remaining_quantity(service, "egg") == quantity_before
    assert _batches(service)[0]["nutrition"] is not None


def test_adjust_discard_open_freeze_thaw_transitions(
    service: DietService,
) -> None:
    _add(service)
    adjusted = _pantry(
        service,
        "adjust",
        {
            "batch_handle": _batch_handle(service),
            "quantity": "10",
            "source_text": "盘点剩余10个",
        },
    )
    assert adjusted["ok"] is True
    assert remaining_quantity(service, "egg") == Decimal("10")

    for action, expected in (
        ("open", "opened"),
        ("freeze", "frozen"),
        ("thaw", "thawed"),
    ):
        result = _pantry(
            service,
            action,
            {
                "batch_handle": _batch_handle(service),
                "source_text": f"{action} contract",
            },
        )
        assert result["ok"] is True
        assert _batches(service)[0]["status"] == expected

    discarded = _pantry(
        service,
        "discard",
        {
            "batch_handle": _batch_handle(service),
            "source_text": "丢弃剩余鸡蛋",
        },
    )
    assert discarded["ok"] is True
    row = service.connection.execute(
        """
        SELECT status, remaining_quantity
        FROM pantry_batches
        WHERE normalized_name = 'egg'
        """
    ).fetchone()
    assert row["status"] == "discarded"
    assert Decimal(str(row["remaining_quantity"])) == 0


def test_preview_deduct_commit_deduct_preserves_nonnegative_stock(
    service: DietService,
) -> None:
    _add(service)
    before = remaining_quantity(service, "egg")
    snapshot = snapshot_business_tables(service.connection)

    preview = _pantry(
        service,
        "preview_deduct",
        {
            "normalized_name": "egg",
            "quantity": "2",
            "unit": "piece",
            "source_text": "用了2个鸡蛋",
        },
    )
    assert preview["ok"] is True
    assert snapshot_business_tables(service.connection) == snapshot

    committed = _pantry(
        service,
        "commit_deduct",
        {
            "commit_handle": preview["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    after = remaining_quantity(service, "egg")
    assert committed["ok"] is True
    assert after == before - Decimal("2")
    assert after >= 0


def test_stale_or_cross_workflow_handles_are_rejected(
    service: DietService,
) -> None:
    _add(service)
    metadata = _pantry(
        service,
        "preview_update_metadata",
        {
            "batch_handle": _batch_handle(service),
            "expires_at": "2026-08-10T00:00:00Z",
            "source_text": "预览新保质期",
        },
    )
    nutrition = _pantry(
        service,
        "preview_link_nutrition",
        {
            "batch_handle": _batch_handle(service),
            "linked_at": "2026-07-29T09:00:00Z",
            "nutrition_profile": _nutrition_profile(),
        },
    )
    cross = _pantry(
        service,
        "commit_update_metadata",
        {
            "commit_handle": nutrition["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    assert cross["ok"] is False

    changed = _pantry(
        service,
        "adjust",
        {
            "batch_handle": _batch_handle(service),
            "quantity": "11",
            "source_text": "使预览失效",
        },
    )
    assert changed["ok"] is True
    stale = _pantry(
        service,
        "commit_update_metadata",
        {
            "commit_handle": metadata["data"]["workflow"][
                "commit_handle"
            ]
        },
    )
    assert stale["ok"] is False


def test_transaction_get_recent_returns_opaque_operation_handles(
    service: DietService,
) -> None:
    _add(service)
    result = service.dispatch(
        {
            "domain": "transaction",
            "action": "get_recent",
            "payload": {
                "operation": "undo",
                "operation_type": "pantry_add",
                "limit": 1,
            },
        }
    )

    assert result["ok"] is True
    candidate = result["data"]["candidates"][0]
    handle = candidate["workflow"]["operation_handle"]
    assert handle.startswith("wfh_")
    assert "transaction_id" not in json.dumps(result)
    assert "txn_" not in candidate["summary"]


def test_transaction_undo_redo_restores_exact_pantry_state(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    _add(service)
    after_add = snapshot_business_tables(service.connection)

    undo = service.dispatch(
        {
            "domain": "transaction",
            "action": "undo",
            "payload": {
                "operation_handle": _transaction_handle(
                    service, "undo", "pantry_add"
                )
            },
        }
    )
    assert undo["ok"] is True
    assert undo["data"]["affected_rows"] > 0
    assert snapshot_business_tables(service.connection) == before

    redo = service.dispatch(
        {
            "domain": "transaction",
            "action": "redo",
            "payload": {
                "operation_handle": _transaction_handle(
                    service, "redo", "pantry_add"
                )
            },
        }
    )
    assert redo["ok"] is True
    assert redo["data"]["affected_rows"] > 0
    assert snapshot_business_tables(service.connection) == after_add


def test_zero_effect_transaction_cannot_return_successful_undo(
    service: DietService,
) -> None:
    service.connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, status, created_at, committed_at,
            source_text, before_snapshot, after_snapshot,
            undo_policy, effect_count
        ) VALUES (
            'txn_empty', 'record_correction', 'committed',
            '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z',
            'empty', '[]', '[]', 'snapshot', 0
        )
        """
    )
    service.connection.commit()

    with pytest.raises(TransactionNotUndoable):
        TransactionManager(service.connection).undo("txn_empty")
