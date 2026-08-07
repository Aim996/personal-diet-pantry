"""Read-only checks for historically implausible nutrition records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import sqlite3
from typing import Literal

from .timezones import local_datetime


@dataclass(frozen=True)
class NutritionAuditFinding:
    """One local diagnostic finding; database identifiers stay administrative."""

    code: str
    severity: Literal["WARN", "FAIL"]
    meal_id: int
    meal_item_id: int | None
    message: str


def audit_nutrition(
    connection: sqlite3.Connection,
) -> list[NutritionAuditFinding]:
    """Inspect active and historical meal rows without mutating them."""

    findings: list[NutritionAuditFinding] = []
    meals = connection.execute(
        """
        SELECT id, occurred_at, meal_type, event_timezone,
               nutrition_status, nutrition_calculation_status,
               total_calories, total_protein, total_fat,
               total_carbohydrate, total_fiber, total_sodium,
               total_hydration_ml
        FROM meals
        ORDER BY id
        """
    ).fetchall()
    items = connection.execute(
        """
        SELECT items.id, items.meal_id, items.item_role,
               items.consumed_weight_g, items.consumed_volume_ml,
               items.hydration_ml, items.inventory_deduction_weight_g,
               items.calories, items.protein, items.fat,
               items.carbohydrate, items.fiber, items.sodium,
               evidence.basis, evidence.scale_factor
        FROM meal_items AS items
        LEFT JOIN meal_item_nutrition_evidence AS evidence
          ON evidence.meal_item_id = items.id
        ORDER BY items.id
        """
    ).fetchall()
    children = {
        int(row["parent_item_id"])
        for row in connection.execute(
            """
            SELECT parent_item_id FROM meal_items
            WHERE parent_item_id IS NOT NULL
            """
        )
    }
    by_meal: dict[int, list[sqlite3.Row]] = {}
    for item in items:
        meal_id = int(item["meal_id"])
        item_id = int(item["id"])
        by_meal.setdefault(meal_id, []).append(item)
        hydration = _decimal(item["hydration_ml"])
        volume = _decimal(item["consumed_volume_ml"])
        weight = _decimal(item["consumed_weight_g"])
        if hydration is not None and volume is not None and hydration > volume:
            findings.append(
                _finding(
                    "hydration_exceeds_volume",
                    "FAIL",
                    meal_id,
                    item_id,
                    "Stored hydration exceeds consumed volume",
                )
            )
        if (
            hydration is not None
            and weight is not None
            and item["basis"] == "per_100g"
            and hydration > weight
        ):
            findings.append(
                _finding(
                    "hydration_exceeds_mass",
                    "FAIL",
                    meal_id,
                    item_id,
                    "Stored hydration exceeds consumed mass",
                )
            )
        is_nutritional = (
            item["item_role"] != "dish" or item_id not in children
        )
        if is_nutritional and item["basis"] is None:
            findings.append(
                _finding(
                    "missing_nutrition_basis",
                    "WARN",
                    meal_id,
                    item_id,
                    "Nutrition has no reproducible serving basis",
                )
            )
        if item["basis"] is not None and _decimal(item["scale_factor"]) is None:
            findings.append(
                _finding(
                    "missing_scale_factor",
                    "FAIL",
                    meal_id,
                    item_id,
                    "Nutrition evidence has no valid scale factor",
                )
            )
        if (
            item["inventory_deduction_weight_g"] is not None
            and connection.execute(
                """
                SELECT 1 FROM pantry_movements
                WHERE linked_meal_item_id = ?
                  AND movement_type = 'consume'
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            is None
        ):
            findings.append(
                _finding(
                    "inventory_deduction_without_movement",
                    "FAIL",
                    meal_id,
                    item_id,
                    "Meal item claims an inventory deduction without a movement",
                )
            )

    nutrient_fields = (
        "calories",
        "protein",
        "fat",
        "carbohydrate",
        "fiber",
        "sodium",
        "hydration_ml",
    )
    for meal in meals:
        meal_id = int(meal["id"])
        if (
            meal["nutrition_status"] == "complete"
            and meal["nutrition_calculation_status"] != "valid"
        ):
            findings.append(
                _finding(
                    "complete_but_unverified",
                    "WARN",
                    meal_id,
                    None,
                    "Complete nutrition fields have not been calculation-verified",
                )
            )
        leaf_items = [
            item
            for item in by_meal.get(meal_id, [])
            if item["item_role"] != "dish" or int(item["id"]) not in children
        ]
        if leaf_items and any(
            not _totals_match(
                meal[f"total_{field}"],
                [item[field] for item in leaf_items],
            )
            for field in nutrient_fields
        ):
            findings.append(
                _finding(
                    "meal_total_mismatch",
                    "FAIL",
                    meal_id,
                    None,
                    "Meal totals do not match the sum of nutritional items",
                )
            )
        if _meal_type_time_mismatch(meal):
            findings.append(
                _finding(
                    "meal_type_local_time_mismatch",
                    "WARN",
                    meal_id,
                    None,
                    "Meal type is implausible for the stored local event time",
                )
            )
    return findings


def _totals_match(total: object, values: list[object]) -> bool:
    expected = _decimal(total)
    parsed = [_decimal(value) for value in values]
    known = [value for value in parsed if value is not None]
    if expected is None:
        return not known
    if not known:
        return False
    actual = sum(known, Decimal("0"))
    return abs(expected - actual) <= Decimal("0.01")


def _meal_type_time_mismatch(meal: sqlite3.Row) -> bool:
    timezone_name = meal["event_timezone"]
    if not isinstance(timezone_name, str) or not timezone_name:
        return False
    try:
        hour = local_datetime(meal["occurred_at"], timezone_name).hour
    except (TypeError, ValueError):
        return True
    expected_hours = {
        "breakfast": range(4, 12),
        "lunch": range(10, 16),
        "dinner": range(16, 24),
    }
    hours = expected_hours.get(meal["meal_type"])
    return hours is not None and hour not in hours


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _finding(
    code: str,
    severity: Literal["WARN", "FAIL"],
    meal_id: int,
    meal_item_id: int | None,
    message: str,
) -> NutritionAuditFinding:
    return NutritionAuditFinding(
        code=code,
        severity=severity,
        meal_id=meal_id,
        meal_item_id=meal_item_id,
        message=message,
    )
