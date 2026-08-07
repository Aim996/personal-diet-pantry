"""Seven-domain request dispatcher for the Personal Diet Pantry business core."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any

from . import __product_version__
from . import (
    backup,
    body_weight,
    costs,
    data_erasure,
    data_export,
    data_import,
    database,
    learning,
    maintenance_control,
    meals,
    inventory_matching,
    nutrition,
    nutrition_normalization,
    nutrition_backfill,
    nutrition_resolution,
    nutrition_profiles,
    pantry,
    pantry_defaults,
    policies,
    portion_evidence,
    prepared_foods,
    goal_profiles,
    insights,
    progress,
    progress_receipt,
    reports,
    recipes,
    self_check,
    shopping,
    temporal,
    trends,
    water,
    waste,
)
from .config import (
    load_settings,
    validate_automation,
    validate_static_rules,
)
from .clock import Clock, system_utc_now, use_clock, utc_now
from .models import ConfigurationError, DataPaths, Settings, NutritionGoals
from .input_limits import (
    MAX_TOTAL_ITEMS,
    validate_json_value,
    validate_meal_payload,
)
from .generated_tool_contracts import (
    ACTIONS as CONTRACT_ACTIONS,
    ACTION_HANDLER_NAMES,
    ACTION_POLICIES,
    FORMAL_MUTATION_ACTIONS,
)
from .paths import ensure_data_directories, resolve_data_paths, validate_owned_path
from .transactions import (
    OperationAlreadyCommitted,
    OperationContext,
    OperationFingerprintConflict,
    RedoConflictError,
    TransactionManager,
    TransactionNotUndoable,
    TransactionStateError,
    TransactionTargetStaleError,
    UndoConflictError,
    UndoFilters,
    find_undo_candidates,
    operation_context,
)
from .timezones import (
    TimezoneConfigurationError,
    local_calendar_date,
    local_date,
    local_datetime,
    local_day_utc_bounds,
    local_expiry_end,
)
from .trusted_workflows import (
    Confirmation,
    DeleteDataCommand,
    DerivedFilesChangedError,
    ErasureVerificationRequired,
    ImportDataCommand,
    RequestContext,
    SelfCheckQuery,
    TrustedWorkflowDependencies,
    TrustedWorkflowModule,
    WorkflowStaleError,
)


LOGGER = logging.getLogger(__name__)

_REQUEST_KEYS = frozenset({"domain", "action", "payload", "context", "_internal"})
_PANTRY_PACKAGE_FIELDS = frozenset(
    {"package_count", "quantity_per_package", "package_unit"}
)
_PRIVATE_KEYS = frozenset(
    {
        "id",
        "database_id",
        "databaseId",
        "transaction_id",
        "transactionId",
        "original_transaction_id",
        "preview_token",
        "previewToken",
        "token",
        "confirmation_reasons",
    }
)
_HANDLE_PREFIX = "wfh_"
_OPERATION_ID_PATTERN = re.compile(
    r"^op_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_REQUEST_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POST_COMMIT_HANDLE_WARNING = (
    "The change was committed, but a follow-up workflow handle is unavailable. "
    "Query current state before making another change."
)
_DEGRADED_ALLOWED_ACTIONS = frozenset(
    {
        ("meal", "query"),
        ("water", "query"),
        ("weight", "query"),
        ("pantry", "query"),
        ("transaction", "get_recent"),
        ("report", "expiring_inventory"),
        ("system", "backup"),
        ("system", "self_check"),
        ("system", "maintenance_status"),
        ("system", "maintenance_history"),
        ("system", "validate_database"),
        ("system", "query_preferences"),
    }
)
_TIMEZONE_DEGRADED_ALLOWED_ACTIONS = frozenset(
    {
        ("system", "backup"),
        ("system", "self_check"),
        ("system", "maintenance_status"),
        ("system", "maintenance_history"),
        ("system", "validate_database"),
        ("system", "query_preferences"),
    }
)
_FORMAL_MUTATION_ACTIONS = FORMAL_MUTATION_ACTIONS
_SAFE_ERROR_MESSAGES = {
    "AMBIGUOUS_BATCH": "More than one pantry batch matches",
    "AMBIGUOUS_TARGET": "More than one target matches",
    "CONFIGURATION_ERROR": "Service configuration is invalid",
    "DATABASE_BUSY": "The database is busy; retry later",
    "DATABASE_INTEGRITY_ERROR": "Service database validation failed",
    "INSUFFICIENT_STOCK": "There is not enough eligible pantry stock",
    "INTERNAL_ERROR": "An unexpected internal error occurred",
    "INVALID_INPUT": "The request is invalid",
    "LOW_CONFIDENCE": "The request needs confirmation before it can be recorded",
    "MAINTENANCE_BUSY": "Another maintenance operation is already active",
    "MAINTENANCE_KEY_CONFLICT": "The maintenance operation key is already used",
    "MAINTENANCE_NOT_FOUND": "The maintenance operation was not found",
    "MAINTENANCE_OPERATION_FAILED": "The maintenance operation failed",
    "NUTRITION_ESTIMATE_REQUIRED": "A complete nutrition estimate is required before recording",
    "NOT_UNDOABLE": "This operation has no safe reversible effect",
    "RESTORE_REQUIRES_CONFIRMATION": "Restore requires explicit confirmation",
    "RULES_INVALID": "Service rules are invalid",
    "STALE_PREVIEW": "The workflow reference is stale",
    "STARTUP_ERROR": "Service startup failed",
}
_SAFE_INPUT_DIAGNOSTICS = {
    "food_name is required": {
        "field": "food_name",
        "reason": "required",
        "expected": "non-empty text",
        "retryable": True,
    },
    "quantity is required": {
        "field": "quantity",
        "reason": "required",
        "expected": "positive number or decimal string",
        "retryable": True,
    },
    "unit is required": {
        "field": "unit",
        "reason": "required",
        "expected": "g, ml, piece, portion, or pack",
        "retryable": True,
    },
    "quantity is not representable as a SQLite REAL": {
        "field": "quantity",
        "reason": "not_representable",
        "expected": "a decimal that round-trips exactly through SQLite REAL",
        "retryable": True,
    },
    "expires_at is required": {
        "field": "expires_at",
        "reason": "required",
        "expected": (
            "a concrete ISO 8601 expiry estimated from the item state and storage"
        ),
        "retryable": True,
    },
    "expires_at must be later than added_at": {
        "field": "expires_at",
        "reason": "must_be_after_added_at",
        "expected": "an ISO 8601 timestamp later than added_at",
        "retryable": True,
    },
}
_INTERNAL_REFERENCE_PATTERN = re.compile(
    r"\b(?:txn|transaction|database|row|record|meal|batch|water)_[A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _HandlerResult:
    data: Mapping[str, Any]
    outcome: str | None = None
    warnings: tuple[str, ...] = ()
    requires_confirmation: bool = False
    confirmation_options: tuple[Mapping[str, Any], ...] = ()


class _ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        requires_confirmation: bool = False,
        confirmation_options: Sequence[Mapping[str, Any]] = (),
        field: str | None = None,
        reason: str | None = None,
        expected: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.requires_confirmation = requires_confirmation
        self.confirmation_options = tuple(confirmation_options)
        self.field = field
        self.reason = reason
        self.expected = expected
        self.retryable = retryable


class _WorkflowConsumedRace(RuntimeError):
    """Internal signal used when another caller consumes a workflow first."""


class DietService:
    """Own service startup state and dispatch one validated domain request."""

    def __init__(
        self,
        source_root: Path | str | None = None,
        *,
        plugin_config: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
        openclaw_data_root: Path | str | None = None,
        data_paths: DataPaths | None = None,
        connection: sqlite3.Connection | None = None,
        _clock: Clock | None = None,
    ) -> None:
        self._clock = _clock or system_utc_now
        self.source_root = (
            Path(source_root).resolve()
            if source_root is not None
            else Path(__file__).resolve().parents[2]
        )
        environment = os.environ if env is None else env
        fallback_root = openclaw_data_root
        if fallback_root is None:
            configured_fallback = environment.get("OPENCLAW_DATA_DIR")
            fallback_root = Path(configured_fallback) if configured_fallback else None
        self.data_paths = data_paths or resolve_data_paths(
            plugin_config,
            environment,
            Path(fallback_root) if fallback_root is not None else None,
        )
        ensure_data_directories(self.data_paths)
        validate_owned_path(self.data_paths, self.data_paths.database)
        validate_owned_path(
            self.data_paths,
            self.data_paths.maintenance_database,
        )
        self.maintenance_controller = (
            maintenance_control.MaintenanceController(
                self.data_paths.maintenance_database,
                self.control_migrations_dir,
                clock=self._clock,
            )
        )
        self._degraded_error: _ServiceError | None = None
        self._timezone_degraded = False
        try:
            self.settings = load_settings(self.source_root, self.data_paths)
            validate_automation(self.settings)
        except TimezoneConfigurationError as error:
            self.settings = load_settings(
                self.source_root,
                self.data_paths,
                include_overrides=False,
            )
            self._degraded_error = _ServiceError("CONFIGURATION_ERROR", str(error))
            self._timezone_degraded = True
        except ConfigurationError as error:
            self.settings = load_settings(
                self.source_root,
                self.data_paths,
                include_overrides=False,
            )
            self._degraded_error = _ServiceError("CONFIGURATION_ERROR", str(error))
        try:
            validate_static_rules(self.source_root)
            self.policies = policies.load_policy_registry(
                self.source_root,
                self.data_paths,
            )
        except ConfigurationError as error:
            self.policies = policies.load_policy_registry(
                self.source_root,
                self.data_paths,
                include_overrides=False,
            )
            if self._degraded_error is None:
                self._degraded_error = _ServiceError("RULES_INVALID", str(error))

        database_existed = (
            Path(self.data_paths.database).is_file()
            and Path(self.data_paths.database).stat().st_size > 0
        )
        self._owns_connection = connection is None
        try:
            self.connection = connection or database.connect_database(
                self.data_paths.database
            )
        except sqlite3.DatabaseError as error:
            if connection is not None:
                raise
            self.connection = _connect_existing_read_only(self.data_paths.database)
            self._degraded_error = _ServiceError(
                "DATABASE_INTEGRITY_ERROR", str(error)
            )
        try:
            if self._degraded_error is None:
                if database_existed and database.has_pending_migrations(
                    self.connection, self.migrations_dir
                ):
                    backup.create_backup(
                        self.connection,
                        self.data_paths,
                        label="pre-migration",
                        _clock=self._clock,
                    )
                database.apply_migrations(self.connection, self.migrations_dir)
                failures = [
                    check
                    for check in database.validate_database(self.connection)
                    if check.level == "FAIL"
                ]
                if failures:
                    self._degraded_error = _ServiceError(
                        "DATABASE_INTEGRITY_ERROR",
                        "; ".join(check.message for check in failures),
                    )
                if self._degraded_error is None:
                    goal_profiles.ensure_goal_profile(
                        self.connection, self.settings.nutrition_goals,
                        self.settings.profile.timezone, utc_now(),
                    )
            if self._degraded_error is not None:
                self.connection.execute("PRAGMA query_only = ON")
        except (
            backup.BackupVerificationError,
            database.MigrationError,
            sqlite3.DatabaseError,
        ) as error:
            if self.connection.in_transaction:
                self.connection.rollback()
            self._degraded_error = _ServiceError(
                "DATABASE_INTEGRITY_ERROR", str(error)
            )
            self.connection.execute("PRAGMA query_only = ON")
        except BaseException:
            if self._owns_connection:
                self.connection.close()
            raise
        try:
            self.trusted_workflows = TrustedWorkflowModule(
                TrustedWorkflowDependencies(
                    connection=lambda: self.connection,
                    replace_connection=self._replace_connection,
                    data_paths=self.data_paths,
                    migrations_dir=self.migrations_dir,
                    source_root=self.source_root,
                    controller=self.maintenance_controller,
                    clock=self._clock,
                    preview_ttl=timedelta(
                        minutes=(
                            self.settings.behavior.inventory.preview_expiration_minutes
                        )
                    ),
                )
            )
            if self._degraded_error is None:
                self.trusted_workflows.recover_startup()
        except BaseException:
            try:
                if self._owns_connection:
                    self.connection.close()
            finally:
                self.maintenance_controller.close()
            raise

    def _replace_connection(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @property
    def migrations_dir(self) -> Path:
        return self.source_root / "migrations"

    @property
    def templates_dir(self) -> Path:
        return self.source_root / "templates"

    @property
    def control_migrations_dir(self) -> Path:
        configured = self.source_root / "control-migrations"
        if configured.is_dir():
            return configured
        return Path(__file__).resolve().parents[2] / "control-migrations"

    @property
    def rules_dir(self) -> Path:
        return self.source_root / "rules"

    def close(self) -> None:
        try:
            if self._owns_connection:
                self.connection.close()
        finally:
            self.maintenance_controller.close()

    def __enter__(self) -> DietService:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def dispatch(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Dispatch one request and always return a complete protocol response."""

        try:
            if isinstance(request, Mapping) and set(request) == {"_internal"}:
                return self._operation_status(request["_internal"])
            validate_json_value(request)
            domain, action, payload, context, private_operation = _validated_request(
                request
            )
            if domain == "meal":
                meal_payload = (
                    payload.get("draft", {})
                    if action == "update"
                    else payload
                )
                if isinstance(meal_payload, Mapping):
                    validate_meal_payload(meal_payload)
            if private_operation is not None:
                receipt = self.connection.execute(
                    """
                    SELECT request_fingerprint
                    FROM operation_receipts
                    WHERE operation_id = ?
                    """,
                    (private_operation.operation_id,),
                ).fetchone()
                if receipt is not None:
                    if (
                        receipt["request_fingerprint"]
                        != private_operation.request_fingerprint
                    ):
                        raise OperationFingerprintConflict(
                            "The internal operation identifier was reused "
                            "for another request"
                        )
                    return _operation_committed_response()
                if private_operation.semantic_fingerprint is not None:
                    semantic_receipt = self.connection.execute(
                        """
                        SELECT transaction_id
                        FROM semantic_operation_receipts
                        WHERE semantic_fingerprint = ?
                        """,
                        (private_operation.semantic_fingerprint,),
                    ).fetchone()
                    if semantic_receipt is not None:
                        return _operation_committed_response()
            if (
                self._degraded_error is not None
                and (
                    (domain, action)
                    not in (
                        _TIMEZONE_DEGRADED_ALLOWED_ACTIONS
                        if self._timezone_degraded
                        else _DEGRADED_ALLOWED_ACTIONS
                    )
                )
            ):
                raise self._degraded_error
            handler = HANDLERS.get(domain)
            if handler is None:
                raise _ServiceError("INVALID_INPUT", f"Unknown domain: {domain!r}")
            with use_clock(self._clock), operation_context(private_operation):
                result = handler(self, action, payload, context)
            if not isinstance(result, _HandlerResult):
                result = _HandlerResult(
                    _result_mapping(result),
                    outcome=_default_outcome(domain, action),
                )
            elif result.outcome is None:
                result = _HandlerResult(
                    result.data,
                    outcome=_default_outcome(
                        domain,
                        action,
                        requires_confirmation=result.requires_confirmation,
                    ),
                    warnings=result.warnings,
                    requires_confirmation=result.requires_confirmation,
                    confirmation_options=result.confirmation_options,
                )
            return _success_response(result)
        except OperationAlreadyCommitted:
            return _operation_committed_response()
        except Exception as error:
            mapped = _mapped_error(error)
            if mapped is None:
                LOGGER.exception("Unexpected diet service failure")
                return internal_error_response()
            return error_response(
                mapped.code,
                str(mapped),
                requires_confirmation=mapped.requires_confirmation,
                confirmation_options=mapped.confirmation_options,
                field=mapped.field,
                reason=mapped.reason,
                expected=mapped.expected,
                retryable=mapped.retryable,
            )

    def _operation_status(self, value: Any) -> Mapping[str, Any]:
        operation_id, expected_fingerprint = _validated_status_request(value)
        try:
            receipt = self.connection.execute(
                """
                SELECT request_fingerprint
                FROM operation_receipts
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        except sqlite3.DatabaseError:
            return _success_response(
                _HandlerResult(
                    {"status": "unknown"}, outcome="read_completed"
                )
            )
        if receipt is None:
            return _success_response(
                _HandlerResult(
                    {"status": "absent"}, outcome="read_completed"
                )
            )
        if not secrets.compare_digest(
            receipt["request_fingerprint"], expected_fingerprint
        ):
            raise OperationFingerprintConflict(
                "The internal operation status fingerprint does not match"
            )
        return _operation_committed_response()


def meal_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(_MEAL_ACTIONS, service, action, payload, context, "meal")


def water_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(_WATER_ACTIONS, service, action, payload, context, "water")


def weight_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(
        _WEIGHT_ACTIONS,
        service,
        action,
        payload,
        context,
        "weight",
    )


def pantry_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(_PANTRY_ACTIONS, service, action, payload, context, "pantry")


def transaction_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(
        _TRANSACTION_ACTIONS, service, action, payload, context, "transaction"
    )


def report_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(_REPORT_ACTIONS, service, action, payload, context, "report")


def system_handler(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _dispatch_action(_SYSTEM_ACTIONS, service, action, payload, context, "system")


HANDLERS: dict[
    str,
    Callable[
        [DietService, str, Mapping[str, Any], Mapping[str, Any]],
        Mapping[str, Any] | _HandlerResult,
    ],
] = {
    "meal": meal_handler,
    "water": water_handler,
    "weight": weight_handler,
    "pantry": pantry_handler,
    "transaction": transaction_handler,
    "report": report_handler,
    "system": system_handler,
}


def _dispatch_action(
    actions: Mapping[
        str,
        Callable[
            [DietService, Mapping[str, Any], Mapping[str, Any]],
            Mapping[str, Any] | _HandlerResult,
        ],
    ],
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    domain: str,
) -> Mapping[str, Any] | _HandlerResult:
    function = actions.get(action)
    if function is None:
        raise _ServiceError(
            "INVALID_INPUT", f"Unknown action {action!r} for domain {domain!r}"
        )
    return function(service, payload, context)


def _meal_preview(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    draft: meals.MealDraft | None = None,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    operation_now = now if now is not None else _operation_now(payload, context)
    meal_draft = draft or _meal_draft(service, payload, now=operation_now)
    preview = meals.preview_meal(
        service.connection,
        meal_draft,
        now=operation_now,
        settings=service.settings,
    )
    resolution = _quantity_resolution(service, meal_draft)
    data: dict[str, Any] = {"preview": preview}
    if resolution is not None:
        data["resolution"] = resolution
    threshold = Decimal(
        str(service.settings.behavior.inventory.ask_below_confidence)
    )
    if preview.confidence < threshold or preview.confirmation_reasons:
        nutrition_unknown = (
            meals.ConfirmationReason.NUTRITION_UNKNOWN
            in preview.confirmation_reasons
        )
        return _HandlerResult(
            data,
            warnings=(
                (
                    "The quantity is an estimate. Review its range and confirm "
                    "before recording."
                    if resolution is not None
                    else (
                        "The inventory portion is known, but nutrition is unavailable. "
                        "Provide the label or confirm recording with nutrition unknown."
                    )
                    if nutrition_unknown
                    else "The meal details are uncertain and need confirmation before recording."
                ),
            ),
            requires_confirmation=True,
            confirmation_options=(
                (
                    {
                        "label": "Record intake and inventory change with nutrition unknown",
                        "needs_confirmation": True,
                    }
                    if nutrition_unknown
                    else {
                        "label": "Review and confirm the meal details before recording",
                        "needs_confirmation": True,
                    }
                ),
            ),
        )
    return data


def _meal_record(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    draft = _meal_draft(service, payload, now=now)
    existing = meals.existing_meal_for_draft(service.connection, draft)
    if existing is not None:
        return _HandlerResult(
            _meal_commit_payload(service, existing),
            outcome="no_op",
        )
    preview_result = _meal_preview(
        service, payload, context, draft=draft, now=now
    )
    if isinstance(preview_result, _HandlerResult):
        preview = preview_result.data["preview"]
        if not isinstance(preview, meals.MealPreview):
            raise ValueError("meal preview result is invalid")
        hard_confirmation_reasons = tuple(
            reason
            for reason in preview.confirmation_reasons
            if reason
            not in {
                meals.ConfirmationReason.NUTRITION_ESTIMATE_REQUIRED,
                meals.ConfirmationReason.OTHER_LOW_CONFIDENCE,
            }
        )
        if hard_confirmation_reasons:
            return preview_result
        return _meal_commit(
            service,
            {"commit_handle": preview.token, "confirmed": True},
            context,
            now=now,
        )
    preview = preview_result["preview"]
    if not isinstance(preview, meals.MealPreview):
        raise ValueError("meal preview result is invalid")
    return _meal_commit(
        service,
        {"commit_handle": preview.token},
        context,
        now=now,
    )


def _meal_record_cooking(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    result = meals.record_cooking(
        service.connection,
        _cooking_draft(service, payload, now=now),
        now=now,
        settings=service.settings,
    )
    return _meal_commit_payload(service, result)


def _meal_record_prepared(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    reference = _prepared_food_reference(service, payload, now=now)
    result = meals.record_prepared(
        service.connection,
        TransactionManager(service.connection),
        reference=reference,
        quantity=_optional_decimal(payload.get("quantity"), "quantity"),
        unit=_optional_text(payload.get("unit"), "unit"),
        source_text=_required_text(payload, "source_text"),
        occurred_at=(
            _datetime_value(payload["occurred_at"], "occurred_at")
            if "occurred_at" in payload
            else now
        ),
        meal_type=_optional_text(payload.get("meal_type"), "meal_type"),
        now=now,
        settings=service.settings,
    )
    return _meal_commit_payload(service, result)


def _meal_commit_payload(
    service: DietService,
    result: meals.MealCommitResult,
) -> Mapping[str, Any]:
    goal_profile = goal_profiles.load_goal_profile(service.connection)
    snapshot = progress.daily_progress_snapshot(
        service.connection,
        occurred_at=result.meal.occurred_at,
        goal_profile=goal_profile,
        increment=progress.increment_from_meal(result.meal),
    )
    public_meal = (
        _meal_public(service, result.meal_id, result.meal, now=service._clock())
        if result.meal_id is not None
        else _meal_record_public(service, result.meal)
    )
    return {
        "meal": public_meal,
        "inventory_effects": result.inventory_effects,
        "daily_progress": snapshot.metrics,
        "rendered_receipt": progress_receipt.render_meal_receipt(
            result.meal,
            inventory_effects=result.inventory_effects,
            metrics=snapshot.metrics,
            goals_confirmed=goal_profile.confirmed,
        ),
    } | goal_profiles.public_provenance(goal_profile)


def _meal_correction_payload(
    service: DietService,
    result: meals.MealCommitResult,
    *,
    previous: meals.MealRecord,
) -> Mapping[str, Any]:
    goal_profile = goal_profiles.load_goal_profile(service.connection)
    increment = progress.NutritionIncrement(
        calories=_nutrition_delta(previous.total_calories, result.meal.total_calories),
        protein=_nutrition_delta(previous.total_protein, result.meal.total_protein),
        fat=_nutrition_delta(previous.total_fat, result.meal.total_fat),
        carbohydrate=_nutrition_delta(
            previous.total_carbohydrate, result.meal.total_carbohydrate
        ),
        fiber=_nutrition_delta(previous.total_fiber, result.meal.total_fiber),
        water_ml=_nutrition_delta(
            previous.total_hydration_ml, result.meal.total_hydration_ml
        ),
    )
    snapshot = progress.daily_progress_snapshot(
        service.connection,
        occurred_at=result.meal.occurred_at,
        goal_profile=goal_profile,
        increment=increment,
    )
    public_meal = (
        _meal_public(service, result.meal_id, result.meal, now=service._clock())
        if result.meal_id is not None
        else _meal_record_public(service, result.meal)
    )
    return {
        "meal": public_meal,
        "inventory_effects": result.inventory_effects,
        "daily_progress": snapshot.metrics,
        "rendered_receipt": progress_receipt.render_meal_receipt(
            result.meal,
            inventory_effects=result.inventory_effects,
            metrics=snapshot.metrics,
            goals_confirmed=goal_profile.confirmed,
            verb="更新",
        ),
    } | goal_profiles.public_provenance(goal_profile)


def _nutrition_delta(
    previous: Decimal | None, current: Decimal | None
) -> Decimal | None:
    if previous is None and current is None:
        return None
    return (current or Decimal("0")) - (previous or Decimal("0"))


def _meal_commit(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    token = _workflow_handle_text(payload, "commit_handle")
    operation_now = now if now is not None else _operation_now(payload, context)
    workflow_type = service.connection.execute(
        "SELECT operation_type, request_json FROM operation_previews WHERE token_hash = ?",
        (_workflow_hash(token),),
    ).fetchone()
    if workflow_type is not None and workflow_type["operation_type"] == "meal_preview":
        workflow_request = _stored_object(
            workflow_type["request_json"], "stored meal workflow"
        )
        if workflow_request.get("workflow_kind") == "update":
            return _meal_commit_update(service, token, now=operation_now)
    quantity_estimates = meals.preview_quantity_estimates(
        service.connection, token
    )
    result = meals.commit_meal(
        service.connection,
        token,
        now=operation_now,
        minimum_confidence=Decimal(
            str(service.settings.behavior.inventory.ask_below_confidence)
        ),
        confirmed=_optional_bool(
            payload.get("confirmed"), "confirmed", default=False
        ),
        deduction_strategy=service.settings.behavior.inventory.deduction_strategy,
    )
    public = dict(_meal_commit_payload(service, result))
    if quantity_estimates:
        public["resolution"] = _confirmed_quantity_resolution(
            quantity_estimates
        )
    return public


def _meal_query(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    window = temporal.resolve_query_window(
        payload,
        now=now,
        timezone_name=service.settings.profile.timezone,
        policies=service.policies,
    )
    result: dict[str, Any] = {
        "meals": tuple(
            _meal_public(service, meal_id, meal, now=now)
            for meal_id, meal in meals._query_meal_targets(
                service.connection,
                start_utc=window.start_utc if window is not None else None,
                end_utc=window.end_utc if window is not None else None,
                meal_type=_optional_text(payload.get("meal_type"), "meal_type"),
                timezone_name=service.settings.profile.timezone,
            )
        )
    }
    if window is not None:
        result["scope"] = window.public_scope()
    return result


def _meal_preview_update(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any] | _HandlerResult:
    """Validate a full correction and freeze it behind one opaque handle."""

    now = _operation_now(payload, context)
    draft_values = dict(_required_mapping(payload, "draft"))
    meal_id, selector, expected_state = _meal_target(service, payload, now=now)
    if "occurred_at" not in draft_values:
        draft_values["occurred_at"] = selector.occurred_at.isoformat()
    is_cooking = "dish" in draft_values
    if is_cooking:
        if "items" in draft_values:
            raise ValueError("meal update draft cannot contain both dish and items")
        preview = meals.preview_cooking_update(
            service.connection,
            _cooking_draft(service, draft_values, now=now),
            replacing_meal_id=meal_id,
            now=now,
            settings=service.settings,
        )
    else:
        preview = meals.preview_meal_update(
            service.connection,
            _meal_draft(service, draft_values, now=now),
            replacing_meal_id=meal_id,
            now=now,
            settings=service.settings,
        )
    handle = _issue_workflow(
        service,
        "meal_preview",
        request={
            "workflow_kind": "update",
            "meal_id": meal_id,
            "selector": {
                "occurred_at": selector.occurred_at,
                "source_text": selector.source_text,
            },
            "draft": draft_values,
            "is_cooking": is_cooking,
        },
        result={"preview": _public_value(preview)},
        resource_versions={
            "updated_at": expected_state[0],
            "deleted_at": expected_state[1],
        },
        now=now,
    )
    public_preview = replace(preview, token=handle)
    return _HandlerResult(
        {"preview": public_preview},
        warnings=(
            "Review the corrected quantity, nutrition, time, and inventory effect before committing.",
        ),
        requires_confirmation=True,
        confirmation_options=(
            {
                "label": "Confirm this correction",
                "needs_confirmation": True,
            },
        ),
    )


def _meal_commit_update(
    service: DietService,
    handle: str,
    *,
    now: datetime,
) -> Mapping[str, Any]:
    row = _workflow_row(
        service.connection,
        handle,
        "meal_preview",
        now=now,
        allow_consumed=True,
    )
    if row["consumed_at"] is not None:
        stored = _stored_object(row["result_json"], "stored meal correction")
        return _stored_object(
            _canonical_json(stored.get("committed_payload")),
            "stored meal correction payload",
        )
    request = _stored_object(row["request_json"], "stored meal correction request")
    expected = _stored_object(
        row["resource_versions_json"], "stored meal correction resource"
    )
    meal_id = _positive_integer(request.get("meal_id"), "stored meal target")
    current = service.connection.execute(
        "SELECT updated_at, deleted_at FROM meals WHERE id = ?", (meal_id,)
    ).fetchone()
    if current is None or {
        "updated_at": current["updated_at"],
        "deleted_at": current["deleted_at"],
    } != dict(expected):
        raise _ServiceError("STALE_PREVIEW", "Meal correction reference is stale")
    selector_values = _mapping_value(request.get("selector"), "stored meal selector")
    selector = meals.MealSelector(
        occurred_at=_datetime_value(
            selector_values.get("occurred_at"), "stored meal occurred_at"
        ),
        source_text=_required_text(selector_values, "source_text"),
    )
    draft_values = dict(_mapping_value(request.get("draft"), "stored meal draft"))
    previous = meals._read_meal(service.connection, meal_id)
    expected_state = (
        _required_text(expected, "updated_at"),
        _optional_text(expected.get("deleted_at"), "deleted_at"),
    )
    if request.get("is_cooking") is True:
        result = meals.update_cooking(
            service.connection,
            selector,
            _cooking_draft(service, draft_values, now=now),
            now=now,
            settings=service.settings,
            _meal_id=meal_id,
            _expected_state=expected_state,
        )
    else:
        result = meals.update_meal(
            service.connection,
            selector,
            _meal_draft(service, draft_values, now=now),
            now=now,
            settings=service.settings,
            _meal_id=meal_id,
            _expected_state=expected_state,
            _confirmed=True,
        )
    public = _meal_correction_payload(service, result, previous=previous)
    changed = service.connection.execute(
        """
        UPDATE operation_previews
        SET result_json = ?, consumed_at = ?
        WHERE token_hash = ? AND consumed_at IS NULL
        """,
        (
            _canonical_json({"committed_payload": _public_value(public)}),
            _workflow_timestamp(now),
            row["token_hash"],
        ),
    ).rowcount
    if changed != 1:
        service.connection.rollback()
        raise _ServiceError("STALE_PREVIEW", "Meal correction reference is stale")
    service.connection.commit()
    return public


def _meal_update(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    if payload.get("_preview_only") is True:
        return _meal_preview_update(service, payload, context)
    now = _operation_now(payload, context)
    draft_values = dict(_required_mapping(payload, "draft"))
    meal_id, selector, expected_state = _meal_target(
        service, payload, now=now
    )
    previous = meals._read_meal(service.connection, meal_id)
    if "occurred_at" not in draft_values:
        draft_values["occurred_at"] = selector.occurred_at.isoformat()
    if "dish" in draft_values:
        if "items" in draft_values:
            raise ValueError("meal update draft cannot contain both dish and items")
        draft_values.setdefault("meal_type", previous.meal_type)
        draft_values.setdefault("source_text", previous.source_text)
        result = meals.update_cooking(
            service.connection,
            selector,
            _cooking_draft(service, draft_values, now=now),
            now=now,
            settings=service.settings,
            _meal_id=meal_id,
            _expected_state=expected_state,
        )
    else:
        draft_values.setdefault("meal_type", previous.meal_type)
        draft_values.setdefault("source_text", previous.source_text)
        draft_values.setdefault("location_type", previous.location_type)
        _inherit_correction_portion_expressions(draft_values, previous)
        result = meals.update_meal(
            service.connection,
            selector,
            _meal_draft(service, draft_values, now=now),
            now=now,
            settings=service.settings,
            _meal_id=meal_id,
            _expected_state=expected_state,
            _confirmed=payload.get("meal_handle") is not None,
        )
    return _meal_correction_payload(service, result, previous=previous)


def _inherit_correction_portion_expressions(
    draft_values: dict[str, Any], previous: meals.MealRecord
) -> None:
    """Hydrate weight-only corrections from one uniquely matching stored item."""

    item_values = draft_values.get("items")
    if not isinstance(item_values, Sequence) or isinstance(
        item_values, (str, bytes, bytearray)
    ):
        return
    source_text = draft_values.get("source_text")
    if not isinstance(source_text, str):
        return

    previous_by_name: dict[str, list[meals.MealItem]] = {}
    for item in previous.items:
        previous_by_name.setdefault(item.normalized_name.casefold(), []).append(item)

    hydrated: list[Any] = []
    for raw_item in item_values:
        if not isinstance(raw_item, Mapping):
            hydrated.append(raw_item)
            continue
        item = dict(raw_item)
        normalized_name = item.get("normalized_name")
        matches = (
            previous_by_name.get(normalized_name.casefold(), [])
            if isinstance(normalized_name, str) and normalized_name.strip()
            else []
        )
        if len(matches) == 1:
            consumed_weight_g = _optional_decimal(
                item.get("consumed_weight_g"), "consumed_weight_g"
            )
            inherited = portion_evidence.inherit_previous_portion_expression(
                portion_expression=_optional_text(
                    item.get("portion_expression"), "portion_expression"
                ),
                previous_portion_expression=matches[0].portion_expression,
                consumed_weight_g=consumed_weight_g,
                source_text=source_text,
            )
            if inherited is not None:
                item["portion_expression"] = inherited
        hydrated.append(item)
    draft_values["items"] = hydrated


def _meal_delete(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    meal_id, selector, expected_state = _meal_target(
        service, payload, now=now
    )
    result = meals.delete_meal(
        service.connection,
        selector,
        intent=_optional_text(payload.get("intent"), "intent") or "record",
        now=now,
        source_text=(
            _optional_text(payload.get("source_text"), "source_text")
            or f"删除：{selector.source_text}"
        ),
        _meal_id=meal_id,
        _expected_state=expected_state,
    )
    goal_profile = goal_profiles.load_goal_profile(service.connection)
    increment = progress.NutritionIncrement(
        calories=_negative_nutrition(result.total_calories),
        protein=_negative_nutrition(result.total_protein),
        fat=_negative_nutrition(result.total_fat),
        carbohydrate=_negative_nutrition(result.total_carbohydrate),
        fiber=_negative_nutrition(result.total_fiber),
        water_ml=_negative_nutrition(result.total_hydration_ml),
    )
    snapshot = progress.daily_progress_snapshot(
        service.connection,
        occurred_at=result.occurred_at,
        goal_profile=goal_profile,
        increment=increment,
    )
    return {
        "meal": result,
        "inventory_effects": (),
        "daily_progress": snapshot.metrics,
        "rendered_receipt": progress_receipt.render_meal_receipt(
            result,
            inventory_effects=(),
            metrics=snapshot.metrics,
            goals_confirmed=goal_profile.confirmed,
            verb="删除",
        ),
    } | goal_profiles.public_provenance(goal_profile)


def _negative_nutrition(value: Decimal | None) -> Decimal | None:
    return -value if value is not None else None


def _meal_nutrition_estimate(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    repository = nutrition.NutritionRepository(
        service.rules_dir,
        service.connection,
        now=_operation_now(payload, context),
    )
    estimate_values = payload.get("estimate")
    estimate = (
        _nutrition_facts(_mapping_value(estimate_values, "estimate"))
        if estimate_values is not None
        else None
    )
    facts = repository.lookup(
        _required_text(payload, "normalized_name"),
        brand=_optional_text(payload.get("brand"), "brand"),
        estimate=estimate,
    )
    result = nutrition.calculate_nutrition(
        facts, _required_decimal(payload, "consumed_weight_g")
    )
    return {"nutrition": result}


def _meal_save_recipe(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    ingredients = payload.get("ingredients")
    if not isinstance(ingredients, Sequence) or isinstance(
        ingredients, (str, bytes, bytearray)
    ):
        raise ValueError("ingredients must be an array")
    recipe = recipes.save_recipe(
        service.connection,
        TransactionManager(service.connection),
        name=_required_text(payload, "name"),
        ingredients=ingredients,
        yield_quantity=_required_decimal(payload, "yield_quantity"),
        yield_unit=_required_text(payload, "yield_unit"),
        notes=_optional_text(payload.get("notes"), "notes"),
        source_text=_required_text(payload, "source_text"),
        now=_operation_now(payload, context),
    )
    return {"recipe": _public_value(recipe)}


def _recipe_candidates(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[recipes.RecipeCandidate, ...]:
    return recipes.suggest_recipes(
        service.connection,
        limit=_positive_integer(payload.get("limit", 3), "limit"),
        max_missing_items=_nonnegative_integer(
            payload.get("max_missing_items", 2),
            "max_missing_items",
        ),
        now=_operation_now(payload, context),
    )


def _meal_suggest_recipes(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "candidates": _public_value(
            _recipe_candidates(service, payload, context)
        )
    }


def _meal_preview_meal_plan(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "meal_type": _optional_text(payload.get("meal_type"), "meal_type"),
        "candidates": _public_value(
            _recipe_candidates(service, payload, context)
        ),
        "candidate_only": True,
    }


def _water_record(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    unit = _required_text(payload, "unit")
    result = water.record_water(
        service.connection,
        TransactionManager(service.connection),
        amount=_required_decimal(payload, "amount"),
        unit=unit,
        occurred_at=(
            _datetime_value(payload["occurred_at"], "occurred_at")
            if "occurred_at" in payload
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        settings=service.settings,
        learned_unit_ml=learning.learned_water_unit_milliliters(
            service.connection, subject=unit
        ),
    )
    return _water_result(
        service,
        result,
        now=now,
        increment_ml=Decimal(result.amount_ml),
        receipt_verb="记录",
    )


def _water_query(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    window = temporal.resolve_query_window(
        payload,
        now=now,
        timezone_name=service.settings.profile.timezone,
        policies=service.policies,
    )
    if window is None:
        raise temporal.TemporalValidationError(
            "water query requires one temporal query mode"
        )
    result = water.query_water(
        service.connection,
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        timezone_name=service.settings.profile.timezone,
    )
    return {
        "summary": {
            "occurred_on": _optional_date(
                payload.get("occurred_on"), "occurred_on"
            ),
            "total_ml": result.total_ml,
            "records": tuple(
                _water_public(service, record, now=now) for record in result.records
            ),
        },
        "scope": window.public_scope(),
    }


def _water_update(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    water_id, expected_state = _water_id(service, payload, now=now)
    previous_amount_ml = int(
        service.connection.execute(
            "SELECT amount_ml FROM water_logs WHERE id = ?", (water_id,)
        ).fetchone()[0]
    )
    unit = _required_text(payload, "unit")
    result = water.update_water(
        service.connection,
        TransactionManager(service.connection),
        water_id=water_id,
        amount=_required_decimal(payload, "amount"),
        unit=unit,
        occurred_at=_required_datetime(payload, "occurred_at"),
        source_text=_required_text(payload, "source_text"),
        settings=service.settings,
        learned_unit_ml=learning.learned_water_unit_milliliters(
            service.connection, subject=unit
        ),
        _expected_state=expected_state,
    )
    return _water_result(
        service,
        result,
        now=now,
        increment_ml=Decimal(result.amount_ml - previous_amount_ml),
        receipt_verb="更新",
    )


def _water_delete(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    water_id, expected_state = _water_id(service, payload, now=now)
    previous_amount_ml = int(
        service.connection.execute(
            "SELECT amount_ml FROM water_logs WHERE id = ?", (water_id,)
        ).fetchone()[0]
    )
    deleted_value = payload.get("deleted_at")
    result = water.delete_water(
        service.connection,
        TransactionManager(service.connection),
        water_id=water_id,
        deleted_at=(
            _datetime_value(deleted_value, "deleted_at")
            if deleted_value is not None
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        _expected_state=expected_state,
    )
    return _water_result(
        service,
        result,
        now=now,
        increment_ml=-Decimal(previous_amount_ml),
        receipt_verb="删除",
    )


def _weight_record(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    result = body_weight.record_body_weight(
        service.connection,
        TransactionManager(service.connection),
        weight=_required_decimal(payload, "weight"),
        unit=_optional_text(payload.get("unit"), "unit") or "kg",
        measured_at=now,
        status_note=_optional_status_note(
            payload.get("status_note"),
            "status_note",
        ),
    )
    return _weight_result(service, result, now=now)


def _weight_query(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    window = temporal.resolve_query_window(
        payload,
        now=now,
        timezone_name=service.settings.profile.timezone,
        policies=service.policies,
    )
    limit_value = payload.get("limit", 20)
    limit = _positive_integer(limit_value, "limit")
    summary = body_weight.query_body_weight(
        service.connection,
        now=now,
        start_utc=window.start_utc if window is not None else None,
        end_utc=window.end_utc if window is not None else None,
        limit=limit,
    )
    result: dict[str, Any] = {
        "summary": _weight_summary_public(
            service,
            summary,
            now=now,
            include_records=True,
        )
    }
    if window is not None:
        result["scope"] = window.public_scope()
    return result


def _weight_update(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    weight_id, expected_version = _weight_id(
        service,
        payload,
        now=now,
    )
    has_weight = "weight" in payload
    has_status = "status_note" in payload
    if not has_weight and not has_status:
        raise body_weight.BodyWeightValidationError(
            "weight or status_note is required"
        )
    if not has_weight and "unit" in payload:
        raise body_weight.BodyWeightValidationError(
            "unit requires weight"
        )
    current = body_weight.get_body_weight(
        service.connection,
        weight_id=weight_id,
    )
    result = body_weight.update_body_weight(
        service.connection,
        TransactionManager(service.connection),
        weight_id=weight_id,
        weight=(
            _required_decimal(payload, "weight")
            if has_weight
            else current.weight_kg
        ),
        unit=(
            _optional_text(payload.get("unit"), "unit") or "kg"
            if has_weight
            else "kg"
        ),
        status_note=(
            _optional_status_note(payload.get("status_note"), "status_note")
            if has_status
            else current.status_note
        ),
        changed_at=now,
        _expected_version=expected_version,
    )
    return _weight_result(service, result, now=now)


def _weight_delete(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    has_record_handle = payload.get("record_handle") is not None
    has_commit_handle = payload.get("commit_handle") is not None
    if has_record_handle == has_commit_handle:
        raise ValueError(
            "exactly one of record_handle or commit_handle is required"
        )

    if has_record_handle:
        weight_id, expected_version = _weight_id(
            service,
            payload,
            now=now,
        )
        current = body_weight.get_body_weight(
            service.connection,
            weight_id=weight_id,
        )
        preview = _weight_record_public(service, current)
        commit_handle = _issue_workflow(
            service,
            "weight_reference",
            request={
                "action": "confirm_weight_delete",
                "weight_id": weight_id,
            },
            result={"preview": preview},
            resource_versions={"version": expected_version},
            now=now,
        )
        return _HandlerResult(
            {
                "preview": preview,
                "workflow": {"commit_handle": commit_handle},
            },
            outcome="preview_ready",
            warnings=(
                "Confirm the exact weight and measurement time before deletion.",
            ),
            requires_confirmation=True,
            confirmation_options=(
                {
                    "label": "Confirm this weight deletion",
                    "needs_confirmation": True,
                },
            ),
        )

    reference = _workflow_row(
        service.connection,
        _required_text(payload, "commit_handle"),
        "weight_reference",
        now=now,
    )
    request = _stored_object(
        reference["request_json"],
        "stored body-weight deletion request",
    )
    if request.get("action") != "confirm_weight_delete":
        raise _ServiceError(
            "STALE_PREVIEW",
            "Body-weight deletion preview is stale",
        )
    weight_id = _positive_integer(
        request.get("weight_id"),
        "stored body-weight deletion target",
    )
    expected = _stored_object(
        reference["resource_versions_json"],
        "stored body-weight deletion state",
    )
    expected_version = _positive_integer(
        expected.get("version"),
        "stored body-weight deletion version",
    )
    current = body_weight.get_body_weight(
        service.connection,
        weight_id=weight_id,
    )
    if current.version != expected_version:
        raise _ServiceError(
            "STALE_PREVIEW",
            "Body-weight deletion preview is stale",
        )
    result = body_weight.delete_body_weight(
        service.connection,
        TransactionManager(service.connection),
        weight_id=weight_id,
        deleted_at=now,
        _expected_version=expected_version,
    )
    public_result = _weight_result(service, result, now=now)
    try:
        _consume_workflow_reference(
            service.connection,
            reference["token_hash"],
            now=now,
        )
    except Exception:
        LOGGER.warning("Post-commit weight deletion workflow consumption failed")
        _cleanup_post_commit_workflow_failure(service)
        data = (
            public_result.data
            if isinstance(public_result, _HandlerResult)
            else public_result
        )
        return _HandlerResult(
            data,
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    return public_result


def _pantry_preview_add(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    normalized_payload = {
        key: item
        for key, item in payload.items()
        if key not in _PANTRY_PACKAGE_FIELDS
    }
    _pantry_add_arguments(
        normalized_payload,
        timezone_name=service.settings.profile.timezone,
    )
    preview = _public_value(normalized_payload)
    handle = _issue_workflow(
        service,
        "pantry_add_preview",
        request=normalized_payload,
        result={"preview": preview},
        resource_versions=(),
        now=_operation_now(normalized_payload, context),
    )
    return {
        "preview": preview,
        "workflow": {"commit_handle": handle},
    }


def _pantry_add(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    arguments = _pantry_add_arguments(
        payload,
        timezone_name=service.settings.profile.timezone,
    )
    profile_value = payload.get("nutrition_profile")
    profile = (
        _nutrition_profile_draft(
            _mapping_value(profile_value, "nutrition_profile")
        )
        if profile_value is not None
        else None
    )
    linked_at = _operation_now(payload, context)

    def mutate(mutation_context):
        batch_id, batch = pantry._add_batch_record_in_context(
            mutation_context,
            **arguments,
        )
        if profile is not None:
            nutrition_profiles._create_and_link_in_context(
                service.connection,
                mutation_context,
                pantry_batch_id=batch_id,
                draft=profile,
                linked_at=linked_at,
            )
        return {
            "batch": _pantry_batch_value(service, batch),
            "nutrition_linked": profile is not None,
        }

    return TransactionManager(service.connection).execute(
        "pantry_add",
        _required_text(payload, "source_text"),
        mutate,
    ).value


def _pantry_commit_add(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    handle = _required_text(payload, "commit_handle")
    now = _operation_now(payload, context)
    preview = _workflow_row(
        service.connection,
        handle,
        "pantry_add_preview",
        now=now,
        allow_consumed=True,
    )
    if preview["consumed_at"] is not None:
        return _stored_object(preview["result_json"], "stored pantry add result")

    request = _stored_object(preview["request_json"], "stored pantry add request")
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mutate(mutation_context):
        current = _workflow_row(
            service.connection,
            handle,
            "pantry_add_preview",
            now=now,
            allow_consumed=True,
        )
        if current["consumed_at"] is not None:
            raise _WorkflowConsumedRace
        result = pantry._add_batch_in_context(
            mutation_context,
            **_pantry_add_arguments(
                request,
                timezone_name=service.settings.profile.timezone,
            ),
        )
        public_result = {"batch": _pantry_batch_value(service, result)}
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace
        return public_result

    try:
        result = TransactionManager(service.connection).execute(
            "pantry_add",
            _required_text(request, "source_text"),
            mutate,
            internal_id=transaction_id,
        )
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "pantry_add_preview",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError("STALE_PREVIEW", "Pantry add preview is stale")
        return _stored_object(raced["result_json"], "stored pantry add result")
    return result.value


def _pantry_query(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    statuses_value = payload.get("statuses")
    statuses: tuple[str, ...] | None = None
    if statuses_value is not None:
        statuses = tuple(_text_sequence(statuses_value, "statuses"))
    normalized_name = _optional_text(
        payload.get("normalized_name"), "normalized_name"
    )
    include_details = _optional_bool(
        payload.get("include_details"), "include_details", default=False
    )
    missing_expiry_only = _optional_bool(
        payload.get("missing_expiry_only"),
        "missing_expiry_only",
        default=False,
    )
    if include_details and normalized_name is None:
        raise ValueError(
            "include_details requires one normalized_name"
        )
    limit = _positive_integer(payload.get("limit", 20), "limit")
    if limit > 20:
        raise ValueError("limit must be at most 20")
    offset = _nonnegative_integer(payload.get("offset", 0), "offset")
    now = _operation_now(payload, context)
    targets = pantry._query_batch_targets(
        service.connection,
        normalized_name=normalized_name,
        statuses=statuses,
        missing_expiry_only=missing_expiry_only,
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(targets) > limit
    page = targets[:limit]
    return {
        "batches": tuple(
            (
                _pantry_public(service, batch_id, batch, now=now)
                if include_details
                else _pantry_compact(
                    batch,
                    now=now,
                    timezone_name=service.settings.profile.timezone,
                )
            )
            for batch_id, batch in page
        ),
        "returned_count": len(page),
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


_PANTRY_SEARCH_DEFAULT_STATUSES = ("active", "opened", "thawed")
_PANTRY_SEARCH_SUMMARY_FIELDS = (
    "calories_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
)


def _pantry_search(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    mode = _optional_text(payload.get("nutrition_mode"), "nutrition_mode") or "none"
    if mode not in {"none", "summary", "full"}:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field="nutrition_mode",
            reason="unsupported_value",
            expected="none, summary, or full",
            retryable=True,
        )
    limit = _positive_integer(payload.get("limit", 5), "limit")
    if limit > 5:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field="limit",
            reason="out_of_range",
            expected="an integer from 1 to 5",
            retryable=True,
        )
    statuses = (
        tuple(_text_sequence(payload["statuses"], "statuses"))
        if "statuses" in payload
        else _PANTRY_SEARCH_DEFAULT_STATUSES
    )
    if not statuses:
        raise ValueError("statuses must contain at least one value")
    now = _operation_now(payload, context)
    candidates = inventory_matching.search_inventory_candidates(
        service.connection,
        _required_text(payload, "search_text"),
        unit=_optional_text(payload.get("unit"), "unit"),
        statuses=statuses,
        storage_location=_optional_text(
            payload.get("storage_location"), "storage_location"
        ),
        limit=limit,
        allow_expired_fallback=True,
    )
    return {
        "candidates": tuple(
            _pantry_search_candidate_public(
                service,
                item,
                mode=mode,
                statuses=statuses,
                now=now,
            )
            for item in candidates
        ),
        "returned_count": len(candidates),
    }


def _pantry_search_candidate_public(
    service: DietService,
    candidate: inventory_matching.InventorySearchCandidate,
    *,
    mode: str,
    statuses: tuple[str, ...],
    now: datetime,
) -> Mapping[str, Any]:
    nutrition_projection = nutrition_profiles.linked_product_nutrition(
        service.connection,
        normalized_name=candidate.normalized_name,
        unit=candidate.unit,
        statuses=statuses,
        batch_ids=candidate.batch_ids,
    )
    package_projection = _pantry_package_projection(
        service,
        candidate,
        statuses=statuses,
    )
    handle = _issue_workflow(
        service,
        "pantry_product_reference",
        request={"action": "select_pantry_product"},
        result={
            "normalized_name": candidate.normalized_name,
            "base_unit": candidate.unit,
            "package": _pantry_package_projection_payload(
                package_projection
            ),
            "nutrition": _pantry_nutrition_projection_payload(
                nutrition_projection
            ),
            "usable_for_consumption": not candidate.expired_only,
        },
        resource_versions=[
            {"batch_id": batch_id, "version": version}
            for batch_id, version in package_projection.resource_versions
        ],
        now=now,
    )
    public: dict[str, Any] = {
        "food_name": candidate.food_name,
        "normalized_name": candidate.normalized_name,
        "unit": candidate.unit,
        "available_quantity": candidate.available_quantity,
        "batch_count": candidate.batch_count,
        "match_kind": candidate.match_kind,
        "match_method": candidate.match_kind,
        "match_rank": candidate.match_rank,
        "availability": (
            "expired_only" if candidate.expired_only else "usable"
        ),
        "nutrition_available": (
            None
            if mode == "none"
            else nutrition_projection.status == "uniform"
        ),
        "nutrition_status": (
            "not_requested"
            if mode == "none"
            else nutrition_projection.status
        ),
        "workflow": {"inventory_match_handle": handle},
    } | _inventory_lineage_projection(
        service,
        candidate,
        statuses=statuses,
    )
    if package_projection.status == "uniform":
        public.update(
            {
                "remaining_display_quantity": (
                    package_projection.remaining_display_quantity
                ),
                "display_unit": package_projection.display_unit,
                "base_quantity_per_display_unit": (
                    package_projection.base_quantity_per_display_unit
                ),
                "package_hierarchy": package_projection.package_hierarchy,
            }
        )
    prepared_handle = _prepared_food_candidate_handle(
        service,
        candidate,
        statuses=statuses,
        now=now,
    )
    if prepared_handle is not None:
        public["workflow"]["prepared_food_handle"] = prepared_handle
    if mode != "none" and nutrition_projection.nutrition is not None:
        nutrition = (
            {
                field: nutrition_projection.nutrition.get(field)
                for field in _PANTRY_SEARCH_SUMMARY_FIELDS
            }
            if mode == "summary"
            else dict(nutrition_projection.nutrition)
        )
        public["nutrition"] = {
            "serving_basis": nutrition_projection.serving_basis,
            "source_grade": nutrition_projection.source_grade,
            **nutrition,
        }
    return public


def _inventory_lineage_projection(
    service: DietService,
    candidate: inventory_matching.InventorySearchCandidate,
    *,
    statuses: tuple[str, ...],
) -> Mapping[str, Any]:
    placeholders = ", ".join("?" for _ in statuses)
    batch_placeholders = ", ".join("?" for _ in candidate.batch_ids)
    row = service.connection.execute(
        f"""
        SELECT
            COUNT(*) AS batch_count,
            SUM(CASE WHEN pfp.id IS NOT NULL THEN 1 ELSE 0 END)
                AS prepared_count,
            SUM(CASE
                WHEN pfp.id IS NOT NULL
                 AND pfp.source_meal_id = pb.source_meal_id
                 AND tx.status = 'committed'
                THEN 1 ELSE 0 END
            ) AS committed_relation_count
        FROM pantry_batches AS pb
        LEFT JOIN prepared_food_profiles AS pfp
          ON pfp.pantry_batch_id = pb.id
        LEFT JOIN transactions AS tx
          ON tx.id = pfp.transaction_id
        WHERE pb.normalized_name = ?
          AND lower(pb.unit) = lower(?)
          AND pb.remaining_quantity > 0
          AND pb.status IN ({placeholders})
          AND pb.id IN ({batch_placeholders})
        """,
        (
            candidate.normalized_name,
            candidate.unit,
            *statuses,
            *candidate.batch_ids,
        ),
    ).fetchone()
    prepared_count = int(row["prepared_count"] or 0)
    relation_count = int(row["committed_relation_count"] or 0)
    kind_key = "kind.prepared_food" if prepared_count else "kind.raw_food"
    kind = _registered_inventory_value(
        service,
        kind_key,
        operator="inventory_kind",
        field="public_name",
    )
    relations: list[dict[str, str]] = []
    if relation_count:
        entry = service.policies.entry(
            "inventory-relations",
            "relation.prepared_from_cooking",
        )
        if entry.operator != "provenance_relation":
            raise ConfigurationError(
                "prepared cooking relation has an invalid operator"
            )
        evidence_type = entry.values.get("evidence_type")
        summary = entry.values.get("public_summary")
        if (
            not isinstance(evidence_type, str)
            or not evidence_type
            or not isinstance(summary, str)
            or not summary
        ):
            raise ConfigurationError(
                "prepared cooking relation has invalid public values"
            )
        relations.append(
            {
                "relation_type": "prepared_from_cooking",
                "evidence_type": evidence_type,
                "summary": summary,
            }
        )
    return {"inventory_kind": kind, "relations": relations}


def _registered_inventory_value(
    service: DietService,
    policy_key: str,
    *,
    operator: str,
    field: str,
) -> str:
    entry = service.policies.entry("inventory-relations", policy_key)
    value = entry.values.get(field)
    if entry.operator != operator or not isinstance(value, str) or not value:
        raise ConfigurationError(
            f"inventory policy is invalid: {policy_key}"
        )
    return value


@dataclass(frozen=True)
class _PantryPackageProjection:
    status: str
    remaining_display_quantity: Decimal | None
    display_unit: str | None
    base_quantity_per_display_unit: Decimal | None
    package_hierarchy: tuple[Mapping[str, str], ...] | None
    resource_versions: tuple[tuple[int, int], ...]


def _pantry_package_projection(
    service: DietService,
    candidate: inventory_matching.InventorySearchCandidate,
    *,
    statuses: tuple[str, ...],
) -> _PantryPackageProjection:
    eligible_ids = set(candidate.batch_ids)
    targets = tuple(
        (batch_id, batch)
        for batch_id, batch in pantry._query_batch_targets(
            service.connection,
            normalized_name=candidate.normalized_name,
            statuses=statuses,
        )
        if batch.remaining_quantity > 0
        and batch_id in eligible_ids
        and batch.unit.casefold() == candidate.unit.casefold()
    )
    resource_versions = tuple(
        (batch_id, batch.version) for batch_id, batch in targets
    )
    specs = tuple(batch.package_spec for _, batch in targets)
    if not specs or all(spec is None for spec in specs):
        return _PantryPackageProjection(
            "none", None, None, None, None, resource_versions
        )
    if any(spec is None for spec in specs):
        return _PantryPackageProjection(
            "partial", None, None, None, None, resource_versions
        )

    complete_specs = tuple(spec for spec in specs if spec is not None)
    first = complete_specs[0]
    signatures = {
        (
            spec.display_unit.casefold(),
            spec.base_quantity_per_display_unit,
            _canonical_json(spec.package_hierarchy),
        )
        for spec in complete_specs
    }
    if len(signatures) != 1:
        return _PantryPackageProjection(
            "mixed", None, None, None, None, resource_versions
        )

    remaining_quantity = sum(
        (batch.remaining_quantity for _, batch in targets), Decimal("0")
    )
    conversion_factor = first.base_quantity_per_display_unit.normalize()
    return _PantryPackageProjection(
        "uniform",
        (remaining_quantity / conversion_factor).normalize(),
        first.display_unit,
        conversion_factor,
        first.package_hierarchy,
        resource_versions,
    )


def _pantry_package_projection_payload(
    projection: _PantryPackageProjection,
) -> Mapping[str, Any]:
    if projection.status != "uniform":
        return {"status": projection.status}
    return {
        "status": projection.status,
        "display_unit": projection.display_unit,
        "base_quantity_per_display_unit": (
            projection.base_quantity_per_display_unit
        ),
        "package_hierarchy": projection.package_hierarchy,
    }


def _pantry_nutrition_projection_payload(
    projection: nutrition_profiles.LinkedNutritionProjection,
) -> Mapping[str, Any]:
    if projection.nutrition is None:
        return {"status": projection.status}
    return {
        "status": projection.status,
        "serving_basis": projection.serving_basis,
        "source_grade": projection.source_grade,
        "snapshot": dict(projection.nutrition),
    }


def _prepared_food_candidate_handle(
    service: DietService,
    candidate: inventory_matching.InventorySearchCandidate,
    *,
    statuses: tuple[str, ...],
    now: datetime,
) -> str | None:
    placeholders = ", ".join("?" for _ in statuses)
    batch_placeholders = ", ".join("?" for _ in candidate.batch_ids)
    rows = service.connection.execute(
        f"""
        SELECT pb.id AS batch_id, pb.version,
               pfp.id AS prepared_food_profile_id
        FROM pantry_batches AS pb
        LEFT JOIN prepared_food_profiles AS pfp
          ON pfp.pantry_batch_id = pb.id
        WHERE pb.normalized_name = ?
          AND lower(pb.unit) = lower(?)
          AND pb.status IN ({placeholders})
          AND pb.remaining_quantity > 0
          AND pb.id IN ({batch_placeholders})
        ORDER BY pb.id
        """,
        (
            candidate.normalized_name,
            candidate.unit,
            *statuses,
            *candidate.batch_ids,
        ),
    ).fetchall()
    if len(rows) != 1 or rows[0]["prepared_food_profile_id"] is None:
        return None
    row = rows[0]
    return _issue_workflow(
        service,
        "prepared_food_reference",
        request={"action": "select_prepared_food"},
        result={
            "batch_id": int(row["batch_id"]),
            "prepared_food_profile_id": int(
                row["prepared_food_profile_id"]
            ),
        },
        resource_versions={"version": int(row["version"])},
        now=now,
    )


def _prepared_food_reference(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> prepared_foods.PreparedFoodReference:
    _reject_raw_identifiers(
        payload,
        "id",
        "batch_id",
        "profile_id",
        "prepared_food_profile_id",
        "database_id",
    )
    row = _workflow_row(
        service.connection,
        _required_text(payload, "prepared_food_handle"),
        "prepared_food_reference",
        now=now,
    )
    target = _stored_object(
        row["result_json"], "stored prepared food reference"
    )
    expected = _stored_object(
        row["resource_versions_json"], "stored prepared food state"
    )
    try:
        return prepared_foods.load_prepared_food_reference(
            service.connection,
            batch_id=_positive_integer(
                target.get("batch_id"), "stored prepared batch"
            ),
            profile_id=_positive_integer(
                target.get("prepared_food_profile_id"),
                "stored prepared food profile",
            ),
            expected_version=_positive_integer(
                expected.get("version"), "stored prepared food version"
            ),
        )
    except prepared_foods.PreparedFoodValidationError as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Prepared food reference is stale"
        ) from error


def _pantry_preview_link_nutrition(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(service, payload, now=now)
    draft = _nutrition_profile_draft(
        _required_mapping(payload, "nutrition_profile")
    )
    linked_at = _required_datetime(payload, "linked_at")
    request = {
        "batch_id": batch_id,
        "expected_version": expected_version,
        "linked_at": linked_at,
        "nutrition_profile": _public_value(draft),
    }
    handle = _issue_workflow(
        service,
        "pantry_adjust_preview",
        request=request,
        result={
            "preview": {
                "food_name": draft.normalized_name,
                "brand": draft.brand,
                "serving_basis": draft.serving_basis,
                "source_grade": draft.source_grade,
            }
        },
        resource_versions={"version": expected_version},
        now=now,
    )
    return {
        "preview": {
            "food_name": draft.normalized_name,
            "brand": draft.brand,
            "serving_basis": draft.serving_basis,
            "source_grade": draft.source_grade,
        },
        "workflow": {"commit_handle": handle},
    }


def _pantry_preview_update_metadata(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(service, payload, now=now)
    row = service.connection.execute(
        "SELECT * FROM pantry_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise KeyError("No pantry batch matches the supplied reference")
    weight_fields = (
        "total_weight_g",
        "average_unit_weight_g",
        "weight_basis",
        "weight_source",
        "weight_confidence",
    )
    has_weight_update = any(field in payload for field in weight_fields)
    has_expiry_update = any(
        payload.get(field) is not None
        for field in ("expires_at", "expiry_date")
    )
    if not has_weight_update and not has_expiry_update:
        raise ValueError("expires_at or weight metadata is required")
    if has_weight_update:
        metadata = pantry._weight_metadata(
            quantity=Decimal(str(row["initial_quantity"])),
            unit=row["unit"],
            total_weight_g=_optional_decimal(
                payload.get("total_weight_g"), "total_weight_g"
            ),
            average_unit_weight_g=_optional_decimal(
                payload.get("average_unit_weight_g"), "average_unit_weight_g"
            ),
            weight_basis=_optional_text(
                payload.get("weight_basis"), "weight_basis"
            ),
            weight_source=_optional_text(
                payload.get("weight_source"), "weight_source"
            ),
            weight_confidence=_optional_text(
                payload.get("weight_confidence"), "weight_confidence"
            ),
            source_text=_required_text(payload, "source_text"),
        )
    else:
        metadata = tuple(row[field] for field in weight_fields)
    request = {
        "batch_id": batch_id,
        "expected_version": expected_version,
        "source_text": _required_text(payload, "source_text"),
        "total_weight_g": metadata[0],
        "average_unit_weight_g": metadata[1],
        "weight_basis": metadata[2],
        "weight_source": metadata[3],
        "weight_confidence": metadata[4],
    }
    if has_expiry_update:
        expires_at = _required_expiry_datetime(
            payload,
            timezone_name=service.settings.profile.timezone,
        )
        pantry._validated_expiry_timestamp(
            expires_at,
            _datetime_value(row["added_at"], "stored added_at"),
        )
        request["expires_at"] = expires_at
    handle = _issue_workflow(
        service,
        "pantry_adjust_preview",
        request=request,
        result={"preview": _public_value(request)},
        resource_versions={"version": expected_version},
        now=now,
    )
    return {
        "preview": _public_value(request),
        "workflow": {"commit_handle": handle},
    }


def _pantry_commit_update_metadata(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    handle = _required_text(payload, "commit_handle")
    now = _operation_now(payload, context)
    preview = _workflow_row(
        service.connection,
        handle,
        "pantry_adjust_preview",
        now=now,
        allow_consumed=True,
    )
    if preview["consumed_at"] is not None:
        return _stored_object(preview["result_json"], "stored metadata result")
    request = _stored_object(preview["request_json"], "stored metadata request")
    batch_id = _positive_integer(request.get("batch_id"), "stored batch")
    expected_version = _positive_integer(
        request.get("expected_version"), "stored batch version"
    )
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mutate(mutation_context):
        row = service.connection.execute(
            "SELECT * FROM pantry_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None or row["version"] != expected_version:
            raise pantry.PantryReferenceStaleError(
                "selected pantry batch is stale"
            )
        updates = {
            "total_weight_g": request.get("total_weight_g"),
            "average_unit_weight_g": request.get("average_unit_weight_g"),
            "weight_basis": request.get("weight_basis"),
            "weight_source": request.get("weight_source"),
            "weight_confidence": request.get("weight_confidence"),
            "version": expected_version + 1,
        }
        if "expires_at" in request:
            updates["expires_at"] = pantry._validated_expiry_timestamp(
                _datetime_value(request["expires_at"], "stored expires_at"),
                _datetime_value(row["added_at"], "stored added_at"),
            )
        updated = mutation_context.update("pantry_batches", batch_id, updates)
        public_result = {"batch": _public_value(pantry._batch(updated))}
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace
        return public_result

    try:
        return TransactionManager(service.connection).execute(
            "pantry_adjust",
            _required_text(request, "source_text"),
            mutate,
            internal_id=transaction_id,
        ).value
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "pantry_adjust_preview",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError("STALE_PREVIEW", "Pantry metadata preview is stale")
        return _stored_object(raced["result_json"], "stored metadata result")


def _pantry_commit_link_nutrition(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    handle = _required_text(payload, "commit_handle")
    now = _operation_now(payload, context)
    preview = _workflow_row(
        service.connection,
        handle,
        "pantry_adjust_preview",
        now=now,
        allow_consumed=True,
    )
    if preview["consumed_at"] is not None:
        return _stored_object(
            preview["result_json"], "stored pantry nutrition result"
        )
    request = _stored_object(
        preview["request_json"], "stored pantry nutrition request"
    )
    batch_id = _positive_integer(request.get("batch_id"), "stored batch")
    expected_version = _positive_integer(
        request.get("expected_version"), "stored batch version"
    )
    current = service.connection.execute(
        "SELECT version FROM pantry_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if current is None or current["version"] != expected_version:
        raise _ServiceError("STALE_PREVIEW", "Pantry batch reference is stale")
    draft = _nutrition_profile_draft(
        _mapping_value(
            request.get("nutrition_profile"), "stored nutrition profile"
        )
    )
    linked_at = _datetime_value(request.get("linked_at"), "stored linked_at")
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mutate(mutation_context):
        profile = nutrition_profiles._create_and_link_in_context(
            service.connection,
            mutation_context,
            pantry_batch_id=batch_id,
            draft=draft,
            linked_at=linked_at,
        )
        public_result = {
            "nutrition_profile": _public_value(profile),
            "nutrition": nutrition_profiles.linked_snapshot(
                service.connection, batch_id
            ),
        }
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace
        return public_result

    try:
        return TransactionManager(service.connection).execute(
            "pantry_adjust",
            draft.source_text,
            mutate,
            internal_id=transaction_id,
        ).value
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "pantry_adjust_preview",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError("STALE_PREVIEW", "Pantry nutrition preview is stale")
        return _stored_object(
            raced["result_json"], "stored pantry nutrition result"
        )


def _pantry_adjust(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(
        service, payload, now=now
    )
    result = pantry.adjust_batch(
        service.connection,
        TransactionManager(service.connection),
        _batch_id=batch_id,
        quantity=_required_decimal(payload, "quantity"),
        source_text=_required_text(payload, "source_text"),
        reason=_optional_text(payload.get("reason"), "reason"),
        _expected_version=expected_version,
    )
    return _pantry_mutation_result(service, batch_id, result, now=now)


def _pantry_discard(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    if payload.get("inventory_match_handle") is not None:
        return _pantry_product_reduction(
            service,
            payload,
            now=now,
            movement_type="discard",
        )
    batch_id, expected_version = _pantry_target_id(
        service, payload, now=now
    )
    result = pantry.discard_batch(
        service.connection,
        TransactionManager(service.connection),
        _batch_id=batch_id,
        discarded_at=(
            _datetime_value(payload["discarded_at"], "discarded_at")
            if "discarded_at" in payload
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        reason=_optional_text(payload.get("reason"), "reason"),
        waste_category=_optional_text(
            payload.get("waste_category"),
            "waste_category",
        ),
        _expected_version=expected_version,
    )
    return _pantry_mutation_result(service, batch_id, result, now=now)


def _pantry_deduct(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _pantry_product_reduction(
        service,
        payload,
        now=_operation_now(payload, context),
        movement_type="consume",
    )


def _pantry_product_reduction(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    movement_type: str,
) -> Mapping[str, Any]:
    normalized_name, base_unit = _pantry_product_reference(
        service,
        _required_text(payload, "inventory_match_handle"),
        now=now,
    )
    quantity, unit = _pantry_product_base_quantity(
        service,
        normalized_name=normalized_name,
        base_unit=base_unit,
        quantity=_required_decimal(payload, "quantity"),
        unit=_required_text(payload, "unit"),
    )
    result = pantry.reduce_inventory(
        service.connection,
        TransactionManager(service.connection),
        normalized_name=normalized_name,
        quantity=quantity,
        unit=unit,
        movement_type=movement_type,
        source_text=_required_text(payload, "source_text"),
        reason=_optional_text(payload.get("reason"), "reason"),
        waste_category=(
            _optional_text(payload.get("waste_category"), "waste_category")
            if movement_type == "discard"
            else None
        ),
        deduction_strategy=service.settings.behavior.inventory.deduction_strategy,
    )
    return {"selection": _public_value(result)}


def _pantry_product_reference(
    service: DietService,
    handle: str,
    *,
    now: datetime,
) -> tuple[str, str]:
    value = _pantry_product_reference_snapshot(service, handle, now=now)
    return (
        _required_text(value, "normalized_name"),
        _required_text(value, "base_unit"),
    )


def _pantry_product_reference_snapshot(
    service: DietService,
    handle: str,
    *,
    now: datetime,
) -> Mapping[str, Any]:
    row = _workflow_row(
        service.connection,
        handle,
        "pantry_product_reference",
        now=now,
    )
    value = _stored_object(row["result_json"], "stored pantry product")
    normalized_name = _required_text(value, "normalized_name")
    base_unit = _required_text(value, "base_unit")
    stored_resources = _stored_array(
        row["resource_versions_json"], "stored pantry resources"
    )
    if not stored_resources:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")

    expected: dict[int, int] = {}
    for resource in stored_resources:
        if not isinstance(resource, Mapping):
            raise _ServiceError(
                "STALE_PREVIEW", "Workflow reference is stale"
            )
        batch_id = resource.get("batch_id")
        version = resource.get("version")
        if (
            isinstance(batch_id, bool)
            or not isinstance(batch_id, int)
            or isinstance(version, bool)
            or not isinstance(version, int)
            or batch_id in expected
        ):
            raise _ServiceError(
                "STALE_PREVIEW", "Workflow reference is stale"
            )
        expected[batch_id] = version

    placeholders = ", ".join("?" for _ in expected)
    rows = service.connection.execute(
        f"""
        SELECT id, normalized_name, unit, status, remaining_quantity, version
        FROM pantry_batches
        WHERE id IN ({placeholders})
        """,
        tuple(expected),
    ).fetchall()
    if len(rows) != len(expected) or any(
        int(current["version"]) != expected[int(current["id"])]
        or str(current["normalized_name"]).casefold()
        != normalized_name.casefold()
        or str(current["unit"]).casefold() != base_unit.casefold()
        or str(current["status"]) not in _PANTRY_SEARCH_DEFAULT_STATUSES
        or Decimal(str(current["remaining_quantity"])) <= 0
        for current in rows
    ):
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    return value


def _pantry_reference_base_amount(
    reference: Mapping[str, Any],
    *,
    amount: Decimal | None,
    unit: str | None,
    field: str,
) -> tuple[Decimal | None, str]:
    base_unit = _required_text(reference, "base_unit")
    supplied_unit = unit or base_unit
    try:
        supplied_base_unit = inventory_matching.canonical_inventory_unit(
            supplied_unit
        )
        selected_base_unit = inventory_matching.canonical_inventory_unit(
            base_unit
        )
    except ValueError:
        supplied_base_unit = supplied_unit.casefold()
        selected_base_unit = base_unit.casefold()
    if supplied_base_unit == selected_base_unit:
        if amount is not None and amount <= 0:
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"{field}.amount",
                reason="required",
                expected="a positive amount for the selected pantry product",
                retryable=True,
            )
        return amount, base_unit

    package = reference.get("package")
    if isinstance(package, Mapping) and package.get("status") == "uniform":
        display_unit = package.get("display_unit")
        factor = package.get("base_quantity_per_display_unit")
        if (
            isinstance(display_unit, str)
            and display_unit.strip()
            and supplied_unit.casefold() == display_unit.strip().casefold()
        ):
            if amount is None or amount <= 0:
                raise _ServiceError(
                    "INVALID_INPUT",
                    "The request is invalid",
                    field=f"{field}.amount",
                    reason="required",
                    expected=(
                        "a positive amount for the selected display unit"
                    ),
                    retryable=True,
                )
            try:
                parsed_factor = Decimal(str(factor))
            except (InvalidOperation, ValueError, TypeError) as error:
                raise _ServiceError(
                    "STALE_PREVIEW", "Workflow reference is stale"
                ) from error
            if not parsed_factor.is_finite() or parsed_factor <= 0:
                raise _ServiceError(
                    "STALE_PREVIEW", "Workflow reference is stale"
                )
            return amount * parsed_factor, base_unit

    raise _ServiceError(
        "INVALID_INPUT",
        "The request is invalid",
        field=f"{field}.unit",
        reason="incompatible",
        expected="the base unit or verified display unit from pantry search",
        retryable=True,
    )


def _pantry_reference_consumed_measures(
    *,
    base_amount: Decimal,
    base_unit: str,
    consumed_weight_g: Decimal | None,
    consumed_volume_ml: Decimal | None,
    consumed_servings: Decimal | None,
    field: str,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    canonical_unit = inventory_matching.canonical_inventory_unit(base_unit)
    target_field = {
        "g": "consumed_weight_g",
        "ml": "consumed_volume_ml",
    }.get(canonical_unit, "consumed_servings")
    measures = {
        "consumed_weight_g": consumed_weight_g,
        "consumed_volume_ml": consumed_volume_ml,
        "consumed_servings": consumed_servings,
    }
    target_measure = measures[target_field]
    if target_measure is not None and target_measure != base_amount:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.{target_field}",
            reason="incompatible",
            expected=(
                f"{format(base_amount, 'f')} in {target_field} "
                "for the selected pantry quantity"
            ),
            retryable=True,
        )
    if all(measure is None for measure in measures.values()):
        measures[target_field] = base_amount
    return (
        measures["consumed_weight_g"],
        measures["consumed_volume_ml"],
        measures["consumed_servings"],
    )


def _pantry_reference_servings(
    reference: Mapping[str, Any],
    *,
    base_amount: Decimal,
    base_unit: str,
    field: str,
) -> Decimal:
    package = reference.get("package")
    reference_base_unit = _required_text(reference, "base_unit")
    try:
        supplied_base_unit = inventory_matching.canonical_inventory_unit(base_unit)
        selected_base_unit = inventory_matching.canonical_inventory_unit(
            reference_base_unit
        )
    except ValueError as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Workflow reference is stale"
        ) from error
    if supplied_base_unit != selected_base_unit:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    if not isinstance(package, Mapping) or package.get("status") != "uniform":
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.consumed_servings",
            reason="required",
            expected="a verified uniform pantry package relation",
            retryable=True,
        )
    try:
        factor = Decimal(str(package.get("base_quantity_per_display_unit")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Workflow reference is stale"
        ) from error
    if not factor.is_finite() or factor <= 0:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    servings = base_amount / factor
    if not servings.is_finite() or servings <= 0:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.consumed_servings",
            reason="required",
            expected="a positive serving count derived from pantry packaging",
            retryable=True,
        )
    return servings


def _pantry_reference_complete_nutrition(
    reference: Mapping[str, Any],
) -> tuple[
    nutrition.NutritionFacts,
    nutrition_normalization.NutritionBasis,
    str,
] | None:
    projection = reference.get("nutrition")
    if not isinstance(projection, Mapping) or projection.get("status") != "uniform":
        return None
    snapshot = projection.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    field_map = {
        "calories": "calories_kcal",
        "protein": "protein_g",
        "fat": "fat_g",
        "carbohydrate": "carbohydrate_g",
        "fiber": "fiber_g",
        "sodium": "sodium_mg",
    }
    if any(snapshot.get(source) is None for source in field_map.values()):
        return None
    try:
        facts = nutrition.NutritionFacts(
            **{
                target: Decimal(str(snapshot[source]))
                for target, source in field_map.items()
            },
            source="packaging_label",
            source_grade=_required_text(projection, "source_grade"),
            hydration_ml=(
                Decimal(str(snapshot["hydration_ml"]))
                if snapshot.get("hydration_ml") is not None
                else None
            ),
        )
        basis = nutrition_normalization.NutritionBasis(
            _required_text(projection, "serving_basis")
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
        nutrition.NutritionValidationError,
    ) as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Workflow reference is stale"
        ) from error
    return facts, basis, "pantry-label-snapshot"


def _pantry_product_base_quantity(
    service: DietService,
    *,
    normalized_name: str,
    base_unit: str,
    quantity: Decimal,
    unit: str,
) -> tuple[Decimal, str]:
    if unit.casefold() == base_unit.casefold():
        return quantity, base_unit
    batches = pantry.query_batches(
        service.connection,
        normalized_name=normalized_name,
        statuses=("active", "opened", "thawed"),
    )
    matching_specs = [
        batch.package_spec
        for batch in batches
        if batch.unit.casefold() == base_unit.casefold()
        and batch.package_spec is not None
        and batch.package_spec.display_unit.casefold() == unit.casefold()
    ]
    conversion_factors = {
        spec.base_quantity_per_display_unit for spec in matching_specs
    }
    if not matching_specs:
        raise ValueError("inventory unit cannot be converted")
    if len(conversion_factors) != 1:
        raise ValueError("inventory package sizes are inconsistent")
    return pantry.to_base_quantity(
        quantity,
        unit,
        base_unit=base_unit,
        spec=matching_specs[0],
    )


def _pantry_open(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(
        service, payload, now=now
    )
    result = pantry.mark_opened(
        service.connection,
        TransactionManager(service.connection),
        _batch_id=batch_id,
        opened_at=(
            _datetime_value(payload["opened_at"], "opened_at")
            if "opened_at" in payload
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        _expected_version=expected_version,
    )
    return _pantry_mutation_result(service, batch_id, result, now=now)


def _pantry_freeze(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(
        service, payload, now=now
    )
    result = pantry.freeze_batch(
        service.connection,
        TransactionManager(service.connection),
        _batch_id=batch_id,
        frozen_at=(
            _datetime_value(payload["frozen_at"], "frozen_at")
            if "frozen_at" in payload
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        _expected_version=expected_version,
    )
    return _pantry_mutation_result(service, batch_id, result, now=now)


def _pantry_thaw(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    batch_id, expected_version = _pantry_target_id(
        service, payload, now=now
    )
    result = pantry.thaw_batch(
        service.connection,
        TransactionManager(service.connection),
        _batch_id=batch_id,
        thawed_at=(
            _datetime_value(payload["thawed_at"], "thawed_at")
            if "thawed_at" in payload
            else now
        ),
        source_text=_required_text(payload, "source_text"),
        _expected_version=expected_version,
    )
    return _pantry_mutation_result(service, batch_id, result, now=now)


def _pantry_preview_deduct(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    normalized_name = _required_text(payload, "normalized_name")
    strategy = service.settings.behavior.inventory.deduction_strategy
    selection = pantry.select_batches(
        service.connection,
        normalized_name,
        _required_decimal(payload, "quantity"),
        selector=_optional_text(payload.get("selector"), "selector"),
        unit=_required_text(payload, "unit"),
        deduction_strategy=strategy,
    )
    _required_text(payload, "source_text")
    handle = _issue_workflow(
        service,
        "pantry_deduct_preview",
        request={**payload, "_deduction_strategy": list(strategy)},
        result={"selection": _public_value(selection)},
        resource_versions=_pantry_resource_versions(
            service.connection, normalized_name
        ),
        now=_operation_now(payload, context),
    )
    return {
        "selection": selection,
        "workflow": {"commit_handle": handle},
    }


def _pantry_commit_deduct(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    handle = _required_text(payload, "commit_handle")
    now = _operation_now(payload, context)
    preview = _workflow_row(
        service.connection,
        handle,
        "pantry_deduct_preview",
        now=now,
        allow_consumed=True,
    )
    if preview["consumed_at"] is not None:
        return _stored_object(
            preview["result_json"], "stored pantry deduction result"
        )

    request = _stored_object(
        preview["request_json"], "stored pantry deduction request"
    )
    stored_strategy = request.get("_deduction_strategy")
    if (
        not isinstance(stored_strategy, list)
        or tuple(stored_strategy)
        != service.settings.behavior.inventory.deduction_strategy
    ):
        raise _ServiceError("STALE_PREVIEW", "Pantry deduction preview is stale")
    stored_resources = _stored_array(
        preview["resource_versions_json"], "stored pantry resources"
    )
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mutate(mutation_context):
        current = _workflow_row(
            service.connection,
            handle,
            "pantry_deduct_preview",
            now=now,
            allow_consumed=True,
        )
        if current["consumed_at"] is not None:
            raise _WorkflowConsumedRace
        normalized_name = _required_text(request, "normalized_name")
        if _pantry_resource_versions(
            service.connection, normalized_name
        ) != stored_resources:
            raise _ServiceError("STALE_PREVIEW", "Pantry deduction preview is stale")
        result = pantry._deduct_inventory_in_context(
            service.connection,
            mutation_context,
            normalized_name=normalized_name,
            quantity=_required_decimal(request, "quantity"),
            unit=_required_text(request, "unit"),
            source_text=_required_text(request, "source_text"),
            selector=_optional_text(request.get("selector"), "selector"),
            reason=_optional_text(request.get("reason"), "reason"),
            deduction_strategy=stored_strategy,
        )
        public_result = _public_value({"selection": result})
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace
        return public_result

    try:
        result = TransactionManager(service.connection).execute(
            "pantry_deduct",
            _required_text(request, "source_text"),
            mutate,
            internal_id=transaction_id,
        )
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "pantry_deduct_preview",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError("STALE_PREVIEW", "Pantry deduction preview is stale")
        return _stored_object(
            raced["result_json"], "stored pantry deduction result"
        )
    return result.value


def _pantry_preview_shopping_list(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    draft = shopping.normalize_draft(payload)
    normalized = shopping.draft_mapping(draft)
    handle = _issue_workflow(
        service,
        "shopping_list_preview",
        request=normalized,
        result={"preview": normalized},
        resource_versions=(),
        now=_operation_now(payload, context),
    )
    return {
        "preview": _public_value(normalized),
        "candidate_only": True,
        "workflow": {"commit_handle": handle},
    }


def _pantry_commit_shopping_list(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    handle = _required_text(payload, "commit_handle")
    now = _operation_now(payload, context)
    preview = _workflow_row(
        service.connection,
        handle,
        "shopping_list_preview",
        now=now,
        allow_consumed=True,
    )
    if preview["consumed_at"] is not None:
        return _stored_object(
            preview["result_json"],
            "stored shopping list result",
        )
    request = _stored_object(
        preview["request_json"],
        "stored shopping list request",
    )
    draft = shopping.normalize_draft(request)
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mark_committed(_context: Any, value: shopping.ShoppingList) -> None:
        public_result = {"shopping_list": _public_value(value)}
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace

    try:
        value = shopping.commit_list(
            service.connection,
            TransactionManager(service.connection),
            draft=draft,
            now=now,
            internal_id=transaction_id,
            after_insert=mark_committed,
        )
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "shopping_list_preview",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError("STALE_PREVIEW", "Shopping list preview is stale")
        return _stored_object(
            raced["result_json"],
            "stored shopping list result",
        )
    return {"shopping_list": _public_value(value)}


def _pantry_query_shopping_list(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    values: list[Mapping[str, Any]] = []
    for list_id, value in shopping.query_lists(
        service.connection,
        status=_optional_text(payload.get("status"), "status"),
        limit=_positive_integer(payload.get("limit", 10), "limit"),
    ):
        handle = _issue_workflow(
            service,
            "shopping_list_reference",
            request={
                "shopping_list_id": list_id,
                "expected_version": value.version,
            },
            result={},
            resource_versions=(
                {
                    "table": "shopping_lists",
                    "id": list_id,
                    "version": value.version,
                },
            ),
            now=now,
        )
        values.append(
            _public_value(value)
            | {"workflow": {"shopping_list_handle": handle}}
        )
    return {"shopping_lists": values}


def _pantry_cancel_shopping_list(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    handle = _required_text(payload, "shopping_list_handle")
    reference = _workflow_row(
        service.connection,
        handle,
        "shopping_list_reference",
        now=now,
        allow_consumed=True,
    )
    if reference["consumed_at"] is not None:
        return _stored_object(
            reference["result_json"],
            "stored shopping list cancellation",
        )
    stored = _stored_object(
        reference["request_json"],
        "stored shopping list reference",
    )
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mark_cancelled(_context: Any, result: shopping.ShoppingList) -> None:
        public_result = {"shopping_list": _public_value(result)}
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(handle),
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace

    try:
        value = shopping.cancel_list(
            service.connection,
            TransactionManager(service.connection),
            shopping_list_id=_positive_integer(
                stored.get("shopping_list_id"),
                "stored shopping list",
            ),
            expected_version=_positive_integer(
                stored.get("expected_version"),
                "stored shopping list version",
            ),
            source_text=_required_text(payload, "source_text"),
            now=now,
            internal_id=transaction_id,
            after_update=mark_cancelled,
        )
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            handle,
            "shopping_list_reference",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError(
                "STALE_PREVIEW",
                "Shopping list reference is stale",
            )
        return _stored_object(
            raced["result_json"],
            "stored shopping list cancellation",
        )
    return {"shopping_list": _public_value(value)}


def _transaction_recent(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    operation = _optional_text(payload.get("operation"), "operation") or "undo"
    candidates = find_undo_candidates(
        _undo_filters(service, payload, context, action=operation)
    )
    limit = _positive_integer(payload.get("limit", 10), "limit")
    now = _operation_now(payload, context)
    if service._degraded_error is not None:
        return {
            "candidates": tuple(
                {"summary": candidate.summary}
                for candidate in candidates[:limit]
            )
        }
    return {
        "candidates": tuple(
            {
                "summary": candidate.summary,
                "workflow": {
                    "operation_handle": _issue_workflow(
                        service,
                        f"transaction_{operation}_reference",
                        request={"action": operation},
                        result={"transaction_id": candidate.transaction_id},
                        resource_versions=_transaction_reference_version(
                            service.connection,
                            candidate.transaction_id,
                            expected_status=(
                                "committed"
                                if operation == "undo"
                                else "reverted"
                            ),
                        ),
                        now=now,
                    )
                },
            }
            for candidate in candidates[:limit]
        )
    }


def _transaction_undo(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    transaction_id, token_hash, expected_status, expected_generation = (
        _transaction_target(
            service, payload, action="undo", now=now
        )
    )
    result = TransactionManager(service.connection).undo(
        transaction_id,
        expected_status=expected_status,
        expected_generation=expected_generation,
    )
    try:
        _consume_workflow_reference(service.connection, token_hash, now=now)
    except Exception:
        LOGGER.warning("Post-commit undo workflow consumption failed")
        _cleanup_post_commit_workflow_failure(service)
        return _HandlerResult(
            {
                "status": "reverted",
                "affected_rows": result.value["affected_rows"],
            },
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    return {
        "status": "reverted",
        "affected_rows": result.value["affected_rows"],
    }


def _transaction_redo(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any] | _HandlerResult:
    now = _operation_now(payload, context)
    transaction_id, token_hash, expected_status, expected_generation = (
        _transaction_target(
            service, payload, action="redo", now=now
        )
    )
    result = TransactionManager(service.connection).redo(
        transaction_id,
        expected_status=expected_status,
        expected_generation=expected_generation,
    )
    try:
        _consume_workflow_reference(service.connection, token_hash, now=now)
    except Exception:
        LOGGER.warning("Post-commit redo workflow consumption failed")
        _cleanup_post_commit_workflow_failure(service)
        return _HandlerResult(
            {
                "status": "committed",
                "affected_rows": result.value["affected_rows"],
            },
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    return {
        "status": "committed",
        "affected_rows": result.value["affected_rows"],
    }


def _report_today(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    report_date = _report_date(service, payload, context)
    return _built_report(
        service,
        "daily",
        reports.build_daily_report(
            service.connection,
            service.data_paths,
            service.settings,
            report_date,
            templates_dir=service.templates_dir,
        ),
    )


def _report_daily(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    return _report_today(service, payload, context)


def _report_weekly(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    path = reports.build_weekly_report(
        service.connection,
        service.data_paths,
        service.settings,
        _report_date(service, payload, context),
        templates_dir=service.templates_dir,
    )
    return _built_report(service, "weekly", path)


def _report_monthly(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    path = reports.build_monthly_report(
        service.connection,
        service.data_paths,
        service.settings,
        _report_date(service, payload, context),
        templates_dir=service.templates_dir,
    )
    return _built_report(service, "monthly", path)


def _report_progress(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    goals = goal_profiles.load_goal_profile(service.connection)
    day = _report_date(service, payload, context, timezone_name=goals.timezone_name)
    snapshot = progress.daily_progress_snapshot(
        service.connection,
        occurred_at=_operation_now(payload, context),
        goal_profile=goals,
        occurred_on=day,
    )
    start, end = local_day_utc_bounds(day, goals.timezone_name)
    return {
        "local_date": snapshot.local_date,
        "known_minimum": snapshot.known_minimum,
        "incomplete_meal_count": snapshot.incomplete_meal_count,
        "nutrition_quality": snapshot.nutrition_quality,
        "metrics": snapshot.metrics,
        "occurred_on": snapshot.local_date,
        "aggregate": progress.aggregate_period(
            service.connection, start_utc=start, end_utc=end
        ),
        "daily_progress": snapshot.metrics,
    } | goal_profiles.public_provenance(goals)


def _report_insights(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    goals = goal_profiles.load_goal_profile(service.connection)
    now = _operation_now(payload, context)
    period = payload.get("period", "daily")
    if not isinstance(period, str):
        raise ValueError("period must be daily, weekly, or monthly")
    within_days = _positive_integer(payload.get("within_days", 7), "within_days")
    limit = _positive_integer(payload.get("limit", 5), "limit")
    if within_days > 30:
        raise ValueError("within_days must be between 1 and 30")
    if limit > 10:
        raise ValueError("limit must be between 1 and 10")
    anchor = _report_date(
        service,
        payload,
        context,
        now=now,
        timezone_name=goals.timezone_name,
    )
    if anchor > local_date(now, goals.timezone_name):
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field="report_date",
            reason="future_date",
            expected="today or an earlier local calendar date",
            retryable=True,
        )
    return _public_value(
        insights.build_insights(
            service.connection,
            anchor=anchor,
            period=period,
            goal_profile=goals,
            within_days=within_days,
            limit=limit,
        ),
    )


def _report_expiring(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    goal_profile = goal_profiles.load_goal_profile(service.connection)
    today = _report_date(
        service,
        payload,
        context,
        now=now,
        timezone_name=goal_profile.timezone_name,
    )
    within_days = _positive_integer(payload.get("within_days", 7), "within_days")
    batches = pantry.query_batches(
        service.connection,
        statuses=("active", "opened", "frozen", "thawed"),
    )
    collection = reports.collect_expiring_inventory(
        batches,
        now=now,
        report_date=today,
        within_days=within_days,
        timezone_name=goal_profile.timezone_name,
        policies=service.policies,
    )
    expiring = tuple(
        _pantry_batch_value(service, batch) | description
        for batch, description in collection.items
    )
    return {
        "as_of": today,
        "within_days": within_days,
        "batches": expiring,
        "state_counts": collection.state_counts,
        "range": collection.range,
        "complete": collection.complete,
        "returned_count": len(expiring),
        "has_more": False,
        "next_offset": None,
    }


def _report_cost_summary(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    start_date, end_date, start_utc, end_utc = _report_range(
        service,
        payload,
        context,
        default_days=30,
    )
    result = costs.cost_summary(
        service.connection,
        start_utc=start_utc,
        end_utc=end_utc,
        currency=_optional_text(payload.get("currency"), "currency"),
    )
    violations = costs.assert_cost_conservation(service.connection)
    return {
        "period": {
            "date_start": start_date,
            "date_end": end_date,
        },
        **result,
        "cost_conservation": {
            "ok": not violations,
            "affected_batches": list(violations),
        },
    }


def _report_waste_summary(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    start_date, end_date, start_utc, end_utc = _report_range(
        service,
        payload,
        context,
        default_days=30,
    )
    return {
        "period": {
            "date_start": start_date,
            "date_end": end_date,
        },
        **waste.waste_summary(
            service.connection,
            start_utc=start_utc,
            end_utc=end_utc,
            currency=_optional_text(payload.get("currency"), "currency"),
        ),
    }


def _report_trend_summary(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    days = _positive_integer(payload.get("days", 90), "days")
    if days > 730:
        raise ValueError("days must be between 1 and 730")
    start_date, end_date, start_utc, end_utc = _report_range(
        service,
        payload,
        context,
        default_days=days,
        explicit_days=days,
    )
    return {
        "period": {
            "date_start": start_date,
            "date_end": end_date,
        },
        **trends.trend_summary(
            service.connection,
            start_date=start_date,
            end_date=end_date,
            start_utc=start_utc,
            end_utc=end_utc,
            currency=_optional_text(payload.get("currency"), "currency"),
        ),
    }


def _system_initialize(
    service: DietService, payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    def work() -> Mapping[str, Any]:
        ensure_data_directories(service.data_paths)
        _apply_system_migrations(service)
        initial_backup_created = False
        if not any(service.data_paths.backups.glob("*.sqlite")):
            backup.create_backup(
                service.connection,
                service.data_paths,
                label="initial",
                _clock=service._clock,
            )
            initial_backup_created = True
        return {
            "initialized": True,
            "initial_backup_created": initial_backup_created,
        }

    return _run_maintenance_operation(
        service,
        "initialize",
        payload,
        work,
        exclusive=True,
    )


def _system_self_check(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    report = service.trusted_workflows.inspect(
        SelfCheckQuery(now=_optional_operation_now(payload, context))
    )
    return {
        "checks": _public_self_check_results(
            service,
            report.payload["checks"],
        )
    }


def _system_repair(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    def work() -> Mapping[str, Any]:
        report_date = _optional_date(payload.get("report_date"), "report_date")
        checks = self_check.repair_safe_issues(
            service.connection,
            service.data_paths,
            service.migrations_dir,
            settings=service.settings,
            templates_dir=service.templates_dir,
            report_date=report_date,
            now=_optional_operation_now(payload, context),
        )
        return {"checks": _public_self_check_results(service, checks)}

    return _run_maintenance_operation(
        service,
        "repair",
        payload,
        work,
        exclusive=True,
    )


def _public_self_check_results(
    service: DietService, checks: Sequence[database.CheckResult]
) -> tuple[database.CheckResult, ...]:
    """Remove private filesystem locations from public health responses."""

    locations = (
        (service.data_paths.root, "dataDir"),
        (service.source_root, "source package"),
    )
    public_checks: list[database.CheckResult] = []
    for check in checks:
        message = check.message
        for location, label in locations:
            variants = {
                str(location),
                location.as_posix(),
                str(location).replace("\\", "/"),
                str(location).replace("/", "\\"),
            }
            for variant in sorted(variants, key=len, reverse=True):
                message = re.sub(
                    re.escape(variant),
                    label,
                    message,
                    flags=re.IGNORECASE,
                )
        public_checks.append(
            database.CheckResult(
                code=check.code,
                level=check.level,
                message=message,
                repairable=check.repairable,
            )
        )
    return tuple(public_checks)


def _system_validate_database(
    service: DietService, _payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    return {"checks": database.validate_database(service.connection)}


def _system_backup(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    def work() -> Mapping[str, Any]:
        path = backup.create_backup(
            service.connection,
            service.data_paths,
            label=_optional_text(payload.get("label"), "label") or "backup",
            _clock=service._clock,
        )
        if service._degraded_error is not None:
            return {"backup": {"name": path.name}}
        handle = _issue_workflow(
            service,
            "backup_reference",
            request={"action": "restore_backup"},
            result={"backup_name": path.name},
            resource_versions={},
            now=_operation_now(payload, context),
        )
        workflow = {"backup_handle": handle}
        return {
            "backup": {"name": path.name, "workflow": workflow},
            "workflow": workflow,
        }

    return _run_maintenance_operation(
        service,
        "backup",
        payload,
        work,
        exclusive=False,
    )


def _connect_existing_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    database.register_database_functions(connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _system_restore(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    if payload.get("confirmed") is not True:
        raise backup.RestoreConfirmationRequired(
            "Restore requires explicit confirmation before replacing the active database"
        )
    def work() -> Mapping[str, Any]:
        now = _operation_now(payload, context)
        reference = _workflow_row(
            service.connection,
            _workflow_handle_text(payload, "backup_handle"),
            "backup_reference",
            now=now,
        )
        target = _stored_object(
            reference["result_json"],
            "stored backup reference",
        )
        backup_name = _required_text(target, "backup_name")
        source = _safe_backup_path(service.data_paths.backups, backup_name)
        try:
            result = backup.restore_backup(
                service.connection,
                service.data_paths,
                source,
                service.migrations_dir,
                confirmed=True,
                _clock=service._clock,
            )
        except backup.RestoreError as error:
            if error.recovered_connection is not None:
                service.connection = error.recovered_connection
                service._degraded_error = _ServiceError(
                    "DATABASE_INTEGRITY_ERROR", str(error)
                )
                service.connection.execute("PRAGMA query_only = ON")
            raise
        service.connection = result.connection
        return {
            "restored": True,
            "pre_restore_backup": {"name": result.pre_restore_backup.name},
        }

    return _run_maintenance_operation(
        service,
        "restore",
        payload,
        work,
        exclusive=True,
    )


def _system_migrate(
    service: DietService, payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    def work() -> Mapping[str, Any]:
        _apply_system_migrations(service)
        return {"migrated": True}

    return _run_maintenance_operation(
        service,
        "migrate",
        payload,
        work,
        exclusive=True,
    )


def _system_export_data(
    service: DietService,
    payload: Mapping[str, Any],
    _context: Mapping[str, Any],
) -> Mapping[str, Any]:
    def work() -> Mapping[str, Any]:
        profile = goal_profiles.load_goal_profile(service.connection)
        return {
            "export": data_export.export_data(
                service.connection,
                service.data_paths,
                export_format=(
                    _optional_text(payload.get("format"), "format") or "json"
                ),
                product_version=__product_version__,
                timezone_name=profile.timezone_name,
                now=service._clock(),
            )
        }

    return _run_maintenance_operation(
        service,
        "export_data",
        payload,
        work,
        exclusive=False,
    )


def _system_validate_import(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    preview = service.trusted_workflows.preview(
        ImportDataCommand(
            import_name=_required_text(payload, "import_name"),
        ),
        RequestContext(
            now=_operation_now(payload, context),
            operation_key=_optional_text(
                payload.get("operation_key"),
                "operation_key",
            ),
        ),
    )
    summary = _result_mapping(_public_value(preview.summary))
    return {
        **summary,
        "requires_confirmation": True,
        "workflow": {"commit_handle": preview.workflow_handle},
    }


def _system_import_data(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    receipt = service.trusted_workflows.commit(
        _workflow_handle_text(payload, "commit_handle"),
        Confirmation(
            confirmed=payload.get("confirmed") is True,
            operation_key=_optional_text(
                payload.get("operation_key"),
                "operation_key",
            ),
        )
    )
    del context
    return _result_mapping(_public_value(receipt.result))


def _system_preview_delete_data(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    scope = _required_text(payload, "scope")
    date_start: date | None = None
    date_end: date | None = None
    start_utc: str | None = None
    end_utc: str | None = None
    if scope == "intake_range":
        start_value = payload.get("date_start")
        end_value = payload.get("date_end")
        if start_value is None or end_value is None:
            raise data_erasure.DataErasureError(
                "intake_range requires date_start and date_end"
            )
        start_date = _date_value(start_value, "date_start")
        end_date = _date_value(end_value, "date_end")
        if end_date < start_date:
            raise data_erasure.DataErasureError(
                "date_end must not be before date_start"
            )
        profile = goal_profiles.load_goal_profile(service.connection)
        start_utc, _ = local_day_utc_bounds(
            start_date,
            profile.timezone_name,
        )
        _, end_utc = local_day_utc_bounds(
            end_date,
            profile.timezone_name,
        )
        date_start = start_date
        date_end = end_date
    elif payload.get("date_start") is not None or payload.get("date_end") is not None:
        raise data_erasure.DataErasureError(
            "Date range is only accepted for intake_range deletion"
        )
    workflow = service.trusted_workflows.preview(
        DeleteDataCommand(
            scope=scope,
            date_start=date_start,
            date_end=date_end,
            start_utc=start_utc,
            end_utc=end_utc,
        ),
        RequestContext(now=_operation_now(payload, context)),
    )
    return {
        **workflow.summary,
        "workflow": {"commit_handle": workflow.workflow_handle},
    }


def _system_commit_delete_data(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    receipt = service.trusted_workflows.commit(
        _workflow_handle_text(payload, "commit_handle"),
        Confirmation(
            confirmed=payload.get("confirmed") is True,
            operation_key=_optional_text(
                payload.get("operation_key"),
                "operation_key",
            ),
        ),
    )
    del context
    return _result_mapping(_public_value(receipt.result))


def _system_maintenance_status(
    service: DietService,
    payload: Mapping[str, Any],
    _context: Mapping[str, Any],
) -> Mapping[str, Any]:
    record = service.maintenance_controller.get(
        _required_text(payload, "operation_handle")
    )
    return {"maintenance": maintenance_control.public_record(record)}


def _system_maintenance_history(
    service: DietService,
    payload: Mapping[str, Any],
    _context: Mapping[str, Any],
) -> Mapping[str, Any]:
    raw_limit = payload.get("limit", 20)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ValueError("limit must be an integer from 1 to 20")
    return {
        "operations": [
            maintenance_control.public_record(record)
            for record in service.maintenance_controller.history(raw_limit)
        ]
    }


def _run_maintenance_operation(
    service: DietService,
    action: str,
    payload: Mapping[str, Any],
    work: Callable[[], Mapping[str, Any]],
    *,
    exclusive: bool,
) -> Mapping[str, Any]:
    operation_key = _optional_text(payload.get("operation_key"), "operation_key")
    parameters = {
        str(key): _json_value(value)
        for key, value in payload.items()
        if key != "operation_key"
    }
    record, replayed = service.maintenance_controller.accept(
        action,
        parameters,
        operation_key=operation_key,
        exclusive=exclusive,
    )
    if replayed:
        if record.status == "committed":
            stored = service.maintenance_controller.result(record.handle) or {}
            return {
                **stored,
                "maintenance": maintenance_control.public_record(record),
            }
        if record.status == "failed":
            raise _ServiceError(
                "MAINTENANCE_OPERATION_FAILED",
                "The maintenance operation failed",
            )
        return {"maintenance": maintenance_control.public_record(record)}

    service.maintenance_controller.mark_running(record.handle)
    try:
        result = work()
        public_result = _result_mapping(_public_value(result))
    except Exception as error:
        service.maintenance_controller.mark_failed(
            record.handle,
            type(error).__name__,
        )
        raise
    committed = service.maintenance_controller.mark_committed(
        record.handle,
        public_result,
    )
    return {
        **public_result,
        "maintenance": maintenance_control.public_record(committed),
    }


def _apply_system_migrations(service: DietService) -> None:
    if database.has_pending_migrations(service.connection, service.migrations_dir):
        backup.create_backup(
            service.connection,
            service.data_paths,
            label="pre-migration",
            _clock=service._clock,
        )
        database.apply_migrations(service.connection, service.migrations_dir)


def _system_query_preferences(
    service: DietService, payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    return {
        "preferences": learning.list_rules(
            service.connection,
            include_inactive=_optional_bool(
                payload.get("include_inactive"), "include_inactive", default=False
            ),
        )
    }


def _system_query_goals(
    service: DietService, _payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    profile = goal_profiles.load_goal_profile(service.connection)
    return {"goal_profile": _public_goal_profile(profile)}


def _system_update_goals(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    fields = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "fiber_g", "sodium_mg", "water_ml")
    draft = NutritionGoals(*(_positive_integer(payload.get(field), field) for field in fields))
    try:
        profile = goal_profiles.update_goal_profile(
            service.connection, TransactionManager(service.connection), draft,
            _required_text(payload, "source_text"), _operation_now(payload, context),
            timezone_name=_required_text(payload, "timezone_name"),
        )
    except TimezoneConfigurationError as error:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field="timezone_name",
            reason="unsupported_value",
            expected="an available IANA timezone name",
            retryable=True,
        ) from error
    return {"goal_profile": _public_goal_profile(profile)}


def _public_goal_profile(
    profile: goal_profiles.GoalProfile,
) -> Mapping[str, object]:
    return {
        "goals": profile.goals,
        "timezone_name": profile.timezone_name,
        "updated_at": profile.updated_at,
    } | goal_profiles.public_provenance(profile)


def _system_update_preferences(
    service: DietService, payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    arguments = {
        "connection": service.connection,
        "manager": TransactionManager(service.connection),
        "rule_type": learning.RuleType(_required_text(payload, "rule_type")),
        "subject": _required_text(payload, "subject"),
        "outcome": _required_mapping(payload, "outcome"),
        "settings": service.settings,
    }
    evidence = payload.get("evidence")
    if evidence is None:
        result = learning.set_explicit_rule(
            **arguments,
            source_text=_required_text(payload, "source_text"),
        )
    else:
        result = learning.record_learning_event(
            **arguments,
            evidence=_mapping_value(evidence, "evidence"),
            source_text=_required_text(payload, "source_text"),
        )
    return {"preference": result}


def _system_forget_preference(
    service: DietService, payload: Mapping[str, Any], _context: Mapping[str, Any]
) -> Mapping[str, Any]:
    result = learning.forget_rule(
        service.connection,
        TransactionManager(service.connection),
        rule_type=learning.RuleType(_required_text(payload, "rule_type")),
        subject=_required_text(payload, "subject"),
        source_text=_required_text(payload, "source_text"),
    )
    return {"preference": result}


def _system_query_nutrition_backfill(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    has_meal_handle = "meal_handle" in payload
    has_batch_handle = "batch_handle" in payload
    if has_meal_handle or has_batch_handle:
        if not has_meal_handle or not has_batch_handle or "limit" in payload:
            raise ValueError(
                "paged nutrition backfill query requires meal_handle and batch_handle"
            )
        meal_handle = _workflow_handle_text(payload, "meal_handle")
        reference = _workflow_row(
            service.connection,
            meal_handle,
            "meal_reference",
            now=now,
        )
        stored = _stored_object(
            reference["result_json"], "stored nutrition backfill reference"
        )
        _assert_nutrition_backfill_fresh(service.connection, stored)
        return {
            "meals": (
                _nutrition_backfill_page(
                    stored,
                    meal_handle=meal_handle,
                    batch_handle=_workflow_handle_text(
                        payload, "batch_handle"
                    ),
                ),
            )
        }

    limit = payload.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be between 1 and 10")
    candidates = nutrition_backfill.list_incomplete_meals(service.connection, limit=limit)
    meals = []
    for candidate in candidates:
        handles_by_row_id: dict[int, str] = {}
        issued_item_handles: set[str] = set()
        for item in candidate.items:
            item_handle = _new_backfill_handle(issued_item_handles)
            handles_by_row_id[item.row_id] = item_handle
            issued_item_handles.add(item_handle)
        item_targets = tuple(
            {
                "item_handle": handles_by_row_id[item.row_id],
                "row_id": item.row_id,
                "parent_row_id": item.parent_row_id,
                "display_order": item.display_order,
                "item_role": item.item_role,
                "parent_item_handle": (
                    handles_by_row_id[item.parent_row_id]
                    if item.parent_row_id is not None
                    else None
                ),
                "raw_name": item.raw_name,
                "amount": item.amount,
                "unit": item.unit,
            }
            for item in candidate.items
        )
        pages = []
        for offset in range(0, len(item_targets), MAX_TOTAL_ITEMS):
            batch_handle = _new_backfill_batch_handle(
                candidate.expected_item_signature,
                offset,
                issued_item_handles,
            )
            issued_item_handles.add(batch_handle)
            pages.append({
                "batch_handle": batch_handle,
                "item_handles": tuple(
                    target["item_handle"]
                    for target in item_targets[
                        offset : offset + MAX_TOTAL_ITEMS
                    ]
                ),
            })
        stored = {
            "meal_id": candidate.meal_id,
            "expected_item_signature": candidate.expected_item_signature,
            "occurred_at": candidate.occurred_at,
            "source_text": candidate.source_text,
            "item_targets": item_targets,
            "pages": tuple(pages),
            "staged_estimates": {},
            "state_version": 0,
        }
        meal_handle = _issue_workflow(
            service,
            "meal_reference",
            request={"action": "nutrition_backfill"},
            result=stored,
            resource_versions={
                "item_signature": candidate.expected_item_signature
            },
            now=now,
        )
        meals.append(
            _nutrition_backfill_page(
                stored,
                meal_handle=meal_handle,
                batch_handle=pages[0]["batch_handle"],
            )
        )
    return {"meals": tuple(meals)}


def _system_commit_nutrition_backfill(
    service: DietService, payload: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    _reject_raw_identifiers(payload, "id", "meal_id", "mealId", "database_id", "databaseId")
    meal_handle = _workflow_handle_text(payload, "meal_handle")
    now = _operation_now(payload, context)
    reference = _workflow_row(
        service.connection,
        meal_handle,
        "meal_reference",
        now=now,
        allow_consumed=True,
    )
    if reference["consumed_at"] is not None:
        return _stored_object(
            reference["result_json"], "stored nutrition backfill result"
        )
    pending = _stage_nutrition_backfill_batch(
        service,
        payload,
        meal_handle=meal_handle,
        now=now,
    )
    if pending is not None:
        return pending
    return _finalize_nutrition_backfill(
        service,
        payload,
        meal_handle=meal_handle,
        now=now,
    )


def _nutrition_backfill_targets(
    stored: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    values = stored.get("item_targets")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or not values
    ):
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill targets are unavailable"
        )
    targets: dict[str, Mapping[str, Any]] = {}
    row_ids: set[int] = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise _ServiceError(
                "STALE_PREVIEW", "Stored nutrition backfill targets are unavailable"
            )
        handle = value.get("item_handle")
        row_id = value.get("row_id")
        parent_row_id = value.get("parent_row_id")
        display_order = value.get("display_order")
        if (
            not isinstance(handle, str)
            or not handle.startswith(_HANDLE_PREFIX)
            or isinstance(row_id, bool)
            or not isinstance(row_id, int)
            or row_id <= 0
            or (
                parent_row_id is not None
                and (
                    isinstance(parent_row_id, bool)
                    or not isinstance(parent_row_id, int)
                    or parent_row_id <= 0
                )
            )
            or isinstance(display_order, bool)
            or not isinstance(display_order, int)
            or display_order < 0
            or handle in targets
            or row_id in row_ids
        ):
            raise _ServiceError(
                "STALE_PREVIEW", "Stored nutrition backfill targets are unavailable"
            )
        targets[handle] = value
        row_ids.add(row_id)
    if any(
        target["parent_row_id"] is not None
        and target["parent_row_id"] not in row_ids
        for target in targets.values()
    ):
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill targets are unavailable"
        )
    return targets


def _new_backfill_handle(issued: set[str]) -> str:
    while True:
        handle = _HANDLE_PREFIX + secrets.token_urlsafe(32)
        if handle not in issued:
            return handle


def _new_backfill_batch_handle(
    item_signature: str, offset: int, issued: set[str]
) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(
            f"nutrition-backfill:{item_signature}:{offset}:{nonce}".encode()
        ).hexdigest()
        handle = _HANDLE_PREFIX + digest
        if handle not in issued:
            return handle
        nonce += 1


def _assert_nutrition_backfill_fresh(
    connection: sqlite3.Connection, stored: Mapping[str, Any]
) -> None:
    try:
        meal_id = _positive_integer(stored.get("meal_id"), "stored meal")
        expected = _required_text(stored, "expected_item_signature")
        row = connection.execute(
            "SELECT deleted_at FROM meals WHERE id = ?", (meal_id,)
        ).fetchone()
        fresh = (
            row is not None
            and row["deleted_at"] is None
            and nutrition_backfill.item_signature(connection, meal_id) == expected
        )
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        raise _ServiceError(
            "STALE_PREVIEW", "Nutrition backfill reference is stale"
        )


def _nutrition_backfill_pages(
    stored: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    values = stored.get("pages")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or not values
    ):
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill pages are unavailable"
        )
    pages: list[Mapping[str, Any]] = []
    batch_handles: set[str] = set()
    paged_handles: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise _ServiceError(
                "STALE_PREVIEW", "Stored nutrition backfill pages are unavailable"
            )
        batch_handle = value.get("batch_handle")
        item_handles = value.get("item_handles")
        if (
            not isinstance(batch_handle, str)
            or not batch_handle.startswith(_HANDLE_PREFIX)
            or batch_handle in batch_handles
            or not isinstance(item_handles, Sequence)
            or isinstance(item_handles, (str, bytes, bytearray))
            or not 1 <= len(item_handles) <= MAX_TOTAL_ITEMS
        ):
            raise _ServiceError(
                "STALE_PREVIEW", "Stored nutrition backfill pages are unavailable"
            )
        normalized = tuple(item_handles)
        if any(
            not isinstance(handle, str)
            or handle not in targets
            or handle in paged_handles
            for handle in normalized
        ):
            raise _ServiceError(
                "STALE_PREVIEW", "Stored nutrition backfill pages are unavailable"
            )
        batch_handles.add(batch_handle)
        paged_handles.update(normalized)
        pages.append(
            {"batch_handle": batch_handle, "item_handles": normalized}
        )
    if paged_handles != set(targets):
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill pages are unavailable"
        )
    return tuple(pages)


def _nutrition_facts_snapshot(
    facts: nutrition.NutritionFacts,
) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        key: format(getattr(facts, key), "f")
        for key in (
            "calories",
            "protein",
            "fat",
            "carbohydrate",
            "fiber",
            "sodium",
        )
    }
    result.update(
        {
            "source": facts.source,
            "source_grade": facts.source_grade,
        }
    )
    if facts.uncertainty is not None:
        result["uncertainty"] = facts.uncertainty
    if facts.hydration_ml is not None:
        result["hydration_ml"] = format(facts.hydration_ml, "f")
    return result


def _nutrition_backfill_staged(
    stored: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    values = stored.get("staged_estimates", {})
    if not isinstance(values, Mapping) or any(
        not isinstance(handle, str) or handle not in targets
        for handle in values
    ):
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill estimates are unavailable"
        )
    try:
        return {
            handle: _nutrition_facts_snapshot(
                _nutrition_facts(_mapping_value(value, "nutrition estimate"))
            )
            for handle, value in values.items()
        }
    except (TypeError, ValueError) as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Stored nutrition backfill estimates are unavailable"
        ) from error


def _nutrition_backfill_page(
    stored: Mapping[str, Any],
    *,
    meal_handle: str,
    batch_handle: str,
) -> Mapping[str, Any]:
    targets = _nutrition_backfill_targets(stored)
    pages = _nutrition_backfill_pages(stored, targets)
    staged = _nutrition_backfill_staged(stored, targets)
    page_index = next(
        (
            index
            for index, page in enumerate(pages)
            if page["batch_handle"] == batch_handle
        ),
        None,
    )
    if page_index is None:
        raise _ServiceError(
            "STALE_PREVIEW", "Nutrition backfill batch is stale"
        )
    page = pages[page_index]
    public_items = tuple(
        {
            key: targets[item_handle].get(key)
            for key in (
                "item_handle",
                "parent_item_handle",
                "item_role",
                "display_order",
                "raw_name",
                "amount",
                "unit",
            )
        }
        for item_handle in page["item_handles"]
    )
    return {
        "meal_handle": meal_handle,
        "occurred_at": stored.get("occurred_at"),
        "source_text": stored.get("source_text"),
        "total_item_count": len(targets),
        "batch_item_count": len(public_items),
        "staged_item_count": len(staged),
        "remaining_item_count": len(targets) - len(staged),
        "batch_handle": batch_handle,
        "next_batch_handle": (
            pages[page_index + 1]["batch_handle"]
            if page_index + 1 < len(pages)
            else None
        ),
        "items": public_items,
    }


def _nutrition_backfill_batch_estimates(
    stored: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, int]]:
    targets = _nutrition_backfill_targets(stored)
    pages = _nutrition_backfill_pages(stored, targets)
    values = payload.get("items")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or not values
        or len(values) > MAX_TOTAL_ITEMS
    ):
        raise ValueError(
            f"items must contain between 1 and {MAX_TOTAL_ITEMS} entries"
        )
    if not all(isinstance(value, Mapping) for value in values):
        raise ValueError("items must contain objects")

    uses_handles = all(
        "item_handle" in value and "display_order" not in value
        for value in values
    )
    uses_display_order = all(
        "display_order" in value and "item_handle" not in value
        for value in values
    )
    expected_handles: set[str]
    resolved: list[tuple[str, Mapping[str, Any], int]]
    if uses_handles:
        supplied_batch = payload.get("batch_handle")
        if len(pages) > 1 and supplied_batch is None:
            raise ValueError("batch_handle is required for paged nutrition backfill")
        if supplied_batch is None:
            page = pages[0]
        else:
            batch_handle = _workflow_handle_text(payload, "batch_handle")
            page = next(
                (
                    candidate
                    for candidate in pages
                    if candidate["batch_handle"] == batch_handle
                ),
                None,
            )
            if page is None:
                raise _ServiceError(
                    "STALE_PREVIEW", "Nutrition backfill batch is stale"
                )
        expected_handles = set(page["item_handles"])
        resolved = []
        for index, value in enumerate(values):
            handle = _workflow_handle_text(value, "item_handle")
            if handle in {item[0] for item in resolved}:
                raise _ServiceError(
                    "INVALID_INPUT",
                    "The request is invalid",
                    field=f"items[{index}].item_handle",
                    reason="duplicate_target",
                    expected="one unique queried item_handle",
                    retryable=True,
                )
            if handle not in expected_handles:
                raise _ServiceError(
                    "INVALID_INPUT",
                    "The request is invalid",
                    field=f"items[{index}].item_handle",
                    reason="unknown_target",
                    expected="an item_handle from the selected batch",
                    retryable=True,
                )
            resolved.append((handle, value, index))
    elif uses_display_order:
        if "batch_handle" in payload:
            raise ValueError(
                "batch_handle cannot be used with legacy display_order items"
            )
        if len(pages) != 1 or any(
            target["parent_row_id"] is not None for target in targets.values()
        ):
            raise ValueError(
                "display_order is only supported for one-page flat legacy meals"
            )
        by_order: dict[int, str] = {}
        for handle, target in targets.items():
            order = target["display_order"]
            if order in by_order:
                raise ValueError(
                    "display_order is ambiguous; use item_handle"
                )
            by_order[order] = handle
        expected_handles = set(targets)
        resolved = []
        for index, value in enumerate(values):
            order = value.get("display_order")
            if (
                isinstance(order, bool)
                or not isinstance(order, int)
                or order not in by_order
            ):
                raise ValueError(f"items[{index}].display_order is invalid")
            resolved.append((by_order[order], value, index))
    else:
        raise ValueError(
            "each item must use exactly one of item_handle or display_order"
        )

    if {handle for handle, _, _ in resolved} != expected_handles:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field="items",
            reason="incomplete_target_set",
            expected="exactly one estimate for every item in the selected batch",
            retryable=True,
        )

    estimates: dict[str, Mapping[str, Any]] = {}
    indexes: dict[str, int] = {}
    for handle, value, index in resolved:
        if handle in estimates:
            raise ValueError(f"items[{index}] targets a duplicate item")
        if handle not in expected_handles:
            raise ValueError(f"items[{index}] targets an unexpected item")
        facts = _nutrition_facts(
            _required_mapping(value, "nutrition_estimate")
        )
        if facts.source_grade not in {"C", "D"}:
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"items[{index}].nutrition_estimate.source_grade",
                reason="unsupported_value",
                expected="C or D",
                retryable=True,
            )
        estimates[handle] = _nutrition_facts_snapshot(facts)
        indexes[handle] = index
    if set(estimates) != expected_handles:
        raise ValueError(
            "every item in the nutrition backfill batch needs exactly one estimate"
        )
    return estimates, indexes


def _merge_nutrition_backfill_estimates(
    stored: Mapping[str, Any],
    batch: Mapping[str, Mapping[str, Any]],
    indexes: Mapping[str, int],
) -> dict[str, Mapping[str, Any]]:
    targets = _nutrition_backfill_targets(stored)
    merged = _nutrition_backfill_staged(stored, targets)
    for handle, estimate in batch.items():
        existing = merged.get(handle)
        if existing is not None and existing != estimate:
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"items[{indexes[handle]}].nutrition_estimate",
                reason="conflicting_replay",
                expected="the same estimate previously staged for this item",
                retryable=True,
            )
        merged[handle] = estimate
    return merged


def _pending_nutrition_backfill_result(
    stored: Mapping[str, Any], meal_handle: str
) -> Mapping[str, Any]:
    targets = _nutrition_backfill_targets(stored)
    pages = _nutrition_backfill_pages(stored, targets)
    staged = _nutrition_backfill_staged(stored, targets)
    next_batch = next(
        (
            page["batch_handle"]
            for page in pages
            if not set(page["item_handles"]).issubset(staged)
        ),
        None,
    )
    return {
        "status": "pending",
        "meal_handle": meal_handle,
        "staged_item_count": len(staged),
        "remaining_item_count": len(targets) - len(staged),
        "next_batch_handle": next_batch,
    }


def _stage_nutrition_backfill_batch(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    meal_handle: str,
    now: datetime,
) -> Mapping[str, Any] | None:
    connection = service.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        reference = _workflow_row(
            connection,
            meal_handle,
            "meal_reference",
            now=now,
            allow_consumed=True,
        )
        if reference["consumed_at"] is not None:
            result = _stored_object(
                reference["result_json"], "stored nutrition backfill result"
            )
            connection.commit()
            return result
        stored = _stored_object(
            reference["result_json"], "stored nutrition backfill reference"
        )
        _assert_nutrition_backfill_fresh(connection, stored)
        batch, indexes = _nutrition_backfill_batch_estimates(stored, payload)
        merged = _merge_nutrition_backfill_estimates(stored, batch, indexes)
        targets = _nutrition_backfill_targets(stored)
        if len(merged) == len(targets):
            connection.rollback()
            return None
        if merged != _nutrition_backfill_staged(stored, targets):
            updated = dict(stored)
            updated["staged_estimates"] = merged
            updated["state_version"] = (
                int(stored.get("state_version", 0)) + 1
            )
            changed = connection.execute(
                """
                UPDATE operation_previews
                SET result_json = ?
                WHERE token_hash = ? AND consumed_at IS NULL
                  AND result_json = ?
                """,
                (
                    _canonical_json(updated),
                    _workflow_hash(meal_handle),
                    reference["result_json"],
                ),
            ).rowcount
            if changed != 1:
                raise _WorkflowConsumedRace
            stored = updated
        connection.commit()
        return _pending_nutrition_backfill_result(stored, meal_handle)
    except BaseException:
        connection.rollback()
        raise


def _finalize_nutrition_backfill(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    meal_handle: str,
    now: datetime,
) -> Mapping[str, Any]:
    transaction_id = f"txn_service_{secrets.token_urlsafe(18)}"
    committed_at = _workflow_timestamp(now)

    def mutate(context):
        reference = _workflow_row(
            service.connection,
            meal_handle,
            "meal_reference",
            now=now,
            allow_consumed=True,
        )
        if reference["consumed_at"] is not None:
            raise _WorkflowConsumedRace
        stored = _stored_object(
            reference["result_json"], "stored nutrition backfill reference"
        )
        _assert_nutrition_backfill_fresh(service.connection, stored)
        batch, indexes = _nutrition_backfill_batch_estimates(stored, payload)
        merged = _merge_nutrition_backfill_estimates(stored, batch, indexes)
        targets = _nutrition_backfill_targets(stored)
        if set(merged) != set(targets):
            raise _ServiceError(
                "STALE_PREVIEW", "Nutrition backfill staging is incomplete"
            )
        meal = nutrition_backfill.apply_backfill_in_context(
            service.connection,
            context,
            meal_id=_positive_integer(stored.get("meal_id"), "stored meal"),
            expected_item_signature=_required_text(
                stored, "expected_item_signature"
            ),
            estimates={
                target["row_id"]: _nutrition_facts(merged[handle])
                for handle, target in targets.items()
            },
            now=now,
        )
        public_result = {
            "status": "committed",
            "meal": _public_value(meal),
        }
        changed = service.connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?, consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
              AND result_json = ?
            """,
            (
                _canonical_json(public_result),
                committed_at,
                transaction_id,
                _workflow_hash(meal_handle),
                reference["result_json"],
            ),
        ).rowcount
        if changed != 1:
            raise _WorkflowConsumedRace
        return public_result

    try:
        return TransactionManager(service.connection).execute(
            "record_correction",
            _required_text(payload, "source_text")
            if "source_text" in payload
            else "nutrition backfill",
            mutate,
            internal_id=transaction_id,
        ).value
    except _WorkflowConsumedRace:
        raced = _workflow_row(
            service.connection,
            meal_handle,
            "meal_reference",
            now=now,
            allow_consumed=True,
        )
        if raced["consumed_at"] is None:
            raise _ServiceError(
                "STALE_PREVIEW", "Nutrition backfill reference is stale"
            )
        return _stored_object(
            raced["result_json"], "stored nutrition backfill result"
        )
    except nutrition_backfill.BackfillStaleError as error:
        raise _ServiceError(
            "STALE_PREVIEW", "Nutrition backfill reference is stale"
        ) from error


