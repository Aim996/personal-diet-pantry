"""Integer-minor-unit pantry cost allocation and currency-safe summaries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import sqlite3
from typing import Any, Mapping

from .transactions import MutationContext


class CostValidationError(ValueError):
    """Raised when structured cost data would be ambiguous or unsafe."""


@dataclass(frozen=True)
class CostAllocation:
    cost_minor: int
    remaining_cost_minor: int
    currency: str


def structured_price(
    price_minor: Any,
    currency: Any,
) -> tuple[int | None, str | None, int | None]:
    """Validate the all-or-none structured batch price fields."""

    if price_minor is None and currency is None:
        return None, None, None
    if price_minor is None or currency is None:
        raise CostValidationError(
            "price_minor and currency must be provided together"
        )
    if (
        isinstance(price_minor, bool)
        or not isinstance(price_minor, int)
        or price_minor < 0
        or price_minor > 9_000_000_000_000_000
    ):
        raise CostValidationError(
            "price_minor must be a non-negative safe integer"
        )
    normalized_currency = validate_currency(currency)
    return price_minor, normalized_currency, price_minor


def validate_currency(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
        or value != value.upper()
    ):
        raise CostValidationError(
            "currency must be a three-letter uppercase ISO code"
        )
    return value


def allocation_for_reduction(
    batch: Mapping[str, Any],
    quantity: Decimal,
) -> CostAllocation | None:
    """Allocate current batch cost proportionally, leaving remainder conserved."""

    price_minor = batch["price_minor"]
    remaining_minor = batch["remaining_cost_minor"]
    currency = batch["currency"]
    if price_minor is None and remaining_minor is None and currency is None:
        return None
    if price_minor is None or remaining_minor is None or currency is None:
        raise CostValidationError("stored structured batch price is incomplete")
    normalized_currency = validate_currency(currency)
    current_quantity = Decimal(str(batch["remaining_quantity"]))
    if quantity <= 0 or quantity > current_quantity:
        raise CostValidationError("cost allocation quantity is invalid")
    current_cost = int(remaining_minor)
    allocated = (
        current_cost
        if quantity == current_quantity
        else int(
            (
                Decimal(current_cost) * quantity / current_quantity
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
    )
    return CostAllocation(
        cost_minor=allocated,
        remaining_cost_minor=current_cost - allocated,
        currency=normalized_currency,
    )


def record_allocation(
    context: MutationContext,
    *,
    batch_id: int,
    movement_id: int,
    allocation_kind: str,
    quantity: Decimal,
    unit: str,
    allocation: CostAllocation | None,
    allocated_at: str,
) -> None:
    if allocation is None:
        return
    if allocation_kind not in {"consume", "waste", "adjustment"}:
        raise CostValidationError("allocation_kind is invalid")
    context.insert(
        "pantry_cost_allocations",
        {
            "pantry_batch_id": batch_id,
            "pantry_movement_id": movement_id,
            "allocation_kind": allocation_kind,
            "quantity": float(quantity),
            "unit": unit,
            "cost_minor": allocation.cost_minor,
            "currency": allocation.currency,
            "allocated_at": allocated_at,
        },
    )


def cost_summary(
    connection: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    currency: str | None = None,
) -> dict[str, Any]:
    """Return per-currency costs; never return a cross-currency total."""

    selected_currency = validate_currency(currency) if currency is not None else None
    currency_clause = "AND currency = ?" if selected_currency is not None else ""
    purchase_values: tuple[Any, ...] = (
        (start_utc, end_utc, selected_currency)
        if selected_currency is not None
        else (start_utc, end_utc)
    )
    allocation_values = purchase_values
    purchased = {
        str(row["currency"]): int(row["amount"])
        for row in connection.execute(
            f"""
            SELECT currency, sum(price_minor) AS amount
            FROM pantry_batches
            WHERE added_at >= ? AND added_at < ?
              AND price_minor IS NOT NULL
              {currency_clause}
            GROUP BY currency
            """,
            purchase_values,
        )
    }
    allocated: dict[str, dict[str, int]] = {}
    for row in connection.execute(
        f"""
        SELECT currency, allocation_kind, sum(cost_minor) AS amount
        FROM pantry_cost_allocations
        WHERE allocated_at >= ? AND allocated_at < ?
          {currency_clause}
        GROUP BY currency, allocation_kind
        """,
        allocation_values,
    ):
        allocated.setdefault(
            str(row["currency"]),
            {"consume": 0, "waste": 0, "adjustment": 0},
        )[str(row["allocation_kind"])] = int(row["amount"])
    currencies = sorted(set(purchased) | set(allocated))
    batch_counts = connection.execute(
        f"""
        SELECT
            count(*) AS total_batches,
            count(price_minor) AS priced_batches
        FROM pantry_batches
        WHERE added_at >= ? AND added_at < ?
          {"AND currency = ?" if selected_currency is not None else ""}
        """,
        purchase_values,
    ).fetchone()
    total_batches = int(batch_counts["total_batches"])
    priced_batches = int(batch_counts["priced_batches"])
    return {
        "currencies": [
            {
                "currency": code,
                "purchased_minor": purchased.get(code, 0),
                "consumed_minor": allocated.get(code, {}).get("consume", 0),
                "waste_minor": allocated.get(code, {}).get("waste", 0),
                "adjustment_minor": allocated.get(code, {}).get(
                    "adjustment", 0
                ),
            }
            for code in currencies
        ],
        "coverage": {
            "total_batches": total_batches,
            "priced_batches": priced_batches,
            "unpriced_batches": total_batches - priced_batches,
            "ratio": (
                format(
                    Decimal(priced_batches) / Decimal(total_batches),
                    "f",
                )
                if total_batches
                else None
            ),
        },
    }


def assert_cost_conservation(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return public-safe names of batches whose structured cost does not conserve."""

    rows = connection.execute(
        """
        SELECT
            pb.food_name,
            pb.price_minor,
            pb.remaining_cost_minor,
            COALESCE(sum(pca.cost_minor), 0) AS allocated_minor
        FROM pantry_batches AS pb
        LEFT JOIN pantry_cost_allocations AS pca
          ON pca.pantry_batch_id = pb.id
        WHERE pb.price_minor IS NOT NULL
        GROUP BY pb.id
        HAVING pb.price_minor <> pb.remaining_cost_minor + allocated_minor
        ORDER BY pb.id
        """
    )
    return tuple(str(row["food_name"]) for row in rows)
