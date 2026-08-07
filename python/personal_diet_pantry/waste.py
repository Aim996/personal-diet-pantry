"""Waste classification and currency-safe waste summaries."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import sqlite3
from typing import Any

from .costs import validate_currency


WASTE_CATEGORIES = frozenset(
    {
        "spoilage",
        "expired",
        "overprepared",
        "quality",
        "other",
        "unspecified",
    }
)


def normalize_category(value: Any) -> str:
    if value is None:
        return "unspecified"
    if not isinstance(value, str) or value not in WASTE_CATEGORIES:
        raise ValueError("waste_category is invalid")
    return value


def waste_summary(
    connection: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    currency: str | None = None,
) -> dict[str, Any]:
    """Summarize actual discard/expiry movements; corrections are excluded."""

    selected_currency = validate_currency(currency) if currency is not None else None
    movements = list(
        connection.execute(
            """
            SELECT
                pm.id,
                COALESCE(pm.waste_category, 'unspecified') AS category,
                pm.quantity,
                pm.unit
            FROM pantry_movements AS pm
            WHERE pm.movement_type IN ('discard', 'expire')
              AND pm.created_at >= ? AND pm.created_at < ?
            ORDER BY pm.created_at, pm.id
            """,
            (start_utc, end_utc),
        )
    )
    by_category: dict[str, dict[str, Any]] = {}
    for row in movements:
        category = normalize_category(row["category"])
        value = by_category.setdefault(
            category,
            {"event_count": 0, "quantities": defaultdict(Decimal)},
        )
        value["event_count"] += 1
        value["quantities"][str(row["unit"])] += Decimal(str(row["quantity"]))

    values: tuple[Any, ...] = (
        (start_utc, end_utc, selected_currency)
        if selected_currency is not None
        else (start_utc, end_utc)
    )
    currency_clause = "AND currency = ?" if selected_currency is not None else ""
    currency_rows = connection.execute(
        f"""
        SELECT currency, sum(cost_minor) AS waste_minor
        FROM pantry_cost_allocations
        WHERE allocation_kind = 'waste'
          AND allocated_at >= ? AND allocated_at < ?
          {currency_clause}
        GROUP BY currency
        ORDER BY currency
        """,
        values,
    )
    # Pricing coverage describes whether a waste event has any structured
    # allocation. A report currency filter must not turn a differently priced
    # event into an "unpriced" event.
    priced_movements = {
        int(row[0])
        for row in connection.execute(
            """
            SELECT pca.pantry_movement_id
            FROM pantry_cost_allocations AS pca
            WHERE pca.allocation_kind = 'waste'
              AND pca.allocated_at >= ? AND pca.allocated_at < ?
            """,
            (start_utc, end_utc),
        )
    }
    return {
        "event_count": len(movements),
        "unpriced_event_count": sum(
            1 for row in movements if int(row["id"]) not in priced_movements
        ),
        "categories": [
            {
                "category": category,
                "event_count": value["event_count"],
                "quantities": [
                    {
                        "unit": unit,
                        "quantity": format(quantity, "f"),
                    }
                    for unit, quantity in sorted(value["quantities"].items())
                ],
            }
            for category, value in sorted(by_category.items())
        ],
        "currencies": [
            {
                "currency": str(row["currency"]),
                "waste_minor": int(row["waste_minor"]),
            }
            for row in currency_rows
        ],
    }