_MEAL_ACTIONS = {
    "record": _meal_record,
    "record_cooking": _meal_record_cooking,
    "record_prepared": _meal_record_prepared,
    "save_recipe": _meal_save_recipe,
    "suggest_recipes": _meal_suggest_recipes,
    "preview_meal_plan": _meal_preview_meal_plan,
    "preview_record": _meal_preview,
    "commit_record": _meal_commit,
    "query": _meal_query,
    "update": _meal_update,
    "delete": _meal_delete,
    "nutrition_estimate": _meal_nutrition_estimate,
}
_WATER_ACTIONS = {
    "record": _water_record,
    "query": _water_query,
    "update": _water_update,
    "delete": _water_delete,
}
_WEIGHT_ACTIONS = {
    "record": _weight_record,
    "query": _weight_query,
    "update": _weight_update,
    "delete": _weight_delete,
}
_PANTRY_ACTIONS = {
    "add": _pantry_add,
    "preview_add": _pantry_preview_add,
    "commit_add": _pantry_commit_add,
    "preview_update_metadata": _pantry_preview_update_metadata,
    "commit_update_metadata": _pantry_commit_update_metadata,
    "preview_link_nutrition": _pantry_preview_link_nutrition,
    "commit_link_nutrition": _pantry_commit_link_nutrition,
    "preview_shopping_list": _pantry_preview_shopping_list,
    "commit_shopping_list": _pantry_commit_shopping_list,
    "query_shopping_list": _pantry_query_shopping_list,
    "cancel_shopping_list": _pantry_cancel_shopping_list,
    "query": _pantry_query,
    "search": _pantry_search,
    "adjust": _pantry_adjust,
    "discard": _pantry_discard,
    "deduct": _pantry_deduct,
    "open": _pantry_open,
    "freeze": _pantry_freeze,
    "thaw": _pantry_thaw,
    "preview_deduct": _pantry_preview_deduct,
    "commit_deduct": _pantry_commit_deduct,
}
_TRANSACTION_ACTIONS = {
    "get_recent": _transaction_recent,
    "undo": _transaction_undo,
    "redo": _transaction_redo,
}
_REPORT_ACTIONS = {
    "today": _report_today,
    "daily": _report_daily,
    "weekly": _report_weekly,
    "monthly": _report_monthly,
    "progress": _report_progress,
    "insights": _report_insights,
    "expiring_inventory": _report_expiring,
    "cost_summary": _report_cost_summary,
    "waste_summary": _report_waste_summary,
    "trend_summary": _report_trend_summary,
}
_SYSTEM_ACTIONS = {
    "initialize": _system_initialize,
    "self_check": _system_self_check,
    "maintenance_status": _system_maintenance_status,
    "maintenance_history": _system_maintenance_history,
    "repair": _system_repair,
    "validate_database": _system_validate_database,
    "backup": _system_backup,
    "restore": _system_restore,
    "migrate": _system_migrate,
    "export_data": _system_export_data,
    "validate_import": _system_validate_import,
    "import_data": _system_import_data,
    "preview_delete_data": _system_preview_delete_data,
    "commit_delete_data": _system_commit_delete_data,
    "query_preferences": _system_query_preferences,
    "query_goals": _system_query_goals,
    "update_goals": _system_update_goals,
    "update_preferences": _system_update_preferences,
    "forget_preference": _system_forget_preference,
    "query_nutrition_backfill": _system_query_nutrition_backfill,
    "commit_nutrition_backfill": _system_commit_nutrition_backfill,
}

