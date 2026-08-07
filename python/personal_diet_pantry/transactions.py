"""Atomic, auditable mutations for formal diet-pantry state."""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import secrets
import sqlite3
from typing import Any, Callable, Generic, Mapping, TypeVar

from .clock import utc_text
from .timezones import local_date, local_datetime


T = TypeVar("T")


_MUTABLE_TABLES = frozenset(
    {
        "meals",
        "meal_items",
        "meal_item_nutrition_evidence",
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
    }
)

# Values assigned to pre-004 rows by migration 004. Replay accepts these exact
# values as the unchanged state of columns absent from an older snapshot.
_APPROVED_SCHEMA_BASELINES: dict[tuple[str, str], Any] = {
    ("meal_items", "parent_item_id"): None,
    ("meal_items", "item_role"): "food",
    ("meal_items", "display_order"): 0,
    ("meal_items", "nutrition_source"): None,
    ("meal_items", "uncertainty"): None,
    ("meal_items", "consumed_volume_ml"): None,
    ("meal_items", "consumed_servings"): None,
    ("pantry_movements", "linked_meal_item_id"): None,
    ("pantry_movements", "prior_status"): None,
    ("pantry_batches", "total_weight_g"): None,
    ("pantry_batches", "average_unit_weight_g"): None,
    ("pantry_batches", "weight_basis"): None,
    ("pantry_batches", "weight_source"): None,
    ("pantry_batches", "weight_confidence"): None,
    ("pantry_batches", "price_minor"): None,
    ("pantry_batches", "currency"): None,
    ("pantry_batches", "remaining_cost_minor"): None,
    ("pantry_movements", "waste_category"): None,
    ("pantry_batches", "source_meal_id"): None,
    ("meals", "total_hydration_ml"): None,
    ("meal_items", "hydration_ml"): None,
    ("meals", "source_session_key"): None,
    ("meals", "event_timezone"): None,
    ("meals", "local_date"): None,
    ("meals", "intake_fingerprint"): None,
    ("meals", "source_session_hash"): None,
    ("meals", "nutrition_calculation_status"): "unverified",
    ("meals", "nutrition_provenance_status"): "untraceable",
    ("meals", "source_model"): None,
    ("meals", "test_run_id"): None,
    ("water_logs", "source_session_key"): None,
    ("water_logs", "source_model"): None,
    ("water_logs", "test_run_id"): None,
}

_MISSING_SCHEMA_BASELINE = object()

_CANONICAL_DECIMAL_COLUMNS = frozenset(
    {
        ("meals", "confidence"),
        ("meal_items", "confidence"),
        ("pending_inventory_links", "confidence"),
    }
)


class TransactionStateError(ValueError):
    """Raised when an undo or redo is not valid for a transaction's state."""


class TransactionNotUndoable(TransactionStateError):
    """Raised when a journal has no safe, applicable snapshot effect."""


class TransactionTargetStaleError(RuntimeError):
    """Raised when a workflow target changes before the formal transaction."""


class UndoConflictError(RuntimeError):
    """Raised when rows no longer have the state required for a safe undo."""


class RedoConflictError(RuntimeError):
    """Raised when a row no longer has the state required for a safe redo."""


class OperationAlreadyCommitted(RuntimeError):
    """Signal that this exact internal operation was previously committed."""


class OperationFingerprintConflict(ValueError):
    """Reject reuse of an internal operation identifier for another request."""


@dataclass(frozen=True)
class OperationContext:
    """Private bridge operation identity bound only while dispatching a write."""

    operation_id: str
    request_fingerprint: str
    semantic_fingerprint: str | None = None
    source_session_key: str | None = None
    source_model: str | None = None
    test_run_id: str | None = None


_CURRENT_OPERATION: ContextVar[OperationContext | None] = ContextVar(
    "personal_diet_pantry_operation",
    default=None,
)


@contextmanager
def operation_context(context: OperationContext | None):
    """Bind private operation state to every journal manager in one dispatch."""

    token: Token[OperationContext | None] = _CURRENT_OPERATION.set(context)
    try:
        yield
    finally:
        _CURRENT_OPERATION.reset(token)


@dataclass(frozen=True)
class MutationResult(Generic[T]):
    """A committed journal identifier and the value produced by its mutation."""

    transaction_id: str
    value: T


@dataclass(frozen=True)
class _RowChange:
    table: str
    row_id: int | str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True)
