"""Bounded, read-only evidence for deciding what to address next."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import sqlite3
from typing import Mapping

from . import pantry, progress
from .goal_profiles import GoalProfile
from .timezones import local_date, local_day_utc_bounds


_PERIODS = frozenset({"daily", "weekly", "monthly"})
_ACTIVE_BATCH_STATUSES = ("active", "opened", "frozen", "thawed")
_NUTRITION_KEYS = frozenset(
    {"calories", "protein", "fat", "carbohydrate", "fiber", "sodium"}
)


@dataclass(frozen=True)
class InsightPeriod:
    kind: str
    start_date: date
    end_date: date
    day_count: int


@dataclass(frozen=True)
class InsightMetric:
    key: str
    current: Decimal
    target: Decimal | None
    delta_to_target: Decimal | None
    unit: str
    goal_type: str
    status: str
    has_unknown: bool


@dataclass(frozen=True)
class InsightDataQuality:
    nutrition_data_state: str
    incomplete_meal_count: int
    unknown_fields: tuple[str, ...]
    weak_estimate_count: int
    pending_inventory_link_count: int
    totals_are_known_minimums: bool


@dataclass(frozen=True)
class ExpiringItem:
    food_name: str
    normalized_name: str
    remaining_quantity: Decimal
    unit: str
    expires_on: date
    days_remaining: int
    storage_location: str | None


@dataclass(frozen=True)
class ExpiringInventory:
    total_count: int
    items: tuple[ExpiringItem, ...]
    truncated: bool


@dataclass(frozen=True)
class InsightSnapshot:
    period: InsightPeriod
    goals_confirmed: bool
    goal_source: str
    confirmed_at: datetime | None
    metrics: tuple[InsightMetric, ...]
    data_quality: InsightDataQuality
    expiring_inventory: ExpiringInventory
    priorities: tuple[Mapping[str, object], ...]


def build_insights(
    connection: sqlite3.Connection,
    *,
    anchor: date,
    period: str,
    goal_profile: GoalProfile,
    within_days: int = 7,
    limit: int = 5,
) -> InsightSnapshot:
    """Combine canonical facts into a small deterministic decision snapshot."""

    if period not in _PERIODS:
        raise ValueError("period must be daily, weekly, or monthly")
    if isinstance(within_days, bool) or not 1 <= within_days <= 30:
        raise ValueError("within_days must be between 1 and 30")
    if isinstance(limit, bool) or not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")

    start_date, end_exclusive = _period_dates(anchor, period)
    start_utc = local_day_utc_bounds(start_date, goal_profile.timezone_name)[0]
    end_utc = local_day_utc_bounds(
        end_exclusive, goal_profile.timezone_name
    )[0]
    aggregate = progress.aggregate_period(
        connection,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    day_count = (end_exclusive - start_date).days
    metrics = _metrics(aggregate, goal_profile, day_count)
    pending_count, weak_count, meal_count = _quality_counts(
        connection,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    expiring = _expiring_inventory(
        connection,
        anchor=anchor,
        cutoff=anchor + timedelta(days=within_days),
        timezone_name=goal_profile.timezone_name,
        limit=limit,
    )
    quality = InsightDataQuality(
        nutrition_data_state=classify_nutrition_state(
            unknown_fields=aggregate.unknown_fields,
            meal_count=meal_count,
            incomplete_meal_count=aggregate.incomplete_meal_count,
        ),
        incomplete_meal_count=aggregate.incomplete_meal_count,
        unknown_fields=tuple(sorted(aggregate.unknown_fields)),
        weak_estimate_count=weak_count,
        pending_inventory_link_count=pending_count,
        totals_are_known_minimums=aggregate.known_minimum,
    )
    return InsightSnapshot(
        period=InsightPeriod(
            kind=period,
            start_date=start_date,
            end_date=end_exclusive - timedelta(days=1),
            day_count=day_count,
        ),
        goals_confirmed=goal_profile.confirmed,
        goal_source=goal_profile.goal_source,
        confirmed_at=goal_profile.confirmed_at,
        metrics=metrics,
        data_quality=quality,
        expiring_inventory=expiring,
        priorities=_priorities(
            quality=quality,
            expiring_count=expiring.total_count,
            metrics=metrics,
            goals_confirmed=goal_profile.confirmed,
        ),
    )


def _period_dates(anchor: date, period: str) -> tuple[date, date]:
    if period == "daily":
        return anchor, anchor + timedelta(days=1)
    if period == "weekly":
        start = anchor - timedelta(days=anchor.weekday())
        return start, anchor + timedelta(days=1)
    if period == "monthly":
        start = anchor.replace(day=1)
        return start, anchor + timedelta(days=1)
    raise ValueError("period must be daily, weekly, or monthly")


def _metrics(
    aggregate: progress.NutritionAggregate,
    profile: GoalProfile,
    day_count: int,
) -> tuple[InsightMetric, ...]:
    specs = (
        (
            "calories",
            aggregate.calories,
            profile.goals.calories_kcal,
            "kcal",
            "maximum",
        ),
        (
            "protein",
            aggregate.protein,
            profile.goals.protein_g,
            "g",
            "minimum",
        ),
        ("fat", aggregate.fat, profile.goals.fat_g, "g", "maximum"),
        (
            "carbohydrate",
            aggregate.carbohydrate,
            profile.goals.carbohydrate_g,
            "g",
            "maximum",
        ),
        (
            "fiber",
            aggregate.fiber,
            profile.goals.fiber_g,
            "g",
            "minimum",
        ),
        (
            "sodium",
            aggregate.sodium,
            profile.goals.sodium_mg,
            "mg",
            "maximum",
        ),
        (
            "water",
            aggregate.water_total_ml,
            profile.goals.water_ml,
            "ml",
            "minimum",
        ),
    )
    return tuple(
        _metric(
            key=key,
            current=current,
            daily_target=Decimal(daily_target),
            day_count=day_count,
            unit=unit,
            goal_type=goal_type,
            confirmed=profile.confirmed,
            has_unknown=key in aggregate.unknown_fields,
        )
        for key, current, daily_target, unit, goal_type in specs
    )


def _metric(
    *,
    key: str,
    current: Decimal,
    daily_target: Decimal,
    day_count: int,
    unit: str,
    goal_type: str,
    confirmed: bool,
    has_unknown: bool,
) -> InsightMetric:
    if not confirmed:
        return InsightMetric(
            key=key,
            current=current,
            target=None,
            delta_to_target=None,
            unit=unit,
            goal_type=goal_type,
            status="unconfirmed",
            has_unknown=has_unknown,
        )
    target = daily_target * day_count
    if goal_type == "minimum":
        status = "met" if current >= target else "below"
    else:
        status = "over" if current > target else "within"
    return InsightMetric(
        key=key,
        current=current,
        target=target,
        delta_to_target=target - current,
        unit=unit,
        goal_type=goal_type,
        status=status,
        has_unknown=has_unknown,
    )


def _quality_counts(
    connection: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
) -> tuple[int, int, int]:
    pending_count = connection.execute(
        """
        SELECT count(*)
        FROM pending_inventory_links AS links
        JOIN meal_items AS items ON items.id = links.meal_item_id
        JOIN meals ON meals.id = items.meal_id
        WHERE links.status = 'pending'
          AND meals.deleted_at IS NULL
          AND meals.occurred_at >= ?
          AND meals.occurred_at < ?
        """,
        (start_utc, end_utc),
    ).fetchone()[0]
    weak_count = connection.execute(
        """
        SELECT count(*)
        FROM meal_items AS items
        JOIN meals ON meals.id = items.meal_id
        WHERE meals.deleted_at IS NULL
          AND meals.occurred_at >= ?
          AND meals.occurred_at < ?
          AND items.source_grade IN ('C', 'D', 'unknown')
        """,
        (start_utc, end_utc),
    ).fetchone()[0]
    meal_count = connection.execute(
        """
        SELECT count(*)
        FROM meals
        WHERE deleted_at IS NULL
          AND occurred_at >= ?
          AND occurred_at < ?
          AND (
              transaction_id IS NULL
              OR EXISTS (
                  SELECT 1
                  FROM transactions
                  WHERE id = meals.transaction_id
                    AND status = 'committed'
              )
          )
        """,
        (start_utc, end_utc),
    ).fetchone()[0]
    return int(pending_count), int(weak_count), int(meal_count)


def classify_nutrition_state(
    *,
    unknown_fields: frozenset[str],
    meal_count: int,
    incomplete_meal_count: int,
) -> str:
    if meal_count == 0:
        return "no_records"
    relevant = unknown_fields & _NUTRITION_KEYS
    if relevant == _NUTRITION_KEYS:
        return "fully_unknown"
    if relevant:
        return "partially_known"
    if incomplete_meal_count:
        return "known_minimum"
    return "known"


def _expiring_inventory(
    connection: sqlite3.Connection,
    *,
    anchor: date,
    cutoff: date,
    timezone_name: str,
    limit: int,
) -> ExpiringInventory:
    candidates = []
    for batch in pantry.query_batches(
        connection,
        statuses=_ACTIVE_BATCH_STATUSES,
    ):
        if batch.expires_at is None:
            continue
        expires_on = local_date(batch.expires_at, timezone_name)
        if anchor <= expires_on <= cutoff:
            candidates.append((expires_on, batch.normalized_name, batch))
    candidates.sort(key=lambda item: (item[0], item[1]))
    items = tuple(
        ExpiringItem(
            food_name=batch.food_name,
            normalized_name=batch.normalized_name,
            remaining_quantity=batch.remaining_quantity,
            unit=batch.unit,
            expires_on=expires_on,
            days_remaining=(expires_on - anchor).days,
            storage_location=batch.storage_location,
        )
        for expires_on, _, batch in candidates[:limit]
    )
    return ExpiringInventory(
        total_count=len(candidates),
        items=items,
        truncated=len(candidates) > len(items),
    )


def _priorities(
    *,
    quality: InsightDataQuality,
    expiring_count: int,
    metrics: tuple[InsightMetric, ...],
    goals_confirmed: bool,
) -> tuple[Mapping[str, object], ...]:
    priorities: list[Mapping[str, object]] = []
    if quality.incomplete_meal_count:
        priorities.append(
            {
                "code": "complete_nutrition",
                "count": quality.incomplete_meal_count,
                "unknown_fields": quality.unknown_fields,
            }
        )
    if expiring_count:
        priorities.append(
            {
                "code": "expiring_inventory",
                "count": expiring_count,
            }
        )
    if quality.pending_inventory_link_count:
        priorities.append(
            {
                "code": "resolve_inventory_links",
                "count": quality.pending_inventory_link_count,
            }
        )
    goal_gap = _largest_goal_gap(metrics) if goals_confirmed else None
    if goal_gap is not None:
        priorities.append(
            {
                "code": "goal_gap",
                "metric_key": goal_gap.key,
                "deviation_percent": _deviation_percent(goal_gap),
                "status": goal_gap.status,
            }
        )
    return tuple(priorities[:3])


def _largest_goal_gap(
    metrics: tuple[InsightMetric, ...],
) -> InsightMetric | None:
    gaps = tuple(
        metric
        for metric in metrics
        if metric.target is not None
        and (
            (metric.goal_type == "minimum" and metric.current < metric.target)
            or (metric.goal_type == "maximum" and metric.current > metric.target)
        )
    )
    if not gaps:
        return None
    return max(gaps, key=_deviation_percent)


def _deviation_percent(metric: InsightMetric) -> int:
    if metric.target is None or metric.target <= 0:
        return 0
    difference = (
        metric.target - metric.current
        if metric.goal_type == "minimum"
        else metric.current - metric.target
    )
    return max(
        0,
        int(
            (difference * 100 / metric.target).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        ),
    )