_IMPLEMENTED_ACTION_MAPS = {
    "meal": _MEAL_ACTIONS,
    "water": _WATER_ACTIONS,
    "weight": _WEIGHT_ACTIONS,
    "pantry": _PANTRY_ACTIONS,
    "transaction": _TRANSACTION_ACTIONS,
    "report": _REPORT_ACTIONS,
    "system": _SYSTEM_ACTIONS,
}
for _domain_name, _implemented_actions in _IMPLEMENTED_ACTION_MAPS.items():
    _declared_handlers = ACTION_HANDLER_NAMES[_domain_name]
    _actual_handlers = {
        action_name: handler.__name__
        for action_name, handler in _implemented_actions.items()
    }
    if _actual_handlers != _declared_handlers:
        raise RuntimeError(
            f"Generated tool contract drift for {_domain_name}"
        )

ACTIONS: dict[str, frozenset[str]] = CONTRACT_ACTIONS


def _validated_request(
    request: Mapping[str, Any],
) -> tuple[
    str,
    str,
    Mapping[str, Any],
    Mapping[str, Any],
    OperationContext | None,
]:
    if not isinstance(request, Mapping):
        raise _ServiceError("INVALID_INPUT", "Request must be a JSON object")
    extra = set(request) - _REQUEST_KEYS
    if extra:
        raise _ServiceError(
            "INVALID_INPUT",
            "Request contains unsupported fields: " + ", ".join(sorted(extra)),
        )
    domain = _required_text(request, "domain")
    action = _required_text(request, "action")
    payload = request.get("payload", {})
    context = request.get("context", {})
    if not isinstance(payload, Mapping):
        raise _ServiceError("INVALID_INPUT", "payload must be a JSON object")
    if not isinstance(context, Mapping):
        raise _ServiceError("INVALID_INPUT", "context must be a JSON object")
    private_operation: OperationContext | None = None
    if "_internal" in request:
        if (domain, action) not in _FORMAL_MUTATION_ACTIONS:
            raise _ServiceError(
                "INVALID_INPUT",
                "Internal operation context is only accepted for formal mutations",
            )
        private_operation = _validated_operation_context(request["_internal"])
    return domain, action, payload, context, private_operation


