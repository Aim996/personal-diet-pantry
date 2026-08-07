from __future__ import annotations

from personal_diet_pantry.service import DietService


def _pantry(
    service: DietService,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": "pantry", "action": action, "payload": payload}
    )


def test_expiry_date_preserves_shanghai_calendar_day(
    service: DietService,
) -> None:
    result = _pantry(
        service,
        "add",
        {
            "food_name": "豆花",
            "normalized_name": "豆花",
            "quantity": "180",
            "unit": "g",
            "added_at": "2026-08-02T08:00:00+08:00",
            "expiry_date": "2026-08-05",
            "source_text": "豆花8月5日到期",
        },
    )
    assert result["ok"] is True

    queried = _pantry(
        service,
        "query",
        {"normalized_name": "豆花", "include_details": True},
    )
    batch = queried["data"]["batches"][0]
    assert batch["expiry_date"] == "2026-08-05"
    assert batch["expires_at"] == "2026-08-05T15:59:59Z"

