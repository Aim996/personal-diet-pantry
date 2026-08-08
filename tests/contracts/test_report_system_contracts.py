from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from personal_diet_pantry import backup as backup_module
from personal_diet_pantry import self_check as self_check_module
from personal_diet_pantry.service import DietService
from scripts.behavior_contract import load_behavior_contract
from tests.test_goal_confirmation import (
    test_goal_preview_then_one_confirmation_commits_without_a_second_prompt as _goal_preview_contract,
)

from tests.contracts.helpers import (
    complete_meal_payload,
    make_latest_meal_nutrition_incomplete,
    nutrition_estimate,
    pantry_add_payload,
    recent_operation_handle,
    snapshot_business_tables,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_goal_preview_then_one_confirmation_commits_without_a_second_prompt(
    service: DietService,
) -> None:
    _goal_preview_contract(service)


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return service.dispatch(
        {
            "domain": domain,
            "action": action,
            "payload": payload or {},
        }
    )


def _report(
    service: DietService,
    action: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return _dispatch(service, "report", action, payload)


def _system(
    service: DietService,
    action: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return _dispatch(service, "system", action, payload)


def _confirm_goals(service: DietService) -> dict[str, object]:
    return _system(
        service,
        "update_goals",
        {
            "calories_kcal": 2100,
            "protein_g": 90,
            "fat_g": 65,
            "carbohydrate_g": 260,
            "fiber_g": 30,
            "sodium_mg": 2000,
            "water_ml": 2200,
            "timezone_name": "Asia/Shanghai",
            "source_text": "确认 0.6.0 契约测试目标",
        },
    )


def _add_pantry(
    service: DietService,
    *,
    normalized_name: str = "egg",
) -> dict[str, object]:
    payload = pantry_add_payload()
    payload["normalized_name"] = normalized_name
    payload["food_name"] = normalized_name
    result = _dispatch(service, "pantry", "add", payload)
    assert result["ok"] is True
    return result


def _incomplete_backfill_page(
    service: DietService,
) -> tuple[int, dict[str, object]]:
    meal_id = make_latest_meal_nutrition_incomplete(service)
    result = _system(
        service,
        "query_nutrition_backfill",
        {"limit": 1},
    )
    assert result["ok"] is True
    assert len(result["data"]["meals"]) == 1
    return meal_id, result["data"]["meals"][0]


def test_report_system_actions_bind_exact_contract_tests() -> None:
    contract = load_behavior_contract(PROJECT_ROOT)

    for domain in ("report", "system"):
        for item in contract[domain].values():
            assert item.python_test.startswith(
                (
                    "tests/contracts/test_report_system_contracts.py::test_",
                    "tests/contracts/test_maintenance_contracts.py::test_",
                    "tests/contracts/test_cost_report_contracts.py::test_",
                    "tests/contracts/test_data_portability_contracts.py::test_",
                    "tests/contracts/test_data_erasure_contracts.py::test_",
                )
            )


def test_today_daily_weekly_monthly_generate_only_derived_files(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)

    for action in ("today", "daily", "weekly", "monthly"):
        result = _report(
            service,
            action,
            {"report_date": "2026-07-29"},
        )
        assert result["ok"] is True
        relative_path = result["data"]["report"]["relative_path"]
        assert (service.data_paths.root / relative_path).is_file()

    assert snapshot_business_tables(service.connection) == before
    assert sorted(
        path.relative_to(service.data_paths.reports).as_posix()
        for path in service.data_paths.reports.rglob("*.md")
    ) == [
        "daily/2026-07-29.md",
        "monthly/2026-07.md",
        "weekly/2026-W31.md",
    ]


def test_progress_is_read_only_and_goal_gated(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    unconfirmed = _report(
        service,
        "progress",
        {"report_date": "2026-07-29"},
    )

    assert unconfirmed["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert unconfirmed["data"]["goals_confirmed"] is False
    assert all(
        metric["target"] is None
        and metric["percent"] is None
        and metric["bar"] is None
        for metric in unconfirmed["data"]["metrics"]
    )

    assert _confirm_goals(service)["ok"] is True
    after_confirmation = snapshot_business_tables(service.connection)
    confirmed = _report(
        service,
        "progress",
        {"report_date": "2026-07-29"},
    )
    assert confirmed["ok"] is True
    assert snapshot_business_tables(service.connection) == after_confirmation
    assert confirmed["data"]["goals_confirmed"] is True
    assert all(
        metric["target"] is not None
        and metric["percent"] is not None
        and metric["bar"] is not None
        for metric in confirmed["data"]["metrics"]
    )


def test_insights_is_read_only_bounded_and_goal_gated(
    service: DietService,
) -> None:
    _add_pantry(service)
    before = snapshot_business_tables(service.connection)
    unconfirmed = _report(
        service,
        "insights",
        {
            "report_date": "2026-07-29",
            "within_days": 7,
            "limit": 1,
        },
    )

    assert unconfirmed["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert unconfirmed["data"]["goals_confirmed"] is False
    assert all(
        metric["target"] is None
        and metric["delta_to_target"] is None
        and metric["status"] == "unconfirmed"
        for metric in unconfirmed["data"]["metrics"]
    )
    assert len(unconfirmed["data"]["priorities"]) <= 3
    assert len(unconfirmed["data"]["expiring_inventory"]["items"]) <= 1

    assert _confirm_goals(service)["ok"] is True
    after_confirmation = snapshot_business_tables(service.connection)
    confirmed = _report(
        service,
        "insights",
        {"report_date": "2026-07-29", "limit": 1},
    )
    assert confirmed["ok"] is True
    assert snapshot_business_tables(service.connection) == after_confirmation
    assert confirmed["data"]["goals_confirmed"] is True
    assert all(
        metric["target"] is not None
        for metric in confirmed["data"]["metrics"]
    )
    assert len(confirmed["data"]["priorities"]) <= 3


def test_expiring_inventory_is_read_only_and_calendar_correct(
    service: DietService,
) -> None:
    _add_pantry(service)
    before = snapshot_business_tables(service.connection)

    result = _report(
        service,
        "expiring_inventory",
        {
            "report_date": "2026-07-29",
            "within_days": 7,
        },
    )

    assert result["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert result["data"]["as_of"] == "2026-07-29"
    assert result["data"]["within_days"] == 7
    assert len(result["data"]["batches"]) == 1
    assert (
        result["data"]["batches"][0]["normalized_name"]
        == "egg"
    )


def test_initialize_and_self_check_on_clean_database(
    service: DietService,
) -> None:
    initialized = _system(service, "initialize")
    checked = _system(service, "self_check")

    assert initialized["ok"] is True
    assert initialized["data"]["initialized"] is True
    assert checked["ok"] is True
    assert checked["data"]["checks"]
    assert not [
        check
        for check in checked["data"]["checks"]
        if check["level"] == "FAIL"
    ]


def test_validate_database_reports_integrity_and_foreign_keys(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    result = _system(service, "validate_database")

    assert result["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert {
        check["code"]: check["level"]
        for check in result["data"]["checks"]
    } == {
        "integrity_check": "PASS",
        "foreign_key_check": "PASS",
    }


def test_backup_is_verified_and_does_not_change_business_rows(
    service: DietService,
) -> None:
    _add_pantry(service)
    before = snapshot_business_tables(service.connection)

    result = _system(service, "backup", {"label": "contract"})

    assert result["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    backup_data = result["data"]["backup"]
    backup_path = service.data_paths.backups / backup_data["name"]
    assert backup_module.verify_backup(
        backup_path,
        data_paths=service.data_paths,
    )
    assert backup_data["workflow"]["backup_handle"].startswith("wfh_")


def test_restore_rejects_missing_confirmation(
    service: DietService,
) -> None:
    backup = _system(service, "backup", {"label": "contract"})
    handle = backup["data"]["backup"]["workflow"]["backup_handle"]
    before = snapshot_business_tables(service.connection)

    denied = _system(
        service,
        "restore",
        {
            "backup_handle": handle,
            "confirmed": False,
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "RESTORE_REQUIRES_CONFIRMATION"
    assert snapshot_business_tables(service.connection) == before


def test_restore_round_trip_recovers_exact_business_state(
    service: DietService,
) -> None:
    _add_pantry(service)
    weight = service.dispatch(
        {
            "domain": "weight",
            "action": "record",
            "payload": {
                "weight": "105",
                "unit": "kg",
                "status_note": "睡前",
            },
        }
    )
    assert weight["ok"] is True
    snapshot_at_backup = snapshot_business_tables(service.connection)
    backup = _system(service, "backup", {"label": "contract"})
    handle = backup["data"]["backup"]["workflow"]["backup_handle"]

    _add_pantry(service, normalized_name="milk")
    assert (
        snapshot_business_tables(service.connection)
        != snapshot_at_backup
    )
    restored = _system(
        service,
        "restore",
        {
            "backup_handle": handle,
            "confirmed": True,
        },
    )

    assert restored["ok"] is True
    assert restored["data"]["restored"] is True
    assert (
        snapshot_business_tables(service.connection)
        == snapshot_at_backup
    )
    restored_weight = service.connection.execute(
        """
        SELECT weight_g, status_note
        FROM body_weight_logs
        WHERE deleted_at IS NULL
        """
    ).fetchone()
    assert dict(restored_weight) == {
        "weight_g": 105_000,
        "status_note": "睡前",
    }


def test_migrate_is_idempotent_at_latest_schema(
    service: DietService,
) -> None:
    before = snapshot_business_tables(service.connection)
    versions_before = [
        row[0]
        for row in service.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]

    first = _system(service, "migrate")
    second = _system(service, "migrate")

    assert first["ok"] is True
    assert second["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    expected_versions = [
        int(path.name.split("_", 1)[0])
        for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    ]
    assert versions_before == expected_versions
    assert [
        row[0]
        for row in service.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ] == versions_before


def test_meal_persists_profile_timezone_and_hashed_session(
    service: DietService,
) -> None:
    goals = _confirm_goals(service)
    assert goals["ok"] is True
    payload = complete_meal_payload()
    payload["occurred_at"] = "2026-07-29T14:15:00Z"
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": payload,
            "_internal": {
                "operation_id": "op_00000000-0000-4000-8000-000000000061",
                "request_fingerprint": "6" * 64,
                "semantic_fingerprint": "7" * 64,
                "source_session_key": "agent:u2:main:full-private-value",
            },
        }
    )

    assert result["ok"] is True
    row = service.connection.execute(
        """
        SELECT event_timezone, local_date,
               source_session_key, source_session_hash
        FROM meals
        """
    ).fetchone()
    assert row["event_timezone"] == "Asia/Shanghai"
    assert row["local_date"] == "2026-07-29"
    assert row["source_session_key"] is None
    assert len(row["source_session_hash"]) == 64
    assert "full-private-value" not in row["source_session_hash"]


def test_progress_distinguishes_complete_from_verified_nutrition(
    service: DietService,
) -> None:
    recorded = service.dispatch(
        {
            "domain": "meal",
            "action": "record",
            "payload": complete_meal_payload(),
        }
    )
    assert recorded["ok"] is True

    verified = _report(
        service, "progress", {"report_date": "2026-07-29"}
    )
    assert verified["data"]["nutrition_quality"] == {
        "field_completeness": "complete",
        "calculation_status": "valid",
        "provenance_status": "traceable",
    }

    service.connection.execute(
        """
        UPDATE meals
        SET nutrition_calculation_status = 'unverified',
            nutrition_provenance_status = 'untraceable'
        """
    )
    service.connection.commit()
    historical = _report(
        service, "progress", {"report_date": "2026-07-29"}
    )
    assert historical["data"]["nutrition_quality"] == {
        "field_completeness": "complete",
        "calculation_status": "unverified",
        "provenance_status": "untraceable",
    }


def test_repair_keeps_recent_expired_preview_for_minimum_retention(
    service: DietService,
) -> None:
    rows = (
        (
            "old-preview",
            "2026-07-27T23:00:00Z",
            "2026-07-28T00:00:00Z",
        ),
        (
            "recent-preview",
            "2026-07-29T23:40:00Z",
            "2026-07-29T23:50:00Z",
        ),
    )
    service.connection.executemany(
        """
        INSERT INTO operation_previews (
            token_hash, operation_type, request_json, result_json,
            resource_versions_json, created_at, expires_at
        ) VALUES (?, 'meal_preview', '{}', '{}', '[]', ?, ?)
        """,
        rows,
    )
    service.connection.commit()

    self_check_module.repair_safe_issues(
        service.connection,
        service.data_paths,
        service.migrations_dir,
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    remaining = {
        row[0]
        for row in service.connection.execute(
            "SELECT token_hash FROM operation_previews"
        )
    }
    assert "old-preview" not in remaining
    assert "recent-preview" in remaining


def test_query_update_and_forget_preferences(
    service: DietService,
) -> None:
    empty = _system(service, "query_preferences")
    assert empty["ok"] is True
    assert empty["data"]["preferences"] == []

    updated = _system(
        service,
        "update_preferences",
        {
            "rule_type": "water_unit",
            "subject": "cup",
            "outcome": {"milliliters": 350},
            "source_text": "一杯按 350 毫升计算",
        },
    )
    assert updated["ok"] is True
    active = _system(service, "query_preferences")
    assert len(active["data"]["preferences"]) == 1
    assert active["data"]["preferences"][0]["active"] is True
    assert active["data"]["preferences"][0]["outcome"] == {
        "milliliters": 350
    }

    forgotten = _system(
        service,
        "forget_preference",
        {
            "rule_type": "water_unit",
            "subject": "cup",
            "source_text": "忘记杯容量偏好",
        },
    )
    assert forgotten["ok"] is True
    assert _system(
        service,
        "query_preferences",
    )["data"]["preferences"] == []
    all_rules = _system(
        service,
        "query_preferences",
        {"include_inactive": True},
    )
    assert len(all_rules["data"]["preferences"]) == 1
    assert all_rules["data"]["preferences"][0]["active"] is False


def test_query_update_undo_redo_goals(
    service: DietService,
) -> None:
    initial = _system(service, "query_goals")["data"]["goal_profile"]
    assert initial["goal_source"] == "configuration_default"
    assert initial["confirmed_at"] is None

    updated = _confirm_goals(service)
    assert updated["ok"] is True
    confirmed = _system(
        service,
        "query_goals",
    )["data"]["goal_profile"]
    assert confirmed["goal_source"] == "user_confirmed"
    assert confirmed["confirmed_at"] is not None

    undo = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": recent_operation_handle(
                service,
                operation="undo",
                operation_type="profile_update",
            )
        },
    )
    assert undo["ok"] is True
    assert (
        _system(service, "query_goals")["data"]["goal_profile"]
        == initial
    )

    redo = _dispatch(
        service,
        "transaction",
        "redo",
        {
            "operation_handle": recent_operation_handle(
                service,
                operation="redo",
                operation_type="profile_update",
            )
        },
    )
    assert redo["ok"] is True
    assert (
        _system(service, "query_goals")["data"]["goal_profile"]
        == confirmed
    )


def test_nutrition_backfill_query_is_bounded(
    service: DietService,
) -> None:
    make_latest_meal_nutrition_incomplete(service)
    make_latest_meal_nutrition_incomplete(service)
    before = snapshot_business_tables(service.connection)

    result = _system(
        service,
        "query_nutrition_backfill",
        {"limit": 1},
    )

    assert result["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert len(result["data"]["meals"]) == 1
    page = result["data"]["meals"][0]
    assert page["total_item_count"] >= 1
    assert 1 <= page["batch_item_count"] <= 100
    assert page["meal_handle"].startswith("wfh_")
    assert page["batch_handle"].startswith("wfh_")
    assert all(
        item["item_handle"].startswith("wfh_")
        for item in page["items"]
    )


def test_nutrition_backfill_commit_is_atomic_and_undoable(
    service: DietService,
) -> None:
    meal_id, page = _incomplete_backfill_page(service)
    before = snapshot_business_tables(service.connection)
    item = page["items"][0]

    invalid = _system(
        service,
        "commit_nutrition_backfill",
        {
            "meal_handle": page["meal_handle"],
            "batch_handle": page["batch_handle"],
            "items": [{"item_handle": item["item_handle"]}],
        },
    )
    assert invalid["ok"] is False
    assert snapshot_business_tables(service.connection) == before

    committed = _system(
        service,
        "commit_nutrition_backfill",
        {
            "meal_handle": page["meal_handle"],
            "batch_handle": page["batch_handle"],
            "items": [
                {
                    "item_handle": candidate["item_handle"],
                    "nutrition_estimate": nutrition_estimate(),
                }
                for candidate in page["items"]
            ],
        },
    )
    assert committed["ok"] is True
    assert committed["data"]["status"] == "committed"
    row = service.connection.execute(
        """
        SELECT nutrition_status, nutrition_missing_fields_json
        FROM meals WHERE id = ?
        """,
        (meal_id,),
    ).fetchone()
    assert tuple(row) == ("complete", "[]")

    undone = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": recent_operation_handle(
                service,
                operation="undo",
                operation_type="record_correction",
            )
        },
    )
    assert undone["ok"] is True
    assert snapshot_business_tables(service.connection) == before


def test_repair_requires_isolated_corruption_fixture(
    service: DietService,
) -> None:
    preview = _dispatch(
        service,
        "meal",
        "preview_record",
        complete_meal_payload(),
    )
    assert preview["ok"] is True
    service.connection.execute(
        """
        UPDATE operation_previews
        SET created_at = '1999-01-01T00:00:00Z',
            expires_at = '2000-01-01T00:00:00Z'
        """
    )
    service.connection.commit()
    assert (
        service.connection.execute(
            """
            SELECT count(*) FROM operation_previews
            WHERE expires_at <= ?
            """,
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchone()[0]
        >= 1
    )
    before = snapshot_business_tables(service.connection)

    result = _system(
        service,
        "repair",
        {"report_date": "2026-07-29"},
    )

    assert result["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert (
        service.connection.execute(
            """
            SELECT count(*) FROM operation_previews
            WHERE expires_at <= ?
            """,
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchone()[0]
        == 0
    )
    assert not [
        check
        for check in result["data"]["checks"]
        if check["level"] == "FAIL"
    ]
    assert (
        service.data_paths.reports / "daily" / "2026-07-29.md"
    ).is_file()