class UndoFilters:
    """Natural-language constraints resolved against committed journal entries.

    ``transaction_id`` values are intentionally not accepted as a public filter:
    callers identify an action by its user-facing context and receive an internal
    reference only after a candidate has been selected.
    """

    connection: sqlite3.Connection
    session_started_at: str | datetime | None = None
    operation_type: str | None = None
    date_start: str | date | datetime | None = None
    date_end: str | date | datetime | None = None
    meal_type: str | None = None
    normalized_food_name: str | None = None
    action: str = "undo"
    timezone_name: str = "UTC"
    now: str | datetime | None = None


@dataclass(frozen=True)
class UndoCandidate:
    """A safe display label paired with the private journal target reference."""

    transaction_id: str = field(repr=False)
    summary: str

    def __str__(self) -> str:
        return self.summary


_MEAL_TYPE_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "加餐",
    "other": "其他餐",
}


def find_undo_candidates(filters: UndoFilters) -> list[UndoCandidate]:
    """Return matching journal entries in deterministic, non-mutating order.

    The public ``summary`` deliberately contains no database or journal IDs;
    ``transaction_id`` is retained solely so a caller can pass a confirmed
    selection to :meth:`TransactionManager.undo` or ``redo``.
    """

    if not isinstance(filters, UndoFilters):
        raise TypeError("filters must be an UndoFilters instance")
    action = _undo_action(filters.action)
    reference_now = _optional_timestamp(filters.now, "now") or datetime.now(
        timezone.utc
    )
    session_start = _optional_timestamp(filters.session_started_at, "session_started_at")
    date_start = _optional_date(
        filters.date_start, "date_start", filters.timezone_name
    )
    date_end = _optional_date(
        filters.date_end, "date_end", filters.timezone_name
    )
    if date_start is not None and date_end is not None and date_start > date_end:
        raise ValueError("date_start must not be after date_end")
    operation_type = _optional_normalized_text(filters.operation_type, "operation_type")
    meal_type = _optional_normalized_text(filters.meal_type, "meal_type")
    food_name = _optional_normalized_text(filters.normalized_food_name, "normalized_food_name")
    status = "committed" if action == "undo" else "reverted"
    rows = filters.connection.execute(
        """
        SELECT
            id,
            rowid AS journal_sequence,
            transaction_type,
            committed_at,
            before_snapshot,
            after_snapshot
        FROM transactions
        WHERE status = ?
          AND undo_policy = 'snapshot'
          AND effect_count > 0
          AND transaction_type NOT IN ('transaction_undo', 'transaction_redo')
        """,
        (status,),
    ).fetchall()
    matches: list[tuple[tuple[int, int, int, int, int], UndoCandidate]] = []
    for row in rows:
        transaction_type = row["transaction_type"]
        if operation_type is not None and transaction_type != operation_type:
            continue
        committed_at = _parse_timestamp(row["committed_at"])
        if session_start is not None and committed_at < session_start:
            continue
        snapshot_rows = _candidate_snapshot_rows(row["before_snapshot"], row["after_snapshot"])
        if meal_type is not None and not _candidate_has_value(snapshot_rows, "meals", "meal_type", meal_type):
            continue
        if food_name is not None and not _candidate_has_food(snapshot_rows, food_name):
            continue
        event_dates = _candidate_dates_for_filters(
            snapshot_rows,
            row["committed_at"],
            meal_type=meal_type,
            food_name=food_name,
            timezone_name=filters.timezone_name,
        )
        if not any(
            (date_start is None or value >= date_start)
            and (date_end is None or value <= date_end)
            for value in event_dates
        ):
            continue
        exact_context = int(
            (meal_type is not None or food_name is not None or date_start is not None or date_end is not None)
        )
        rank = (
            int(operation_type is not None),
            exact_context,
            int(session_start is not None),
            int(committed_at.timestamp()),
            int(row["journal_sequence"]),
        )
        matches.append(
            (
                rank,
                UndoCandidate(
                    row["id"],
                    _candidate_summary(
                        snapshot_rows,
                        committed_at,
                        timezone_name=filters.timezone_name,
                        now=reference_now,
                    ),
                ),
            )
        )
    matches.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in matches]


def _undo_action(value: str) -> str:
    action = _optional_normalized_text(value, "action")
    if action not in {"undo", "redo"}:
        raise ValueError("action must be 'undo' or 'redo'")
    return action


def _optional_normalized_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string when supplied")
    return value.strip().lower()


