"""Bounded daily nutrition progress for compact post-commit replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import sqlite3
from typing import Sequence

from .models import NutritionGoals
from .timezones import local_date, local_day_utc_bounds


@dataclass(frozen=True)
class NutritionIncrement:
    calories: Decimal | None = None
    protein: Decimal | None = None
    fat: Decimal | None = None
    carbohydrate: Decimal | None = None
    fiber: Decimal | None = None
    water_ml: Decimal | None = None


@dataclass(frozen=True)
class NutritionQuality:
    field_completeness: str
    calculation_status: str
    provenance_status: str


@dataclass(frozen=True)
class NutritionAggregate:
    calories: Decimal
    protein: Decimal
    fat: Decimal
    carbohydrate: Decimal
    fiber: Decimal
    sodium: Decimal
    water_explicit_ml: Decimal
    water_hidden_ml: Decimal
    water_total_ml: Decimal
    incomplete_meal_count: int
    known_minimum: bool
    unknown_fields: frozenset[str]
    unknown_field_counts: tuple[tuple[str, int], ...] = ()
    nutrition_quality: NutritionQuality = NutritionQuality(
        "empty", "unverified", "untraceable"
    )


def aggregate_period(connection: sqlite3.Connection, *, start_utc: str, end_utc: str, test_run_id: str | None = None) -> NutritionAggregate:
    suffix, args = ("", []) if test_run_id is None else (" AND test_run_id = ?", [test_run_id])
    meals = connection.execute(f"""SELECT
        total_calories, total_protein, total_fat, total_carbohydrate,
        total_fiber, total_sodium, total_hydration_ml, nutrition_status,
        nutrition_missing_fields_json, nutrition_calculation_status,
        nutrition_provenance_status
        FROM meals WHERE deleted_at IS NULL AND occurred_at >= ? AND occurred_at < ?
        AND (transaction_id IS NULL OR EXISTS (SELECT 1 FROM transactions WHERE id = meals.transaction_id AND status = 'committed')){suffix}""", [start_utc, end_utc, *args]).fetchall()
    water = connection.execute(f"SELECT amount_ml FROM water_logs WHERE deleted_at IS NULL AND occurred_at >= ? AND occurred_at < ? AND (transaction_id IS NULL OR EXISTS (SELECT 1 FROM transactions WHERE id = water_logs.transaction_id AND status = 'committed')){suffix}", [start_utc, end_utc, *args]).fetchall()
    fields = ("total_calories", "total_protein", "total_fat", "total_carbohydrate", "total_fiber", "total_sodium")
    values = tuple(sum((_decimal(row[field]) for row in meals), Decimal("0")) for field in fields)
    explicit = sum((_decimal(row["amount_ml"]) for row in water), Decimal("0"))
    hidden = sum((_decimal(row["total_hydration_ml"]) for row in meals), Decimal("0"))
    incomplete = sum(row["nutrition_status"] != "complete" for row in meals)
    public_nutrition_fields = {
        "calories", "protein", "fat", "carbohydrate", "fiber", "sodium"
    }
    missing_fields_by_meal = tuple(
        {
            field
            for field in json.loads(row["nutrition_missing_fields_json"])
            if field in public_nutrition_fields
        }
        for row in meals
    )
    unknown_fields = frozenset(
        field for missing_fields in missing_fields_by_meal for field in missing_fields
    )
    unknown_field_counts = tuple(
        (field, sum(field in missing_fields for missing_fields in missing_fields_by_meal))
        for field in sorted(unknown_fields)
    )
    completeness_values = {
        row["nutrition_status"] for row in meals
    }
    calculation_values = {
        row["nutrition_calculation_status"] for row in meals
    }
    provenance_values = {
        row["nutrition_provenance_status"] for row in meals
    }
    quality = NutritionQuality(
        field_completeness=(
            "empty"
            if not meals
            else "complete"
            if completeness_values == {"complete"}
            else "incomplete"
            if completeness_values == {"incomplete"}
            else "partial"
        ),
        calculation_status=(
            "invalid"
            if "invalid" in calculation_values
            else "valid"
            if calculation_values == {"valid"}
            else "unverified"
        ),
        provenance_status=(
            "traceable"
            if provenance_values == {"traceable"}
            else "untraceable"
            if not provenance_values
            or provenance_values == {"untraceable"}
            else "partial"
        ),
    )
    return NutritionAggregate(
        *values,
        explicit,
        hidden,
        explicit + hidden,
        incomplete,
        bool(unknown_fields),
        unknown_fields,
        unknown_field_counts,
        quality,
    )


@dataclass(frozen=True)
class ProgressMetric:
    key: str
    label: str
    emoji: str
    current: Decimal | None
    target: Decimal | None
    unit: str
    goal_type: str
    increment: Decimal | None
    has_unknown: bool
    over_by: Decimal | None
    percent: int | None
    bar: str | None
    completeness: str = "complete"
    unknown_item_count: int = 0

    @property
    def name(self) -> str:
        """Compatibility alias for callers predating the structured API."""

        return self.label


@dataclass(frozen=True)
class ProgressSnapshot:
    local_date: date
    metrics: Sequence[ProgressMetric]
    incomplete_meal_count: int
    known_minimum: bool
    nutrition_quality: NutritionQuality


def daily_progress(
    connection: sqlite3.Connection,
    *,
    occurred_at: datetime,
    timezone_name: str,
    goals: NutritionGoals,
    increment: NutritionIncrement,
    occurred_on: date | None = None,
    test_run_id: str | None = None,
) -> tuple[ProgressMetric, ...]:
    """Aggregate one local day without returning underlying meal or pantry rows."""

    day = occurred_on or local_date(occurred_at, timezone_name)
    start, end = local_day_utc_bounds(day, timezone_name)
    return daily_progress_snapshot(
        connection,
        occurred_at=occurred_at,
        goal_profile=_GoalProfile(goals, timezone_name),
        increment=increment,
        occurred_on=day,
        test_run_id=test_run_id,
    ).metrics


@dataclass(frozen=True)
class _GoalProfile:
    goals: NutritionGoals
    timezone_name: str


def daily_progress_snapshot(
    connection: sqlite3.Connection,
    *,
    occurred_at: datetime,
    goal_profile,
    increment: NutritionIncrement = NutritionIncrement(),
    occurred_on: date | None = None,
    test_run_id: str | None = None,
) -> ProgressSnapshot:
    """Return one local day's public progress from the canonical aggregate."""

    day = occurred_on or local_date(occurred_at, goal_profile.timezone_name)
    start, end = local_day_utc_bounds(day, goal_profile.timezone_name)
    aggregate = aggregate_period(
        connection, start_utc=start, end_utc=end, test_run_id=test_run_id
    )
    unknown_field_counts = dict(aggregate.unknown_field_counts)
    specs = (
        (
            "calories",
            "热量",
            "🔥",
            aggregate.calories,
            goal_profile.goals.calories_kcal,
            "kcal",
            "maximum",
            increment.calories,
            "calories" in aggregate.unknown_fields,
            unknown_field_counts.get("calories", 0),
        ),
        (
            "protein",
            "蛋白",
            "🥩",
            aggregate.protein,
            goal_profile.goals.protein_g,
            "g",
            "minimum",
            increment.protein,
            "protein" in aggregate.unknown_fields,
            unknown_field_counts.get("protein", 0),
        ),
        (
            "fat",
            "脂肪",
            "🧈",
            aggregate.fat,
            goal_profile.goals.fat_g,
            "g",
            "maximum",
            increment.fat,
            "fat" in aggregate.unknown_fields,
            unknown_field_counts.get("fat", 0),
        ),
        (
            "carbohydrate",
            "碳水",
            "🌾",
            aggregate.carbohydrate,
            goal_profile.goals.carbohydrate_g,
            "g",
            "maximum",
            increment.carbohydrate,
            "carbohydrate" in aggregate.unknown_fields,
            unknown_field_counts.get("carbohydrate", 0),
        ),
        (
            "fiber",
            "纤维",
            "🥬",
            aggregate.fiber,
            goal_profile.goals.fiber_g,
            "g",
            "minimum",
            increment.fiber,
            "fiber" in aggregate.unknown_fields,
            unknown_field_counts.get("fiber", 0),
        ),
        (
            "water",
            "饮水",
            "💧",
            aggregate.water_total_ml,
            goal_profile.goals.water_ml,
            "ml",
            "minimum",
            increment.water_ml,
            False,
            0,
        ),
    )
    goals_confirmed = bool(getattr(goal_profile, "confirmed", True))
    metrics = tuple(
        ProgressMetric(
            key=key,
            label=label,
            emoji=emoji,
            current=current,
            target=Decimal(target) if goals_confirmed else None,
            unit=unit,
            goal_type=goal_type,
            increment=_nonzero_or_none(metric_increment),
            has_unknown=has_unknown,
            over_by=(
                max(current - Decimal(target), Decimal("0"))
                if goals_confirmed
                and goal_type == "maximum"
                and current is not None
                else None
            ),
            percent=(
                _percent(current, Decimal(target))
                if goals_confirmed
                else None
            ),
            bar=(
                _bar(current, Decimal(target))
                if goals_confirmed
                else None
            ),
            completeness=(
                "unknown"
                if has_unknown and current == 0
                else "partial"
                if has_unknown
                else "complete"
            ),
            unknown_item_count=unknown_item_count,
        )
        for (
            key,
            label,
            emoji,
            current,
            target,
            unit,
            goal_type,
            metric_increment,
            has_unknown,
            unknown_item_count,
        ) in specs
    )
    return ProgressSnapshot(
        local_date=day,
        metrics=metrics,
        incomplete_meal_count=aggregate.incomplete_meal_count,
        known_minimum=aggregate.known_minimum,
        nutrition_quality=aggregate.nutrition_quality,
    )


def increment_from_meal(meal) -> NutritionIncrement:
    """Extract the real increment already persisted on a meal record."""

    return NutritionIncrement(
        calories=meal.total_calories,
        protein=meal.total_protein,
        fat=meal.total_fat,
        carbohydrate=meal.total_carbohydrate,
        fiber=meal.total_fiber,
        water_ml=meal.total_hydration_ml,
    )


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    number = Decimal(str(value))
    return number.normalize() if number != 0 else Decimal("0")


def _nonzero_or_none(value: Decimal | None) -> Decimal | None:
    if value is None or value == 0:
        return None
    return value.normalize()


def _percent(current: Decimal | None, target: Decimal) -> int:
    if current is None or target <= 0:
        return 0
    return int((current * 100 / target).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _bar(current: Decimal | None, target: Decimal) -> str:
    if current is None or target <= 0:
        filled = 0
    else:
        filled = min(
            10,
            int((current * 10 / target).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        )
    return "\u2588" * filled + "\u2591" * (10 - filled)