def _validated_operation_context(value: Any) -> OperationContext:
    allowed = {
        "operation_id",
        "request_fingerprint",
        "semantic_fingerprint",
        "source_session_key",
        "source_model",
        "test_run_id",
    }
    if not isinstance(value, Mapping) or not {"operation_id", "request_fingerprint"} <= set(value) or set(value) - allowed:
        raise _ServiceError("INVALID_INPUT", "Internal operation context is invalid")
    return OperationContext(
        operation_id=_operation_id(value.get("operation_id")),
        request_fingerprint=_request_fingerprint(
            value.get("request_fingerprint")
        ),
        semantic_fingerprint=(
            _request_fingerprint(value["semantic_fingerprint"])
            if value.get("semantic_fingerprint") is not None
            else None
        ),
        source_session_key=_provenance_text(value.get("source_session_key")),
        source_model=_provenance_text(value.get("source_model")),
        test_run_id=_provenance_text(value.get("test_run_id")),
    )


def _provenance_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise _ServiceError("INVALID_INPUT", "Internal operation context is invalid")
    return value


def _validated_status_request(value: Any) -> tuple[str, str]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"kind", "operation_id", "request_fingerprint"}
        or value.get("kind") != "operation_status"
    ):
        raise _ServiceError("INVALID_INPUT", "Internal status request is invalid")
    try:
        fingerprint = _request_fingerprint(value.get("request_fingerprint"))
    except _ServiceError as error:
        raise _ServiceError(
            "INVALID_INPUT", "Internal status request is invalid"
        ) from error
    return _operation_id(value.get("operation_id")), fingerprint