def _optional_timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        timestamp = _parse_timestamp(value)
    else:
        raise ValueError(f"{field} must be an ISO timestamp when supplied")
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _optional_date(
    value: object, field: str, timezone_name: str
) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return local_date(_optional_timestamp(value, field), timezone_name)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return local_date(
                _optional_timestamp(value, field), timezone_name
            )
    raise ValueError(f"{field} must be an ISO date or timestamp when supplied")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from error
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _candidate_snapshot_rows(before_json: str, after_json: str) -> list[tuple[str, dict[str, Any]]]:
    before = json.loads(before_json)
    after = json.loads(after_json)
    rows: list[tuple[str, dict[str, Any]]] = []
    for before_entry, after_entry in zip(before, after, strict=True):
        table = after_entry.get("table")
        if not isinstance(table, str):
            continue
        row = after_entry.get("row") or before_entry.get("row")
        if isinstance(row, dict):
            rows.append((table, row))
    return rows


def _candidate_has_value(
    rows: list[tuple[str, dict[str, Any]]], table: str, column: str, expected: str
) -> bool:
    return any(
        row_table == table and str(row.get(column, "")).strip().lower() == expected
        for row_table, row in rows
    )


def _candidate_has_food(rows: list[tuple[str, dict[str, Any]]], expected: str) -> bool:
    return any(
        row_table in {"meal_items", "pantry_batches", "nutrition_cache"}
        and str(row.get("normalized_name", "")).strip().lower() == expected
        for row_table, row in rows
    )


def _candidate_dates_for_filters(
    rows: list[tuple[str, dict[str, Any]]],
    committed_at: str,
    *,
    meal_type: str | None,
    food_name: str | None,
    timezone_name: str,
) -> list[date]:
    meals = [row for table, row in rows if table == "meals"]
    water = [row for table, row in rows if table == "water_logs"]
    weights = [row for table, row in rows if table == "body_weight_logs"]
    batches = [row for table, row in rows if table == "pantry_batches"]
    movements = [row for table, row in rows if table == "pantry_movements"]
    meal_items = [row for table, row in rows if table == "meal_items"]

    if meal_type is not None:
        return _dates_from_rows(
            (row for row in meals if str(row.get("meal_type", "")).lower() == meal_type),
            "occurred_at",
            timezone_name,
        )
    if food_name is not None:
        matching_meal_ids = {
            row.get("meal_id")
            for row in meal_items
            if str(row.get("normalized_name", "")).strip().lower() == food_name
        }
        if matching_meal_ids:
            return _dates_from_rows(
                (row for row in meals if row.get("id") in matching_meal_ids),
                "occurred_at",
                timezone_name,
            )
        matching_batch_ids = {
            row.get("id")
            for row in batches
            if str(row.get("normalized_name", "")).strip().lower() == food_name
        }
        if matching_batch_ids:
            matching_movements = [
                row for row in movements if row.get("pantry_batch_id") in matching_batch_ids
            ]
            return _dates_from_rows(
                matching_movements, "created_at", timezone_name
            ) or _dates_from_rows(
                (row for row in batches if row.get("id") in matching_batch_ids),
                "added_at",
                timezone_name,
            )
    if meals:
        return _dates_from_rows(meals, "occurred_at", timezone_name)
    if water:
        return _dates_from_rows(water, "occurred_at", timezone_name)
    if weights:
        return _dates_from_rows(weights, "measured_at", timezone_name)
    if movements:
        return _dates_from_rows(movements, "created_at", timezone_name)
    if batches:
        return _dates_from_rows(batches, "added_at", timezone_name)
    return [local_date(committed_at, timezone_name)]


def _dates_from_rows(
    rows: Any, column: str, timezone_name: str
) -> list[date]:
    return [
        local_date(value, timezone_name)
        for row in rows
        if isinstance(value := row.get(column), str)
    ]


