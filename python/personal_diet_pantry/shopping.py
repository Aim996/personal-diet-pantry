"""Preview-bound shopping lists that remain separate from pantry inventory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import sqlite3
from typing import Any, Mapping, Sequence

from .transactions import TransactionManager


class ShoppingValidationError(ValueError):
    """Raised for malformed or stale shopping-list requests."""


@dataclass(frozen=True)
class ShoppingItemDraft:
    food_name: str
    normalized_name: str
    quantity: Decimal
    unit: str
    reason: str | None


@dataclass(frozen=True)
class ShoppingListDraft:
    title: str
    source_text: str
    items: tuple[ShoppingItemDraft, ...]


@dataclass(frozen=True)
class ShoppingListItem:
    food_name: str
    normalized_name: str
    quantity: Decimal
    unit: str
    reason: str | None
    status: str


@dataclass(frozen=True)
class ShoppingList:
    title: str
    status: str
    items: tuple[ShoppingListItem, ...]
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None
    version: int


def normalize_draft(value: Mapping[str, Any]) -> ShoppingListDraft:
    """Validate and normalize the exact shopping-list preview payload."""

    if not isinstance(value, Mapping):
        raise ShoppingValidationError("shopping list must be an object")
    title = _text(value.get("title"), "title", maximum=120)
    source_text = _text(value.get("source_text"), "source_text", maximum=1000)
    raw_items = value.get("items")
    if (
        isinstance(raw_items, (str, bytes))
        or not isinstance(raw_items, Sequence)
        or not 1 <= len(raw_items) <= 50
    ):
        raise ShoppingValidationError("items must contain 1 to 50 entries")
    return ShoppingListDraft(
        title=title,
        source_text=source_text,
        items=tuple(_draft_item(item) for item in raw_items),
    )


def draft_mapping(value: ShoppingListDraft) -> dict[str, Any]:
    return {
        "title": value.title,
        "source_text": value.source_text,
        "items": [
            {
                "food_name": item.food_name,
                "normalized_name": item.normalized_name,
                "quantity": format(item.quantity, "f"),
                "unit": item.unit,
                **({"reason": item.reason} if item.reason is not None else {}),
            }
            for item in value.items
        ],
    }


def commit_list(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    draft: ShoppingListDraft,
    now: datetime,
    internal_id: str | None = None,
    after_insert: Any | None = None,
) -> ShoppingList:
    """Commit an exact preview as a list without creating pantry batches."""

    timestamp = _timestamp(now)

    def mutate(context):
        list_row = context.insert(
            "shopping_lists",
            {
                "title": draft.title,
                "status": "active",
                "source_text": draft.source_text,
                "created_at": timestamp,
                "updated_at": timestamp,
                "cancelled_at": None,
                "version": 1,
            },
        )
        list_id = int(list_row["id"])
        for item in draft.items:
            context.insert(
                "shopping_list_items",
                {
                    "shopping_list_id": list_id,
                    "food_name": item.food_name,
                    "normalized_name": item.normalized_name,
                    "quantity": _sqlite_real(item.quantity, "quantity"),
                    "unit": item.unit,
                    "reason": item.reason,
                    "status": "pending",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "version": 1,
                },
            )
        result = _shopping_list(connection, list_id)
        if after_insert is not None:
            after_insert(context, result)
        return result

    return manager.execute(
        "reminder_manage",
        draft.source_text,
        mutate,
        internal_id=internal_id,
    ).value


def query_lists(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 10,
) -> tuple[tuple[int, ShoppingList], ...]:
    if status is not None and status not in {"active", "cancelled", "completed"}:
        raise ShoppingValidationError("status is invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ShoppingValidationError("limit must be an integer from 1 to 20")
    where = "WHERE status = ?" if status is not None else ""
    values: tuple[Any, ...] = (status, limit) if status is not None else (limit,)
    rows = connection.execute(
        f"""
        SELECT id
        FROM shopping_lists
        {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        values,
    )
    return tuple(
        (int(row["id"]), _shopping_list(connection, int(row["id"])))
        for row in rows
    )