def _request_fingerprint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _REQUEST_FINGERPRINT_PATTERN.fullmatch(value) is None
    ):
        raise _ServiceError(
            "INVALID_INPUT", "Internal request fingerprint is invalid"
        )
    return value


def _operation_id(value: Any) -> str:
    if not isinstance(value, str) or _OPERATION_ID_PATTERN.fullmatch(value) is None:
        raise _ServiceError("INVALID_INPUT", "Internal operation identifier is invalid")
    return value


def _meal_draft(
    service: DietService, value: Mapping[str, Any], *, now: datetime
) -> meals.MealDraft:
    item_values = value.get("items")
    if not isinstance(item_values, Sequence) or isinstance(
        item_values, (str, bytes, bytearray)
    ):
        raise ValueError("items must be an array")
    allow_expired_consumption = _optional_bool(
        value.get("_turn_completed_consumption"),
        "_turn_completed_consumption",
        default=False,
    )
    source_text = _required_text(value, "source_text")
    return meals.MealDraft(
        intent=_optional_text(value.get("intent"), "intent") or "record",
        occurred_at=(
            _datetime_value(value["occurred_at"], "occurred_at")
            if "occurred_at" in value
            else now
        ),
        meal_type=_required_text(value, "meal_type"),
        source_text=source_text,
        location_type=_required_text(value, "location_type"),
        items=tuple(
            _meal_item(
                service,
                _mapping_value(item, "meal item"),
                now=now,
                field=f"items[{index}]",
                allow_expired_consumption=allow_expired_consumption,
                source_text=source_text,
            )
            for index, item in enumerate(item_values)
        ),
        nutrition_repository=nutrition.NutritionRepository(
            service.rules_dir, service.connection
        ),
    )


