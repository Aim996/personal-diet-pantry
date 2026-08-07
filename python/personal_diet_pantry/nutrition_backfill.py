"""Journaled, nutrition-only repairs for historical meals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3

from . import meals
from .nutrition import NutritionFacts
from .transactions import MutationContext, TransactionManager


MEAL_ITEM_NUTRITION_COLUMNS = frozenset({"calories", "protein", "fat", "carbohydrate", "fiber", "sodium", "hydration_ml", "source_grade", "nutrition_source", "uncertainty"})
MEAL_TOTAL_COLUMNS = frozenset({"total_calories", "total_protein", "total_fat", "total_carbohydrate", "total_fiber", "total_sodium", "total_hydration_ml", "nutrition_status", "nutrition_missing_fields_json", "updated_at"})
_CORE = ("calories", "protein", "fat", "carbohydrate", "fiber", "sodium")


class BackfillStaleError(RuntimeError):
    """The historical meal changed after the read-only preview."""


@dataclass(frozen=True)
class BackfillItem:
    row_id: int = field(repr=False)
    parent_row_id: int | None = field(repr=False)
    item_role: str
    display_order: int
    raw_name: str
    amount: str | None
    unit: str | None


@dataclass(frozen=True)
class BackfillCandidate:
    meal_id: int = field(repr=False)
    expected_item_signature: str = field(repr=False)
    occurred_at: str
    source_text: str
    items: tuple[BackfillItem, ...]


def list_incomplete_meals(connection: sqlite3.Connection, *, limit: int) -> Sequence[BackfillCandidate]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    rows = connection.execute("SELECT id, occurred_at, source_text FROM meals WHERE deleted_at IS NULL AND nutrition_status IN ('partial', 'incomplete') ORDER BY occurred_at, id LIMIT ?", (limit,)).fetchall()
    return tuple(_candidate(connection, row) for row in rows)


def item_signature(connection: sqlite3.Connection, meal_id: int) -> str:
    rows = _item_rows(connection, meal_id)
    payload = [
        meal_id,
        *[
            (
                row["id"],
                row["parent_item_id"],
                row["item_role"],
                row["display_order"],
                row["raw_name"],
                row["amount"],
                row["unit"],
            )
            for row in rows
        ],
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def commit_backfill(connection: sqlite3.Connection, manager: TransactionManager, *, meal_id: int, expected_item_signature: str, estimates: Mapping[int, NutritionFacts], source_text: str, now: datetime | None = None) -> meals.MealRecord:
    """Apply complete estimates atomically without touching meal history or inventory."""
    def mutate(context):
        return apply_backfill_in_context(
            connection,
            context,
            meal_id=meal_id,
            expected_item_signature=expected_item_signature,
            estimates=estimates,
            now=now,
        )

    return manager.execute("record_correction", source_text, mutate).value


def apply_backfill_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    meal_id: int,
    expected_item_signature: str,
    estimates: Mapping[int, NutritionFacts],
    now: datetime | None = None,
) -> meals.MealRecord:
    """Apply one complete backfill inside the caller's journal transaction."""
    current = connection.execute(
        "SELECT id, deleted_at FROM meals WHERE id = ?", (meal_id,)
    ).fetchone()
    if (
        current is None
        or current["deleted_at"] is not None
        or item_signature(connection, meal_id) != expected_item_signature
    ):
        raise BackfillStaleError("meal changed after preview")
    rows = _item_rows(connection, meal_id)
    row_ids = {row["id"] for row in rows}
    if set(estimates) != row_ids:
        raise ValueError("every queried meal item needs exactly one complete nutrition estimate")
    parent_ids = {
        row["parent_item_id"]
        for row in rows
        if row["parent_item_id"] is not None
    }
    nutritional_leaf_ids = row_ids - parent_ids
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    totals = {key: Decimal("0") for key in _CORE}
    hydration = Decimal("0")
    has_hydration = False
    for row in rows:
        facts = estimates[row["id"]]
        values = {key: _decimal(getattr(facts, key)) for key in _CORE}
        if row["id"] in nutritional_leaf_ids:
            totals = {
                key: totals[key] + Decimal(value)
                for key, value in values.items()
            }
            if facts.hydration_ml is not None:
                hydration += facts.hydration_ml
                has_hydration = True
        context.update("meal_items", row["id"], values | {
            "hydration_ml": _decimal(facts.hydration_ml) if facts.hydration_ml is not None else None,
            "source_grade": facts.source_grade,
            "nutrition_source": facts.source,
            "uncertainty": facts.uncertainty,
        })
    context.update("meals", meal_id, {
        "total_calories": _decimal(totals["calories"]), "total_protein": _decimal(totals["protein"]),
        "total_fat": _decimal(totals["fat"]), "total_carbohydrate": _decimal(totals["carbohydrate"]),
        "total_fiber": _decimal(totals["fiber"]), "total_sodium": _decimal(totals["sodium"]),
        "total_hydration_ml": _decimal(hydration) if has_hydration else None,
        "nutrition_status": "complete", "nutrition_missing_fields_json": "[]", "updated_at": timestamp,
    })
    return meals._read_meal(connection, meal_id)


def _candidate(connection: sqlite3.Connection, row: sqlite3.Row) -> BackfillCandidate:
    items = _item_rows(connection, row["id"])
    return BackfillCandidate(
        row["id"],
        item_signature(connection, row["id"]),
        row["occurred_at"],
        row["source_text"],
        tuple(
            BackfillItem(
                item["id"],
                item["parent_item_id"],
                item["item_role"],
                item["display_order"],
                item["raw_name"],
                item["amount"],
                item["unit"],
            )
            for item in items
        ),
    )


def _item_rows(
    connection: sqlite3.Connection, meal_id: int
) -> Sequence[sqlite3.Row]:
    return connection.execute(
        """
        SELECT id, parent_item_id, item_role, display_order, raw_name, amount, unit
        FROM meal_items
        WHERE meal_id = ?
        ORDER BY id
        """,
        (meal_id,),
    ).fetchall()


def _decimal(value: Decimal | None) -> str:
    if value is None:
        raise ValueError("nutrition estimate must be complete")
    return format(value, "f")
