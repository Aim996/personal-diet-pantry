from __future__ import annotations

from copy import deepcopy

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import complete_meal_payload, pantry_add_payload


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": domain, "action": action, "payload": payload}
    )


def test_public_outcomes_distinguish_read_preview_write_and_failure(
    service: DietService,
) -> None:
    assert _dispatch(service, "pantry", "query", {})["outcome"] == (
        "read_completed"
    )
    assert _dispatch(
        service, "pantry", "preview_add", pantry_add_payload()
    )["outcome"] == "preview_ready"
    assert _dispatch(
        service, "pantry", "add", pantry_add_payload()
    )["outcome"] == "write_committed"

    failed = _dispatch(service, "pantry", "add", {"food_name": "x"})

    assert failed["outcome"] == "failed"
    assert {"field", "reason", "expected", "retryable"} <= set(
        failed["error"]
    )


def test_semantic_duplicate_is_no_op_without_new_transaction(
    service: DietService,
) -> None:
    payload = complete_meal_payload()
    first = _dispatch(service, "meal", "record", deepcopy(payload))
    assert first["outcome"] == "write_committed"
    before = service.connection.execute(
        "SELECT count(*) FROM transactions"
    ).fetchone()[0]

    duplicate = _dispatch(service, "meal", "record", deepcopy(payload))
    after = service.connection.execute(
        "SELECT count(*) FROM transactions"
    ).fetchone()[0]

    assert duplicate["ok"] is True
    assert duplicate["outcome"] == "no_op"
    assert after == before


def test_empty_meal_is_invalid_not_a_successful_no_op(
    service: DietService,
) -> None:
    payload = complete_meal_payload()
    payload["items"] = []

    result = _dispatch(service, "meal", "record", payload)

    assert result["ok"] is False
    assert result["outcome"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


def test_product_unit_error_returns_actionable_recovery_fields(
    service: DietService,
) -> None:
    assert _dispatch(
        service, "pantry", "add", pantry_add_payload()
    )["ok"] is True
    search = _dispatch(
        service, "pantry", "search", {"search_text": "egg"}
    )
    handle = search["data"]["candidates"][0]["workflow"][
        "inventory_match_handle"
    ]

    result = _dispatch(
        service,
        "pantry",
        "deduct",
        {
            "inventory_match_handle": handle,
            "quantity": "1",
            "unit": "kg",
            "source_text": "used one egg",
        },
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "INVALID_INPUT",
        "message": "The request is invalid",
        "field": "unit",
        "reason": "unsupported_conversion",
        "expected": "the stored base unit or display unit",
        "retryable": True,
    }