def _cooking_draft(
    service: DietService, value: Mapping[str, Any], *, now: datetime
) -> meals.CookingDraft:
    dish = _required_mapping(value, "dish")
    ingredients = dish.get("ingredients")
    if not isinstance(ingredients, Sequence) or isinstance(
        ingredients, (str, bytes, bytearray)
    ) or not ingredients:
        raise ValueError("dish.ingredients must be a non-empty array")
    leftover_value = dish.get("leftover")
    leftover = None
    if leftover_value is not None:
        leftover_values = _mapping_value(leftover_value, "dish.leftover")
        leftover = prepared_foods.LeftoverDraft(
            food_name=_required_text(leftover_values, "food_name"),
            normalized_name=_required_text(leftover_values, "normalized_name"),
            quantity=_required_decimal(leftover_values, "quantity"),
            unit=_required_text(leftover_values, "unit"),
            storage_location=_required_text(leftover_values, "storage_location"),
            expires_at=_required_expiry_datetime(
                leftover_values,
                timezone_name=service.settings.profile.timezone,
            ),
        )
    return meals.CookingDraft(
        occurred_at=(
            _datetime_value(value["occurred_at"], "occurred_at")
            if "occurred_at" in value
            else now
        ),
        meal_type=_required_text(value, "meal_type"),
        source_text=_required_text(value, "source_text"),
        dish_name=_required_text(dish, "raw_name"),
        normalized_name=_required_text(dish, "normalized_name"),
        unit=_required_text(dish, "unit"),
        consumed_quantity=_required_decimal(dish, "consumed_quantity"),
        leftover=leftover,
        ingredients=tuple(
            _meal_item(
                service,
                _mapping_value(item, "dish ingredient"),
                now=now,
                field=f"dish.ingredients[{index}]",
            )
            for index, item in enumerate(ingredients)
        ),
        nutrition_repository=nutrition.NutritionRepository(
            service.rules_dir, service.connection
        ),
    )


def _meal_item(
    service: DietService,
    value: Mapping[str, Any],
    *,
    now: datetime,
    field: str,
    allow_expired_consumption: bool = False,
    source_text: str | None = None,
) -> meals.MealItemDraft:
    ingredients_value = value.get("ingredients", ())
    if not isinstance(ingredients_value, Sequence) or isinstance(
        ingredients_value, (str, bytes, bytearray)
    ):
        raise ValueError("ingredients must be an array")
    preparation_losses_value = value.get("preparation_losses", ())
    if not isinstance(preparation_losses_value, Sequence) or isinstance(
        preparation_losses_value, (str, bytes, bytearray)
    ):
        raise ValueError("preparation_losses must be an array")
    if len(preparation_losses_value) > 8:
        raise ValueError("preparation_losses must contain at most 8 entries")
    preparation_losses: list[meals.PreparationLossDraft] = []
    for entry in preparation_losses_value:
        loss = _mapping_value(entry, "preparation loss")
        kind = _required_text(loss, "kind")
        if kind not in {"bone", "shell", "skin", "fat", "other"}:
            raise ValueError("preparation loss kind is invalid")
        quantity = _required_decimal(loss, "quantity")
        if quantity <= 0:
            raise ValueError("preparation loss quantity must be positive")
        unit = _required_text(loss, "unit")
        if unit != "g":
            raise ValueError("preparation loss unit must be g")
        preparation_losses.append(
            meals.PreparationLossDraft(
                kind=kind,
                quantity=quantity,
                unit=unit,
                nutrition_facts=_nutrition_facts(
                    _required_mapping(loss, "nutrition_facts")
                ),
            )
        )
    signals_value = value.get("confidence_signals", {})
    signals = _mapping_value(signals_value, "confidence_signals")
    leftover_value = value.get("leftover")
    leftover = None
    if leftover_value is not None:
        leftover_values = _mapping_value(leftover_value, "leftover")
        leftover = prepared_foods.LeftoverDraft(
            food_name=_required_text(leftover_values, "food_name"),
            normalized_name=_required_text(leftover_values, "normalized_name"),
            quantity=_required_decimal(leftover_values, "quantity"),
            unit=_required_text(leftover_values, "unit"),
            storage_location=_required_text(
                leftover_values, "storage_location"
            ),
            expires_at=_required_expiry_datetime(
                leftover_values,
                timezone_name=service.settings.profile.timezone,
            ),
        )

    def confidence(name: str) -> Decimal | None:
        source = signals[name] if name in signals else value.get(name)
        return _optional_decimal(source, name)

    has_nutrition_facts = value.get("nutrition_facts") is not None
    has_nutrition_estimate = value.get("nutrition_estimate") is not None
    has_direct_nutrition = has_nutrition_facts or has_nutrition_estimate
    basis_value = value.get("nutrition_basis")
    if has_nutrition_facts and has_nutrition_estimate:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.nutrition_estimate",
            reason="incompatible",
            expected="exactly one of nutrition_facts or nutrition_estimate",
            retryable=True,
        )
    if has_direct_nutrition and basis_value is None:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.nutrition_basis",
            reason="required",
            expected="a nutrition basis for direct nutrition evidence",
            retryable=True,
        )
    if not has_direct_nutrition and basis_value is not None:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.nutrition_basis",
            reason="incompatible",
            expected="nutrition_facts or nutrition_estimate with the basis",
            retryable=True,
        )
    try:
        nutrition_basis = (
            nutrition_normalization.NutritionBasis(
                _required_text(value, "nutrition_basis")
            )
            if basis_value is not None
            else None
        )
    except ValueError as error:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.nutrition_basis",
            reason="incompatible",
            expected="per_100g, per_100ml, per_serving, or consumed_total",
            retryable=True,
        ) from error
    normalized_name = _required_text(value, "normalized_name")
    item_amount = _optional_decimal(value.get("amount"), "amount")
    effective_unit = _optional_text(value.get("unit"), "unit")
    consumed_weight_g = _optional_decimal(
        value.get("consumed_weight_g"), "consumed_weight_g"
    )
    consumed_volume_ml = _optional_decimal(
        value.get("consumed_volume_ml"), "consumed_volume_ml"
    )
    consumed_servings = _optional_decimal(
        value.get("consumed_servings"), "consumed_servings"
    )
    resolved_nutrition_facts = (
        _nutrition_facts(
            _required_mapping(value, "nutrition_facts"),
            allow_partial=True,
        )
        if value.get("nutrition_facts") is not None
        else None
    )
    resolved_nutrition_estimate = (
        _nutrition_facts(_required_mapping(value, "nutrition_estimate"))
        if value.get("nutrition_estimate") is not None
        else None
    )
    nutrition_dataset_version = _optional_text(
        value.get("nutrition_dataset_version"),
        "nutrition_dataset_version",
    )
    inventory_match_name = None
    handle = _optional_text(
        value.get("inventory_match_handle"), "inventory_match_handle"
    )
    if handle is not None:
        selected = _pantry_product_reference_snapshot(
            service,
            handle,
            now=now,
        )
        if (
            selected.get("usable_for_consumption") is False
            and not allow_expired_consumption
        ):
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"{field}.inventory_match_handle",
                reason="expired_inventory",
                expected=(
                    "a usable inventory selection or host-authorized "
                    "completed consumption"
                ),
                retryable=True,
            )
        selected_name = _required_text(selected, "normalized_name")
        if normalized_name.casefold() != selected_name.casefold():
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"{field}.inventory_match_handle",
                reason="identity_mismatch",
                expected=(
                    "the normalized_name and unit returned with this handle"
                ),
                retryable=True,
            )
        item_amount, effective_unit = _pantry_reference_base_amount(
            selected,
            amount=item_amount,
            unit=effective_unit,
            field=field,
        )
        if item_amount is not None:
            (
                consumed_weight_g,
                consumed_volume_ml,
                consumed_servings,
            ) = _pantry_reference_consumed_measures(
                base_amount=item_amount,
                base_unit=effective_unit,
                consumed_weight_g=consumed_weight_g,
                consumed_volume_ml=consumed_volume_ml,
                consumed_servings=consumed_servings,
                field=field,
            )
        if not has_direct_nutrition:
            linked_nutrition = _pantry_reference_complete_nutrition(selected)
            if linked_nutrition is not None:
                (
                    resolved_nutrition_facts,
                    nutrition_basis,
                    nutrition_dataset_version,
                ) = linked_nutrition
                if (
                    nutrition_basis
                    is nutrition_normalization.NutritionBasis.PER_SERVING
                    and consumed_servings is None
                    and item_amount is not None
                    and effective_unit is not None
                ):
                    consumed_servings = _pantry_reference_servings(
                        selected,
                        base_amount=item_amount,
                        base_unit=effective_unit,
                        field=field,
                    )
        normalized_name = selected_name
        inventory_match_name = selected_name

    if nutrition_basis is not None:
        measure_fields = {
            nutrition_normalization.NutritionBasis.PER_100G: (
                "consumed_weight_g",
                consumed_weight_g,
            ),
            nutrition_normalization.NutritionBasis.PER_100ML: (
                "consumed_volume_ml",
                consumed_volume_ml,
            ),
            nutrition_normalization.NutritionBasis.PER_SERVING: (
                "consumed_servings",
                consumed_servings,
            ),
            nutrition_normalization.NutritionBasis.CONSUMED_TOTAL: (None, None),
        }
        measure_field, measure_value = measure_fields[nutrition_basis]
        if measure_field is not None and (
            measure_value is None or measure_value <= 0
        ):
            raise _ServiceError(
                "INVALID_INPUT",
                "The request is invalid",
                field=f"{field}.{measure_field}",
                reason="required",
                expected=(
                    f"a positive {measure_field} for {nutrition_basis.value}"
                ),
                retryable=True,
            )

    quantity_estimate = _quantity_estimate(
        service,
        value,
        amount=item_amount,
        unit=effective_unit,
        consumed_weight_g=consumed_weight_g,
        consumed_volume_ml=consumed_volume_ml,
        field=field,
    )
    submitted_portion_expression = _optional_text(
        value.get("portion_expression"), "portion_expression"
    )
    portion_nutrition = resolved_nutrition_facts or resolved_nutrition_estimate
    normalized_portion_expression = portion_evidence.normalize_portion_expression(
        portion_expression=submitted_portion_expression,
        amount=item_amount,
        unit=effective_unit,
        consumed_weight_g=consumed_weight_g,
        source_text=source_text or "",
        quantity_estimated=quantity_estimate is not None,
        nutrition_source=(
            portion_nutrition.source if portion_nutrition is not None else None
        ),
        nutrition_uncertainty=(
            portion_nutrition.uncertainty if portion_nutrition is not None else None
        ),
    )
    source_confidence = confidence("source_confidence")
    if handle is not None and source_confidence is None:
        # A validated pantry product reference is positive evidence that this
        # item is meant to consume the selected household stock.  Without this
        # normalization, an omitted meal location is scored as only 0.5 and a
        # fully bound item falls below the auto-deduction threshold, leaving
        # the meal committed while the pantry remains unchanged.
        source_confidence = Decimal("1")

    return meals.MealItemDraft(
        raw_name=_required_text(value, "raw_name"),
        normalized_name=normalized_name,
        inventory_match_name=inventory_match_name,
        amount=item_amount,
        unit=effective_unit,
        portion_expression=normalized_portion_expression,
        quantity_estimate=quantity_estimate,
        consumed_weight_g=consumed_weight_g,
        consumed_volume_ml=consumed_volume_ml,
        consumed_servings=consumed_servings,
        raw_weight_g=_optional_decimal(value.get("raw_weight_g"), "raw_weight_g"),
        inventory_deduction_weight_g=_optional_decimal(
            value.get("inventory_deduction_weight_g"),
            "inventory_deduction_weight_g",
        ),
        edible_ratio=_optional_decimal(value.get("edible_ratio"), "edible_ratio"),
        cooking_yield=_optional_decimal(
            value.get("cooking_yield"), "cooking_yield"
        ),
        nutrition_facts=resolved_nutrition_facts,
        preparation_losses=tuple(preparation_losses),
        nutrition_basis=nutrition_basis,
        nutrition_dataset_version=nutrition_dataset_version,
        brand=_optional_text(value.get("brand"), "brand"),
        nutrition_estimate=resolved_nutrition_estimate,
        confidence_signals=meals.ConfidenceSignals(
            source_confidence=source_confidence,
            name_match_confidence=confidence("name_match_confidence"),
            quantity_confidence=confidence("quantity_confidence"),
            batch_uniqueness=confidence("batch_uniqueness"),
            context_consistency=confidence("context_consistency"),
            personal_rule_confidence=confidence("personal_rule_confidence"),
        ),
        ingredients=tuple(
            _meal_item(
                service,
                _mapping_value(item, "ingredient"),
                now=now,
                field=f"{field}.ingredients[{index}]",
                allow_expired_consumption=allow_expired_consumption,
                source_text=source_text,
            )
            for index, item in enumerate(ingredients_value)
        ),
        leftover=leftover,
    )


