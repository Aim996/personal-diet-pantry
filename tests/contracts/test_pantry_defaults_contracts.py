from __future__ import annotations

from personal_diet_pantry.service import DietService


def _add(service: DietService, payload: dict[str, object]) -> dict[str, object]:
    return service.dispatch(
        {"domain": "pantry", "action": "add", "payload": payload}
    )


def test_ordinary_pantry_add_infers_missing_location_and_expiry(
    service: DietService,
) -> None:
    result = _add(
        service,
        {
            "food_name": "苹果",
            "normalized_name": "苹果",
            "quantity": "2",
            "unit": "pieces",
            "added_at": "2026-08-07T19:20:00+08:00",
            "source_text": "刚买了俩苹果，放冰箱了",
        },
    )

    assert result["ok"] is True
    batch = result["data"]["batch"]
    assert batch["storage_location"] == "冷藏"
    assert batch["storage_location_source"] == "user"
    assert batch["expiry_source"] == "estimated"
    assert batch["expires_at"] is not None
    row = service.connection.execute(
        """
        SELECT storage_location, storage_location_source,
               expires_at, expiry_source
        FROM pantry_batches WHERE normalized_name = '苹果'
        """
    ).fetchone()
    assert tuple(row) == (
        batch["storage_location"],
        batch["storage_location_source"],
        batch["expires_at"],
        batch["expiry_source"],
    )


def test_user_metadata_overrides_inference_and_is_marked_as_user_fact(
    service: DietService,
) -> None:
    result = _add(
        service,
        {
            "food_name": "酸奶",
            "normalized_name": "酸奶",
            "quantity": "2",
            "unit": "packs",
            "added_at": "2026-08-07T19:20:00+08:00",
            "storage_location": "常温",
            "expiry_date": "2026-08-10",
            "source_text": "两盒酸奶先放常温，8月10日到期",
        },
    )

    assert result["ok"] is True
    batch = result["data"]["batch"]
    assert batch["storage_location"] == "常温"
    assert batch["storage_location_source"] == "user"
    assert batch["expiry_source"] == "user"


def test_compact_query_exposes_location_and_estimate_provenance(
    service: DietService,
) -> None:
    assert _add(
        service,
        {
            "food_name": "速冻水饺",
            "normalized_name": "速冻水饺",
            "quantity": "2",
            "unit": "packs",
            "added_at": "2026-08-07T19:20:00+08:00",
            "source_text": "买了两袋速冻水饺",
        },
    )["ok"] is True

    result = service.dispatch(
        {"domain": "pantry", "action": "query", "payload": {}}
    )

    assert result["ok"] is True
    batch = result["data"]["batches"][0]
    assert batch["storage_location"] == "冷冻"
    assert batch["storage_location_source"] == "inferred"
    assert batch["expiry_source"] == "estimated"
