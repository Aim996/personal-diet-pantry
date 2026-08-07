from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
import sqlite3

from personal_diet_pantry.service import DietService


BUSINESS_TABLES = (
    "meals",
    "meal_items",
    "water_logs",
    "body_weight_logs",
    "pantry_batches",
    "pantry_movements",
    "pantry_cost_allocations",
    "nutrition_cache",
    "nutrition_profiles",
    "pantry_nutrition_links",
    "prepared_food_profiles",
    "nutrition_goal_profiles",
    "personal_rules",
    "learning_events",
    "pending_inventory_links",
    "recipe_profiles",
    "shopping_lists",
    "shopping_list_items",
)

PROVENANCE_KEYS = {"goals_confirmed", "goal_source", "confirmed_at"}


def nutrition_estimate() -> dict[str, str]:
    return {
        "calories": "180",
        "protein": "4",
        "fat": "1",
        "carbohydrate": "40",
        "fiber": "1",
        "sodium": "5",
        "source": "contract fixture",
        "source_grade": "C",
        "uncertainty": "controlled test estimate",
    }


def complete_meal_payload() -> dict[str, object]:
    return {
        "occurred_at": "2026-07-29T12:00:00+08:00",
        "meal_type": "lunch",
        "source_text": "午餐吃了150克米饭",
        "location_type": "home",
        "items": [
            {
                "raw_name": "米饭",
                "normalized_name": "rice",
                "amount": "150",
                "unit": "g",
                "consumed_weight_g": "150",
                "nutrition_basis": "per_100g",
                "nutrition_dataset_version": "contract-fixture-1",
                "nutrition_facts": nutrition_estimate(),
            }
        ],
    }


def pantry_add_payload() -> dict[str, object]:
    return {
        "food_name": "鸡蛋",
        "normalized_name": "egg",
        "quantity": "12",
        "unit": "piece",
        "added_at": "2026-07-29T08:00:00Z",
        "source_text": "买了12个鸡蛋",
        "storage_location": "fridge",
        "expires_at": "2026-08-05T00:00:00Z",
    }


def recorded_meal(
    service: DietService,
    *,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    request = {
        "domain": "meal",
        "action": "record",
        "payload": deepcopy(
            dict(payload) if payload is not None else complete_meal_payload()
        ),
    }
    result = service.dispatch(request)
    assert result["ok"] is True
    if result["requires_confirmation"]:
        result = service.dispatch(
            {
                "domain": "meal",
                "action": "commit_record",
                "payload": {
                    "commit_handle": result["data"]["preview"]["workflow"][
                        "commit_handle"
                    ],
                    "confirmed": True,
                },
            }
        )
        assert result["ok"] is True
    return result


def query_meals(
    service: DietService,
    occurred_on: str = "2026-07-29",
) -> list[dict[str, object]]:
    result = service.dispatch(
        {
            "domain": "meal",
            "action": "query",
            "payload": {"occurred_on": occurred_on},
        }
    )
    assert result["ok"] is True
    return result["data"]["meals"]


def recorded_water(
    service: DietService,
    *,
    amount: str = "300",
) -> dict[str, object]:
    result = service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": amount,
                "unit": "ml",
                "occurred_at": "2026-07-29T08:00:00+08:00",
                "source_text": f"喝了{amount}ml水",
            },
        }
    )
    assert result["ok"] is True
    return result


def query_water(
    service: DietService,
    occurred_on: str = "2026-07-29",
) -> dict[str, object]:
    result = service.dispatch(
        {
            "domain": "water",
            "action": "query",
            "payload": {"occurred_on": occurred_on},
        }
    )
    assert result["ok"] is True
    return result["data"]["summary"]


def assert_write_envelope(value: Mapping[str, object]) -> None:
    assert PROVENANCE_KEYS <= value.keys()
    assert "daily_progress" in value
    assert isinstance(value.get("rendered_receipt"), str)
    assert value["rendered_receipt"]


def snapshot_business_tables(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    snapshot = {}
    for table in BUSINESS_TABLES:
        columns = tuple(
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        assert columns, f"missing business table {table}"
        order = "id" if "id" in columns else ", ".join(columns)
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"
        ).fetchall()
        snapshot[table] = tuple(
            tuple(row[column] for column in columns)
            for row in rows
        )
    return snapshot


def remaining_quantity(
    service: DietService,
    normalized_name: str,
) -> Decimal:
    row = service.connection.execute(
        """
        SELECT COALESCE(sum(CAST(remaining_quantity AS TEXT)), '0')
        FROM pantry_batches
        WHERE normalized_name = ?
          AND status NOT IN ('discarded', 'expired', 'consumed')
        """,
        (normalized_name,),
    ).fetchone()
    return Decimal(str(row[0]))


def recent_operation_handle(
    service: DietService,
    *,
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
    assert len(result["data"]["candidates"]) == 1
    return result["data"]["candidates"][0]["workflow"][
        "operation_handle"
    ]


def make_latest_meal_nutrition_incomplete(
    service: DietService,
) -> int:
    """Create one valid historical meal, then model a legacy unknown record."""

    recorded_meal(service)
    meal_id = service.connection.execute(
        "SELECT id FROM meals ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    service.connection.execute(
        """
        UPDATE meal_items
        SET calories = NULL,
            protein = NULL,
            fat = NULL,
            carbohydrate = NULL,
            fiber = NULL,
            sodium = NULL,
            hydration_ml = NULL,
            source_grade = 'D',
            nutrition_source = 'legacy unknown',
            uncertainty = 'nutrition values unavailable'
        WHERE meal_id = ?
        """,
        (meal_id,),
    )
    service.connection.execute(
        """
        UPDATE meals
        SET total_calories = NULL,
            total_protein = NULL,
            total_fat = NULL,
            total_carbohydrate = NULL,
            total_fiber = NULL,
            total_sodium = NULL,
            total_hydration_ml = NULL,
            nutrition_status = 'incomplete',
            nutrition_missing_fields_json = ?
        WHERE id = ?
        """,
        (
            (
                '["calories","protein","fat","carbohydrate",'
                '"fiber","sodium"]'
            ),
            meal_id,
        ),
    )
    service.connection.commit()
    return int(meal_id)
