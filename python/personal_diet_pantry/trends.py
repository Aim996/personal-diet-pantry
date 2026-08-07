"""Bounded cost/waste trend buckets for personal-scale history."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import sqlite3
from typing import Any

from .costs import validate_currency


def trend_summary(
    connection: sqlite3.Connection,
    *,
    start_date: date,
    end_date: date,
    start_utc: str,
    end_utc: str,
    currency: str | None = None,
) -> dict[str, Any]:
    """Return daily buckets up to 90 days, otherwise calendar-month buckets."""

    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    days = (end_date - start_date).days + 1
    if not 1 <= days <= 730:
        raise ValueError("trend period must contain 1 to 730 days")
    selected_currency = validate_currency(currency) if currency is not None else None
    granularity = "day" if days <= 90 else "month"
    labels = _labels(start_date, end_date, granularity)
    costs: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(
            lambda: {"consumed_minor": 0, "waste_minor": 0}
        )
    )
    values: tuple[Any, ...] = (
        (start_utc, end_utc, selected_currency)
        if selected_currency is not None
        else (start_utc, end_utc)
    )
    currency_clause = "AND currency = ?" if selected_currency is not None else ""
    for row in connection.execute(
        f"""
        SELECT allocated_at, currency, allocation_kind, sum(cost_minor) AS amount
        FROM pantry_cost_allocations
        WHERE allocated_at >= ? AND allocated_at < ?
          AND allocation_kind IN ('consume', 'waste')
          {currency_clause}
        GROUP BY allocated_at, currency, allocation_kind
        ORDER BY allocated_at, currency
        """,
        values,
    ):
        label = (
            str(row["allocated_at"])[:10]
            if granularity == "day"
            else str(row["allocated_at"])[:7]
        )
        field = (
            "consumed_minor"
            if row["allocation_kind"] == "consume"
            else "waste_minor"
        )
        costs[label][str(row["currency"])][field] += int(row["amount"])

    waste_events: dict[str, int] = defaultdict(int)
    for row in connection.execute(
        """
        SELECT created_at, count(*) AS event_count
        FROM pantry_movements
        WHERE movement_type IN ('discard', 'expire')
          AND created_at >= ? AND created_at < ?
        GROUP BY created_at
        ORDER BY created_at
        """,
        (start_utc, end_utc),
    ):
        label = (
            str(row["created_at"])[:10]
            if granularity == "day"
            else str(row["created_at"])[:7]
        )
        waste_events[label] += int(row["event_count"])
    return {
        "granularity": granularity,
        "buckets": [
            {
                "period": label,
                "waste_event_count": waste_events.get(label, 0),
                "currencies": [
                    {"currency": code} | amounts
                    for code, amounts in sorted(costs.get(label, {}).items())
                ],
            }
            for label in labels
        ],
    }


def _labels(start: date, end: date, granularity: str) -> list[str]:
    if granularity == "day":
        return [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        ]
    labels: list[str] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        labels.append(cursor.strftime("%Y-%m"))
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return labels