def _candidate_summary(
    rows: list[tuple[str, dict[str, Any]]],
    committed_at: datetime,
    *,
    timezone_name: str,
    now: datetime,
) -> str:
    meal = next((row for table, row in rows if table == "meals"), None)
    if meal is not None:
        occurred_at = local_datetime(str(meal["occurred_at"]), timezone_name)
        meal_id = meal.get("id")
        names = [
            str(row["raw_name"])
            for table, row in rows
            if table == "meal_items" and row.get("meal_id") == meal_id and row.get("raw_name")
        ]
        description = "、".join(dict.fromkeys(names)) or "已记录餐食"
        label = _MEAL_TYPE_LABELS.get(str(meal.get("meal_type", "")).lower(), "用餐")
        return f"{_display_time(occurred_at, now, timezone_name)} {label}：{description}"
    water = next((row for table, row in rows if table == "water_logs"), None)
    if water is not None:
        return (
            f"{_display_time(water.get('occurred_at', committed_at), now, timezone_name)} "
            f"饮水：{water['amount_ml']} 毫升"
        )
    weight = next(
        (row for table, row in rows if table == "body_weight_logs"),
        None,
    )
    if weight is not None:
        kilograms = Decimal(int(weight["weight_g"])) / Decimal(1000)
        return (
            f"{_display_time(weight.get('measured_at', committed_at), now, timezone_name)} "
            f"体重：{format(kilograms, 'f')} kg"
        )
    recipe = next(
        (row for table, row in rows if table == "recipe_profiles"),
        None,
    )
    if recipe is not None:
        return (
            f"{_display_time(committed_at, now, timezone_name)} "
            f"菜谱：{recipe.get('name') or '已保存菜谱'}"
        )
    shopping_list = next(
        (row for table, row in rows if table == "shopping_lists"),
        None,
    )
    if shopping_list is not None:
        return (
            f"{_display_time(committed_at, now, timezone_name)} "
            f"购物清单：{shopping_list.get('title') or '已更新清单'}"
        )
    movement = next((row for table, row in rows if table == "pantry_movements"), None)
    batch_rows = [row for table, row in rows if table == "pantry_batches"]
    if movement is not None or batch_rows:
        batch_id = movement.get("pantry_batch_id") if movement is not None else None
        batch = next((row for row in batch_rows if row.get("id") == batch_id), batch_rows[0] if batch_rows else None)
        name = str((batch or {}).get("food_name") or (batch or {}).get("normalized_name") or "食材")
        if movement is None:
            return f"{_display_time(committed_at, now, timezone_name)} 库存：{name}已更新"
        verb = {
            "add": "增加",
            "consume": "减少",
            "discard": "减少",
            "expire": "减少",
            "restore": "增加",
        }.get(str(movement.get("movement_type", "")), "调整")
        return f"{_display_time(committed_at, now, timezone_name)} 库存：{name}{verb} {movement['quantity']} {movement['unit']}"
    return f"{_display_time(committed_at, now, timezone_name)} 已记录操作"


def _display_time(
    timestamp: datetime | str,
    now: datetime,
    timezone_name: str,
) -> str:
    local_timestamp = local_datetime(timestamp, timezone_name)
    day = (
        "今天"
        if local_timestamp.date() == local_date(now, timezone_name)
        else local_timestamp.date().isoformat()
    )
    return f"{day} {local_timestamp:%H:%M}"


class MutationContext:
    """The only row-level write surface available inside a journaled mutation."""

    def __init__(self, connection: sqlite3.Connection, transaction_id: str) -> None:
        self._connection = connection
        self._transaction_id = transaction_id
        self._changes: list[_RowChange] = []

    def insert(self, table: str, values: Mapping[str, Any]) -> sqlite3.Row:
        self._check_table(table)
        inserted_values = dict(values)
        operation = _CURRENT_OPERATION.get()
        schema_columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        if table in {"meals", "water_logs"} and "source_session_key" in schema_columns:
            inserted_values["source_session_key"] = None
            inserted_values.setdefault("source_model", operation.source_model if operation else None)
            inserted_values.setdefault("test_run_id", operation.test_run_id if operation else None)
        if table == "meals" and "source_session_hash" in schema_columns:
            session_key = (
                operation.source_session_key
                if operation is not None
                else None
            )
            inserted_values["source_session_hash"] = (
                hashlib.sha256(session_key.encode("utf-8")).hexdigest()
                if session_key is not None
                else None
            )
        if "id" in inserted_values:
            raise ValueError("MutationContext assigns row ids through SQLite")
        inserted_values["transaction_id"] = self._transaction_id
        if not inserted_values:
            raise ValueError("insert requires values")
        columns = tuple(inserted_values)
        placeholders = ", ".join("?" for _ in columns)
        self._connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(inserted_values[column] for column in columns),
        )
        row_id = self._connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = self._require_row(table, row_id)
        self._changes.append(_RowChange(table, row_id, None, _row_dict(row)))
        return row

    def update(self, table: str, row_id: int | str, changes: Mapping[str, Any]) -> sqlite3.Row:
        self._check_table(table)
        before = self._require_row(table, row_id)
        updated_values = dict(changes)
        if not updated_values:
            raise ValueError("update requires changes")
        if {"id", "transaction_id"} & updated_values.keys():
            raise ValueError("id and transaction_id are managed by MutationContext")
        updated_values["transaction_id"] = self._transaction_id
        assignments = ", ".join(f"{column} = ?" for column in updated_values)
        self._connection.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            (*updated_values.values(), row_id),
        )
        after = self._require_row(table, row_id)
        self._changes.append(_RowChange(table, row_id, _row_dict(before), _row_dict(after)))
        return after

    def soft_delete(self, table: str, row_id: int | str, deleted_at: str) -> sqlite3.Row:
        self._check_table(table)
        columns = {row["name"] for row in self._connection.execute(f"PRAGMA table_info({table})")}
        if "deleted_at" not in columns:
            raise ValueError(f"Table {table!r} does not support soft deletion")
        return self.update(table, row_id, {"deleted_at": deleted_at})

    @property
    def changes(self) -> tuple[_RowChange, ...]:
        return tuple(self._changes)

    def _check_table(self, table: str) -> None:
        if table not in _MUTABLE_TABLES:
            raise ValueError(f"Table {table!r} is not allowed for journaled mutation")

    def _require_row(self, table: str, row_id: int | str) -> sqlite3.Row:
        row = self._connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise KeyError(f"No {table} row with id {row_id!r}")
        return row