def _quantity_estimate(
    service: DietService,
    value: Mapping[str, Any],
    *,
    amount: Decimal | None,
    unit: str | None,
    consumed_weight_g: Decimal | None,
    consumed_volume_ml: Decimal | None,
    field: str,
) -> meals.QuantityEstimate | None:
    raw = value.get("quantity_estimate")
    if raw is None:
        return None
    estimate = _mapping_value(raw, "quantity_estimate")
    expression = _optional_text(
        value.get("portion_expression"), "portion_expression"
    )
    if expression is None:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.portion_expression",
            reason="required",
            expected="the original non-exact portion expression",
            retryable=True,
        )
    if amount is None or unit is None:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.quantity_estimate",
            reason="missing_dependency",
            expected="amount and unit with quantity_estimate",
            retryable=True,
        )
    suggested = _required_decimal(estimate, "suggested")
    lower = _required_decimal(estimate, "lower")
    upper = _required_decimal(estimate, "upper")
    if (
        not lower.is_finite()
        or not suggested.is_finite()
        or not upper.is_finite()
        or lower <= 0
        or not lower <= suggested <= upper
    ):
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.quantity_estimate",
            reason="invalid_bounds",
            expected="0 < lower <= suggested <= upper",
            retryable=True,
        )
    estimate_unit = _required_text(estimate, "unit")
    matches_display_quantity = (
        estimate_unit.casefold() == unit.casefold() and suggested == amount
    )
    normalized_estimate_unit = estimate_unit.casefold()
    consumed_measure = (
        consumed_weight_g
        if normalized_estimate_unit in {"g", "克"}
        else consumed_volume_ml
        if normalized_estimate_unit in {"ml", "毫升"}
        else None
    )
    if not matches_display_quantity and suggested != consumed_measure:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.quantity_estimate",
            reason="value_mismatch",
            expected=(
                "suggested/unit matching the meal item amount/unit or its "
                "consumed weight/volume"
            ),
            retryable=True,
        )
    evidence_type = _required_text(estimate, "evidence_type")
    policy_key = _required_text(estimate, "policy_key")
    try:
        policy = service.policies.entry("quantity-evidence", policy_key)
    except ConfigurationError as error:
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.quantity_estimate.policy_key",
            reason="unknown_policy",
            expected="a registered bounded quantity policy",
            retryable=True,
        ) from error
    authority = policy.values.get("authority")
    confirmation_required = policy.values.get("confirmation_required")
    if (
        policy.operator != "bounded_quantity"
        or not isinstance(authority, str)
        or authority != evidence_type
        or not isinstance(confirmation_required, bool)
    ):
        raise _ServiceError(
            "INVALID_INPUT",
            "The request is invalid",
            field=f"{field}.quantity_estimate",
            reason="policy_mismatch",
            expected="evidence matching a registered bounded quantity policy",
            retryable=True,
        )
    return meals.QuantityEstimate(
        suggested=suggested,
        lower=lower,
        upper=upper,
        unit=estimate_unit,
        evidence_type=evidence_type,
        policy_key=policy_key,
        confirmation_required=confirmation_required,
    )


def _quantity_resolution(
    service: DietService,
    draft: meals.MealDraft,
) -> Mapping[str, Any] | None:
    resolutions: list[dict[str, Any]] = []

    def visit(item: meals.MealItemDraft) -> None:
        estimate = item.quantity_estimate
        if estimate is not None:
            learned = (
                learning.learned_portion(
                    service.connection,
                    item.normalized_name,
                    item.portion_expression or "",
                )
                if item.portion_expression is not None
                else None
            )
            if learned is not None:
                resolution = {
                    "subject": item.normalized_name,
                    "state": "confirmed_fact",
                    "normalized_value": {
                        "value": learned.amount,
                        "unit": learned.unit,
                    },
                    "interval": None,
                    "evidence": {
                        "type": "user_confirmed",
                        "policy_key": "portion.confirmed_personal_rule",
                    },
                    "policy_key": "portion.confirmed_personal_rule",
                    "requires_confirmation": False,
                    "confirmation_options": (),
                    "warnings": (),
                }
            else:
                resolution = {
                    "subject": item.normalized_name,
                    "state": "bounded_estimate",
                    "normalized_value": {
                        "value": estimate.suggested,
                        "unit": estimate.unit,
                    },
                    "interval": {
                        "lower": estimate.lower,
                        "upper": estimate.upper,
                        "unit": estimate.unit,
                    },
                    "evidence": {
                        "type": estimate.evidence_type,
                        "policy_key": estimate.policy_key,
                    },
                    "policy_key": estimate.policy_key,
                    "requires_confirmation": estimate.confirmation_required,
                    "confirmation_options": (
                        {
                            "label": "Use the suggested estimate",
                            "value": estimate.suggested,
                            "unit": estimate.unit,
                        },
                        {
                            "label": "Provide a different exact quantity",
                            "requires_new_preview": True,
                        },
                    ),
                    "warnings": (
                        "Estimated quantity; no meal or inventory change has been committed.",
                    ),
                }
            resolutions.append(resolution)
        for ingredient in item.ingredients:
            visit(ingredient)

    for item in draft.items:
        visit(item)
    if not resolutions:
        return None
    if len(resolutions) == 1:
        return resolutions[0]
    return {
        "subject": "meal_items",
        "state": "bounded_estimates",
        "items": tuple(resolutions),
        "requires_confirmation": any(
            item["requires_confirmation"] for item in resolutions
        ),
        "confirmation_options": (
            {
                "label": "Confirm all displayed estimates",
                "needs_confirmation": True,
            },
        ),
        "warnings": (
            "Multiple estimates are combined in this one non-writing preview.",
        ),
    }


def _confirmed_quantity_resolution(
    estimates: Sequence[Mapping[str, object]],
) -> Mapping[str, Any]:
    items = tuple(
        {
            "subject": _required_text(estimate, "subject"),
            "state": "confirmed_estimate",
            "normalized_value": {
                "value": _required_text(estimate, "suggested"),
                "unit": _required_text(estimate, "unit"),
            },
            "interval": {
                "lower": _required_text(estimate, "lower"),
                "upper": _required_text(estimate, "upper"),
                "unit": _required_text(estimate, "unit"),
            },
            "evidence": {
                "type": _required_text(estimate, "evidence_type"),
                "policy_key": _required_text(estimate, "policy_key"),
            },
            "policy_key": _required_text(estimate, "policy_key"),
            "requires_confirmation": False,
            "confirmation_options": (),
            "warnings": (
                "The confirmed estimate was committed exactly once.",
            ),
        }
        for estimate in estimates
    )
    if len(items) == 1:
        return items[0]
    return {
        "subject": "meal_items",
        "state": "confirmed_estimates",
        "items": items,
        "requires_confirmation": False,
        "confirmation_options": (),
        "warnings": (
            "All confirmed estimates were committed in one meal transaction.",
        ),
    }


def _nutrition_facts(
    value: Mapping[str, Any], *, allow_partial: bool = False
) -> nutrition.NutritionFacts:
    nutrient_fields = (
        "calories",
        "protein",
        "fat",
        "carbohydrate",
        "fiber",
    )
    nutrients = {
        field: (
            _optional_decimal(value.get(field), field)
            if allow_partial
            else _required_decimal(value, field)
        )
        for field in nutrient_fields
    }
    sodium = _optional_decimal(value.get("sodium"), "sodium")
    hydration_ml = _optional_decimal(value.get("hydration_ml"), "hydration_ml")
    if allow_partial and all(
        nutrient is None
        for nutrient in (*nutrients.values(), sodium, hydration_ml)
    ):
        raise ValueError("nutrition facts must include at least one known nutrient")
    return nutrition.NutritionFacts(
        **nutrients,
        sodium=sodium,
        source=_required_text(value, "source"),
        source_grade=_required_text(value, "source_grade"),
        uncertainty=_optional_text(value.get("uncertainty"), "uncertainty"),
        hydration_ml=hydration_ml,
    )


def _meal_public(
    service: DietService,
    meal_id: int,
    meal: meals.MealRecord,
    *,
    now: datetime,
) -> Mapping[str, Any]:
    public = _meal_record_public(service, meal)
    if service._degraded_error is not None:
        return public
    handle = _issue_workflow(
        service,
        "meal_reference",
        request={"action": "select_meal"},
        result={"meal_id": meal_id},
        resource_versions={
            "updated_at": _public_value(meal.updated_at),
            "deleted_at": _public_value(meal.deleted_at),
        },
        now=now,
    )
    return public | {"workflow": {"meal_handle": handle}}


def _meal_record_public(
    service: DietService,
    meal: meals.MealRecord,
) -> Mapping[str, Any]:
    timezone_name = service.settings.profile.timezone
    return _public_value(meal) | {
        "occurred_at_local": _public_local_timestamp(
            meal.occurred_at, timezone_name
        ),
        "timezone_name": timezone_name,
    }


def _meal_target(
    service: DietService, payload: Mapping[str, Any], *, now: datetime
) -> tuple[int, meals.MealSelector, tuple[str, str | None]]:
    _reject_raw_identifiers(payload, "id", "meal_id", "mealId", "database_id", "databaseId")
    handle = payload.get("meal_handle")
    if handle is not None:
        reference = _workflow_row(
            service.connection,
            _text_value(handle, "meal_handle"),
            "meal_reference",
            now=now,
        )
        target = _stored_object(reference["result_json"], "stored meal reference")
        meal_id = _positive_integer(
            target.get("meal_id"), "stored meal reference"
        )
        expected = _stored_object(
            reference["resource_versions_json"], "stored meal state"
        )
        expected_state = (
            _required_text(expected, "updated_at"),
            _optional_text(expected.get("deleted_at"), "deleted_at"),
        )
        current = service.connection.execute(
            """
            SELECT occurred_at, source_text, updated_at, deleted_at
            FROM meals
            WHERE id = ?
            """,
            (meal_id,),
        ).fetchone()
        if current is None or {
            "updated_at": current["updated_at"],
            "deleted_at": current["deleted_at"],
        } != dict(expected):
            raise _ServiceError("STALE_PREVIEW", "Meal reference is stale")
        return (
            meal_id,
            meals.MealSelector(
                occurred_at=_datetime_value(
                    current["occurred_at"], "meal occurred_at"
                ),
                source_text=current["source_text"],
            ),
            expected_state,
        )

    selector_values = _required_mapping(payload, "selector")
    selector = meals.MealSelector(
        occurred_at=_required_datetime(selector_values, "occurred_at"),
        source_text=_required_text(selector_values, "source_text"),
    )
    candidates = meals._matching_meal_targets(service.connection, selector)
    if not candidates:
        raise KeyError("No active meal matches the supplied selector")
    if len(candidates) == 1:
        meal_id, meal = candidates[0]
        return meal_id, selector, (
            _public_value(meal.updated_at),
            _public_value(meal.deleted_at),
        )
    options = tuple(
        {
            "label": _meal_option_label(meal),
            "meal_handle": _issue_workflow(
                service,
                "meal_reference",
                request={"action": "select_meal"},
                result={"meal_id": meal_id},
                resource_versions={
                    "updated_at": _public_value(meal.updated_at),
                    "deleted_at": _public_value(meal.deleted_at),
                },
                now=now,
            ),
        }
        for meal_id, meal in candidates
    )
    raise _ServiceError(
        "AMBIGUOUS_TARGET",
        "More than one meal matches",
        requires_confirmation=True,
        confirmation_options=options,
    )


def _meal_option_label(meal: meals.MealRecord) -> str:
    occurred = _public_value(meal.occurred_at)
    recorded = _public_value(meal.created_at)
    return f"{meal.meal_type.title()} at {occurred} — recorded {recorded}"[:96]


def _pantry_add_arguments(
    value: Mapping[str, Any],
    *,
    timezone_name: str,
) -> dict[str, Any]:
    food_name = _required_text(value, "food_name")
    normalized_name = _optional_text(
        value.get("normalized_name"), "normalized_name"
    )
    quantity = _required_decimal(value, "quantity")
    pantry._sqlite_real(quantity, "quantity")
    unit = _required_text(value, "unit")
    added_at = _required_datetime(value, "added_at")
    supplied_expiry = _optional_expiry_datetime(
        value,
        timezone_name=timezone_name,
    )
    defaults = pantry_defaults.resolve_pantry_defaults(
        food_name=food_name,
        source_text=_required_text(value, "source_text"),
        added_at=added_at,
        storage_location=_optional_text(
            value.get("storage_location"), "storage_location"
        ),
        expires_at=supplied_expiry,
    )
    return {
        "food_name": food_name,
        "normalized_name": normalized_name,
        "quantity": quantity,
        "unit": unit,
        "added_at": added_at,
        "source_text": _required_text(value, "source_text"),
        "batch_code": _optional_text(value.get("batch_code"), "batch_code"),
        "storage_location": defaults.storage_location,
        "storage_location_source": defaults.storage_location_source,
        "purchase_date": _optional_text(
            value.get("purchase_date"), "purchase_date"
        ),
        "expires_at": defaults.expires_at,
        "expiry_source": defaults.expiry_source,
        "price": _optional_decimal(value.get("price"), "price"),
        "price_minor": (
            _nonnegative_integer(value.get("price_minor"), "price_minor")
            if value.get("price_minor") is not None
            else None
        ),
        "currency": _optional_text(value.get("currency"), "currency"),
        "source": _optional_text(value.get("source"), "source") or "manual",
        "notes": _optional_text(value.get("notes"), "notes"),
        "total_weight_g": _optional_decimal(
            value.get("total_weight_g"), "total_weight_g"
        ),
        "average_unit_weight_g": _optional_decimal(
            value.get("average_unit_weight_g"), "average_unit_weight_g"
        ),
        "weight_basis": _optional_text(
            value.get("weight_basis"), "weight_basis"
        ),
        "weight_source": _optional_text(
            value.get("weight_source"), "weight_source"
        ),
        "weight_confidence": _optional_text(
            value.get("weight_confidence"), "weight_confidence"
        ),
        "initial_display_quantity": _optional_decimal(
            value.get("display_quantity"), "display_quantity"
        ),
        "display_unit": _optional_text(
            value.get("display_unit"), "display_unit"
        ),
        "base_quantity_per_display_unit": _optional_decimal(
            value.get("base_quantity_per_display_unit"),
            "base_quantity_per_display_unit",
        ),
        "package_hierarchy": _package_hierarchy(
            value.get("package_hierarchy")
        ),
    }


