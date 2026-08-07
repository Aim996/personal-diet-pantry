from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def cost_service(tmp_path: Path):
    instance = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: NOW,
    )
    try:
        yield instance
    finally:
        instance.close()


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": domain, "action": action, "payload": payload}
    )


def _add_priced(
    service: DietService,
    *,
    food_name: str,
    normalized_name: str,
    quantity: str,
    price_minor: int,
    currency: str,
) -> dict[str, object]:
    result = _dispatch(
        service,
        "pantry",
        "add",
        {
            "food_name": food_name,
            "normalized_name": normalized_name,
            "quantity": quantity,
            "unit": "piece",
            "price_minor": price_minor,
            "currency": currency,
            "added_at": "2026-07-30T00:00:00Z",
            "expires_at": "2026-08-30T00:00:00Z",
            "source_text": f"买了{quantity}个{food_name}",
        },
    )
    assert result["ok"] is True
    return result


def _batch_handle(service: DietService, normalized_name: str) -> str:
    result = _dispatch(
        service,
        "pantry",
        "query",
        {"normalized_name": normalized_name, "include_details": True},
    )
    return result["data"]["batches"][0]["workflow"]["batch_handle"]


def test_cost_allocation_conserves_batch_price_through_consume_and_waste(
    cost_service: DietService,
) -> None:
    service = cost_service
    _add_priced(
        service,
        food_name="鸡蛋",
        normalized_name="egg",
        quantity="12",
        price_minor=1200,
        currency="CNY",
    )
    preview = _dispatch(
        service,
        "pantry",
        "preview_deduct",
        {
            "normalized_name": "egg",
            "quantity": "2",
            "unit": "piece",
            "source_text": "吃了2个鸡蛋",
        },
    )
    assert _dispatch(
        service,
        "pantry",
        "commit_deduct",
        {"commit_handle": preview["data"]["workflow"]["commit_handle"]},
    )["ok"] is True
    assert _dispatch(
        service,
        "pantry",
        "discard",
        {
            "batch_handle": _batch_handle(service, "egg"),
            "source_text": "剩下鸡蛋坏了",
            "waste_category": "spoilage",
        },
    )["ok"] is True

    row = service.connection.execute(
        """
        SELECT price_minor, remaining_cost_minor
        FROM pantry_batches
        WHERE normalized_name = 'egg'
        """
    ).fetchone()
    allocated = service.connection.execute(
        "SELECT sum(cost_minor) FROM pantry_cost_allocations"
    ).fetchone()[0]
    assert row["price_minor"] == 1200
    assert row["remaining_cost_minor"] == 0
    assert allocated == 1200

    report = _dispatch(
        service,
        "report",
        "cost_summary",
        {"date_start": "2026-07-30", "date_end": "2026-07-30"},
    )
    assert report["ok"] is True
    assert report["data"]["currencies"] == [
        {
            "currency": "CNY",
            "purchased_minor": 1200,
            "consumed_minor": 200,
            "waste_minor": 1000,
            "adjustment_minor": 0,
        }
    ]
    assert "combined_total_minor" not in report["data"]

    recent = _dispatch(
        service,
        "transaction",
        "get_recent",
        {"operation": "undo", "operation_type": "pantry_adjust", "limit": 1},
    )
    operation_handle = recent["data"]["candidates"][0]["workflow"][
        "operation_handle"
    ]
    assert _dispatch(
        service,
        "transaction",
        "undo",
        {"operation_handle": operation_handle},
    )["ok"] is True
    restored = service.connection.execute(
        """
        SELECT remaining_quantity, remaining_cost_minor
        FROM pantry_batches
        WHERE normalized_name = 'egg'
        """
    ).fetchone()
    assert restored["remaining_quantity"] == 10
    assert restored["remaining_cost_minor"] == 1000
    assert service.connection.execute(
        "SELECT sum(cost_minor) FROM pantry_cost_allocations"
    ).fetchone()[0] == 200

    redo = _dispatch(
        service,
        "transaction",
        "get_recent",
        {"operation": "redo", "operation_type": "pantry_adjust", "limit": 1},
    )
    assert _dispatch(
        service,
        "transaction",
        "redo",
        {
            "operation_handle": redo["data"]["candidates"][0]["workflow"][
                "operation_handle"
            ]
        },
    )["ok"] is True
    assert service.connection.execute(
        """
        SELECT remaining_cost_minor
        FROM pantry_batches
        WHERE normalized_name = 'egg'
        """
    ).fetchone()[0] == 0
    assert service.connection.execute(
        "SELECT sum(cost_minor) FROM pantry_cost_allocations"
    ).fetchone()[0] == 1200


def test_waste_defaults_unspecified_and_adjustment_is_not_waste(
    cost_service: DietService,
) -> None:
    service = cost_service
    _add_priced(
        service,
        food_name="苹果",
        normalized_name="apple",
        quantity="4",
        price_minor=800,
        currency="CNY",
    )
    assert _dispatch(
        service,
        "pantry",
        "discard",
        {
            "batch_handle": _batch_handle(service, "apple"),
            "source_text": "苹果扔掉了",
        },
    )["ok"] is True
    _add_priced(
        service,
        food_name="鸡蛋",
        normalized_name="egg",
        quantity="12",
        price_minor=1200,
        currency="CNY",
    )
    assert _dispatch(
        service,
        "pantry",
        "adjust",
        {
            "batch_handle": _batch_handle(service, "egg"),
            "quantity": "10",
            "source_text": "盘点修正为10个",
        },
    )["ok"] is True

    report = _dispatch(
        service,
        "report",
        "waste_summary",
        {"date_start": "2026-07-30", "date_end": "2026-07-30"},
    )
    assert report["ok"] is True
    assert report["data"]["event_count"] == 1
    assert report["data"]["categories"][0]["category"] == "unspecified"
    assert report["data"]["currencies"][0]["waste_minor"] == 800


def test_cost_summary_never_sums_different_currencies(
    cost_service: DietService,
) -> None:
    service = cost_service
    _add_priced(
        service,
        food_name="苹果",
        normalized_name="apple",
        quantity="4",
        price_minor=800,
        currency="CNY",
    )
    _add_priced(
        service,
        food_name="牛油果",
        normalized_name="avocado",
        quantity="2",
        price_minor=500,
        currency="USD",
    )

    result = _dispatch(
        service,
        "report",
        "cost_summary",
        {"date_start": "2026-07-30", "date_end": "2026-07-30"},
    )

    assert result["ok"] is True
    assert [item["currency"] for item in result["data"]["currencies"]] == [
        "CNY",
        "USD",
    ]
    assert "total_minor" not in result["data"]
    assert result["data"]["coverage"]["priced_batches"] == 2


def test_trend_summary_is_bounded_for_two_year_personal_window(
    cost_service: DietService,
) -> None:
    service = cost_service
    result = _dispatch(
        service,
        "report",
        "trend_summary",
        {"days": 730},
    )

    assert result["ok"] is True
    assert result["data"]["granularity"] == "month"
    assert len(result["data"]["buckets"]) <= 25