class TransactionManager:
    """Commit mutations and their full-row journal snapshots atomically."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        kind: str,
        source_text: str,
        mutate: Callable[[MutationContext], T],
        *,
        internal_id: str | None = None,
    ) -> MutationResult[T]:
        transaction_id = internal_id or _new_transaction_id()
        operation = _CURRENT_OPERATION.get()
        self._begin()
        try:
            self._reject_replayed_operation(operation)
            self._insert_pending(transaction_id, kind, source_text)
            context = MutationContext(self._connection, transaction_id)
            value = mutate(context)
            changes = context.changes
            before, after = _snapshots(changes)
            effect_count = len(changes)
            undo_policy = "snapshot" if effect_count > 0 else "none"
            self._finalize(
                transaction_id,
                before,
                after,
                undo_policy=undo_policy,
                effect_count=effect_count,
            )
            self._record_operation(operation, transaction_id)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return MutationResult(transaction_id=transaction_id, value=value)

    def undo(
        self,
        transaction_id: str,
        *,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> MutationResult[dict[str, Any]]:
        operation = _CURRENT_OPERATION.get()
        self._begin()
        try:
            self._reject_replayed_operation(operation)
            self._assert_expected_target(
                transaction_id,
                required_status="committed",
                expected_status=expected_status,
                expected_generation=expected_generation,
            )
            original = self._original(transaction_id, "committed")
            if (
                original["undo_policy"] != "snapshot"
                or int(original["effect_count"]) <= 0
            ):
                raise TransactionNotUndoable(
                    "Transaction has no reversible effect"
                )
            changes = _changes_from_snapshots(original["before_snapshot"], original["after_snapshot"])
            if not changes:
                raise TransactionNotUndoable(
                    "Transaction snapshot has no applicable changes"
                )
            self._reject_forget_generation_conflicts(changes)
            undo_id = _new_transaction_id()
            self._insert_pending(undo_id, "transaction_undo", f"undo:{transaction_id}")
            undo_changes: list[_RowChange] = []
            for change in reversed(changes):
                undo_changes.append(
                    self._apply_expected(
                        change.table,
                        change.row_id,
                        change.after,
                        change.before,
                        conflict_error=UndoConflictError,
                        operation="undo",
                    )
                )
            self._set_original_status(
                transaction_id,
                status="reverted",
                reverted_at=_utc_now(),
            )
            before, after = _snapshots(undo_changes, original_transaction_id=transaction_id)
            affected_rows = len(undo_changes)
            self._finalize(
                undo_id,
                before,
                after,
                undo_policy="snapshot",
                effect_count=affected_rows,
            )
            self._record_operation(operation, undo_id)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return MutationResult(
            transaction_id=undo_id,
            value={
                "original_transaction_id": transaction_id,
                "status": "reverted",
                "affected_rows": affected_rows,
            },
        )

    def redo(
        self,
        transaction_id: str,
        *,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> MutationResult[dict[str, Any]]:
        operation = _CURRENT_OPERATION.get()
        self._begin()
        try:
            self._reject_replayed_operation(operation)
            self._assert_expected_target(
                transaction_id,
                required_status="reverted",
                expected_status=expected_status,
                expected_generation=expected_generation,
            )
            original = self._original(transaction_id, "reverted")
            if (
                original["undo_policy"] != "snapshot"
                or int(original["effect_count"]) <= 0
            ):
                raise TransactionNotUndoable(
                    "Transaction has no reversible effect"
                )
            changes = _changes_from_snapshots(original["before_snapshot"], original["after_snapshot"])
            if not changes:
                raise TransactionNotUndoable(
                    "Transaction snapshot has no applicable changes"
                )
            redo_id = _new_transaction_id()
            self._insert_pending(redo_id, "transaction_redo", f"redo:{transaction_id}")
            redo_changes: list[_RowChange] = []
            for change in changes:
                redo_changes.append(
                    self._apply_expected(
                        change.table,
                        change.row_id,
                        change.before,
                        change.after,
                        conflict_error=RedoConflictError,
                        operation="redo",
                    )
                )
            self._set_original_status(
                transaction_id,
                status="committed",
                reverted_at=None,
            )
            before, after = _snapshots(redo_changes, original_transaction_id=transaction_id)
            affected_rows = len(redo_changes)
            self._finalize(
                redo_id,
                before,
                after,
                undo_policy="snapshot",
                effect_count=affected_rows,
            )
            self._record_operation(operation, redo_id)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return MutationResult(
            transaction_id=redo_id,
            value={
                "original_transaction_id": transaction_id,
                "status": "committed",
                "affected_rows": affected_rows,
            },
        )

    def _begin(self) -> None:
        if self._connection.in_transaction:
            raise TransactionStateError("Cannot start a journaled mutation inside an active transaction")
        self._connection.execute("BEGIN IMMEDIATE")

    def _assert_expected_target(
        self,
        transaction_id: str,
        *,
        required_status: str,
        expected_status: str | None,
        expected_generation: int | None,
    ) -> None:
        if expected_status is None and expected_generation is None:
            return
        if (
            expected_status != required_status
            or not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 0
        ):
            raise TransactionTargetStaleError(
                "Transaction workflow reference is stale"
            )
        changed = self._connection.execute(
            """
            UPDATE transactions
            SET generation = generation
            WHERE id = ? AND status = ? AND generation = ?
            """,
            (transaction_id, expected_status, expected_generation),
        ).rowcount
        if changed != 1:
            raise TransactionTargetStaleError(
                "Transaction workflow reference is stale"
            )

    def _set_original_status(
        self,
        transaction_id: str,
        *,
        status: str,
        reverted_at: str | None,
    ) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(transactions)")
        }
        generation_assignment = (
            ", generation = generation + 1"
            if "generation" in columns
            else ""
        )
        self._connection.execute(
            f"""
            UPDATE transactions
            SET status = ?, reverted_at = ?{generation_assignment}
            WHERE id = ?
            """,
            (status, reverted_at, transaction_id),
        )

    def _reject_replayed_operation(
        self, operation: OperationContext | None
    ) -> None:
        if operation is None:
            return
        receipt = self._connection.execute(
            """
            SELECT request_fingerprint
            FROM operation_receipts
            WHERE operation_id = ?
            """,
            (operation.operation_id,),
        ).fetchone()
        if receipt is None:
            if operation.semantic_fingerprint is None:
                return
        else:
            if receipt["request_fingerprint"] != operation.request_fingerprint:
                raise OperationFingerprintConflict(
                    "The internal operation identifier was reused for another request"
                )
            raise OperationAlreadyCommitted
        if operation.semantic_fingerprint is not None:
            semantic_receipt = self._connection.execute(
                """
                SELECT transaction_id
                FROM semantic_operation_receipts
                WHERE semantic_fingerprint = ?
                """,
                (operation.semantic_fingerprint,),
            ).fetchone()
            if semantic_receipt is not None:
                raise OperationAlreadyCommitted

    def _record_operation(
        self, operation: OperationContext | None, transaction_id: str
    ) -> None:
        if operation is None:
            return
        self._connection.execute(
            """
            INSERT INTO operation_receipts (
                operation_id, request_fingerprint, transaction_id, committed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation.operation_id,
                operation.request_fingerprint,
                transaction_id,
                _utc_now(),
            ),
        )
        if operation.semantic_fingerprint is not None:
            self._connection.execute(
                """
                INSERT INTO semantic_operation_receipts (
                    semantic_fingerprint, transaction_id, committed_at
                ) VALUES (?, ?, ?)
                """,
                (operation.semantic_fingerprint, transaction_id, _utc_now()),
            )

    def _insert_pending(self, transaction_id: str, kind: str, source_text: str) -> None:
        self._connection.execute(
            """
            INSERT INTO transactions (id, transaction_type, status, created_at, source_text)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (transaction_id, kind, _utc_now(), source_text),
        )

    def _finalize(
        self,
        transaction_id: str,
        before: str,
        after: str,
        *,
        undo_policy: str,
        effect_count: int,
    ) -> None:
        committed_at = _utc_now()
        self._connection.execute(
            """
            UPDATE transactions
            SET status = 'committed',
                committed_at = ?,
                before_snapshot = ?,
                after_snapshot = ?,
                undo_policy = ?,
                effect_count = ?
            WHERE id = ?
            """,
            (
                committed_at,
                before,
                after,
                undo_policy,
                effect_count,
                transaction_id,
            ),
        )
        from .workflow_lineage import index_transaction_snapshots

        index_transaction_snapshots(
            self._connection,
            transaction_id=transaction_id,
            before_snapshot=before,
            after_snapshot=after,
            created_at=committed_at,
        )

    def _original(self, transaction_id: str, expected_status: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if row is None:
            raise TransactionStateError(f"Unknown transaction {transaction_id!r}")
        if row["transaction_type"] in {"transaction_undo", "transaction_redo"}:
            raise TransactionStateError(
                f"Transaction {transaction_id!r} is an operation journal "
                "and cannot be an undo or redo target"
            )
        if row["status"] != expected_status:
            raise TransactionStateError(
                f"Transaction {transaction_id!r} must be {expected_status}, not {row['status']}"
            )
        return row

    def _reject_forget_generation_conflicts(
        self, changes: list[_RowChange]
    ) -> None:
        """Prevent undo from reactivating a forgotten rule over a successor."""

        rejected_rule_ids = {
            change.after.get("rule_id")
            for change in changes
            if change.table == "learning_events"
            and change.before is None
            and change.after is not None
            and change.after.get("event_type") == "rejected"
        }
        for change in changes:
            if (
                change.table != "personal_rules"
                or change.before is None
                or change.after is None
                or change.row_id not in rejected_rule_ids
                or not bool(change.before.get("active"))
                or bool(change.after.get("active"))
            ):
                continue
            candidates = self._connection.execute(
                """
                SELECT id, rule_json
                FROM personal_rules
                WHERE rule_type = ?
                  AND subject = ?
                  AND id > ?
                ORDER BY id
                """,
                (
                    change.before.get("rule_type"),
                    change.before.get("subject"),
                    change.row_id,
                ),
            ).fetchall()
            canonical_type = _canonical_rule_type(
                change.before.get("rule_json")
            )
            if any(
                canonical_type is None
                or _canonical_rule_type(row["rule_json"]) in {None, canonical_type}
                for row in candidates
            ):
                raise UndoConflictError(
                    "Cannot undo a forgotten preference after a newer "
                    "generation was created"
                )

    def _apply_expected(
        self,
        table: str,
        row_id: int | str,
        expected: dict[str, Any] | None,
        desired: dict[str, Any] | None,
        *,
        conflict_error: type[RuntimeError],
        operation: str,
    ) -> _RowChange:
        current_row = self._connection.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        current = _row_dict(current_row) if current_row is not None else None
        if not _row_matches_expected(table, current, expected):
            raise conflict_error(
                f"Cannot {operation} transaction because row {table}:{row_id} "
                f"no longer matches its expected {operation} state"
            )
        if desired is None:
            self._connection.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        elif current is None:
            schema_columns = {
                row["name"]
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            }
            insert_values = _add_approved_schema_baselines(
                table, desired, schema_columns
            )
            columns = tuple(insert_values)
            placeholders = ", ".join("?" for _ in columns)
            self._connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(insert_values[column] for column in columns),
            )
        else:
            update_values = _add_approved_schema_baselines(
                table, desired, set(current)
            )
            columns = tuple(column for column in update_values if column != "id")
            assignments = ", ".join(f"{column} = ?" for column in columns)
            self._connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*(update_values[column] for column in columns), row_id),
            )
        applied_row = self._connection.execute(
            f"SELECT * FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        applied = _row_dict(applied_row) if applied_row is not None else None
        return _RowChange(table, row_id, current, applied)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _row_matches_expected(
    table: str,
    current: dict[str, Any] | None,
    expected: dict[str, Any] | None,
) -> bool:
    if current is None or expected is None:
        return current is expected
    for column, expected_value in expected.items():
        if column not in current or not _values_equivalent(
            table, column, current[column], expected_value
        ):
            return False
    for column in current.keys() - expected.keys():
        baseline = _approved_schema_baseline(table, column, expected)
        if baseline is _MISSING_SCHEMA_BASELINE:
            return False
        if current[column] != baseline:
            return False
    return True


def _values_equivalent(
    table: str, column: str, current: Any, expected: Any
) -> bool:
    if current == expected:
        return True
    if (table, column) not in _CANONICAL_DECIMAL_COLUMNS:
        return False
    try:
        return Decimal(str(current)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return False


def _add_approved_schema_baselines(
    table: str, desired: dict[str, Any], schema_columns: set[str]
) -> dict[str, Any]:
    values = dict(desired)
    for (baseline_table, column), baseline in _APPROVED_SCHEMA_BASELINES.items():
        if (
            baseline_table == table
            and column in schema_columns
            and column not in values
        ):
            values[column] = baseline
    for column in schema_columns - values.keys():
        baseline = _approved_schema_baseline(table, column, values)
        if baseline is not _MISSING_SCHEMA_BASELINE:
            values[column] = baseline
    return values


def _approved_schema_baseline(
    table: str, column: str, row: Mapping[str, Any]
) -> Any:
    baseline_key = (table, column)
    if baseline_key in _APPROVED_SCHEMA_BASELINES:
        return _APPROVED_SCHEMA_BASELINES[baseline_key]
    if table == "meals" and column in {
        "nutrition_status",
        "nutrition_missing_fields_json",
    }:
        status, missing_fields_json = _meal_nutrition_baseline(row)
        return (
            status
            if column == "nutrition_status"
            else missing_fields_json
        )
    return _MISSING_SCHEMA_BASELINE


def _meal_nutrition_baseline(row: Mapping[str, Any]) -> tuple[str, str]:
    fields = (
        "total_calories",
        "total_protein",
        "total_fat",
        "total_carbohydrate",
        "total_fiber",
        "total_sodium",
    )
    missing = [
        field.removeprefix("total_")
        for field in fields
        if row.get(field) is None
    ]
    status = "complete" if not missing else "incomplete" if len(missing) == 6 else "partial"
    return status, json.dumps(missing, ensure_ascii=False, separators=(",", ":"))


def _snapshots(
    changes: tuple[_RowChange, ...] | list[_RowChange], *, original_transaction_id: str | None = None
) -> tuple[str, str]:
    before = [_snapshot_entry(change, change.before, original_transaction_id) for change in changes]
    after = [_snapshot_entry(change, change.after, original_transaction_id) for change in changes]
    return _canonical_json(before), _canonical_json(after)


def _snapshot_entry(
    change: _RowChange, row: dict[str, Any] | None, original_transaction_id: str | None
) -> dict[str, Any]:
    entry: dict[str, Any] = {"row": row, "row_id": change.row_id, "table": change.table}
    if original_transaction_id is not None:
        entry["original_transaction_id"] = original_transaction_id
    return entry


def _changes_from_snapshots(before_json: str, after_json: str) -> list[_RowChange]:
    before = json.loads(before_json)
    after = json.loads(after_json)
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
        raise TransactionStateError("Transaction snapshots are not compatible journal arrays")
    changes: list[_RowChange] = []
    for before_entry, after_entry in zip(before, after, strict=True):
        if (
            not isinstance(before_entry, dict)
            or not isinstance(after_entry, dict)
            or before_entry.get("table") not in _MUTABLE_TABLES
            or before_entry.get("table") != after_entry.get("table")
            or before_entry.get("row_id") != after_entry.get("row_id")
        ):
            raise TransactionStateError("Transaction snapshots contain an unsupported row mutation")
        changes.append(
            _RowChange(
                table=before_entry["table"],
                row_id=before_entry["row_id"],
                before=before_entry.get("row"),
                after=after_entry.get("row"),
            )
        )
    return changes


def _canonical_rule_type(rule_json: object) -> str | None:
    if not isinstance(rule_json, str):
        return None
    try:
        value = json.loads(rule_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, Mapping):
        return None
    rule_type = value.get("rule_type")
    return rule_type if isinstance(rule_type, str) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _utc_now() -> str:
    return utc_text()


def _new_transaction_id() -> str:
    timestamp = _utc_now().replace("-", "").replace(":", "")
    return f"txn_{timestamp}_{secrets.token_hex(8)}"