def _package_hierarchy(value: Any) -> list[Mapping[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ValueError("package_hierarchy must be an array")
    return [
        _mapping_value(item, f"package_hierarchy[{index}]")
        for index, item in enumerate(value)
    ]


def _nutrition_profile_draft(
    value: Mapping[str, Any],
) -> nutrition_profiles.NutritionProfileDraft:
    nutrition_value = _required_mapping(value, "nutrition")
    return nutrition_profiles.NutritionProfileDraft(
        normalized_name=_required_text(value, "normalized_name"),
        brand=_profile_optional_text(value.get("brand"), "brand"),
        product_key=_profile_optional_text(
            value.get("product_key"), "product_key"
        ),
        serving_basis=_required_text(value, "serving_basis"),
        nutrition={
            str(key): (
                None
                if item is None
                else _optional_decimal(item, f"nutrition.{key}")
            )
            for key, item in nutrition_value.items()
        },
        source_text=_required_text(value, "source_text"),
        source_grade=_required_text(value, "source_grade"),
    )


def _profile_optional_text(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value.strip()


def _pantry_resource_versions(
    connection: sqlite3.Connection, normalized_name: str
) -> list[dict[str, int]]:
    rows = connection.execute(
        """
        SELECT id, version
        FROM pantry_batches
        WHERE normalized_name = ?
        ORDER BY id
        """,
        (normalized_name.strip().lower(),),
    ).fetchall()
    return [
        {"batch_id": int(row["id"]), "version": int(row["version"])}
        for row in rows
    ]


def _pantry_public(
    service: DietService,
    batch_id: int,
    batch: pantry.PantryBatch,
    *,
    now: datetime,
) -> Mapping[str, Any]:
    public = _pantry_batch_value(service, batch) | {
        "remaining_display_quantity": batch.remaining_display_quantity,
        "nutrition": nutrition_profiles.linked_snapshot(
            service.connection, batch_id
        ),
        **reports.describe_expiry(batch.expires_at, now),
    }
    if service._degraded_error is not None:
        return public
    handle = _issue_workflow(
        service,
        "pantry_batch_reference",
        request={"action": "select_pantry_batch"},
        result={"batch_id": batch_id},
        resource_versions={"version": batch.version},
        now=now,
    )
    return public | {"workflow": {"batch_handle": handle}}


def _pantry_compact(
    batch: pantry.PantryBatch,
    *,
    now: datetime,
    timezone_name: str,
) -> Mapping[str, Any]:
    return {
        "food_name": batch.food_name,
        "normalized_name": batch.normalized_name,
        "remaining_quantity": batch.remaining_quantity,
        "unit": batch.unit,
        "remaining_display_quantity": batch.remaining_display_quantity,
        "display_unit": batch.display_unit,
        "status": batch.status,
        "storage_location": batch.storage_location,
        "storage_location_source": batch.storage_location_source,
        "expires_at": batch.expires_at,
        "expiry_source": batch.expiry_source,
        "expiry_date": (
            local_calendar_date(batch.expires_at, timezone_name)
            if batch.expires_at is not None
            else None
        ),
    } | reports.describe_expiry(batch.expires_at, now)


def _pantry_batch_value(
    service: DietService,
    batch: pantry.PantryBatch,
) -> Mapping[str, Any]:
    return _public_value(batch) | {
        "expiry_date": (
            local_calendar_date(
                batch.expires_at,
                service.settings.profile.timezone,
            )
            if batch.expires_at is not None
            else None
        )
    }


def _pantry_mutation_result(
    service: DietService,
    batch_id: int,
    batch: pantry.PantryBatch,
    *,
    now: datetime,
) -> Mapping[str, Any] | _HandlerResult:
    public = _pantry_batch_value(service, batch)
    if service._degraded_error is not None:
        return {"batch": public}
    try:
        handle = _issue_workflow(
            service,
            "pantry_batch_reference",
            request={"action": "select_pantry_batch"},
            result={"batch_id": batch_id},
            resource_versions={"version": batch.version},
            now=now,
        )
    except Exception:
        LOGGER.warning("Post-commit workflow handle creation failed")
        return _HandlerResult(
            {"batch": public},
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    return {
        "batch": public | {"workflow": {"batch_handle": handle}},
    }


def _pantry_target_id(
    service: DietService, payload: Mapping[str, Any], *, now: datetime
) -> tuple[int, int]:
    _reject_raw_identifiers(payload, "id", "batch_id", "batchId", "database_id", "databaseId")
    handle = payload.get("batch_handle")
    if handle is not None:
        reference = _workflow_row(
            service.connection,
            _text_value(handle, "batch_handle"),
            "pantry_batch_reference",
            now=now,
        )
        target = _stored_object(
            reference["result_json"], "stored pantry batch reference"
        )
        batch_id = _positive_integer(
            target.get("batch_id"), "stored pantry batch reference"
        )
        expected = _stored_object(
            reference["resource_versions_json"], "stored pantry batch state"
        )
        expected_version = _positive_integer(
            expected.get("version"), "stored pantry batch version"
        )
        current = service.connection.execute(
            "SELECT version FROM pantry_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if current is None or current["version"] != expected_version:
            raise _ServiceError("STALE_PREVIEW", "Pantry batch reference is stale")
        return batch_id, expected_version

    code = _optional_text(payload.get("batch_code"), "batch_code")
    if code is None:
        raise ValueError("batch_handle is required")
    candidates = tuple(
        (batch_id, batch)
        for batch_id, batch in pantry._query_batch_targets(service.connection)
        if batch.batch_code == code
    )
    if len(candidates) > 1:
        options = tuple(
            {
                "label": _pantry_option_label(batch),
                "batch_handle": _issue_workflow(
                    service,
                    "pantry_batch_reference",
                    request={"action": "select_pantry_batch"},
                    result={"batch_id": batch_id},
                    resource_versions={"version": batch.version},
                    now=now,
                ),
            }
            for batch_id, batch in candidates
        )
        raise _ServiceError(
            "AMBIGUOUS_BATCH",
            "More than one pantry batch matches",
            requires_confirmation=True,
            confirmation_options=options,
        )
    if len(candidates) == 1:
        batch_id, batch = candidates[0]
        return batch_id, batch.version
    raise KeyError("No pantry batch matches the supplied batch code")


def _pantry_option_label(batch: pantry.PantryBatch) -> str:
    label = (
        f"{batch.food_name} — added {batch.added_at.date().isoformat()}, "
        f"{format(batch.remaining_quantity, 'f')} {batch.unit} remaining"
    )
    return label[:96]


def _water_id(
    service: DietService, payload: Mapping[str, Any], *, now: datetime
) -> tuple[int, tuple[str, str | None]]:
    _reject_raw_identifiers(
        payload,
        "id",
        "record_id",
        "recordId",
        "water_id",
        "waterId",
        "database_id",
        "databaseId",
    )
    if payload.get("record_handle") is None:
        raise ValueError("record_handle is required")
    row = _workflow_row(
        service.connection,
        _required_text(payload, "record_handle"),
        "water_reference",
        now=now,
    )
    target = _stored_object(row["result_json"], "stored water reference")
    water_id = _positive_integer(target.get("water_id"), "stored water reference")
    expected = _stored_object(
        row["resource_versions_json"], "stored water resource"
    )
    expected_state = (
        _required_text(expected, "updated_at"),
        _optional_text(expected.get("deleted_at"), "deleted_at"),
    )
    current = service.connection.execute(
        "SELECT updated_at, deleted_at FROM water_logs WHERE id = ?", (water_id,)
    ).fetchone()
    if current is None or {
        "updated_at": current["updated_at"],
        "deleted_at": current["deleted_at"],
    } != dict(expected):
        raise _ServiceError("STALE_PREVIEW", "Water reference is stale")
    return water_id, expected_state


def _water_result(
    service: DietService,
    record: water.WaterRecord,
    *,
    now: datetime,
    increment_ml: Decimal,
    receipt_verb: str,
) -> Mapping[str, Any] | _HandlerResult:
    public = {
        field.name: _public_value(getattr(record, field.name))
        for field in fields(record)
        if field.name != "id"
    } | _local_time_projection(
        "occurred_at",
        record.occurred_at,
        service.settings.profile.timezone,
    )
    goal_profile = goal_profiles.load_goal_profile(service.connection)
    snapshot = progress.daily_progress_snapshot(
        service.connection,
        occurred_at=record.occurred_at,
        goal_profile=goal_profile,
        increment=progress.NutritionIncrement(water_ml=increment_ml),
    )
    daily_progress = snapshot.metrics
    rendered_receipt = progress_receipt.render_water_receipt(
        record.amount_ml,
        metrics=daily_progress,
        goals_confirmed=goal_profile.confirmed,
        verb=receipt_verb,
    )
    if service._degraded_error is not None:
        return {
            "record": public,
            "daily_progress": daily_progress,
            "rendered_receipt": rendered_receipt,
        } | goal_profiles.public_provenance(goal_profile)
    try:
        handle = _issue_workflow(
            service,
            "water_reference",
            request={"action": "select_water_record"},
            result={"water_id": record.id},
            resource_versions={
                "updated_at": _public_value(record.updated_at),
                "deleted_at": _public_value(record.deleted_at),
            },
            now=now,
        )
    except Exception:
        LOGGER.warning("Post-commit workflow handle creation failed")
        return _HandlerResult(
            {
                "record": public,
                "daily_progress": daily_progress,
                "rendered_receipt": rendered_receipt,
            } | goal_profiles.public_provenance(goal_profile),
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    workflow = {"record_handle": handle}
    return {
        "record": public | {"workflow": workflow},
        "workflow": workflow,
        "daily_progress": daily_progress,
        "rendered_receipt": rendered_receipt,
    } | goal_profiles.public_provenance(goal_profile)


def _water_public(
    service: DietService, record: water.WaterRecord, *, now: datetime
) -> Mapping[str, Any]:
    public = {
        field.name: _public_value(getattr(record, field.name))
        for field in fields(record)
        if field.name != "id"
    } | _local_time_projection(
        "occurred_at",
        record.occurred_at,
        service.settings.profile.timezone,
    )
    if service._degraded_error is not None:
        return public
    handle = _issue_workflow(
        service,
        "water_reference",
        request={"action": "select_water_record"},
        result={"water_id": record.id},
        resource_versions={
            "updated_at": _public_value(record.updated_at),
            "deleted_at": _public_value(record.deleted_at),
        },
        now=now,
    )
    return public | {"workflow": {"record_handle": handle}}


def _weight_id(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[int, int]:
    _reject_raw_identifiers(
        payload,
        "id",
        "record_id",
        "recordId",
        "weight_id",
        "weightId",
        "database_id",
        "databaseId",
    )
    if payload.get("record_handle") is None:
        raise ValueError("record_handle is required")
    row = _workflow_row(
        service.connection,
        _required_text(payload, "record_handle"),
        "weight_reference",
        now=now,
    )
    target = _stored_object(
        row["result_json"],
        "stored body-weight reference",
    )
    weight_id = _positive_integer(
        target.get("weight_id"),
        "stored body-weight reference",
    )
    expected = _stored_object(
        row["resource_versions_json"],
        "stored body-weight resource",
    )
    expected_version = _positive_integer(
        expected.get("version"),
        "stored body-weight version",
    )
    current = service.connection.execute(
        """
        SELECT version
        FROM body_weight_logs
        WHERE id = ?
        """,
        (weight_id,),
    ).fetchone()
    if current is None or current["version"] != expected_version:
        raise _ServiceError(
            "STALE_PREVIEW",
            "Body-weight reference is stale",
        )
    return weight_id, expected_version


def _weight_result(
    service: DietService,
    record: body_weight.BodyWeightRecord,
    *,
    now: datetime,
) -> Mapping[str, Any] | _HandlerResult:
    public = _weight_record_public(service, record)
    summary = body_weight.query_body_weight(
        service.connection,
        now=now,
        limit=20,
    )
    data: dict[str, Any] = {
        "record": public,
        "summary": _weight_summary_public(
            service,
            summary,
            now=now,
            include_records=False,
        ),
    }
    if record.deleted_at is not None or service._degraded_error is not None:
        return data
    try:
        handle = _issue_workflow(
            service,
            "weight_reference",
            request={"action": "select_weight_record"},
            result={"weight_id": record.id},
            resource_versions={
                "version": record.version,
            },
            now=now,
        )
    except Exception:
        LOGGER.warning("Post-commit body-weight handle creation failed")
        return _HandlerResult(
            data,
            warnings=(_POST_COMMIT_HANDLE_WARNING,),
        )
    workflow = {"record_handle": handle}
    data["record"] = public | {"workflow": workflow}
    data["workflow"] = workflow
    return data


def _weight_record_public(
    service: DietService,
    record: body_weight.BodyWeightRecord,
) -> Mapping[str, Any]:
    return {
        "measured_at": _public_value(record.measured_at),
        "weight_kg": _public_value(record.weight_kg),
        "status_note": record.status_note,
        "created_at": _public_value(record.created_at),
        "updated_at": _public_value(record.updated_at),
        "deleted_at": _public_value(record.deleted_at),
    } | _local_time_projection(
        "measured_at",
        record.measured_at,
        service.settings.profile.timezone,
    )


def _weight_summary_public(
    service: DietService,
    summary: body_weight.BodyWeightSummary,
    *,
    now: datetime,
    include_records: bool,
) -> dict[str, Any]:
    public: dict[str, Any] = {}
    if include_records:
        records: list[Mapping[str, Any]] = []
        for record in summary.records:
            item = dict(_weight_record_public(service, record))
            if service._degraded_error is None:
                handle = _issue_workflow(
                    service,
                    "weight_reference",
                    request={"action": "select_weight_record"},
                    result={"weight_id": record.id},
                    resource_versions={
                        "version": record.version,
                    },
                    now=now,
                )
                item["workflow"] = {"record_handle": handle}
            records.append(item)
        public["records"] = records
    if summary.seven_day_average_kg is not None:
        public["seven_day_average_kg"] = _public_value(
            summary.seven_day_average_kg
        )
    if summary.trend is not None:
        public["trend"] = _public_value(summary.trend)
    return public


def _undo_filters(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    action: str,
) -> UndoFilters:
    return UndoFilters(
        connection=service.connection,
        session_started_at=payload.get(
            "session_started_at", context.get("session_started_at")
        ),
        operation_type=_optional_text(
            payload.get("operation_type"), "operation_type"
        ),
        date_start=payload.get("date_start"),
        date_end=payload.get("date_end"),
        meal_type=_optional_text(payload.get("meal_type"), "meal_type"),
        normalized_food_name=_optional_text(
            payload.get("normalized_food_name"), "normalized_food_name"
        ),
        action=action,
        timezone_name=service.settings.profile.timezone,
        now=_operation_now(payload, context),
    )


def _transaction_target(
    service: DietService,
    payload: Mapping[str, Any],
    *,
    action: str,
    now: datetime,
) -> tuple[str, str, str, int]:
    _reject_raw_identifiers(
        payload,
        "id",
        "transaction_id",
        "transactionId",
        "database_id",
        "databaseId",
    )
    handle = payload.get("operation_handle")
    if handle is None:
        raise ValueError("operation_handle is required")
    reference = _workflow_row(
        service.connection,
        _text_value(handle, "operation_handle"),
        f"transaction_{action}_reference",
        now=now,
    )
    target = _stored_object(
        reference["result_json"], "stored transaction reference"
    )
    transaction_id = _required_text(target, "transaction_id")
    expected = _stored_object(
        reference["resource_versions_json"], "stored transaction state"
    )
    expected_status = expected.get("status")
    required_status = "committed" if action == "undo" else "reverted"
    expected_generation = expected.get("generation")
    if (
        expected_status != required_status
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
        or expected_generation < 0
    ):
        raise _ServiceError("STALE_PREVIEW", "Transaction reference is stale")
    current = service.connection.execute(
        "SELECT status, generation FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if (
        current is None
        or current["status"] != expected.get("status")
        or current["generation"] != expected_generation
    ):
        raise _ServiceError("STALE_PREVIEW", "Transaction reference is stale")
    return (
        transaction_id,
        reference["token_hash"],
        expected_status,
        expected_generation,
    )


def _transaction_reference_version(
    connection: sqlite3.Connection,
    transaction_id: str,
    *,
    expected_status: str,
) -> Mapping[str, Any]:
    row = connection.execute(
        "SELECT status, generation FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if row is None or row["status"] != expected_status:
        raise _ServiceError("STALE_PREVIEW", "Transaction reference is stale")
    return {
        "status": expected_status,
        "generation": row["generation"],
    }


def _report_date(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> date:
    supplied = payload.get("report_date", payload.get("date"))
    if supplied is not None:
        return _date_value(supplied, "report_date")
    effective_now = now if now is not None else _operation_now(payload, context)
    effective_timezone = timezone_name
    if effective_timezone is None:
        effective_timezone = goal_profiles.load_goal_profile(
            service.connection
        ).timezone_name
    return effective_now.astimezone(
        reports.resolve_timezone(effective_timezone)
    ).date()


def _report_range(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    default_days: int,
    explicit_days: int | None = None,
) -> tuple[date, date, str, str]:
    profile = goal_profiles.load_goal_profile(service.connection)
    today = local_date(
        _operation_now(payload, context),
        profile.timezone_name,
    )
    supplied_start = payload.get("date_start")
    supplied_end = payload.get("date_end")
    if (supplied_start is None) != (supplied_end is None):
        raise ValueError("date_start and date_end must be provided together")
    if supplied_start is None:
        end_date = today
        span = explicit_days if explicit_days is not None else default_days
        start_date = end_date - timedelta(days=span - 1)
    else:
        start_date = _date_value(supplied_start, "date_start")
        end_date = _date_value(supplied_end, "date_end")
    if end_date < start_date:
        raise ValueError("date_end must not be before date_start")
    if end_date > today:
        raise ValueError("date_end must not be in the future")
    if (end_date - start_date).days >= 730:
        raise ValueError("report range must not exceed 730 days")
    start_utc, _ = local_day_utc_bounds(start_date, profile.timezone_name)
    _, end_utc = local_day_utc_bounds(
        end_date,
        profile.timezone_name,
    )
    return start_date, end_date, start_utc, end_utc


def _built_report(service: DietService, kind: str, path: Path) -> Mapping[str, Any]:
    try:
        relative = path.relative_to(service.data_paths.root).as_posix()
    except ValueError:
        relative = path.name
    return {
        "report": {
            "kind": kind,
            "name": path.name,
            "relative_path": relative,
        }
    }


def _safe_backup_path(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    resolved_root = root.resolve()
    if candidate.parent != resolved_root or candidate.name != name:
        raise ValueError("backup_handle does not identify a safe backup file")
    return candidate


def _operation_now(
    payload: Mapping[str, Any], context: Mapping[str, Any]
) -> datetime:
    del payload, context
    return utc_now()


def _optional_operation_now(
    payload: Mapping[str, Any], context: Mapping[str, Any]
) -> datetime | None:
    return _operation_now(payload, context)


def _issue_workflow(
    service: DietService,
    operation_type: str,
    *,
    request: Any,
    result: Any,
    resource_versions: Any,
    now: datetime,
) -> str:
    handle = _HANDLE_PREFIX + secrets.token_hex(24)
    created_at = now.astimezone(timezone.utc).replace(microsecond=0)
    expires_at = created_at + timedelta(
        minutes=service.settings.behavior.inventory.preview_expiration_minutes
    )
    try:
        service.connection.execute(
            """
            INSERT INTO operation_previews (
                token_hash, operation_type, request_json, result_json,
                resource_versions_json, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _workflow_hash(handle),
                operation_type,
                _canonical_json(request),
                _canonical_json(result),
                _canonical_json(resource_versions),
                _workflow_timestamp(created_at),
                _workflow_timestamp(expires_at),
            ),
        )
        service.connection.commit()
    except BaseException:
        service.connection.rollback()
        raise
    return handle


def _workflow_row(
    connection: sqlite3.Connection,
    handle: str,
    operation_type: str,
    *,
    now: datetime,
    allow_consumed: bool = False,
) -> sqlite3.Row:
    if not isinstance(handle, str) or not handle.startswith(_HANDLE_PREFIX):
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    row = connection.execute(
        """
        SELECT *
        FROM operation_previews
        WHERE token_hash = ? AND operation_type = ?
        """,
        (_workflow_hash(handle), operation_type),
    ).fetchone()
    if row is None:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    expires_at = _datetime_value(row["expires_at"], "workflow expiry")
    if now.astimezone(timezone.utc) >= expires_at:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    if row["consumed_at"] is not None and not allow_consumed:
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    return row


def _workflow_hash(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _consume_workflow_reference(
    connection: sqlite3.Connection, token_hash: str, *, now: datetime
) -> None:
    changed = connection.execute(
        """
        UPDATE operation_previews
        SET consumed_at = ?
        WHERE token_hash = ? AND consumed_at IS NULL
        """,
        (_workflow_timestamp(now), token_hash),
    ).rowcount
    if changed != 1:
        connection.rollback()
        raise _ServiceError("STALE_PREVIEW", "Workflow reference is stale")
    connection.commit()


def _cleanup_post_commit_workflow_failure(service: DietService) -> None:
    """Restore a usable connection without changing the committed outcome."""

    try:
        service.connection.rollback()
        return
    except Exception:
        LOGGER.exception("Workflow cleanup rollback failed")

    if service._owns_connection:
        try:
            service.connection.close()
        except Exception:
            LOGGER.exception("Failed to close connection during workflow recovery")
        try:
            service.connection = database.connect_database(
                service.data_paths.database
            )
            return
        except Exception:
            LOGGER.exception("Failed to reopen connection during workflow recovery")

    service._degraded_error = _ServiceError(
        "DATABASE_INTEGRITY_ERROR",
        "Database connection requires recovery after workflow cleanup failure",
    )
    service._timezone_degraded = False


def _workflow_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stored_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise _ServiceError("STALE_PREVIEW", f"{label} is unavailable") from error
    if not isinstance(decoded, Mapping):
        raise _ServiceError("STALE_PREVIEW", f"{label} is unavailable")
    return decoded


def _stored_array(value: str, label: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise _ServiceError("STALE_PREVIEW", f"{label} is unavailable") from error
    if not isinstance(decoded, list):
        raise _ServiceError("STALE_PREVIEW", f"{label} is unavailable")
    return decoded


def _workflow_handle_text(value: Mapping[str, Any], key: str) -> str:
    handle = _required_text(value, key)
    if not handle.startswith(_HANDLE_PREFIX):
        raise ValueError(f"{key} is invalid")
    return handle


def _reject_raw_identifiers(value: Mapping[str, Any], *keys: str) -> None:
    if any(key in value for key in keys):
        raise ValueError("Raw persistence identifiers are not accepted")


def _success_response(result: _HandlerResult) -> Mapping[str, Any]:
    if result.outcome not in {
        "write_committed",
        "preview_ready",
        "read_completed",
        "no_op",
    }:
        raise ValueError("successful service response outcome is invalid")
    return {
        "ok": True,
        "outcome": result.outcome,
        "data": _public_value(dict(result.data)),
        "warnings": list(result.warnings),
        "requires_confirmation": result.requires_confirmation,
        "confirmation_options": _public_value(list(result.confirmation_options)),
    }


def _operation_committed_response() -> Mapping[str, Any]:
    return _success_response(
        _HandlerResult(
            {
                "status": "committed",
                "message": (
                    "The operation committed. "
                    "Query current state for the latest result."
                ),
            },
            outcome="write_committed",
        )
    )


def _default_outcome(
    domain: str,
    action: str,
    *,
    requires_confirmation: bool = False,
) -> str:
    if requires_confirmation or action.startswith("preview_"):
        return "preview_ready"
    policy = ACTION_POLICIES.get((domain, action))
    if policy is None:
        return "read_completed"
    mode = policy[0]
    if mode in {"mutation", "maintenance"}:
        return "write_committed"
    return "read_completed"


def error_response(
    code: str,
    message: str,
    *,
    requires_confirmation: bool = False,
    confirmation_options: Sequence[Mapping[str, Any]] = (),
    field: str | None = None,
    reason: str | None = None,
    expected: str | None = None,
    retryable: bool | None = None,
) -> Mapping[str, Any]:
    """Build one stable, complete protocol error response."""

    del message
    error: dict[str, Any] = {
        "code": code,
        "message": _SAFE_ERROR_MESSAGES.get(
            code, "The request could not be completed"
        ),
    }
    if code == "INVALID_INPUT" and field is None:
        field = "request"
        reason = "invalid"
        expected = "a valid public action payload"
        retryable = True
    if field is not None:
        error.update(
            {
                "field": field,
                "reason": reason,
                "expected": expected,
                "retryable": retryable,
            }
        )
    if code == "DATABASE_INTEGRITY_ERROR":
        error["retryable"] = False
    return {
        "ok": False,
        "outcome": "failed",
        "data": {},
        "warnings": [],
        "requires_confirmation": requires_confirmation,
        "confirmation_options": _scrub_error_value(list(confirmation_options)),
        "error": error,
    }


def internal_error_response() -> Mapping[str, Any]:
    """Return the non-diagnostic public shape for an unexpected exception."""

    return error_response(
        "INTERNAL_ERROR", "An unexpected internal error occurred"
    )


def startup_error_response(error: Exception) -> Mapping[str, Any]:
    """Map startup failures separately so the CLI can pair them with nonzero exit."""

    if isinstance(error, ConfigurationError):
        return error_response("CONFIGURATION_ERROR", "Service configuration is invalid")
    if isinstance(error, (database.MigrationError, sqlite3.DatabaseError)):
        return error_response(
            "DATABASE_INTEGRITY_ERROR", "Service database startup failed"
        )
    return error_response("STARTUP_ERROR", "Service startup failed")


def invalid_input_response(message: str) -> Mapping[str, Any]:
    """Build the protocol response used when a JSON Lines request cannot be read."""

    return error_response("INVALID_INPUT", message)


def _mapped_error(error: Exception) -> _ServiceError | None:
    if isinstance(error, _ServiceError):
        return error
    if isinstance(error, TransactionNotUndoable):
        return _ServiceError(
            "NOT_UNDOABLE",
            "This operation has no safe reversible effect",
        )
    if isinstance(error, WorkflowStaleError):
        return _ServiceError("STALE_PREVIEW", str(error))
    if isinstance(error, data_erasure.StaleErasurePreviewError):
        return _ServiceError("STALE_PREVIEW", str(error))
    if isinstance(error, DerivedFilesChangedError):
        return _ServiceError("DERIVED_FILES_CHANGED", str(error))
    if isinstance(error, ErasureVerificationRequired):
        return _ServiceError("VERIFICATION_REQUIRED", str(error))
    if isinstance(error, nutrition_resolution.NutritionEstimateRequired):
        return _ServiceError(
            "NUTRITION_ESTIMATE_REQUIRED",
            "A complete nutrition estimate is required before recording",
            field=f"items[{error.item_index}].nutrition_estimate",
            reason="missing_local_nutrition",
            expected="complete C or D estimate",
            retryable=True,
        )
    if isinstance(error, backup.RestoreConfirmationRequired):
        return _ServiceError(
            "RESTORE_REQUIRES_CONFIRMATION",
            str(error),
            requires_confirmation=True,
            confirmation_options=(
                {
                    "label": "Confirm replacement of the active database",
                    "confirmation": True,
                },
            ),
        )
    if isinstance(error, maintenance_control.MaintenanceKeyConflict):
        return _ServiceError(
            "MAINTENANCE_KEY_CONFLICT",
            "The maintenance operation key is already used",
        )
    if isinstance(error, maintenance_control.MaintenanceBusyError):
        return _ServiceError(
            "MAINTENANCE_BUSY",
            "Another maintenance operation is already active",
        )
    if isinstance(error, maintenance_control.MaintenanceNotFound):
        return _ServiceError(
            "MAINTENANCE_NOT_FOUND",
            "The maintenance operation was not found",
        )
    if isinstance(error, maintenance_control.MaintenanceStateError):
        return _ServiceError(
            "MAINTENANCE_OPERATION_FAILED",
            "The maintenance operation failed",
        )
    if isinstance(error, pantry.InsufficientStockError):
        return _ServiceError("INSUFFICIENT_STOCK", str(error))
    if isinstance(error, meals.LowConfidenceError):
        return _ServiceError(
            "LOW_CONFIDENCE",
            str(error),
            requires_confirmation=True,
            confirmation_options=(
                {
                    "label": "Review and confirm the meal details before recording",
                    "needs_confirmation": True,
                },
            ),
            field=error.field,
            reason=error.reason,
            expected=error.expected,
            retryable=error.retryable,
        )
    if isinstance(error, inventory_matching.AmbiguousInventoryMatchError):
        return _ServiceError(
            "AMBIGUOUS_TARGET",
            str(error),
            requires_confirmation=True,
            confirmation_options=tuple(
                {"label": candidate, "normalized_name": candidate}
                for candidate in error.candidates
            ),
        )
    if isinstance(
        error,
        (
            meals.MealReferenceStaleError,
            pantry.PantryReferenceStaleError,
            water.WaterReferenceStaleError,
            body_weight.BodyWeightReferenceStaleError,
        ),
    ):
        return _ServiceError("STALE_PREVIEW", str(error))
    if isinstance(error, TransactionTargetStaleError):
        return _ServiceError("STALE_PREVIEW", str(error))
    if isinstance(
        error,
        (
            meals.PreviewNotFoundError,
            meals.PreviewExpiredError,
            meals.PreviewStaleError,
            meals.PreviewConsumedError,
        ),
    ):
        return _ServiceError("STALE_PREVIEW", str(error))
    if isinstance(error, (UndoConflictError, RedoConflictError)):
        return _ServiceError(
            "AMBIGUOUS_TARGET",
            str(error),
            requires_confirmation=True,
        )
    if isinstance(error, sqlite3.OperationalError):
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return _ServiceError("DATABASE_BUSY", "The database is busy; retry later")
        return _ServiceError("DATABASE_INTEGRITY_ERROR", "Database operation failed")
    if isinstance(error, sqlite3.DatabaseError):
        return _ServiceError(
            "DATABASE_INTEGRITY_ERROR", "Database integrity validation failed"
        )
    if isinstance(
        error,
        (
            backup.BackupVerificationError,
            backup.RestoreError,
            database.MigrationError,
            self_check.SafeRepairTransactionError,
        ),
    ):
        return _ServiceError("DATABASE_INTEGRITY_ERROR", str(error))
    if isinstance(error, ConfigurationError):
        return _ServiceError("CONFIGURATION_ERROR", str(error))
    if isinstance(
        error,
        (
            meals.MealValidationError,
            meals.PreviewError,
            water.WaterValidationError,
            body_weight.BodyWeightValidationError,
            pantry.PantryValidationError,
            nutrition.NutritionValidationError,
            learning.LearningValidationError,
            OperationFingerprintConflict,
            TransactionStateError,
            ValueError,
            TypeError,
            KeyError,
        ),
    ):
        diagnostics = _safe_input_diagnostics(
            _clean_exception_message(error)
        )
        return _ServiceError(
            "INVALID_INPUT",
            _clean_exception_message(error),
            **diagnostics,
        )
    return None


def _safe_input_diagnostics(message: str) -> Mapping[str, Any]:
    exact = _SAFE_INPUT_DIAGNOSTICS.get(message)
    if exact is not None:
        return exact
    lowered = message.casefold()
    guided_fields = (
        (
            "prepared_food_handle",
            "a current prepared_food_handle from pantry search",
        ),
        (
            "inventory_match_handle",
            "a current inventory_match_handle from pantry search",
        ),
        ("batch_handle", "a current batch_handle from pantry query or search"),
        ("expiry_date", "a valid local YYYY-MM-DD calendar date"),
        ("expires_at", "a valid timezone-aware ISO 8601 timestamp"),
        (
            "package_hierarchy",
            "a valid package hierarchy with positive conversion quantities",
        ),
        (
            "base_quantity_per_display_unit",
            "a positive quantity in the stored base unit",
        ),
        ("display_unit", "a non-empty package display unit"),
        ("unit", "the stored base unit or display unit"),
        ("quantity", "a positive number or decimal string"),
    )
    for field, expected in guided_fields:
        if field in lowered:
            unsupported_conversion = (
                field == "unit"
                and (
                    "convert" in lowered
                    or "conversion" in lowered
                )
            )
            return {
                "field": field,
                "reason": (
                    "unsupported_conversion"
                    if unsupported_conversion
                    else "required"
                    if "required" in lowered
                    else "invalid"
                ),
                "expected": expected,
                "retryable": True,
            }
    required = re.match(r"^([a-z][a-z0-9_]*) is required$", lowered)
    if required is not None:
        return {
            "field": required.group(1),
            "reason": "required",
            "expected": "a valid value for this field",
            "retryable": True,
        }
    return {
        "field": "request",
        "reason": "invalid",
        "expected": "a valid public action payload",
        "retryable": True,
    }


def _clean_exception_message(error: Exception) -> str:
    if isinstance(error, KeyError) and error.args:
        return str(error.args[0])
    return str(error)


def _scrub_error_value(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(item_key): _scrub_error_value(item, key=str(item_key))
            for item_key, item in value.items()
            if not _private_public_key(str(item_key))
        }
    if isinstance(value, (tuple, list)):
        return [_scrub_error_value(item) for item in value]
    if isinstance(value, str):
        if key is not None and key.endswith("_handle"):
            return value
        return _INTERNAL_REFERENCE_PATTERN.sub("[redacted]", value)
    return _public_value(value)


def _private_public_key(key: str) -> bool:
    lowered = key.lower()
    compact = lowered.replace("_", "").replace("-", "")
    return (
        key in _PRIVATE_KEYS
        or lowered == "id"
        or lowered.endswith("_id")
        or lowered == "confidence"
        or lowered.endswith("_confidence")
        or compact.endswith("confidence")
        or compact
        in {
            "confidencesignals",
            "signals",
            "candidatejson",
            "rawcandidate",
            "rawcandidates",
            "diagnostic",
            "diagnostics",
            "internaldiagnostics",
        }
    )


def _result_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {"result": value}


def _public_local_timestamp(value: datetime, timezone_name: str) -> str:
    return local_datetime(value, timezone_name).isoformat()


def _local_time_projection(
    field_name: str,
    value: datetime,
    timezone_name: str,
) -> Mapping[str, str]:
    return {
        f"{field_name}_local": _public_local_timestamp(value, timezone_name),
        "timezone_name": timezone_name,
    }


def _public_value(value: Any) -> Any:
    if isinstance(value, water.WaterRecord):
        return {
            field.name: _public_value(getattr(value, field.name))
            for field in fields(value)
            if field.name != "id"
        }
    if isinstance(value, meals.MealPreview):
        public = {
            field.name: _public_value(getattr(value, field.name))
            for field in fields(value)
            if field.name != "token" and not _private_public_key(field.name)
        }
        public["workflow"] = {
            "commit_handle": value.token
        }
        return public
    if isinstance(value, learning.LearningResult):
        return {
            field.name: _public_value(getattr(value, field.name))
            for field in fields(value)
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _public_value(getattr(value, field.name))
            for field in fields(value)
            if not _private_public_key(field.name)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _private_public_key(str(key))
        }
    if isinstance(value, (tuple, list)):
        return [_public_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_public_value(item) for item in sorted(value, key=str)]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.name
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("workflow handle payload contains an unsupported value")


def _required_mapping(
    values: Mapping[str, Any], key: str
) -> Mapping[str, Any]:
    if key not in values:
        raise ValueError(f"{key} is required")
    return _mapping_value(values[key], key)


def _mapping_value(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _required_text(values: Mapping[str, Any], key: str) -> str:
    if key not in values:
        raise ValueError(f"{key} is required")
    return _text_value(values[key], key)


def _text_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text_value(value, field)


def _optional_status_note(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value


def _required_decimal(values: Mapping[str, Any], key: str) -> Decimal:
    if key not in values:
        raise ValueError(f"{key} is required")
    value = _optional_decimal(values[key], key)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite number")
    return parsed


def _required_datetime(values: Mapping[str, Any], key: str) -> datetime:
    if key not in values:
        raise ValueError(f"{key} is required")
    return _datetime_value(values[key], key)


def _required_expiry_datetime(
    values: Mapping[str, Any],
    *,
    timezone_name: str,
) -> datetime:
    has_timestamp = values.get("expires_at") is not None
    has_calendar_date = values.get("expiry_date") is not None
    if has_timestamp == has_calendar_date:
        raise ValueError(
            "exactly one of expiry_date or expires_at is required"
        )
    if has_calendar_date:
        return local_expiry_end(
            _required_date(values, "expiry_date"),
            timezone_name,
        )
    return _required_datetime(values, "expires_at")


def _optional_expiry_datetime(
    values: Mapping[str, Any],
    *,
    timezone_name: str,
) -> datetime | None:
    has_timestamp = values.get("expires_at") is not None
    has_calendar_date = values.get("expiry_date") is not None
    if not has_timestamp and not has_calendar_date:
        return None
    return _required_expiry_datetime(values, timezone_name=timezone_name)


def _datetime_value(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    else:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _required_date(values: Mapping[str, Any], key: str) -> date:
    if key not in values:
        raise ValueError(f"{key} is required")
    return _date_value(values[key], key)


def _optional_date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    return _date_value(value, field)


def _date_value(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 date") from error


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a positive integer") from error
    if parsed <= 0 or str(parsed) != str(value).strip():
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _optional_bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _text_sequence(value: Any, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ValueError(f"{field} must be an array of strings")
    return [_text_value(item, field) for item in value]
