"""Independent control plane for retry-safe maintenance operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Literal

from . import database
from .clock import Clock, system_utc_now


MaintenanceStatus = Literal[
    "accepted",
    "running",
    "committed",
    "failed",
    "interrupted",
    "reconciling",
]
ReconciliationOutcome = Literal["committed", "failed"]

_HANDLE_PATTERN = re.compile(r"^mop_[0-9a-f]{32}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ERROR_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_TERMINAL = frozenset({"committed", "failed"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "accepted": frozenset({"running", "interrupted", "failed"}),
    "running": frozenset({"committed", "failed", "interrupted"}),
    "interrupted": frozenset({"reconciling"}),
    "reconciling": frozenset({"committed", "failed"}),
    "committed": frozenset(),
    "failed": frozenset(),
}


class MaintenanceControlError(RuntimeError):
    """Base error for maintenance control-plane failures."""


class MaintenanceKeyConflict(MaintenanceControlError):
    """Raised when an operation key is reused for different parameters."""


class MaintenanceBusyError(MaintenanceControlError):
    """Raised when another exclusive maintenance operation is active."""


class MaintenanceStateError(MaintenanceControlError):
    """Raised when an operation attempts an invalid state transition."""


class MaintenanceNotFound(MaintenanceControlError):
    """Raised when a public operation handle is unknown."""


@dataclass(frozen=True)
class MaintenanceRecord:
    """An operation without internal identifiers or raw parameters."""

    handle: str
    action: str
    status: MaintenanceStatus
    accepted_at: str
    started_at: str | None
    finished_at: str | None
    error_code: str | None


class MaintenanceController:
    """Own the separate maintenance SQLite database for one data directory."""

    def __init__(
        self,
        database_path: Path,
        migrations_dir: Path,
        *,
        clock: Clock | None = None,
    ) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = path
        self.migrations_dir = Path(migrations_dir)
        self._clock = clock or system_utc_now
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        database.apply_migrations(self.connection, self.migrations_dir)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> MaintenanceController:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def accept(
        self,
        action: str,
        parameters: Mapping[str, Any],
        *,
        operation_key: str | None,
        exclusive: bool,
    ) -> tuple[MaintenanceRecord, bool]:
        """Accept one operation or return the one already bound to its key."""

        normalized_action = _required_action(action)
        normalized_key = _optional_key(operation_key)
        fingerprint = _fingerprint(parameters)
        if normalized_key is not None:
            existing = self._row_for_key(normalized_key)
            if existing is not None:
                return self._replayed(existing, normalized_action, fingerprint)

        handle = f"mop_{secrets.token_hex(16)}"
        accepted_at = _timestamp(self._clock())
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if normalized_key is not None:
                existing = self._row_for_key(normalized_key)
                if existing is not None:
                    self.connection.rollback()
                    return self._replayed(
                        existing,
                        normalized_action,
                        fingerprint,
                    )
            cursor = self.connection.execute(
                """
                INSERT INTO maintenance_operations (
                    operation_handle,
                    operation_key,
                    action,
                    parameters_sha256,
                    status,
                    exclusive_operation,
                    accepted_at
                )
                VALUES (?, ?, ?, ?, 'accepted', ?, ?)
                """,
                (
                    handle,
                    normalized_key,
                    normalized_action,
                    fingerprint,
                    1 if exclusive else 0,
                    accepted_at,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO maintenance_events (
                    operation_id,
                    from_status,
                    to_status,
                    occurred_at
                )
                VALUES (?, NULL, 'accepted', ?)
                """,
                (cursor.lastrowid, accepted_at),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            self.connection.rollback()
            if normalized_key is not None:
                existing = self._row_for_key(normalized_key)
                if existing is not None:
                    return self._replayed(
                        existing,
                        normalized_action,
                        fingerprint,
                    )
            raise MaintenanceBusyError(
                "Another exclusive maintenance operation is active"
            ) from error
        return self.get(handle), False

    def mark_running(self, handle: str) -> MaintenanceRecord:
        return self._transition(handle, "running")

    def mark_committed(
        self,
        handle: str,
        result: Mapping[str, Any],
    ) -> MaintenanceRecord:
        return self._transition(
            handle,
            "committed",
            result_json=_canonical_json(result),
        )

    def mark_failed(
        self,
        handle: str,
        error_code: str,
    ) -> MaintenanceRecord:
        normalized = str(error_code).strip().upper()
        if _ERROR_PATTERN.fullmatch(normalized) is None:
            normalized = "MAINTENANCE_FAILED"
        return self._transition(handle, "failed", error_code=normalized)

    def get(self, handle: str) -> MaintenanceRecord:
        row = self._row_for_handle(handle)
        if row is None:
            raise MaintenanceNotFound("Maintenance operation was not found")
        return _record(row)

    def result(self, handle: str) -> Mapping[str, Any] | None:
        row = self._row_for_handle(handle)
        if row is None:
            raise MaintenanceNotFound("Maintenance operation was not found")
        raw = row["result_json"]
        if raw is None:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise MaintenanceStateError("Stored maintenance result is invalid")
        return value

    def history(self, limit: int = 20) -> tuple[MaintenanceRecord, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            raise ValueError("limit must be an integer from 1 to 20")
        rows = self.connection.execute(
            """
            SELECT *
            FROM maintenance_operations
            ORDER BY accepted_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def events(self, handle: str) -> tuple[Mapping[str, Any], ...]:
        row = self._row_for_handle(handle)
        if row is None:
            raise MaintenanceNotFound("Maintenance operation was not found")
        events = self.connection.execute(
            """
            SELECT from_status, to_status, occurred_at, reason_code
            FROM maintenance_events
            WHERE operation_id = ?
            ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
        return tuple(
            {
                "from_status": event["from_status"],
                "to_status": event["to_status"],
                "occurred_at": event["occurred_at"],
                "reason_code": event["reason_code"],
            }
            for event in events
        )

    def reconcile_interrupted(
        self,
        inspector: Callable[[MaintenanceRecord], ReconciliationOutcome],
    ) -> tuple[MaintenanceRecord, ...]:
        """Classify unfinished work from evidence without replaying its action."""

        rows = self.connection.execute(
            """
            SELECT operation_handle
            FROM maintenance_operations
            WHERE status IN ('accepted', 'running')
            ORDER BY id
            """
        ).fetchall()
        reconciled: list[MaintenanceRecord] = []
        for row in rows:
            handle = str(row["operation_handle"])
            interrupted = self._transition(
                handle,
                "interrupted",
                reason_code="PROCESS_INTERRUPTED",
            )
            reconciling = self._transition(
                interrupted.handle,
                "reconciling",
                reason_code="EVIDENCE_INSPECTION",
            )
            outcome = inspector(reconciling)
            if outcome == "committed":
                reconciled.append(
                    self._transition(
                        handle,
                        "committed",
                        result_json=_canonical_json({"reconciled": True}),
                        reason_code="EVIDENCE_COMMITTED",
                    )
                )
            elif outcome == "failed":
                reconciled.append(
                    self._transition(
                        handle,
                        "failed",
                        error_code="INTERRUPTED",
                        reason_code="EVIDENCE_FAILED",
                    )
                )
            else:
                raise MaintenanceStateError(
                    "Reconciliation inspector must return committed or failed"
                )
        return tuple(reconciled)

    def validate(self) -> tuple[database.CheckResult, ...]:
        raw_checks = database.validate_database(self.connection)
        checks = tuple(
            database.CheckResult(
                code=f"maintenance_control_{check.code}",
                level=check.level,
                message=check.message.replace("SQLite", "Maintenance control"),
                repairable=False,
            )
            for check in raw_checks
        )
        active = self.connection.execute(
            """
            SELECT count(*)
            FROM maintenance_operations
            WHERE status IN ('accepted', 'running', 'interrupted', 'reconciling')
            """
        ).fetchone()[0]
        latest = self.history(1)
        latest_check = database.CheckResult(
            code="maintenance_latest_operation",
            level=(
                "PASS"
                if latest and latest[0].status in _TERMINAL
                else "WARN"
            ),
            message=(
                "Latest maintenance operation reached a terminal state"
                if latest and latest[0].status in _TERMINAL
                else (
                    "Latest maintenance operation is unfinished"
                    if latest
                    else "No maintenance operation has been recorded"
                )
            ),
            repairable=False,
        )
        return (
            *checks,
            database.CheckResult(
                code="maintenance_pending",
                level="WARN" if active else "PASS",
                message=(
                    f"Found {active} unfinished maintenance operation(s)"
                    if active
                    else "No unfinished maintenance operations"
                ),
                repairable=False,
            ),
            latest_check,
        )

    def _transition(
        self,
        handle: str,
        target: MaintenanceStatus,
        *,
        result_json: str | None = None,
        error_code: str | None = None,
        reason_code: str | None = None,
    ) -> MaintenanceRecord:
        _required_handle(handle)
        row = self._row_for_handle(handle)
        if row is None:
            raise MaintenanceNotFound("Maintenance operation was not found")
        current = str(row["status"])
        if target not in _TRANSITIONS[current]:
            raise MaintenanceStateError(
                f"Invalid maintenance transition: {current} -> {target}"
            )
        now = _timestamp(self._clock())
        started_at = now if target == "running" else row["started_at"]
        finished_at = now if target in _TERMINAL else None
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            changed = self.connection.execute(
                """
                UPDATE maintenance_operations
                SET status = ?,
                    started_at = ?,
                    finished_at = ?,
                    result_json = COALESCE(?, result_json),
                    error_code = COALESCE(?, error_code)
                WHERE operation_handle = ? AND status = ?
                """,
                (
                    target,
                    started_at,
                    finished_at,
                    result_json,
                    error_code,
                    handle,
                    current,
                ),
            ).rowcount
            if changed != 1:
                raise MaintenanceStateError(
                    "Maintenance operation changed concurrently"
                )
            self.connection.execute(
                """
                INSERT INTO maintenance_events (
                    operation_id,
                    from_status,
                    to_status,
                    occurred_at,
                    reason_code
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["id"], current, target, now, reason_code),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.get(handle)

    def _replayed(
        self,
        row: sqlite3.Row,
        action: str,
        fingerprint: str,
    ) -> tuple[MaintenanceRecord, bool]:
        if (
            row["action"] != action
            or row["parameters_sha256"] != fingerprint
        ):
            raise MaintenanceKeyConflict(
                "The maintenance operation key is already used"
            )
        return _record(row), True

    def _row_for_key(self, operation_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT *
            FROM maintenance_operations
            WHERE operation_key = ?
            """,
            (operation_key,),
        ).fetchone()

    def _row_for_handle(self, handle: str) -> sqlite3.Row | None:
        _required_handle(handle)
        return self.connection.execute(
            """
            SELECT *
            FROM maintenance_operations
            WHERE operation_handle = ?
            """,
            (handle,),
        ).fetchone()


def public_record(record: MaintenanceRecord) -> Mapping[str, Any]:
    value: dict[str, Any] = {
        "operation_handle": record.handle,
        "action": record.action,
        "status": record.status,
        "accepted_at": record.accepted_at,
    }
    if record.started_at is not None:
        value["started_at"] = record.started_at
    if record.finished_at is not None:
        value["finished_at"] = record.finished_at
    if record.error_code is not None:
        value["error_code"] = record.error_code
    return value


def _record(row: sqlite3.Row) -> MaintenanceRecord:
    return MaintenanceRecord(
        handle=str(row["operation_handle"]),
        action=str(row["action"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        accepted_at=str(row["accepted_at"]),
        started_at=(
            None if row["started_at"] is None else str(row["started_at"])
        ),
        finished_at=(
            None if row["finished_at"] is None else str(row["finished_at"])
        ),
        error_code=(
            None if row["error_code"] is None else str(row["error_code"])
        ),
    )


def _fingerprint(parameters: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(parameters).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Maintenance parameters must be bounded JSON values"
        ) from error


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Maintenance clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_action(value: str) -> str:
    action = str(value).strip()
    if not action or len(action) > 64:
        raise ValueError("Maintenance action must be non-empty")
    return action


def _optional_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    if _KEY_PATTERN.fullmatch(key) is None:
        raise ValueError("operation_key has an invalid format")
    return key


def _required_handle(value: str) -> str:
    handle = str(value).strip()
    if _HANDLE_PATTERN.fullmatch(handle) is None:
        raise ValueError("operation_handle has an invalid format")
    return handle
