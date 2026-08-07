from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import (
    complete_meal_payload,
    snapshot_business_tables,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def _service(tmp_path: Path) -> DietService:
    return DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=lambda: NOW,
    )


def _portion_payload(
    *,
    expression: str = "一点",
    suggested: str = "25",
    lower: str = "10",
    upper: str = "40",
    include_estimate: bool = True,
) -> dict[str, object]:
    payload = deepcopy(complete_meal_payload())
    payload.update(
        occurred_at="2026-08-04T10:00:00+08:00",
        meal_type="snack",
        source_text=f"吃了{expression}花生",
    )
    item = payload["items"][0]
    item.update(
        raw_name="花生",
        normalized_name="peanut",
        amount=suggested,
        unit="g",
        portion_expression=expression,
        consumed_weight_g=suggested,
    )
    if include_estimate:
        item["quantity_estimate"] = {
            "suggested": suggested,
            "lower": lower,
            "upper": upper,
            "unit": "g",
            "evidence_type": "household_range",
            "policy_key": "portion.generic.small_amount",
        }
    return payload


def _record(service: DietService, payload: dict[str, object]) -> dict[str, object]:
    return service.dispatch(
        {"domain": "meal", "action": "record", "payload": payload}
    )


def test_vague_portion_returns_bounded_preview_without_writing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        before = snapshot_business_tables(service.connection)

        preview = _record(service, _portion_payload())

        assert preview["ok"] is True
        assert preview["outcome"] == "preview_ready"
        assert preview["requires_confirmation"] is True
        assert snapshot_business_tables(service.connection) == before
        resolution = preview["data"]["resolution"]
        assert resolution["subject"] == "peanut"
        assert resolution["state"] == "bounded_estimate"
        assert resolution["normalized_value"] == {"value": "25", "unit": "g"}
        assert resolution["interval"] == {
            "lower": "10",
            "upper": "40",
            "unit": "g",
        }
        assert resolution["evidence"] == {
            "type": "household_range",
            "policy_key": "portion.generic.small_amount",
        }
        assert resolution["requires_confirmation"] is True

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
        assert committed["data"]["resolution"]["state"] == "confirmed_estimate"
        assert committed["data"]["resolution"]["requires_confirmation"] is False
        assert service.connection.execute(
            "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
        ).fetchone()[0] == 1
    finally:
        service.close()


def test_confirmed_learned_portion_records_without_an_estimate_preview(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        learned = service.dispatch(
            {
                "domain": "system",
                "action": "update_preferences",
                "payload": {
                    "rule_type": "portion",
                    "subject": "peanut|我的一小把",
                    "outcome": {"amount": "18", "unit": "g"},
                    "source_text": "我的一小把花生固定按18克",
                },
            }
        )
        assert learned["ok"] is True

        result = _record(
            service,
            _portion_payload(
                expression="我的一小把",
                include_estimate=False,
            ),
        )

        assert result["ok"] is True
        assert result["outcome"] == "write_committed"
        assert result["requires_confirmation"] is False
        assert result["data"]["meal"]["items"][0]["amount"] == "18"
    finally:
        service.close()


def test_invalid_quantity_estimate_bounds_fail_without_writes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        before = snapshot_business_tables(service.connection)

        result = _record(
            service,
            _portion_payload(lower="40", upper="10"),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert snapshot_business_tables(service.connection) == before
    finally:
        service.close()


def test_changed_quantity_requires_a_new_preview_instead_of_old_handle(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        before = snapshot_business_tables(service.connection)
        original = _record(service, _portion_payload(suggested="25"))
        changed = _record(
            service,
            _portion_payload(suggested="5", lower="3", upper="8"),
        )

        original_handle = original["data"]["preview"]["workflow"][
            "commit_handle"
        ]
        changed_handle = changed["data"]["preview"]["workflow"][
            "commit_handle"
        ]
        assert original_handle != changed_handle
        assert changed["data"]["resolution"]["normalized_value"] == {
            "value": "5",
            "unit": "g",
        }
        assert snapshot_business_tables(service.connection) == before
    finally:
        service.close()


def test_multiple_estimates_share_one_preview_and_one_final_commit(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        payload = _portion_payload()
        second = deepcopy(payload["items"][0])
        second.update(
            raw_name="瓜子",
            normalized_name="sunflower-seed",
            amount="12",
            consumed_weight_g="12",
            portion_expression="几口",
            quantity_estimate={
                "suggested": "12",
                "lower": "6",
                "upper": "20",
                "unit": "g",
                "evidence_type": "household_range",
                "policy_key": "portion.generic.small_amount",
            },
        )
        payload["items"].append(second)
        before = snapshot_business_tables(service.connection)

        preview = _record(service, payload)

        assert preview["ok"] is True
        assert preview["outcome"] == "preview_ready"
        assert preview["data"]["resolution"]["state"] == "bounded_estimates"
        assert len(preview["data"]["resolution"]["items"]) == 2
        assert snapshot_business_tables(service.connection) == before

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
        assert committed["data"]["resolution"]["state"] == "confirmed_estimates"
        assert len(committed["data"]["meal"]["items"]) == 2
        assert service.connection.execute(
            "SELECT count(*) FROM meals WHERE deleted_at IS NULL"
        ).fetchone()[0] == 1
    finally:
        service.close()