def cancel_list(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    shopping_list_id: int,
    expected_version: int,
    source_text: str,
    now: datetime,
    internal_id: str | None = None,
    after_update: Any | None = None,
) -> ShoppingList:
    normalized_source = _text(source_text, "source_text", maximum=1000)
    timestamp = _timestamp(now)

    def mutate(context):
        row = connection.execute(
            "SELECT * FROM shopping_lists WHERE id = ?",
            (shopping_list_id,),
        ).fetchone()
        if row is None or int(row["version"]) != expected_version:
            raise ShoppingValidationError("shopping list reference is stale")
        if row["status"] == "cancelled":
            return _shopping_list(connection, shopping_list_id)
        if row["status"] != "active":
            raise ShoppingValidationError("only an active shopping list can be cancelled")
        context.update(
            "shopping_lists",
            shopping_list_id,
            {
                "status": "cancelled",
                "updated_at": timestamp,
                "cancelled_at": timestamp,
                "version": expected_version + 1,
            },
        )
        for item in connection.execute(
            """
            SELECT id, version
            FROM shopping_list_items
            WHERE shopping_list_id = ? AND status = 'pending'
            ORDER BY id
            """,
            (shopping_list_id,),
        ):
            context.update(
                "shopping_list_items",
                int(item["id"]),
                {
                    "status": "cancelled",
                    "updated_at": timestamp,
                    "version": int(item["version"]) + 1,
                },
            )
        result = _shopping_list(connection, shopping_list_id)
        if after_update is not None:
            after_update(context, result)
        return result

    return manager.execute(
        "reminder_manage",
        normalized_source,
        mutate,
        internal_id=internal_id,
    ).value


def _shopping_list(connection: sqlite3.Connection, list_id: int) -> ShoppingList:
    row = connection.execute(
        "SELECT * FROM shopping_lists WHERE id = ?",
        (list_id,),
    ).fetchone()
    if row is None:
        raise ShoppingValidationError("shopping list does not exist")
    items = tuple(
        ShoppingListItem(
            food_name=str(item["food_name"]),
            normalized_name=str(item["normalized_name"]),
            quantity=Decimal(str(item["quantity"])),
            unit=str(item["unit"]),
            reason=str(item["reason"]) if item["reason"] is not None else None,
            status=str(item["status"]),
        )
        for item in connection.execute(
            """
            SELECT *
            FROM shopping_list_items
            WHERE shopping_list_id = ?
            ORDER BY id
            """,
            (list_id,),
        )
    )
    return ShoppingList(
        title=str(row["title"]),
        status=str(row["status"]),
        items=items,
        created_at=_datetime(str(row["created_at"])),
        updated_at=_datetime(str(row["updated_at"])),
        cancelled_at=(
            _datetime(str(row["cancelled_at"]))
            if row["cancelled_at"] is not None
            else None
        ),
        version=int(row["version"]),
    )


def _draft_item(value: Any) -> ShoppingItemDraft:
    if not isinstance(value, Mapping):
        raise ShoppingValidationError("each item must be an object")
    food_name = _text(value.get("food_name"), "food_name", maximum=120)
    normalized = value.get("normalized_name")
    return ShoppingItemDraft(
        food_name=food_name,
        normalized_name=(
            _text(normalized, "normalized_name", maximum=120).casefold()
            if normalized is not None
            else food_name.casefold()
        ),
        quantity=_positive(value.get("quantity"), "quantity"),
        unit=_text(value.get("unit"), "unit", maximum=24),
        reason=(
            _text(value.get("reason"), "reason", maximum=240)
            if value.get("reason") is not None
            else None
        ),
    )


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShoppingValidationError(f"{field} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ShoppingValidationError(f"{field} is too long")
    return normalized


def _positive(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ShoppingValidationError(f"{field} must be positive")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ShoppingValidationError(f"{field} must be positive") from None
    if not number.is_finite() or number <= 0:
        raise ShoppingValidationError(f"{field} must be positive")
    _sqlite_real(number, field)
    return number


def _sqlite_real(value: Decimal, field: str) -> float:
    converted = float(value)
    if not Decimal(str(converted)).is_finite():
        raise ShoppingValidationError(f"{field} is not representable")
    return converted


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ShoppingValidationError("now must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
