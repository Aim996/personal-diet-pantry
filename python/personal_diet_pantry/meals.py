"""Two-stage meal recording with exact nutrition and atomic pantry linking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import StrEnum
import hashlib
import json
import math
import secrets
import sqlite3
from typing import Any

from .models import Settings
from .inventory_order import deduction_order_sql, normalized_deduction_strategy
from .nutrition import (
    NutritionFacts,
    NutritionResult,
    NutritionRepository,
    calculate_inventory_deduction,
    calculate_nutrition,
    decode_decimal_text,
    encode_decimal_text,
    scale_nutrition,
    weakest_grade,
)
from .nutrition_normalization import (
    ConsumptionMeasure,
    NormalizedNutrition,
    NutritionBasis,
    NutritionEvidence,
    NutritionNormalizationError,
    normalize_nutrition,
    validate_consumed_hydration,
)
from .intake_identity import (
    IntakeIdentity,
    IntakeIdentityItem,
    intake_event_fingerprint,
)
from .transactions import MutationContext, TransactionManager
from .pantry import InsufficientStockError
from .timezones import local_date as timezone_local_date, local_day_utc_bounds
from . import (
    goal_profiles,
    inventory_matching,
    learning,
    nutrition_profiles,
    prepared_foods,
    nutrition_resolution,
)


_MEAL_TYPES = frozenset({"breakfast", "lunch", "dinner", "snack", "other"})
_LOCATION_TYPES = frozenset({"home", "restaurant", "takeout", "unknown"})
_EXTERNAL_LOCATIONS = frozenset({"restaurant", "takeout"})
_ELIGIBLE_BATCH_STATUSES = ("active", "opened", "thawed")
_STATUS_ONLY_MOVEMENTS = {
    "open": "opened",
    "freeze": "frozen",
    "thaw": "thawed",
}
_RELATIVE_INVENTORY_MOVEMENTS = frozenset({"consume"})
_CONFIDENCE_FIELDS = (
    "source_confidence",
    "name_match_confidence",
    "quantity_confidence",
    "batch_uniqueness",
    "context_consistency",
    "personal_rule_confidence",
)
_NUTRITION_RULES_VERSION = "0.6.1"


class MealValidationError(ValueError):
    """Raised when a meal request is invalid or is not a record intent."""


class MealReferenceStaleError(MealValidationError):
    """Raised when a persisted meal reference no longer matches its row."""


class LowConfidenceError(MealValidationError):
    """Raised before mutation when a formal write needs user confirmation."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        reason: str | None = None,
        expected: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.reason = reason
        self.expected = expected
        self.retryable = retryable


class PreviewError(RuntimeError):
    """Base class for meal-preview commit failures."""


class PreviewNotFoundError(PreviewError):
    """Raised when no meal preview matches an opaque token."""


class PreviewExpiredError(PreviewError):
    """Raised when a meal preview has passed its configured expiry."""


class PreviewStaleError(PreviewError):
    """Raised when pantry resources have changed since preview."""


class PreviewConsumedError(PreviewError):
    """Raised when a one-time preview has already committed."""


class _AlreadyCommittedRace(RuntimeError):
    """Internal signal used to roll back a raced empty transaction."""


class InventoryLinkAction(StrEnum):
    """The inventory action approved by a meal preview."""

    DEDUCT = "deduct"
    PENDING = "pending"
    NONE = "none"


class MealItemRole(StrEnum):
    """The durable display/hierarchy role of one meal item."""

    FOOD = "food"
    DISH = "dish"
    INGREDIENT = "ingredient"


class ConfirmationReason(StrEnum):
    """Machine-readable causes for a low-confidence meal preview."""

    NUTRITION_ESTIMATE_REQUIRED = "nutrition_estimate_required"
    NUTRITION_UNKNOWN = "nutrition_unknown"
    SOURCE_CONFIDENCE = "source_confidence"
    NAME_MATCH_CONFIDENCE = "name_match_confidence"
    QUANTITY_CONFIDENCE = "quantity_confidence"
    BATCH_UNIQUENESS = "batch_uniqueness"
    CONTEXT_CONSISTENCY = "context_consistency"
    PERSONAL_RULE_CONFIDENCE = "personal_rule_confidence"
    QUANTITY_UNCERTAIN = "quantity_uncertain"
    UNIT_UNCERTAIN = "unit_uncertain"
    OTHER_LOW_CONFIDENCE = "other_low_confidence"
    PORTION_ESTIMATE_UNCONFIRMED = "portion_estimate_unconfirmed"


@dataclass(frozen=True)
class ConfidenceSignals:
    """Optional confidence inputs; omitted data is derived from local state."""

    source_confidence: Decimal | None = None
    name_match_confidence: Decimal | None = None
    quantity_confidence: Decimal | None = None
    batch_uniqueness: Decimal | None = None
    context_consistency: Decimal | None = None
    personal_rule_confidence: Decimal | None = None


@dataclass(frozen=True)
class PreparationLossDraft:
    """Explicit removed material with its consumed-total nutrition."""

    kind: str
    quantity: Decimal
    unit: str
    nutrition_facts: NutritionFacts


@dataclass(frozen=True)
class QuantityEstimate:
    """Declared bounded quantity evidence awaiting explicit confirmation."""

    suggested: Decimal
    lower: Decimal
    upper: Decimal
    unit: str
    evidence_type: str
    policy_key: str
    confirmation_required: bool


@dataclass(frozen=True)
class MealItemDraft:
    """One described food, optionally containing decomposed ingredients."""

    raw_name: str
    normalized_name: str
    inventory_match_name: str | None = field(
        default=None, repr=False, compare=False
    )
    inventory_batch_id: int | None = field(
        default=None, repr=False, compare=False
    )
    amount: Decimal | None = None
    unit: str | None = None
    portion_expression: str | None = None
    quantity_estimate: QuantityEstimate | None = None
    consumed_weight_g: Decimal | None = None
    consumed_volume_ml: Decimal | None = None
    consumed_servings: Decimal | None = None
    raw_weight_g: Decimal | None = None
    inventory_deduction_weight_g: Decimal | None = None
    edible_ratio: Decimal | None = None
    cooking_yield: Decimal | None = None
    nutrition_facts: NutritionFacts | None = None
    preparation_losses: tuple[PreparationLossDraft, ...] = ()
    nutrition_basis: NutritionBasis | None = None
    nutrition_dataset_version: str | None = None
    brand: str | None = None
    nutrition_estimate: NutritionFacts | None = None
    confidence_signals: ConfidenceSignals = field(default_factory=ConfidenceSignals)
    ingredients: tuple["MealItemDraft", ...] = ()
    source_confidence: Decimal | None = None
    name_match_confidence: Decimal | None = None
    quantity_confidence: Decimal | None = None
    batch_uniqueness: Decimal | None = None
    context_consistency: Decimal | None = None
    personal_rule_confidence: Decimal | None = None
    leftover: prepared_foods.LeftoverDraft | None = None


@dataclass(frozen=True)
class MealDraft:
    """A complete interpreted meal request before any formal state is written."""

    intent: str
    occurred_at: datetime
    meal_type: str
    source_text: str
    location_type: str
    items: tuple[MealItemDraft, ...]
    nutrition_repository: NutritionRepository | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class CookingDraft:
    """One complete home-cooking event, including all raw ingredients."""

    occurred_at: datetime
    meal_type: str
    source_text: str
    dish_name: str
    normalized_name: str
    unit: str
    consumed_quantity: Decimal
    leftover: prepared_foods.LeftoverDraft | None
    ingredients: Sequence[MealItemDraft]
    nutrition_repository: NutritionRepository | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class MealSelector:
    """A human-domain meal selector that contains no persistence identifier."""

    occurred_at: datetime
    source_text: str


@dataclass(frozen=True)
class InventoryDeductionLine:
    """One public pantry deduction line without a database identifier."""

    batch_code: str | None
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class MealItem:
    """A public meal item shared by preview, commit, and query results."""

    raw_name: str
    normalized_name: str
    amount: Decimal | None
    unit: str | None
    consumed_weight_g: Decimal | None
    consumed_volume_ml: Decimal | None
    consumed_servings: Decimal | None
    raw_weight_g: Decimal | None
    inventory_deduction_weight_g: Decimal | None
    edible_ratio: Decimal | None
    cooking_yield: Decimal | None
    calories: Decimal | None
    protein: Decimal | None
    fat: Decimal | None
    carbohydrate: Decimal | None
    fiber: Decimal | None
    sodium: Decimal | None
    hydration_ml: Decimal | None
    source_grade: str
    nutrition_source: str | None
    uncertainty: str | None
    confidence: Decimal
    inventory_action: InventoryLinkAction
    deductions: tuple[InventoryDeductionLine, ...]
    role: MealItemRole = MealItemRole.FOOD
    ingredients: tuple["MealItem", ...] = ()
    leftover: prepared_foods.LeftoverDraft | None = None
    quantity_estimate: QuantityEstimate | None = None
    portion_expression: str | None = None


@dataclass(frozen=True)
class MealPreview:
    """An expiring, opaque commit capability and its user-reviewable result."""

    token: str
    expires_at: datetime
    occurred_at: datetime
    meal_type: str
    source_text: str
    location_type: str
    items: tuple[MealItem, ...]
    total_calories: Decimal | None
    total_protein: Decimal | None
    total_fat: Decimal | None
    total_carbohydrate: Decimal | None
    total_fiber: Decimal | None
    total_sodium: Decimal | None
    total_hydration_ml: Decimal | None
    source_grade: str
    confidence: Decimal
    confirmation_reasons: tuple[ConfirmationReason, ...]


@dataclass(frozen=True)
class MealRecord:
    """A persisted meal with no database or transaction identifiers."""

    occurred_at: datetime
    meal_type: str
    source_text: str
    location_type: str
    items: tuple[MealItem, ...]
    total_calories: Decimal | None
    total_protein: Decimal | None
    total_fat: Decimal | None
    total_carbohydrate: Decimal | None
    total_fiber: Decimal | None
    total_sodium: Decimal | None
    total_hydration_ml: Decimal | None
    source_grade: str
    confidence: Decimal
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class MealCommitResult:
    """The committed public meal; internal transaction identity stays private."""

    meal: MealRecord
    inventory_effects: tuple[prepared_foods.InventoryEffect, ...]
    transaction_id: str | None = field(default=None, repr=False, compare=False)
    meal_id: int | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class _PreparedItem:
    public: MealItem
    nutrition_evidence: "_PreparedNutritionEvidence | None"
    planned_inventory_deduction_weight_g: Decimal | None
    candidates: tuple[InventoryDeductionLine, ...]
    parent_index: int | None
    display_order: int
    confirmation_reasons: tuple[ConfirmationReason, ...]


@dataclass(frozen=True)
class _PreparedNutritionEvidence:
    basis: NutritionBasis
    input_facts: NutritionFacts | NutritionResult
    scale_factor: Decimal
    dataset_version: str | None
    rules_version: str
    portion_evidence: Mapping[str, object]
    calculation_status: str
    provenance_status: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _DraftNode:
    item: MealItemDraft
    role: MealItemRole
    parent_index: int | None
    display_order: int


@dataclass(frozen=True)
class _PreparedMeal:
    occurred_at: str
    event_timezone: str
    local_date: str
    intake_fingerprint: str
    meal_type: str
    source_text: str
    location_type: str
    items: tuple[_PreparedItem, ...]
    total_calories: Decimal | None
    total_protein: Decimal | None
    total_fat: Decimal | None
    total_carbohydrate: Decimal | None
    total_fiber: Decimal | None
    total_sodium: Decimal | None
    total_hydration_ml: Decimal | None
    source_grade: str
    confidence: Decimal
    confirmation_reasons: tuple[ConfirmationReason, ...]
    resource_versions: tuple[tuple[int, int], ...]
    deduction_strategy: tuple[str, ...]


def preview_meal(
    connection: sqlite3.Connection,
    draft: MealDraft,
    *,
    now: datetime,
    settings: Settings,
) -> MealPreview:
    """Validate and persist an expiring preview without changing formal state."""

    _require_record_draft(draft)
    created_at = _timestamp(now, "now")
    if connection.in_transaction:
        raise MealValidationError("preview requires a connection without an active transaction")
    prepared = _prepare_meal(connection, draft, settings)
    expires_at = _parse_timestamp(created_at) + timedelta(
        minutes=_preview_expiration_minutes(settings)
    )
    token = "wfh_" + secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    request_json = _canonical_json(_draft_payload(draft))
    result_json = _canonical_json(_prepared_payload(prepared))
    resources_json = _canonical_json(
        [
            {"batch_id": batch_id, "version": version}
            for batch_id, version in prepared.resource_versions
        ]
    )
    try:
        connection.execute(
            """
            INSERT INTO operation_previews (
                token_hash, operation_type, request_json, result_json,
                resource_versions_json, created_at, expires_at
            )
            VALUES (?, 'meal_preview', ?, ?, ?, ?, ?)
            """,
            (
                token_hash,
                request_json,
                result_json,
                resources_json,
                created_at,
                _timestamp(expires_at, "expires_at"),
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return _preview_from_prepared(token, expires_at, prepared)


def preview_meal_update(
    connection: sqlite3.Connection,
    draft: MealDraft,
    *,
    replacing_meal_id: int,
    now: datetime,
    settings: Settings,
) -> MealPreview:
    """Build a correction preview with old inventory credited, without business writes."""

    _require_record_draft(draft)
    prepared = _prepare_meal(
        connection,
        draft,
        settings,
        inventory_credits=_meal_inventory_credits(connection, replacing_meal_id),
    )
    expires_at = now.astimezone(timezone.utc) + timedelta(
        minutes=_preview_expiration_minutes(settings)
    )
    return _preview_from_prepared("", expires_at, prepared)


def preview_cooking_update(
    connection: sqlite3.Connection,
    draft: CookingDraft,
    *,
    replacing_meal_id: int,
    now: datetime,
    settings: Settings,
) -> MealPreview:
    """Build a cooking-correction preview without changing formal state."""

    prepared = prepare_cooking_for_commit(
        connection,
        draft,
        settings,
        replacing_meal_id=replacing_meal_id,
    )
    expires_at = now.astimezone(timezone.utc) + timedelta(
        minutes=_preview_expiration_minutes(settings)
    )
    return _preview_from_prepared("", expires_at, prepared)


def commit_meal(
    connection: sqlite3.Connection,
    token: str,
    *,
    now: datetime,
    minimum_confidence: Decimal,
    confirmed: bool = False,
    deduction_strategy: Sequence[str] | None = None,
) -> MealCommitResult:
    """Commit one unexpired, unchanged preview exactly once."""

    confidence_threshold = _control_decimal(
        minimum_confidence, "minimum_confidence"
    )
    if not isinstance(confirmed, bool):
        raise MealValidationError("confirmed must be a boolean")
    token_hash = _token_hash(token)
    committed_at = _timestamp(now, "now")
    preview_row = connection.execute(
        """
        SELECT *
        FROM operation_previews
        WHERE token_hash = ? AND operation_type = 'meal_preview'
        """,
        (token_hash,),
    ).fetchone()
    if preview_row is None:
        raise PreviewNotFoundError("Unknown meal preview token")
    if preview_row["consumed_at"] is not None:
        return _existing_commit_result(connection, preview_row)
    request = _json_object(preview_row["request_json"], "preview request")
    source_text = _required_text(request.get("source_text"), "source_text")
    payload = _json_object(preview_row["result_json"], "preview result")
    intake_fingerprint = _intake_fingerprint(payload)
    existing_intake = _active_meal_by_fingerprint(
        connection, intake_fingerprint
    )
    if existing_intake is not None:
        return _consume_preview_as_existing(
            connection,
            preview_row,
            existing_intake,
            committed_at=committed_at,
        )
    if deduction_strategy is not None and _payload_deduction_strategy(
        payload
    ) != _commit_deduction_strategy(deduction_strategy):
        raise PreviewStaleError(
            "Inventory deduction strategy changed after meal preview"
        )
    confidence = _stored_decimal(payload.get("confidence"), "confidence")
    confirmation_reasons = _stored_confirmation_reasons(
        payload.get("confirmation_reasons")
    )
    if (
        confidence < confidence_threshold or confirmation_reasons
    ) and not confirmed:
        raise LowConfidenceError(
            "Meal details need confirmation before recording",
            field="confirmed",
            reason="confirmation_required",
            expected="true",
            retryable=True,
        )
    internal_transaction_id = f"txn_meal_{secrets.token_urlsafe(18)}"
    manager = TransactionManager(connection)

    def mutate(context: MutationContext) -> int:
        current = connection.execute(
            """
            SELECT *
            FROM operation_previews
            WHERE token_hash = ? AND operation_type = 'meal_preview'
            """,
            (token_hash,),
        ).fetchone()
        if current is None:
            raise PreviewNotFoundError("Unknown meal preview token")
        if current["consumed_at"] is not None:
            raise _AlreadyCommittedRace
        if _parse_timestamp(committed_at) >= _parse_timestamp(current["expires_at"]):
            raise PreviewExpiredError("Meal preview has expired")

        current_request = _json_object(current["request_json"], "preview request")
        if current_request.get("intent") != "record":
            raise MealValidationError("only intent='record' may commit a meal")
        payload = _json_object(current["result_json"], "preview result")
        resources = _resource_versions(current["resource_versions_json"])
        _verify_resource_versions(connection, payload, resources)
        meal_id = _insert_prepared_meal(
            connection,
            context,
            payload,
            committed_at=committed_at,
            source_text=source_text,
        )
        changed = connection.execute(
            """
            UPDATE operation_previews
            SET consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (committed_at, internal_transaction_id, token_hash),
        ).rowcount
        if changed != 1:
            raise PreviewConsumedError("Meal preview has already been consumed")
        return meal_id

    try:
        result = manager.execute(
            "meal_record",
            source_text,
            mutate,
            internal_id=internal_transaction_id,
        )
    except _AlreadyCommittedRace:
        raced = connection.execute(
            """
            SELECT *
            FROM operation_previews
            WHERE token_hash = ? AND operation_type = 'meal_preview'
            """,
            (token_hash,),
        ).fetchone()
        if raced is None or raced["consumed_at"] is None:
            raise PreviewError("Meal preview commit race did not produce a result")
        return _existing_commit_result(connection, raced)
    except sqlite3.IntegrityError as error:
        existing_intake = _active_meal_by_fingerprint(
            connection, intake_fingerprint
        )
        if existing_intake is None:
            raise
        return _consume_preview_as_existing(
            connection,
            preview_row,
            existing_intake,
            committed_at=committed_at,
        )
    return MealCommitResult(
        meal=_read_meal(connection, result.value),
        inventory_effects=prepared_foods.inventory_effects_for_meal(
            connection, result.value
        ),
        meal_id=int(result.value),
    )


def _existing_commit_result(
    connection: sqlite3.Connection, preview_row: sqlite3.Row
) -> MealCommitResult:
    transaction_id = preview_row["transaction_id"]
    if not isinstance(transaction_id, str) or not transaction_id:
        raise PreviewError("Consumed meal preview has no committed transaction")
    meals = connection.execute(
        "SELECT id FROM meals WHERE transaction_id = ? ORDER BY id",
        (transaction_id,),
    ).fetchall()
    if len(meals) != 1:
        raise PreviewError("Consumed meal preview has no unique committed meal")
    meal_id = int(meals[0]["id"])
    return MealCommitResult(
        meal=_read_meal(connection, meal_id),
        inventory_effects=prepared_foods.inventory_effects_for_meal(
            connection, meal_id
        ),
        meal_id=meal_id,
    )


def preview_quantity_estimates(
    connection: sqlite3.Connection,
    token: str,
) -> tuple[dict[str, object], ...]:
    """Read generated estimate evidence from an opaque meal preview."""

    row = connection.execute(
        """
        SELECT result_json
        FROM operation_previews
        WHERE token_hash = ? AND operation_type = 'meal_preview'
        """,
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        raise PreviewNotFoundError("Unknown meal preview token")
    payload = _json_object(row["result_json"], "preview result")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise PreviewStaleError("Stored preview items are invalid")
    estimates: list[dict[str, object]] = []
    for raw_item in raw_items:
        item = _mapping_payload(raw_item, "preview item")
        raw_estimate = item.get("quantity_estimate")
        if raw_estimate is None:
            continue
        estimate = _mapping_payload(
            raw_estimate, "preview quantity_estimate"
        )
        estimates.append(
            {
                "subject": _required_text(
                    item.get("normalized_name"), "normalized_name"
                ),
                "suggested": _required_text(
                    estimate.get("suggested"), "suggested"
                ),
                "lower": _required_text(estimate.get("lower"), "lower"),
                "upper": _required_text(estimate.get("upper"), "upper"),
                "unit": _required_text(estimate.get("unit"), "unit"),
                "evidence_type": _required_text(
                    estimate.get("evidence_type"), "evidence_type"
                ),
                "policy_key": _required_text(
                    estimate.get("policy_key"), "policy_key"
                ),
            }
        )
    return tuple(estimates)


def _active_meal_by_fingerprint(
    connection: sqlite3.Connection,
    intake_fingerprint: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, transaction_id
        FROM meals
        WHERE intake_fingerprint = ? AND deleted_at IS NULL
        """,
        (intake_fingerprint,),
    ).fetchone()


def existing_meal_for_draft(
    connection: sqlite3.Connection,
    draft: MealDraft,
) -> MealCommitResult | None:
    """Return a committed equivalent before a retry touches inventory state."""

    _require_record_draft(draft)
    meal_row = _active_meal_by_fingerprint(
        connection, _draft_intake_fingerprint(draft)
    )
    if meal_row is None:
        return None
    meal_id = int(meal_row["id"])
    return MealCommitResult(
        meal=_read_meal(connection, meal_id),
        inventory_effects=prepared_foods.inventory_effects_for_meal(
            connection, meal_id
        ),
        meal_id=meal_id,
    )


def _consume_preview_as_existing(
    connection: sqlite3.Connection,
    preview_row: sqlite3.Row,
    meal_row: sqlite3.Row,
    *,
    committed_at: str,
) -> MealCommitResult:
    try:
        changed = connection.execute(
            """
            UPDATE operation_previews
            SET consumed_at = ?, transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                committed_at,
                meal_row["transaction_id"],
                preview_row["token_hash"],
            ),
        ).rowcount
        if changed not in {0, 1}:
            raise PreviewError("Duplicate preview state is invalid")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    meal_id = int(meal_row["id"])
    return MealCommitResult(
        meal=_read_meal(connection, meal_id),
        inventory_effects=prepared_foods.inventory_effects_for_meal(
            connection, meal_id
        ),
        meal_id=meal_id,
    )


def query_meals(
    connection: sqlite3.Connection,
    *,
    occurred_on: date | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    meal_type: str | None = None,
    timezone_name: str = "UTC",
) -> tuple[MealRecord, ...]:
    """Read active meals, optionally limited by a half-open UTC range."""

    return tuple(
        record
        for _, record in _query_meal_targets(
            connection,
            occurred_on=occurred_on,
            start_utc=start_utc,
            end_utc=end_utc,
            meal_type=meal_type,
            timezone_name=timezone_name,
        )
    )


def _query_meal_targets(
    connection: sqlite3.Connection,
    *,
    occurred_on: date | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    meal_type: str | None = None,
    timezone_name: str = "UTC",
) -> tuple[tuple[int, MealRecord], ...]:
    """Return internal meal targets paired with public records."""

    clauses = ["deleted_at IS NULL"]
    parameters: list[str] = []
    has_range = start_utc is not None or end_utc is not None
    if occurred_on is not None and has_range:
        raise MealValidationError(
            "occurred_on cannot be combined with start_utc and end_utc"
        )
    if has_range and (start_utc is None or end_utc is None):
        raise MealValidationError("start_utc and end_utc must be supplied together")
    if occurred_on is not None:
        if not isinstance(occurred_on, date) or isinstance(occurred_on, datetime):
            raise MealValidationError("occurred_on must be a date")
        start, end = local_day_utc_bounds(occurred_on, timezone_name)
        clauses.append("occurred_at >= ? AND occurred_at < ?")
        parameters.extend((start, end))
    elif start_utc is not None and end_utc is not None:
        start = _timestamp(start_utc, "start_utc")
        end = _timestamp(end_utc, "end_utc")
        if end <= start:
            raise MealValidationError("end_utc must be after start_utc")
        clauses.append("occurred_at >= ? AND occurred_at < ?")
        parameters.extend((start, end))
    if meal_type is not None:
        clauses.append("meal_type = ?")
        parameters.append(_meal_type(meal_type))
    rows = connection.execute(
        f"""
        SELECT *
        FROM meals
        WHERE {' AND '.join(clauses)}
        ORDER BY occurred_at, id
        """,
        parameters,
    ).fetchall()
    return tuple((int(row["id"]), _meal_record(connection, row)) for row in rows)


def _matching_meal_targets(
    connection: sqlite3.Connection, selector: MealSelector
) -> tuple[tuple[int, MealRecord], ...]:
    """Return every active row matching a human-domain selector."""

    occurred_at, source_text = _selector_values(selector)
    rows = connection.execute(
        """
        SELECT *
        FROM meals
        WHERE occurred_at = ? AND source_text = ? AND deleted_at IS NULL
        ORDER BY id
        """,
        (occurred_at, source_text),
    ).fetchall()
    return tuple((int(row["id"]), _meal_record(connection, row)) for row in rows)


def update_meal(
    connection: sqlite3.Connection,
    selector: MealSelector,
    draft: MealDraft,
    *,
    now: datetime,
    settings: Settings,
    _meal_id: int | None = None,
    _expected_state: tuple[str, str | None] | None = None,
    _confirmed: bool = False,
) -> MealCommitResult:
    """Replace one selected meal with a journaled corrected record."""

    _require_record_draft(draft)
    corrected_at = _timestamp(now, "now")
    selected_at, selected_source = _selector_values(selector)
    selected = _selected_meal_row(
        connection,
        selected_at,
        selected_source,
        meal_id=_meal_id,
        expected_state=_expected_state,
    )
    prepared = _prepare_meal_for_commit(
        connection,
        draft,
        settings,
        replacing_meal_id=int(selected["id"]),
        confirmed=_confirmed,
    )
    manager = TransactionManager(connection)

    def mutate(context: MutationContext) -> int:
        existing = _selected_meal_row(
            connection,
            selected_at,
            selected_source,
            meal_id=_meal_id,
            expected_state=_expected_state,
        )
        context.update(
            "meals",
            existing["id"],
            {"deleted_at": corrected_at, "updated_at": corrected_at},
        )
        _detach_meal_inventory(
            connection,
            context,
            existing["id"],
            changed_at=corrected_at,
            reason=draft.source_text,
        )
        return _insert_prepared(
            connection,
            context,
            prepared,
            committed_at=corrected_at,
            source_text=draft.source_text,
        )

    result = manager.execute("record_correction", draft.source_text, mutate)
    return MealCommitResult(
        meal=_read_meal(connection, result.value),
        inventory_effects=prepared_foods.inventory_effects_for_transaction(
            connection, result.transaction_id
        ),
        transaction_id=result.transaction_id,
        meal_id=int(result.value),
    )


def update_cooking(
    connection: sqlite3.Connection,
    selector: MealSelector,
    draft: CookingDraft,
    *,
    now: datetime,
    settings: Settings,
    _meal_id: int | None = None,
    _expected_state: tuple[str, str | None] | None = None,
) -> MealCommitResult:
    """Replace one cooking event and all of its derived state atomically."""

    corrected_at = _timestamp(now, "now")
    selected_at, selected_source = _selector_values(selector)
    selected = _selected_meal_row(
        connection,
        selected_at,
        selected_source,
        meal_id=_meal_id,
        expected_state=_expected_state,
    )
    prepared = prepare_cooking_for_commit(
        connection,
        draft,
        settings,
        replacing_meal_id=int(selected["id"]),
    )
    source = _required_text(draft.source_text, "source_text")
    manager = TransactionManager(connection)

    def mutate(context: MutationContext) -> int:
        existing = _selected_meal_row(
            connection,
            selected_at,
            selected_source,
            meal_id=_meal_id,
            expected_state=_expected_state,
        )
        context.update(
            "meals",
            existing["id"],
            {"deleted_at": corrected_at, "updated_at": corrected_at},
        )
        _detach_meal_inventory(
            connection,
            context,
            existing["id"],
            changed_at=corrected_at,
            reason=source,
        )
        return _insert_prepared(
            connection,
            context,
            prepared,
            committed_at=corrected_at,
            source_text=source,
        )

    result = manager.execute("record_correction", source, mutate)
    return MealCommitResult(
        meal=_read_meal(connection, result.value),
        inventory_effects=prepared_foods.inventory_effects_for_transaction(
            connection, result.transaction_id
        ),
        transaction_id=result.transaction_id,
        meal_id=int(result.value),
    )


def _prepare_meal_for_commit(
    connection: sqlite3.Connection,
    draft: MealDraft,
    settings: Settings,
    *,
    replacing_meal_id: int | None = None,
    confirmed: bool = False,
) -> _PreparedMeal:
    """Prepare a formal meal mutation and fail before any write if uncertain."""

    _require_record_draft(draft)
    inventory_credits = (
        {}
        if replacing_meal_id is None
        else _meal_inventory_credits(connection, replacing_meal_id)
    )
    prepared = _prepare_meal(
        connection,
        draft,
        settings,
        inventory_credits=inventory_credits,
    )
    threshold = _control_decimal(
        settings.behavior.inventory.ask_below_confidence,
        "ask_below_confidence",
    )
    if prepared.confidence < threshold and not confirmed:
        raise LowConfidenceError(
            "Meal details need confirmation before recording"
        )
    return prepared


def delete_meal(
    connection: sqlite3.Connection,
    selector: MealSelector,
    *,
    intent: str,
    now: datetime,
    source_text: str,
    _meal_id: int | None = None,
    _expected_state: tuple[str, str | None] | None = None,
) -> MealRecord:
    """Logically delete one human-selected meal through the journal."""

    _require_record_intent(intent)
    deleted_at = _timestamp(now, "now")
    source = _required_text(source_text, "source_text")
    selected_at, selected_source = _selector_values(selector)
    manager = TransactionManager(connection)

    def mutate(context: MutationContext) -> int:
        row = _selected_meal_row(
            connection,
            selected_at,
            selected_source,
            meal_id=_meal_id,
            expected_state=_expected_state,
        )
        context.update(
            "meals",
            row["id"],
            {"deleted_at": deleted_at, "updated_at": deleted_at},
        )
        _detach_meal_inventory(
            connection,
            context,
            row["id"],
            changed_at=deleted_at,
            reason=source,
        )
        return row["id"]

    result = manager.execute("record_correction", source, mutate)
    return _read_meal(connection, result.value)


def _prepare_meal(
    connection: sqlite3.Connection,
    draft: MealDraft,
    settings: Settings,
    *,
    inventory_enabled: bool = True,
    inventory_credits: Mapping[int, Decimal] | None = None,
) -> _PreparedMeal:
    occurred_at = _timestamp(draft.occurred_at, "occurred_at")
    try:
        event_timezone = goal_profiles.load_goal_profile(
            connection
        ).timezone_name
    except LookupError:
        event_timezone = settings.profile.timezone
    event_local_date = timezone_local_date(
        occurred_at, event_timezone
    ).isoformat()
    meal_type = _meal_type(draft.meal_type)
    source_text = _required_text(draft.source_text, "source_text")
    location_type = _location_type(draft.location_type)
    normalized_draft = replace(
        draft,
        meal_type=meal_type,
        source_text=source_text,
        location_type=location_type,
    )
    items: list[_PreparedItem] = []
    resources: dict[int, int] = {}
    reserved_quantities: dict[int, Decimal] = {}
    deduction_strategy = _deduction_strategy(settings)
    for item_index, node in enumerate(_flatten_items(draft.items)):
        prepared, item_resources = _prepare_item(
            connection,
            node,
            normalized_draft,
            settings,
            inventory_enabled=inventory_enabled,
            inventory_credits=inventory_credits or {},
            reserved_quantities=reserved_quantities,
            deduction_strategy=deduction_strategy,
            item_index=item_index,
        )
        items.append(prepared)
        resources.update(item_resources)

    if not items:
        raise MealValidationError("a meal must contain at least one item")
    nutritional_items = tuple(
        item
        for item_index, item in enumerate(items)
        if item.public.role is not MealItemRole.DISH
        or not any(child.parent_index == item_index for child in items)
    )
    totals = tuple(
        _nutrient_total(nutritional_items, nutrient)
        for nutrient in (
            "calories", "protein", "fat", "carbohydrate", "fiber", "sodium",
            "hydration_ml",
        )
    )
    confidence_items = nutritional_items or tuple(items)
    confidence = _mean(tuple(item.public.confidence for item in confidence_items))
    confirmation_reasons = tuple(
        dict.fromkeys(
            reason
            for item in confidence_items
            for reason in item.confirmation_reasons
        )
    )
    grades = tuple(item.public.source_grade for item in nutritional_items)
    return _PreparedMeal(
        occurred_at=occurred_at, event_timezone=event_timezone,
        local_date=event_local_date,
        intake_fingerprint=_draft_intake_fingerprint(normalized_draft),
        meal_type=meal_type, source_text=source_text,
        location_type=location_type, items=tuple(items), total_calories=totals[0],
        total_protein=totals[1], total_fat=totals[2], total_carbohydrate=totals[3],
        total_fiber=totals[4], total_sodium=totals[5], total_hydration_ml=totals[6],
        source_grade=_combined_grade(grades), confidence=confidence,
        confirmation_reasons=confirmation_reasons,
        resource_versions=tuple(sorted(resources.items())), deduction_strategy=deduction_strategy,
    )


def _cooking_meal_draft(
    draft: CookingDraft,
) -> tuple[MealDraft, Decimal, Decimal]:
    if not isinstance(draft, CookingDraft):
        raise MealValidationError("draft must be a CookingDraft")
    consumed = _positive_cooking_quantity(draft.consumed_quantity, "consumed_quantity")
    unit = _required_text(draft.unit, "unit")
    leftover_quantity = Decimal("0")
    if draft.leftover is not None:
        if draft.leftover.unit.strip().lower().rstrip("s") != unit.lower().rstrip("s"):
            raise MealValidationError("consumed and leftover dish units must match")
        leftover_quantity = _positive_cooking_quantity(draft.leftover.quantity, "leftover quantity")
    prepared_quantity = consumed + leftover_quantity
    if prepared_quantity <= 0:
        raise MealValidationError("consumed plus leftover quantity must be positive")
    if not isinstance(draft.ingredients, Sequence) or not draft.ingredients:
        raise MealValidationError("ingredients must contain at least one item")
    meal_draft = MealDraft(
        intent="record",
        occurred_at=draft.occurred_at,
        meal_type=draft.meal_type,
        source_text=draft.source_text,
        location_type="home",
        items=(MealItemDraft(
            raw_name=draft.dish_name,
            normalized_name=draft.normalized_name,
            amount=consumed,
            unit=unit,
            ingredients=tuple(draft.ingredients),
            leftover=draft.leftover,
        ),),
        nutrition_repository=draft.nutrition_repository,
    )
    return meal_draft, consumed, prepared_quantity


def prepare_cooking_for_commit(
    connection: sqlite3.Connection,
    draft: CookingDraft,
    settings: Settings,
    *,
    replacing_meal_id: int | None = None,
) -> _PreparedMeal:
    """Prepare a whole cooking event, optionally crediting one replaced meal."""

    meal_draft, consumed, prepared_quantity = _cooking_meal_draft(draft)
    inventory_credits = (
        _meal_inventory_credits(connection, replacing_meal_id)
        if replacing_meal_id is not None
        else {}
    )
    whole_recipe = _prepare_meal(
        connection,
        meal_draft,
        settings,
        inventory_credits=inventory_credits,
    )
    for item in whole_recipe.items:
        if item.public.role is MealItemRole.INGREDIENT and (
            item.public.inventory_action is not InventoryLinkAction.DEDUCT
        ):
            raise InsufficientStockError(
                f"INSUFFICIENT_STOCK: insufficient eligible stock for {item.public.normalized_name}"
            )
    return _scale_prepared_meal(
        whole_recipe, consumed / prepared_quantity
    )


def record_cooking(
    connection: sqlite3.Connection,
    draft: CookingDraft,
    *,
    now: datetime,
    settings: Settings,
) -> MealCommitResult:
    """Atomically deduct a whole recipe, record its eaten fraction, and store leftovers."""

    meal_draft, _, _ = _cooking_meal_draft(draft)
    existing = existing_meal_for_draft(connection, meal_draft)
    if existing is not None:
        return existing
    prepared = prepare_cooking_for_commit(connection, draft, settings)
    committed_at = _timestamp(now, "now")
    manager = TransactionManager(connection)
    try:
        result = manager.execute(
            "meal_record",
            _required_text(draft.source_text, "source_text"),
            lambda context: _insert_prepared(
                connection, context, prepared, committed_at=committed_at,
                source_text=_required_text(draft.source_text, "source_text"),
            ),
            internal_id=f"txn_meal_{secrets.token_urlsafe(18)}",
        )
    except sqlite3.IntegrityError:
        raced = _active_meal_by_fingerprint(
            connection, prepared.intake_fingerprint
        )
        if raced is None:
            raise
        meal_id = int(raced["id"])
        return MealCommitResult(
            meal=_read_meal(connection, meal_id),
            inventory_effects=prepared_foods.inventory_effects_for_meal(
                connection, meal_id
            ),
            meal_id=meal_id,
        )
    return MealCommitResult(
        meal=_read_meal(connection, result.value),
        inventory_effects=prepared_foods.inventory_effects_for_meal(connection, result.value),
        meal_id=int(result.value),
    )


def record_prepared(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    reference: prepared_foods.PreparedFoodReference,
    quantity: Decimal | None,
    unit: str | None,
    source_text: str,
    occurred_at: datetime,
    meal_type: str | None,
    now: datetime,
    settings: Settings,
) -> MealCommitResult:
    """Record one exact prepared batch using its immutable nutrition snapshot."""

    if not isinstance(reference, prepared_foods.PreparedFoodReference):
        raise MealValidationError("prepared food reference is invalid")
    amount = (
        reference.remaining_quantity
        if quantity is None
        else _optional_decimal(quantity, "quantity")
    )
    if amount is None or amount <= 0:
        raise MealValidationError("quantity must be positive")
    if amount > reference.remaining_quantity:
        raise InsufficientStockError(
            "INSUFFICIENT_STOCK: insufficient prepared food stock"
        )
    selected_unit = reference.unit if unit is None else _required_text(unit, "unit")
    try:
        unit_matches = (
            inventory_matching.canonical_inventory_unit(selected_unit)
            == inventory_matching.canonical_inventory_unit(reference.unit)
        )
    except ValueError as error:
        raise MealValidationError(str(error)) from error
    if not unit_matches:
        raise MealValidationError("prepared food unit does not match")
    signals = ConfidenceSignals(
        source_confidence=Decimal("1"),
        name_match_confidence=Decimal("1"),
        quantity_confidence=Decimal("1"),
        batch_uniqueness=Decimal("1"),
        context_consistency=Decimal("1"),
        personal_rule_confidence=Decimal("1"),
    )
    draft = MealDraft(
        intent="record",
        occurred_at=occurred_at,
        meal_type=meal_type or "snack",
        source_text=_required_text(source_text, "source_text"),
        location_type="home",
        items=(
            MealItemDraft(
                raw_name=reference.food_name,
                normalized_name=reference.normalized_name,
                inventory_match_name=reference.normalized_name,
                inventory_batch_id=reference.batch_id,
                amount=amount,
                unit=reference.unit,
                consumed_weight_g=(amount if reference.unit == "g" else None),
                consumed_volume_ml=(amount if reference.unit == "ml" else None),
                consumed_servings=(
                    amount
                    if reference.unit in {"piece", "portion", "pack"}
                    else None
                ),
                confidence_signals=signals,
            ),
        ),
    )
    existing = existing_meal_for_draft(connection, draft)
    if existing is not None:
        return existing
    prepared = _prepare_meal_for_commit(connection, draft, settings)
    committed_at = _timestamp(now, "now")
    try:
        result = manager.execute(
            "meal_record",
            draft.source_text,
            lambda context: _insert_prepared(
                connection,
                context,
                prepared,
                committed_at=committed_at,
                source_text=draft.source_text,
            ),
            internal_id=f"txn_meal_{secrets.token_urlsafe(18)}",
        )
    except sqlite3.IntegrityError:
        raced = _active_meal_by_fingerprint(
            connection, prepared.intake_fingerprint
        )
        if raced is None:
            raise
        meal_id = int(raced["id"])
    else:
        meal_id = int(result.value)
    return MealCommitResult(
        meal=_read_meal(connection, meal_id),
        inventory_effects=prepared_foods.inventory_effects_for_meal(
            connection, meal_id
        ),
        meal_id=meal_id,
    )


def _scale_prepared_meal(prepared: _PreparedMeal, fraction: Decimal) -> _PreparedMeal:
    """Keep raw deductions intact while assigning only the eaten nutrition."""

    if not fraction.is_finite() or fraction <= 0 or fraction > 1:
        raise MealValidationError("consumed fraction must be greater than zero and at most one")

    def scale(value: Decimal | None) -> Decimal | None:
        return value * fraction if value is not None else None

    def scale_portion(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return (value * fraction).quantize(
            Decimal("0.000000000001")
        )

    def scale_measure(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return (value * fraction).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    scaled_items = []
    for item in prepared.items:
        is_ingredient = item.public.role is MealItemRole.INGREDIENT
        public = replace(
            item.public,
            amount=(
                scale_portion(item.public.amount)
                if is_ingredient
                else item.public.amount
            ),
            consumed_weight_g=(
                scale_measure(item.public.consumed_weight_g)
                if is_ingredient
                else item.public.consumed_weight_g
            ),
            consumed_volume_ml=(
                scale_measure(item.public.consumed_volume_ml)
                if is_ingredient
                else item.public.consumed_volume_ml
            ),
            consumed_servings=(
                scale_portion(item.public.consumed_servings)
                if is_ingredient
                else item.public.consumed_servings
            ),
        )
        evidence = item.nutrition_evidence
        if evidence is not None:
            if evidence.basis is NutritionBasis.CONSUMED_TOTAL:
                scaled_result = scale_nutrition(
                    evidence.input_facts, fraction
                )
                input_facts = scaled_result
                scale_factor = Decimal("1")
            else:
                input_facts = evidence.input_facts
                scale_factor = scale_portion(evidence.scale_factor)
                if scale_factor is None:
                    raise MealValidationError(
                        "nutrition evidence scale factor is required"
                    )
                scaled_result = scale_nutrition(
                    input_facts, scale_factor
                )
            public = replace(
                public,
                calories=scaled_result.calories,
                protein=scaled_result.protein,
                fat=scaled_result.fat,
                carbohydrate=scaled_result.carbohydrate,
                fiber=scaled_result.fiber,
                sodium=scaled_result.sodium,
                hydration_ml=scaled_result.hydration_ml,
            )
            evidence = replace(
                evidence,
                input_facts=input_facts,
                scale_factor=scale_factor,
                portion_evidence={
                    **dict(evidence.portion_evidence),
                    "cooking_consumed_fraction": _decimal_payload(
                        fraction, "cooking consumed fraction"
                    ),
                    "consumed_weight_g": (
                        _decimal_payload(
                            public.consumed_weight_g,
                            "consumed_weight_g",
                        )
                        if public.consumed_weight_g is not None
                        else None
                    ),
                    "consumed_volume_ml": (
                        _decimal_payload(
                            public.consumed_volume_ml,
                            "consumed_volume_ml",
                        )
                        if public.consumed_volume_ml is not None
                        else None
                    ),
                    "consumed_servings": (
                        _decimal_payload(
                            public.consumed_servings,
                            "consumed_servings",
                        )
                        if public.consumed_servings is not None
                        else None
                    ),
                },
            )
        else:
            public = replace(
                public,
                calories=scale(public.calories),
                protein=scale(public.protein),
                fat=scale(public.fat),
                carbohydrate=scale(public.carbohydrate),
                fiber=scale(public.fiber),
                sodium=scale(public.sodium),
                hydration_ml=scale(public.hydration_ml),
            )
        if public.hydration_ml is not None:
            try:
                validate_consumed_hydration(
                    NutritionResult(
                        calories=public.calories,
                        protein=public.protein,
                        fat=public.fat,
                        carbohydrate=public.carbohydrate,
                        fiber=public.fiber,
                        sodium=public.sodium,
                        source=public.nutrition_source or "cooking",
                        source_grade=(
                            public.source_grade
                            if public.source_grade in {"A", "B", "C", "D"}
                            else "D"
                        ),
                        uncertainty=public.uncertainty,
                        hydration_ml=public.hydration_ml,
                    ),
                    ConsumptionMeasure(
                        weight_g=public.consumed_weight_g,
                        volume_ml=public.consumed_volume_ml,
                        servings=public.consumed_servings,
                    ),
                )
            except NutritionNormalizationError as error:
                raise MealValidationError(str(error)) from error
        scaled_items.append(
            replace(item, public=public, nutrition_evidence=evidence)
        )
    items = tuple(scaled_items)
    nutritional_items = tuple(
        item
        for item_index, item in enumerate(items)
        if item.public.role is not MealItemRole.DISH
        or not any(child.parent_index == item_index for child in items)
    )
    totals = tuple(
        _nutrient_total(nutritional_items, nutrient)
        for nutrient in (
            "calories",
            "protein",
            "fat",
            "carbohydrate",
            "fiber",
            "sodium",
            "hydration_ml",
        )
    )
    return replace(
        prepared,
        items=items,
        total_calories=totals[0],
        total_protein=totals[1],
        total_fat=totals[2],
        total_carbohydrate=totals[3],
        total_fiber=totals[4],
        total_sodium=totals[5],
        total_hydration_ml=totals[6],
    )


def _resolved_nutrition_or_none(
    resolution: nutrition_resolution.NutritionResolution,
) -> NutritionResult | None:
    fields = (*nutrition_resolution.CORE_FIELDS, "hydration_ml")
    if all(getattr(resolution.result, field) is None for field in fields):
        return None
    return resolution.result


def _positive_cooking_quantity(value: object, field_name: str) -> Decimal:
    number = _optional_decimal(value, field_name)
    if number is None or number <= 0:
        raise MealValidationError(f"{field_name} must be a positive finite Decimal")
    return number


def _prepare_item(
    connection: sqlite3.Connection,
    node: _DraftNode,
    draft: MealDraft,
    settings: Settings,
    *,
    inventory_enabled: bool,
    inventory_credits: Mapping[int, Decimal],
    reserved_quantities: dict[int, Decimal],
    deduction_strategy: tuple[str, ...],
    item_index: int,
) -> tuple[_PreparedItem, dict[int, int]]:
    item = node.item
    if not isinstance(item, MealItemDraft):
        raise MealValidationError("items must contain MealItemDraft values")
    grouping = node.role is MealItemRole.DISH
    raw_name = _required_text(item.raw_name, "raw_name")
    normalized_name = _required_text(item.normalized_name, "normalized_name").lower()
    amount = _optional_decimal(item.amount, "amount")
    supplied_consumed = _optional_decimal(
        item.consumed_weight_g, "consumed_weight_g"
    )
    supplied_volume = _optional_decimal(
        item.consumed_volume_ml, "consumed_volume_ml"
    )
    supplied_servings = _optional_decimal(
        item.consumed_servings, "consumed_servings"
    )
    supplied_raw = _optional_decimal(item.raw_weight_g, "raw_weight_g")
    supplied_deduction = _optional_decimal(
        item.inventory_deduction_weight_g, "inventory_deduction_weight_g"
    )
    supplied_edible_ratio = _optional_decimal(item.edible_ratio, "edible_ratio")
    supplied_cooking_yield = _optional_decimal(item.cooking_yield, "cooking_yield")
    consumed = None if grouping else supplied_consumed
    consumed_volume = None if grouping else supplied_volume
    consumed_servings = None if grouping else supplied_servings
    explicit_raw = None if grouping else supplied_raw
    explicit_deduction = None if grouping else supplied_deduction
    edible_ratio = None if grouping else supplied_edible_ratio
    cooking_yield = None if grouping else supplied_cooking_yield
    if edible_ratio is not None and edible_ratio > 1:
        raise MealValidationError("edible_ratio must be at most one")
    unit = _optional_text(item.unit, "unit")
    amount_before_portion = amount
    unit_before_portion = unit
    portion_expression = _optional_text(item.portion_expression, "portion_expression")
    learned_portion_applied = False
    if portion_expression is not None:
        portion = learning.learned_portion(
            connection, normalized_name, portion_expression
        )
        if portion is not None:
            learned_portion_applied = True
            amount = portion.amount
            unit = portion.unit
            # A model commonly derives consumed/raw/deduction gram fields from
            # its generic amount.  When they are exactly that former amount,
            # they are not an independent label weight and must follow the
            # durable personal portion rule.  Different values remain an
            # explicit label-derived weight.
            try:
                replaces_gram_amount = (
                    amount_before_portion is not None
                    and unit_before_portion is not None
                    and inventory_matching.canonical_inventory_unit(unit_before_portion) == "g"
                    and inventory_matching.canonical_inventory_unit(portion.unit) == "g"
                )
            except ValueError:
                replaces_gram_amount = False
            if replaces_gram_amount:
                if consumed == amount_before_portion:
                    consumed = portion.amount
                if explicit_raw == amount_before_portion:
                    explicit_raw = portion.amount
                if explicit_deduction == amount_before_portion:
                    explicit_deduction = portion.amount
    leftover = item.leftover
    if leftover is not None:
        if leftover.expires_at is None:
            raise MealValidationError("leftover expires_at is required")
        if (
            leftover.expires_at.tzinfo is None
            or leftover.expires_at.utcoffset() is None
        ):
            raise MealValidationError(
                "leftover expires_at must include a timezone"
            )
        if not grouping:
            raise MealValidationError("leftover is only valid on a prepared dish")
        if draft.location_type in _EXTERNAL_LOCATIONS:
            raise MealValidationError("external meals cannot create pantry leftovers")
        if amount is None or amount <= 0 or unit is None:
            raise MealValidationError(
                "a leftover dish requires a positive consumed amount and unit"
            )
        if leftover.unit.strip().lower().rstrip("s") != unit.strip().lower().rstrip("s"):
            raise MealValidationError(
                "consumed and leftover dish units must match"
            )
    signals = _item_confidence_signals(item)
    quantity_estimate = item.quantity_estimate
    if quantity_estimate is not None and not isinstance(
        quantity_estimate, QuantityEstimate
    ):
        raise MealValidationError(
            "quantity_estimate must contain validated bounded evidence"
        )
    if learned_portion_applied:
        quantity_estimate = None
    nutrition = None
    nutrition_evidence = None
    caller_estimate_was_required = False

    canonical_unit = (
        inventory_matching.canonical_inventory_unit(unit)
        if unit is not None
        and unit.casefold()
        in {
            "g",
            "gram",
            "grams",
            "ml",
            "milliliter",
            "milliliters",
            "piece",
            "pieces",
            "portion",
            "portions",
            "pack",
            "packs",
        }
        else None
    )
    direct_inventory = (
        not grouping
        and canonical_unit is not None
        and amount is not None
        and amount > 0
    )
    counted_inventory = (
        not grouping
        and canonical_unit in {"piece", "portion", "pack"}
        and amount is not None
        and amount > 0
    )
    inventory_unit = (
        canonical_unit
        if direct_inventory or item.inventory_match_name is not None
        else "g"
    )
    deduction_quantity = (
        amount
        if direct_inventory
        and explicit_deduction is None
        and explicit_raw is None
        else explicit_deduction
        if explicit_deduction is not None
        else explicit_raw
    )
    if deduction_quantity is None and consumed is not None:
        if edible_ratio is not None or cooking_yield is not None:
            if edible_ratio is None or cooking_yield is None:
                raise MealValidationError(
                    "edible_ratio and cooking_yield must be supplied together"
                )
            try:
                deduction_quantity = calculate_inventory_deduction(
                    consumed, edible_ratio, cooking_yield
                )
            except ValueError as error:
                raise MealValidationError(str(error)) from error
        else:
            deduction_quantity = consumed
    raw_weight = explicit_raw
    if raw_weight is None and edible_ratio is not None and cooking_yield is not None:
        raw_weight = deduction_quantity

    eligible: list[sqlite3.Row] = []
    selection: list[tuple[sqlite3.Row, Decimal]] = []
    resources: dict[int, int] = {}
    if (
        not grouping
        and inventory_enabled
        and deduction_quantity is not None
        and deduction_quantity > 0
        and draft.location_type not in _EXTERNAL_LOCATIONS
    ):
        if item.inventory_batch_id is not None:
            if isinstance(item.inventory_batch_id, bool) or not isinstance(
                item.inventory_batch_id, int
            ):
                raise MealValidationError("prepared inventory target is invalid")
            row = connection.execute(
                "SELECT * FROM pantry_batches WHERE id = ?",
                (item.inventory_batch_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] not in _ELIGIBLE_BATCH_STATUSES
                or Decimal(str(row["remaining_quantity"])) <= 0
                or str(row["normalized_name"]).casefold()
                != normalized_name.casefold()
                or inventory_matching.canonical_inventory_unit(str(row["unit"]))
                != inventory_matching.canonical_inventory_unit(inventory_unit)
            ):
                raise MealValidationError("prepared inventory target is stale")
            eligible = [row]
            selection = _select_batch_rows(
                eligible,
                deduction_quantity,
                inventory_credits=inventory_credits,
                reserved_quantities=reserved_quantities,
            )
            resources = {int(row["id"]): int(row["version"])}
        elif item.inventory_match_name is not None:
            resolved_name = _required_text(
                item.inventory_match_name, "inventory_match_name"
            ).lower()
            normalized_name = resolved_name
        if item.inventory_batch_id is None:
            if item.inventory_match_name is None:
                resolved_name = inventory_matching.resolve_meal_inventory_name(
                    connection,
                    raw_name,
                    normalized_name,
                    inventory_unit,
                )
                if resolved_name is not None:
                    normalized_name = resolved_name
            eligible = _eligible_batches(
                connection,
                normalized_name,
                inventory_unit,
                inventory_credits=inventory_credits,
                deduction_strategy=deduction_strategy,
            )
            selection = _select_batch_rows(
                eligible,
                deduction_quantity,
                inventory_credits=inventory_credits,
                reserved_quantities=reserved_quantities,
            )
            resources = {row["id"]: row["version"] for row in eligible}

    if consumed is None and canonical_unit == "g" and amount is not None:
        consumed = amount
    if consumed is None and counted_inventory and selection:
        weighted = Decimal("0")
        for row, selected_quantity in selection:
            average = row["average_unit_weight_g"]
            if average is None:
                weighted = Decimal("0")
                break
            weighted += selected_quantity * Decimal(str(average))
        if weighted > 0:
            consumed = weighted
    direct_nutrition = _direct_nutrition_for_item(
        item,
        consumed_weight=consumed,
        consumed_volume=consumed_volume,
        consumed_servings=consumed_servings,
        portion_expression=portion_expression,
    )
    trusted_direct = (
        direct_nutrition.result
        if direct_nutrition is not None and item.nutrition_facts is not None
        else None
    )
    estimated_direct = (
        direct_nutrition.result
        if direct_nutrition is not None
        and item.nutrition_facts is None
        and item.nutrition_estimate is not None
        else None
    )
    if not grouping:
        prepared_nutrition = _prepared_nutrition_for_selection(
            connection,
            selection,
        )
        label_nutrition = _label_nutrition_for_selection(
            connection, selection, consumed, inventory_unit=inventory_unit
        )
        repository_nutrition = _nutrition_sources_for_item(
            item, consumed, draft.nutrition_repository
        )
        resolution_without_estimate = nutrition_resolution.merge_sources(
            prepared_nutrition,
            label_nutrition,
            trusted_direct,
            *repository_nutrition,
        )
        resolution = nutrition_resolution.merge_sources(
            prepared_nutrition,
            label_nutrition,
            trusted_direct,
            *repository_nutrition,
            estimated_direct,
        )
        nutrition = _resolved_nutrition_or_none(resolution)
        caller_estimate_was_required = (
            item.nutrition_estimate is not None
            and resolution_without_estimate.status != "complete"
        )
    elif not item.ingredients:
        resolution_without_estimate = nutrition_resolution.merge_sources(
            trusted_direct,
            *_nutrition_sources_for_item(
                item, amount, draft.nutrition_repository
            ),
        )
        resolution = nutrition_resolution.merge_sources(
            trusted_direct,
            *_nutrition_sources_for_item(
                item, amount, draft.nutrition_repository
            ),
            estimated_direct,
        )
        nutrition = _resolved_nutrition_or_none(resolution)
        caller_estimate_was_required = (
            item.nutrition_estimate is not None
            and resolution_without_estimate.status != "complete"
        )
    if nutrition is not None:
        nutrition = _apply_preparation_losses(
            nutrition, item.preparation_losses
        )
        try:
            validate_consumed_hydration(
                nutrition,
                ConsumptionMeasure(
                    weight_g=consumed,
                    volume_ml=consumed_volume,
                    servings=consumed_servings,
                ),
            )
        except NutritionNormalizationError as error:
            raise MealValidationError(str(error)) from error
        nutrition_evidence = _prepared_nutrition_evidence(
            nutrition,
            item=item,
            direct=direct_nutrition,
            portion_expression=portion_expression,
            consumed_weight=consumed,
            consumed_volume=consumed_volume,
            consumed_servings=consumed_servings,
        )

    derived = {
        "source_confidence": Decimal("0")
        if draft.location_type in _EXTERNAL_LOCATIONS
        else Decimal("1")
        if draft.location_type == "home"
        else Decimal("0.5"),
        "name_match_confidence": Decimal("1") if eligible else Decimal("0.5"),
        "quantity_confidence": Decimal("1") if selection else Decimal("0"),
        # Multiple physical batches of the same resolved product are not an
        # ambiguity: selection is deterministic under the configured FIFO
        # strategy. Product-name ambiguity is rejected by resolve_inventory_name.
        "batch_uniqueness": Decimal("1") if eligible else Decimal("0"),
        "context_consistency": Decimal("1"),
        "personal_rule_confidence": _personal_rule_confidence(
            connection, normalized_name
        ),
    }
    values = {
        name: getattr(signals, name)
        if getattr(signals, name) is not None
        else derived[name]
        for name in _CONFIDENCE_FIELDS
    }
    confidence = _weighted_confidence(values, settings)
    confirmation_reasons = _confirmation_reasons_for_item(
        signals=signals,
        settings=settings,
        confidence=confidence,
        amount=amount,
        unit=unit,
        canonical_unit=canonical_unit,
        consumed_weight=consumed,
        consumed_volume=consumed_volume,
        consumed_servings=consumed_servings,
        caller_estimate_was_required=caller_estimate_was_required,
        nutrition_unknown_requires_confirmation=(
            bool(selection) and nutrition is None
        ),
        quantity_estimate_unconfirmed=(
            quantity_estimate is not None
            and quantity_estimate.confirmation_required
        ),
    )
    candidates = tuple(
        InventoryDeductionLine(
            batch_code=row["batch_code"], quantity=quantity, unit=row["unit"]
        )
        for row, quantity in selection
    )
    action = (
        InventoryLinkAction.NONE
        if grouping
        else _inventory_action(
            confidence,
            draft.location_type,
            bool(selection),
            settings,
            inventory_enabled=inventory_enabled,
        )
    )
    if action is InventoryLinkAction.NONE:
        candidates = ()
    elif action is InventoryLinkAction.DEDUCT:
        for row, quantity in selection:
            reserved_quantities[row["id"]] = (
                reserved_quantities.get(row["id"], Decimal("0")) + quantity
            )
    deductions = candidates if action is InventoryLinkAction.DEDUCT else ()
    public = MealItem(
        raw_name=raw_name,
        normalized_name=normalized_name,
        amount=amount,
        unit=unit,
        consumed_weight_g=consumed,
        consumed_volume_ml=consumed_volume,
        consumed_servings=consumed_servings,
        raw_weight_g=raw_weight,
        inventory_deduction_weight_g=None,
        edible_ratio=edible_ratio,
        cooking_yield=cooking_yield,
        calories=nutrition.calories if nutrition is not None else None,
        protein=nutrition.protein if nutrition is not None else None,
        fat=nutrition.fat if nutrition is not None else None,
        carbohydrate=nutrition.carbohydrate if nutrition is not None else None,
        fiber=nutrition.fiber if nutrition is not None else None,
        sodium=nutrition.sodium if nutrition is not None else None,
        # Food moisture is nutrition composition, not drinking behavior.  Only
        # an item with an explicit consumed liquid volume may advance the
        # hydration ledger (for example soy milk or another nutritious drink).
        hydration_ml=(
            nutrition.hydration_ml
            if nutrition is not None and consumed_volume is not None
            else None
        ),
        source_grade=nutrition.source_grade if nutrition is not None else "unknown",
        nutrition_source=nutrition.source if nutrition is not None else None,
        uncertainty=nutrition.uncertainty if nutrition is not None else None,
        confidence=confidence,
        inventory_action=action,
        deductions=deductions,
        role=node.role,
        leftover=leftover,
        quantity_estimate=quantity_estimate,
        portion_expression=portion_expression,
    )
    return (
        _PreparedItem(
            public=public,
            nutrition_evidence=nutrition_evidence,
            planned_inventory_deduction_weight_g=deduction_quantity,
            candidates=candidates,
            parent_index=node.parent_index,
            display_order=node.display_order,
            confirmation_reasons=confirmation_reasons,
        ),
        resources,
    )


def _confirmation_reasons_for_item(
    *,
    signals: ConfidenceSignals,
    settings: Settings,
    confidence: Decimal,
    amount: Decimal | None,
    unit: str | None,
    canonical_unit: str | None,
    consumed_weight: Decimal | None,
    consumed_volume: Decimal | None,
    consumed_servings: Decimal | None,
    caller_estimate_was_required: bool,
    nutrition_unknown_requires_confirmation: bool,
    quantity_estimate_unconfirmed: bool,
) -> tuple[ConfirmationReason, ...]:
    threshold = _control_decimal(
        settings.behavior.inventory.ask_below_confidence,
        "ask_below_confidence",
    )
    reasons: list[ConfirmationReason] = []
    if quantity_estimate_unconfirmed:
        reasons.append(ConfirmationReason.PORTION_ESTIMATE_UNCONFIRMED)
    if nutrition_unknown_requires_confirmation:
        reasons.append(ConfirmationReason.NUTRITION_UNKNOWN)
    has_consumed_measure = any(
        value is not None and value > 0
        for value in (consumed_weight, consumed_volume, consumed_servings)
    )
    if unit is not None and canonical_unit is None and not has_consumed_measure:
        reasons.append(ConfirmationReason.UNIT_UNCERTAIN)
    quantity_is_clear = has_consumed_measure or (
        amount is not None
        and amount > 0
        and canonical_unit is not None
    )
    if not quantity_is_clear:
        reasons.append(ConfirmationReason.QUANTITY_UNCERTAIN)
    if confidence >= threshold:
        return tuple(dict.fromkeys(reasons))

    configured_weights = settings.behavior.inventory.confidence_weights
    if (
        signals.quantity_confidence is not None
        and signals.quantity_confidence < threshold
        and Decimal(
            str(configured_weights.get("quantity_confidence", 0))
        ) > 0
    ):
        reasons.append(ConfirmationReason.QUANTITY_CONFIDENCE)
    if caller_estimate_was_required:
        reasons.append(ConfirmationReason.NUTRITION_ESTIMATE_REQUIRED)
    if not reasons:
        reasons.append(ConfirmationReason.OTHER_LOW_CONFIDENCE)
    return tuple(dict.fromkeys(reasons))


def _direct_nutrition_for_item(
    item: MealItemDraft,
    *,
    consumed_weight: Decimal | None,
    consumed_volume: Decimal | None,
    consumed_servings: Decimal | None,
    portion_expression: str | None,
) -> NormalizedNutrition | None:
    if item.nutrition_facts is not None and item.nutrition_estimate is not None:
        raise MealValidationError(
            "nutrition_facts and nutrition_estimate cannot both be supplied"
        )
    facts = item.nutrition_facts or item.nutrition_estimate
    if facts is None:
        if item.nutrition_basis is not None:
            raise MealValidationError(
                "nutrition_basis requires nutrition_facts or nutrition_estimate"
            )
        return None
    if not isinstance(facts, NutritionFacts):
        raise MealValidationError(
            "direct nutrition must contain NutritionFacts"
        )
    if item.nutrition_basis is None:
        raise MealValidationError(
            "nutrition_basis is required when direct nutrition is supplied"
        )
    if not isinstance(item.nutrition_basis, NutritionBasis):
        raise MealValidationError("nutrition_basis is invalid")
    portion_evidence = {
        "portion_expression": portion_expression,
        "consumed_weight_g": (
            _decimal_payload(consumed_weight, "consumed_weight_g")
            if consumed_weight is not None
            else None
        ),
        "consumed_volume_ml": (
            _decimal_payload(consumed_volume, "consumed_volume_ml")
            if consumed_volume is not None
            else None
        ),
        "consumed_servings": (
            _decimal_payload(consumed_servings, "consumed_servings")
            if consumed_servings is not None
            else None
        ),
    }
    try:
        return normalize_nutrition(
            NutritionEvidence(
                facts=facts,
                basis=item.nutrition_basis,
                dataset_version=item.nutrition_dataset_version,
                rules_version=_NUTRITION_RULES_VERSION,
                portion_evidence=portion_evidence,
            ),
            ConsumptionMeasure(
                weight_g=consumed_weight,
                volume_ml=consumed_volume,
                servings=consumed_servings,
            ),
        )
    except NutritionNormalizationError as error:
        raise MealValidationError(str(error)) from error


def _prepared_nutrition_evidence(
    nutrition: NutritionResult,
    *,
    item: MealItemDraft,
    direct: NormalizedNutrition | None,
    portion_expression: str | None,
    consumed_weight: Decimal | None,
    consumed_volume: Decimal | None,
    consumed_servings: Decimal | None,
) -> _PreparedNutritionEvidence:
    facts = item.nutrition_facts or item.nutrition_estimate
    if (
        direct is not None
        and facts is not None
        and nutrition.source == direct.result.source
    ):
        return _PreparedNutritionEvidence(
            basis=item.nutrition_basis or NutritionBasis.CONSUMED_TOTAL,
            input_facts=facts,
            scale_factor=direct.scale_factor,
            dataset_version=item.nutrition_dataset_version,
            rules_version=_NUTRITION_RULES_VERSION,
            portion_evidence={
                "portion_expression": portion_expression,
                "quantity_estimate": (
                    _quantity_estimate_payload(item.quantity_estimate)
                    if item.quantity_estimate is not None
                    else None
                ),
                "consumed_weight_g": (
                    _decimal_payload(consumed_weight, "consumed_weight_g")
                    if consumed_weight is not None
                    else None
                ),
                "consumed_volume_ml": (
                    _decimal_payload(consumed_volume, "consumed_volume_ml")
                    if consumed_volume is not None
                    else None
                ),
                "consumed_servings": (
                    _decimal_payload(consumed_servings, "consumed_servings")
                    if consumed_servings is not None
                    else None
                ),
                **_preparation_loss_evidence(item.preparation_losses),
            },
            calculation_status=direct.calculation_status,
            provenance_status=direct.provenance_status,
            warnings=direct.warnings,
        )
    return _PreparedNutritionEvidence(
        basis=NutritionBasis.CONSUMED_TOTAL,
        input_facts=nutrition,
        scale_factor=Decimal("1"),
        dataset_version=None,
        rules_version=_NUTRITION_RULES_VERSION,
        portion_evidence={
            "portion_expression": portion_expression,
            "quantity_estimate": (
                _quantity_estimate_payload(item.quantity_estimate)
                if item.quantity_estimate is not None
                else None
            ),
            "normalization_note": "resolved result persisted as consumed total",
            **_preparation_loss_evidence(item.preparation_losses),
        },
        calculation_status="valid",
        provenance_status="partial",
        warnings=("source basis persisted as consumed total",),
    )


def _apply_preparation_losses(
    nutrition: NutritionResult,
    losses: Sequence[PreparationLossDraft],
) -> NutritionResult:
    """Subtract explicit removed nutrients after portion scaling."""

    if not losses:
        return nutrition
    updates: dict[str, Decimal | None] = {}
    for nutrient in (*nutrition_resolution.CORE_FIELDS, "hydration_ml"):
        current = getattr(nutrition, nutrient)
        deduction = sum(
            (
                getattr(loss.nutrition_facts, nutrient) or Decimal("0")
                for loss in losses
            ),
            Decimal("0"),
        )
        if current is None:
            if deduction > 0:
                raise MealValidationError(
                    f"preparation loss cannot subtract unknown {nutrient}"
                )
            updates[nutrient] = None
            continue
        remaining = current - deduction
        if remaining < 0:
            raise MealValidationError(
                f"preparation loss exceeds consumed {nutrient}"
            )
        updates[nutrient] = remaining
    return replace(nutrition, **updates)


def _preparation_loss_evidence(
    losses: Sequence[PreparationLossDraft],
) -> dict[str, Any]:
    if not losses:
        return {}
    return {
        "preparation_losses": [
            {
                "kind": loss.kind,
                "quantity": _decimal_payload(
                    loss.quantity, "preparation loss quantity"
                ),
                "unit": loss.unit,
                "nutrition_facts": _facts_payload(loss.nutrition_facts),
            }
            for loss in losses
        ]
    }


def _nutrition_sources_for_item(
    item: MealItemDraft,
    consumed_weight: Decimal | None,
    repository: NutritionRepository | None,
) -> tuple[NutritionResult | None, ...]:
    if consumed_weight is None or repository is None:
        return ()
    return tuple(
        calculate_nutrition(source, consumed_weight)
        if source is not None
        else None
        for source in repository.resolution_sources(
            item.normalized_name,
            brand=item.brand,
            estimate=None,
        )
    )


def _prepared_nutrition_for_selection(
    connection: sqlite3.Connection,
    selection: Sequence[tuple[sqlite3.Row, Decimal]],
):
    if not selection:
        return None
    prepared_results = []
    for row, selected_quantity in selection:
        prepared = connection.execute(
            """
            SELECT nutrition_json, initial_quantity, source_grade
            FROM prepared_food_profiles
            WHERE pantry_batch_id = ?
            """,
            (row["id"],),
        ).fetchone()
        if prepared is None:
            prepared_results = []
            break
        snapshot = json.loads(prepared["nutrition_json"])
        if not isinstance(snapshot, Mapping):
            raise MealValidationError(
                "stored prepared nutrition must be an object"
            )
        prepared_results.append(
            prepared_foods.calculate_prepared_nutrition(
                snapshot,
                selected_quantity=selected_quantity,
                initial_quantity=Decimal(str(prepared["initial_quantity"])),
                source_grade=prepared["source_grade"],
            )
        )
    return _combine_nutrition_results(prepared_results) if prepared_results else None


def _label_nutrition_for_selection(
    connection: sqlite3.Connection,
    selection: Sequence[tuple[sqlite3.Row, Decimal]],
    consumed_weight: Decimal | None,
    *,
    inventory_unit: str,
):
    if not selection:
        return None
    snapshots: list[tuple[str, str, str]] = []
    for row, _quantity in selection:
        linked = connection.execute(
            """
            SELECT links.nutrition_snapshot_json, profiles.source_grade,
                   profiles.serving_basis
            FROM pantry_nutrition_links AS links
            JOIN nutrition_profiles AS profiles
              ON profiles.id = links.nutrition_profile_id
            WHERE links.pantry_batch_id = ?
            """,
            (row["id"],),
        ).fetchone()
        if linked is None:
            return None
        snapshots.append(
            (
                linked["nutrition_snapshot_json"],
                linked["source_grade"],
                linked["serving_basis"],
            )
        )
    if any(snapshot != snapshots[0] for snapshot in snapshots[1:]):
        return None
    snapshot_json, source_grade, serving_basis = snapshots[0]
    decoded = json.loads(snapshot_json)
    if not isinstance(decoded, Mapping):
        return None
    selected_amount = sum(
        (quantity for _, quantity in selection), Decimal("0")
    )
    if serving_basis == "per_serving":
        package_factors = tuple(
            row["base_quantity_per_display_unit"]
            for row, _quantity in selection
        )
        if all(factor is not None for factor in package_factors):
            try:
                basis_amount = sum(
                    (
                        quantity / Decimal(str(factor))
                        for (row, quantity), factor in zip(
                            selection, package_factors, strict=True
                        )
                    ),
                    Decimal("0"),
                )
            except (InvalidOperation, ValueError, ZeroDivisionError) as error:
                raise MealValidationError(
                    "stored pantry package relation is invalid"
                ) from error
        elif inventory_matching.canonical_inventory_unit(inventory_unit) in {
            "piece",
            "portion",
            "pack",
        }:
            basis_amount = selected_amount
        else:
            basis_amount = None
    else:
        basis_amount = (
            selected_amount
            if serving_basis == "per_100ml"
            else consumed_weight
            if consumed_weight is not None
            else selected_amount
            if inventory_unit == "ml"
            else None
        )
    if basis_amount is None:
        return None
    if serving_basis == "per_100ml" and inventory_unit != "ml":
        return None
    try:
        result = nutrition_profiles.calculate_snapshot_nutrition(
            decoded,
            basis_amount,
            source_grade=source_grade,
            serving_basis=serving_basis,
        )
        if inventory_unit == "ml" and result.hydration_ml is None:
            result = replace(
                result,
                hydration_ml=selected_amount,
                uncertainty=(
                    result.uncertainty
                    or "Liquid hydration inferred from consumed volume"
                ),
            )
        return result
    except ValueError as error:
        raise MealValidationError(str(error)) from error


def _combine_nutrition_results(results):
    def total(field: str) -> Decimal | None:
        values = [
            getattr(result, field)
            for result in results
            if getattr(result, field) is not None
        ]
        return sum(values, Decimal("0")) if values else None

    return NutritionResult(
        calories=total("calories"),
        protein=total("protein"),
        fat=total("fat"),
        carbohydrate=total("carbohydrate"),
        fiber=total("fiber"),
        sodium=total("sodium"),
        hydration_ml=total("hydration_ml"),
        source="prepared_leftover",
        source_grade=_combined_grade(
            tuple(result.source_grade for result in results)
        ),
        uncertainty=(
            "Some nutrients in the prepared food are unknown"
            if any(result.uncertainty for result in results)
            else None
        ),
    )


def _inventory_action(
    confidence: Decimal,
    location_type: str,
    has_selection: bool,
    settings: Settings,
    *,
    inventory_enabled: bool,
) -> InventoryLinkAction:
    if (
        not inventory_enabled
        or location_type in _EXTERNAL_LOCATIONS
        or not has_selection
    ):
        return InventoryLinkAction.NONE
    auto_threshold = settings.behavior.inventory.auto_deduct_confidence
    pending_threshold = settings.behavior.inventory.pending_link_confidence
    if pending_threshold > auto_threshold:
        raise MealValidationError("pending confidence threshold cannot exceed auto threshold")
    auto_deduct = settings.behavior.inventory.auto_deduct
    if confidence >= auto_threshold and bool(auto_deduct):
        return InventoryLinkAction.DEDUCT
    if confidence >= pending_threshold:
        return InventoryLinkAction.PENDING
    return InventoryLinkAction.NONE


def _weighted_confidence(
    values: Mapping[str, Decimal], settings: Settings
) -> Decimal:
    configured = settings.behavior.inventory.confidence_weights
    weights = {
        name: _weight_decimal(configured.get(name, 0), f"weight {name}")
        for name in _CONFIDENCE_FIELDS
    }
    total_weight = sum(weights.values(), Decimal("0"))
    if total_weight <= 0:
        raise MealValidationError("confidence weights must have a positive total")
    with localcontext() as context:
        context.prec = 50
        result = sum(
            (values[name] * weights[name] for name in _CONFIDENCE_FIELDS),
            Decimal("0"),
        ) / total_weight
        return result.quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )


def _confidence_signals(value: ConfidenceSignals) -> ConfidenceSignals:
    if not isinstance(value, ConfidenceSignals):
        raise MealValidationError("confidence_signals must be ConfidenceSignals")
    checked = {
        name: _optional_control_decimal(getattr(value, name), name)
        for name in _CONFIDENCE_FIELDS
    }
    return ConfidenceSignals(**checked)


def _item_confidence_signals(item: MealItemDraft) -> ConfidenceSignals:
    signals = _confidence_signals(item.confidence_signals)
    return ConfidenceSignals(
        **{
            name: _optional_control_decimal(getattr(item, name), name)
            if getattr(item, name) is not None
            else getattr(signals, name)
            for name in _CONFIDENCE_FIELDS
        }
    )


def _personal_rule_confidence(
    connection: sqlite3.Connection, normalized_name: str
) -> Decimal:
    row = connection.execute(
        """
        SELECT confidence
        FROM personal_rules
        WHERE active = 1
          AND rule_type = 'inventory_link'
          AND lower(subject) = ?
        ORDER BY confidence DESC, id DESC
        LIMIT 1
        """,
        (normalized_name,),
    ).fetchone()
    return Decimal(str(row["confidence"])) if row is not None else Decimal("0")


def _eligible_batches(
    connection: sqlite3.Connection,
    normalized_name: str,
    unit: str,
    *,
    inventory_credits: Mapping[int, Decimal] | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> list[sqlite3.Row]:
    credits = inventory_credits or {}
    order = deduction_order_sql(deduction_strategy)
    rows = connection.execute(
        f"""
        SELECT *
        FROM pantry_batches
        WHERE normalized_name = ?
          AND lower(unit) = ?
        ORDER BY {order}
        """,
        (normalized_name, unit),
    ).fetchall()
    return [
        row
        for row in rows
        if (
            Decimal(str(row["remaining_quantity"])) > 0
            and row["status"] in _ELIGIBLE_BATCH_STATUSES
        )
        or credits.get(row["id"], Decimal("0")) > 0
    ]


def _payload_inventory_unit(value: Any) -> str:
    if isinstance(value, str):
        try:
            return inventory_matching.canonical_inventory_unit(value)
        except ValueError:
            pass
    return "g"


def _meal_inventory_credits(
    connection: sqlite3.Connection, meal_id: int
) -> dict[int, Decimal]:
    rows = connection.execute(
        """
        SELECT pantry_batch_id, quantity
        FROM pantry_movements
        WHERE linked_meal_id = ?
          AND movement_type = 'consume'
        ORDER BY id
        """,
        (meal_id,),
    ).fetchall()
    credits: dict[int, Decimal] = {}
    for row in rows:
        batch_id = int(row["pantry_batch_id"])
        credits[batch_id] = credits.get(batch_id, Decimal("0")) + Decimal(
            str(row["quantity"])
        )
    return credits


def _select_batch_rows(
    rows: Sequence[sqlite3.Row],
    required: Decimal,
    *,
    inventory_credits: Mapping[int, Decimal] | None = None,
    reserved_quantities: Mapping[int, Decimal] | None = None,
) -> list[tuple[sqlite3.Row, Decimal]]:
    remaining = required
    selected: list[tuple[sqlite3.Row, Decimal]] = []
    credits = inventory_credits or {}
    reserved = reserved_quantities or {}
    for row in rows:
        available = (
            Decimal(str(row["remaining_quantity"]))
            + credits.get(row["id"], Decimal("0"))
            - reserved.get(row["id"], Decimal("0"))
        )
        quantity = min(remaining, available)
        if quantity > 0:
            selected.append((row, quantity))
            remaining -= quantity
        if remaining == 0:
            return selected
    return []


def _verify_resource_versions(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    resources: tuple[tuple[int, int], ...],
) -> None:
    expected = dict(resources)
    strategy = _payload_deduction_strategy(payload)
    for batch_id, version in resources:
        row = connection.execute(
            "SELECT version FROM pantry_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None or row["version"] != version:
            raise PreviewStaleError("Pantry resources changed after meal preview")

    location_type = payload.get("location_type")
    current_ids: set[int] = set()
    if location_type not in _EXTERNAL_LOCATIONS:
        for item in _payload_items(payload):
            quantity = _payload_optional_decimal(
                item.get("planned_inventory_deduction_weight_g"),
                "planned_inventory_deduction_weight_g",
            )
            if quantity is None or quantity <= 0:
                continue
            name = _required_text(item.get("normalized_name"), "normalized_name")
            inventory_unit = _payload_inventory_unit(item.get("unit"))
            current_ids.update(
                row["id"]
                for row in _eligible_batches(
                    connection,
                    name,
                    inventory_unit,
                    deduction_strategy=strategy,
                )
            )
    if current_ids != set(expected):
        raise PreviewStaleError("Pantry resources changed after meal preview")


def _insert_prepared_meal(
    connection: sqlite3.Connection,
    context: MutationContext,
    payload: Mapping[str, Any],
    *,
    committed_at: str,
    source_text: str,
) -> int:
    payload_items = _payload_items(payload)
    evidence_payloads = tuple(
        _mapping_payload(item["nutrition_evidence"], "nutrition_evidence")
        for item in payload_items
        if item.get("nutrition_evidence") is not None
    )
    calculation_status = (
        "valid"
        if evidence_payloads
        and all(
            evidence.get("calculation_status") == "valid"
            for evidence in evidence_payloads
        )
        else "unverified"
    )
    provenance_values = {
        evidence.get("provenance_status") for evidence in evidence_payloads
    }
    provenance_status = (
        "traceable"
        if provenance_values == {"traceable"}
        else "untraceable"
        if not evidence_payloads or provenance_values == {"untraceable"}
        else "partial"
    )
    missing_totals = tuple(
        field
        for field in nutrition_resolution.CORE_FIELDS
        if payload.get(f"total_{field}") is None
    )
    nutrition_status = (
        "complete"
        if not missing_totals
        else "incomplete"
        if len(missing_totals) == len(nutrition_resolution.CORE_FIELDS)
        else "partial"
    )
    committed_datetime = _parse_timestamp(committed_at)
    for item in payload_items:
        leftover_value = item.get("leftover")
        if leftover_value is None:
            continue
        prepared_foods.validate_leftover_expiry(
            _leftover_from_payload(
                _mapping_payload(leftover_value, "leftover")
            ),
            committed_datetime,
        )
    deduction_strategy = _payload_deduction_strategy(payload)
    meal_row = context.insert(
        "meals",
        {
            "occurred_at": _required_text(payload.get("occurred_at"), "occurred_at"),
            "event_timezone": _required_text(
                payload.get("event_timezone"), "event_timezone"
            ),
            "local_date": _required_text(
                payload.get("local_date"), "local_date"
            ),
            "meal_type": _meal_type(payload.get("meal_type")),
            "source_text": _required_text(payload.get("source_text"), "source_text"),
            "location_type": _location_type(payload.get("location_type")),
            "nutrition_status": nutrition_status,
            "nutrition_missing_fields_json": _canonical_json(
                list(missing_totals)
            ),
            "intake_fingerprint": _intake_fingerprint(payload),
            "nutrition_calculation_status": calculation_status,
            "nutrition_provenance_status": provenance_status,
            "total_calories": _canonical_stored_decimal(
                payload.get("total_calories"), "total_calories"
            ),
            "total_protein": _canonical_stored_decimal(
                payload.get("total_protein"), "total_protein"
            ),
            "total_fat": _canonical_stored_decimal(
                payload.get("total_fat"), "total_fat"
            ),
            "total_carbohydrate": _canonical_stored_decimal(
                payload.get("total_carbohydrate"), "total_carbohydrate"
            ),
            "total_fiber": _canonical_stored_decimal(
                payload.get("total_fiber"), "total_fiber"
            ),
            "total_sodium": _canonical_stored_decimal(
                payload.get("total_sodium"), "total_sodium"
            ),
            "total_hydration_ml": _canonical_stored_decimal(
                payload.get("total_hydration_ml"), "total_hydration_ml"
            ),
            "confidence": encode_decimal_text(
                _payload_decimal(payload.get("confidence"), "confidence"),
                "confidence",
            ),
            "created_at": committed_at,
            "updated_at": committed_at,
        },
    )
    meal_id = meal_row["id"]
    inserted_item_ids: list[int] = []
    for item_index, item in enumerate(payload_items):
        parent_index = item.get("parent_index")
        if parent_index is not None and (
            isinstance(parent_index, bool)
            or not isinstance(parent_index, int)
            or parent_index < 0
            or parent_index >= item_index
        ):
            raise PreviewError("Stored meal item hierarchy is invalid")
        display_order = item.get("display_order")
        if (
            isinstance(display_order, bool)
            or not isinstance(display_order, int)
            or display_order < 0
        ):
            raise PreviewError("Stored meal item display order is invalid")
        role = _meal_item_role(item.get("item_role"))
        action = _inventory_link_action(item.get("inventory_action"))
        candidates = _candidate_lines(item)
        actual_deduction = (
            item.get("planned_inventory_deduction_weight_g")
            if action is InventoryLinkAction.DEDUCT and candidates
            else None
        )
        item_row = context.insert(
            "meal_items",
            {
                "meal_id": meal_id,
                "parent_item_id": inserted_item_ids[parent_index]
                if parent_index is not None
                else None,
                "item_role": role.value,
                "display_order": display_order,
                "raw_name": _required_text(item.get("raw_name"), "raw_name"),
                "normalized_name": _required_text(
                    item.get("normalized_name"), "normalized_name"
                ),
                "amount": _canonical_stored_decimal(item.get("amount"), "amount"),
                "unit": item.get("unit"),
                "consumed_weight_g": _canonical_stored_decimal(
                    item.get("consumed_weight_g"), "consumed_weight_g"
                ),
                "consumed_volume_ml": _canonical_stored_decimal(
                    item.get("consumed_volume_ml"), "consumed_volume_ml"
                ),
                "consumed_servings": _canonical_stored_decimal(
                    item.get("consumed_servings"), "consumed_servings"
                ),
                "raw_weight_g": _canonical_stored_decimal(
                    item.get("raw_weight_g"), "raw_weight_g"
                ),
                "inventory_deduction_weight_g": _canonical_stored_decimal(
                    actual_deduction, "inventory_deduction_weight_g"
                ),
                "edible_ratio": _canonical_stored_decimal(
                    item.get("edible_ratio"), "edible_ratio"
                ),
                "cooking_yield": _canonical_stored_decimal(
                    item.get("cooking_yield"), "cooking_yield"
                ),
                "calories": _canonical_stored_decimal(
                    item.get("calories"), "calories"
                ),
                "protein": _canonical_stored_decimal(
                    item.get("protein"), "protein"
                ),
                "fat": _canonical_stored_decimal(item.get("fat"), "fat"),
                "carbohydrate": _canonical_stored_decimal(
                    item.get("carbohydrate"), "carbohydrate"
                ),
                "fiber": _canonical_stored_decimal(item.get("fiber"), "fiber"),
                "sodium": _canonical_stored_decimal(item.get("sodium"), "sodium"),
                "hydration_ml": _canonical_stored_decimal(
                    item.get("hydration_ml"), "hydration_ml"
                ),
                "source_grade": _source_grade(item.get("source_grade")),
                "nutrition_source": _optional_text(
                    item.get("nutrition_source"), "nutrition_source"
                ),
                "uncertainty": _optional_text(
                    item.get("uncertainty"), "uncertainty"
                ),
                "confidence": encode_decimal_text(
                    _payload_decimal(item.get("confidence"), "confidence"),
                    "confidence",
                ),
            },
        )
        inserted_item_ids.append(item_row["id"])
        evidence_value = item.get("nutrition_evidence")
        if evidence_value is not None:
            evidence = _mapping_payload(
                evidence_value, "nutrition_evidence"
            )
            context.insert(
                "meal_item_nutrition_evidence",
                {
                    "meal_item_id": item_row["id"],
                    "basis": _required_text(
                        evidence.get("basis"), "nutrition evidence basis"
                    ),
                    "input_facts_json": _canonical_json(
                        _mapping_payload(
                            evidence.get("input_facts"),
                            "nutrition evidence input_facts",
                        )
                    ),
                    "scale_factor": _canonical_stored_decimal(
                        evidence.get("scale_factor"),
                        "nutrition evidence scale_factor",
                    ),
                    "source_name": _required_text(
                        evidence.get("source_name"),
                        "nutrition evidence source_name",
                    ),
                    "source_grade": _source_grade(
                        evidence.get("source_grade")
                    ),
                    "dataset_version": _optional_text(
                        evidence.get("dataset_version"),
                        "nutrition evidence dataset_version",
                    ),
                    "rules_version": _required_text(
                        evidence.get("rules_version"),
                        "nutrition evidence rules_version",
                    ),
                    "portion_evidence_json": _canonical_json(
                        _mapping_payload(
                            evidence.get("portion_evidence"),
                            "nutrition evidence portion_evidence",
                        )
                    ),
                    "calculation_status": _required_text(
                        evidence.get("calculation_status"),
                        "nutrition evidence calculation_status",
                    ),
                    "provenance_status": _required_text(
                        evidence.get("provenance_status"),
                        "nutrition evidence provenance_status",
                    ),
                    "warnings_json": _canonical_json(
                        list(
                            _string_sequence(
                                evidence.get("warnings"),
                                "nutrition evidence warnings",
                            )
                        )
                    ),
                    "created_at": committed_at,
                },
            )
        if action is InventoryLinkAction.DEDUCT:
            _deduct_item(
                connection,
                context,
                meal_id,
                item_row["id"],
                item,
                candidates,
                source_text=source_text,
                committed_at=committed_at,
                deduction_strategy=deduction_strategy,
            )
        elif action is InventoryLinkAction.PENDING:
            context.insert(
                "pending_inventory_links",
                {
                    "meal_item_id": item_row["id"],
                    "candidate_json": _canonical_json(
                        {
                            "batches": [
                                _deduction_payload(line) for line in candidates
                            ],
                            "normalized_name": item["normalized_name"],
                            "quantity": item.get(
                                "planned_inventory_deduction_weight_g"
                            ),
                            "unit": "g",
                        }
                    ),
                    "confidence": encode_decimal_text(
                        _payload_decimal(item.get("confidence"), "confidence"),
                        "confidence",
                    ),
                    "status": "pending",
                    "created_at": committed_at,
                },
            )
    for item_index, item in enumerate(payload_items):
        leftover_value = item.get("leftover")
        if leftover_value is None:
            continue
        if _meal_item_role(item.get("item_role")) is not MealItemRole.DISH:
            raise PreviewError("Stored leftover must belong to a prepared dish")
        leftover = _leftover_from_payload(
            _mapping_payload(leftover_value, "leftover")
        )
        consumed_quantity = _payload_decimal(
            item.get("amount"), "consumed dish amount"
        )
        prepared_foods.create_leftover_in_context(
            connection,
            context,
            source_meal_id=meal_id,
            draft=leftover,
            consumed_nutrition=_descendant_nutrition(
                payload_items, item_index
            ),
            consumed_quantity=consumed_quantity,
            committed_at=_parse_timestamp(committed_at),
            source_text=source_text,
            aliases={},
            source_grade=_source_grade(payload.get("source_grade")),
        )
    return meal_id


def _insert_prepared(
    connection: sqlite3.Connection,
    context: MutationContext,
    prepared: _PreparedMeal,
    *,
    committed_at: str,
    source_text: str,
) -> int:
    payload = _prepared_payload(prepared)
    return _insert_prepared_meal(
        connection,
        context,
        payload,
        committed_at=committed_at,
        source_text=source_text,
    )


def _detach_meal_inventory(
    connection: sqlite3.Connection,
    context: MutationContext,
    meal_id: int,
    *,
    changed_at: str,
    reason: str,
) -> None:
    """Restore deductions and expire unresolved links for a corrected meal."""

    _retire_derived_leftovers(
        connection,
        context,
        meal_id,
        changed_at=changed_at,
        reason=reason,
    )

    movements = connection.execute(
        """
        SELECT
            movements.id AS movement_id,
            movements.pantry_batch_id,
            movements.quantity AS movement_quantity,
            movements.unit AS movement_unit,
            movements.linked_meal_item_id,
            movements.prior_status,
            (
                SELECT MAX(later.id)
                FROM pantry_movements AS later
                WHERE later.pantry_batch_id = movements.pantry_batch_id
            ) AS latest_movement_id
        FROM pantry_movements AS movements
        WHERE movements.linked_meal_id = ?
          AND movements.movement_type = 'consume'
        ORDER BY movements.id DESC
        """,
        (meal_id,),
    ).fetchall()
    for movement in movements:
        later_movements = connection.execute(
            """
            SELECT movement_type
            FROM pantry_movements
            WHERE pantry_batch_id = ?
              AND id > ?
              AND id <= ?
            ORDER BY id
            """,
            (
                movement["pantry_batch_id"],
                movement["movement_id"],
                movement["latest_movement_id"],
            ),
        ).fetchall()
        for later in later_movements:
            movement_type = later["movement_type"]
            if (
                movement_type not in _STATUS_ONLY_MOVEMENTS
                and movement_type not in _RELATIVE_INVENTORY_MOVEMENTS
            ):
                raise PreviewStaleError(
                    "Cannot safely restore inventory after a later "
                    "absolute pantry movement"
                )
        batch = connection.execute(
            "SELECT * FROM pantry_batches WHERE id = ?",
            (movement["pantry_batch_id"],),
        ).fetchone()
        if batch is None:
            raise PreviewStaleError("Linked pantry batch no longer exists")
        quantity = Decimal(str(movement["movement_quantity"]))
        current_remaining = Decimal(str(batch["remaining_quantity"]))
        remaining = current_remaining + quantity
        restored_status = batch["status"]
        if batch["status"] == "consumed":
            if current_remaining != 0:
                raise PreviewStaleError(
                    "Cannot safely restore a consumed pantry batch with nonzero stock"
                )
            restored_status = movement["prior_status"] or "active"
            for later in later_movements:
                status = _STATUS_ONLY_MOVEMENTS.get(later["movement_type"])
                if status is not None:
                    restored_status = status
        context.update(
            "pantry_batches",
            batch["id"],
            {
                "remaining_quantity": _sqlite_real(
                    remaining, "remaining_quantity"
                ),
                "status": restored_status,
                "version": batch["version"] + 1,
            },
        )
        context.insert(
            "pantry_movements",
            {
                "pantry_batch_id": batch["id"],
                "movement_type": "restore",
                "quantity": _sqlite_real(quantity, "movement quantity"),
                "unit": movement["movement_unit"],
                "reason": reason,
                "linked_meal_id": meal_id,
                "linked_meal_item_id": movement["linked_meal_item_id"],
                "prior_status": None,
                "created_at": changed_at,
            },
        )

    pending_rows = connection.execute(
        """
        SELECT links.id
        FROM pending_inventory_links AS links
        JOIN meal_items AS items ON items.id = links.meal_item_id
        WHERE items.meal_id = ? AND links.status = 'pending'
        ORDER BY links.id
        """,
        (meal_id,),
    ).fetchall()
    for pending in pending_rows:
        context.update(
            "pending_inventory_links",
            pending["id"],
            {"status": "expired", "resolved_at": changed_at},
        )


def _retire_derived_leftovers(
    connection: sqlite3.Connection,
    context: MutationContext,
    meal_id: int,
    *,
    changed_at: str,
    reason: str,
) -> None:
    """Retire untouched leftovers or reject a correction after downstream use."""

    rows = connection.execute(
        "SELECT * FROM pantry_batches WHERE source_meal_id = ? ORDER BY id",
        (meal_id,),
    ).fetchall()
    for row in rows:
        movements = connection.execute(
            """
            SELECT movement_type
            FROM pantry_movements
            WHERE pantry_batch_id = ?
            ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
        untouched = (
            Decimal(str(row["remaining_quantity"]))
            == Decimal(str(row["initial_quantity"]))
            and [movement["movement_type"] for movement in movements] == ["add"]
        )
        if not untouched:
            raise PreviewStaleError(
                "Cannot safely correct cooking after derived leftovers changed"
            )
        quantity = Decimal(str(row["remaining_quantity"]))
        context.update(
            "pantry_batches",
            row["id"],
            {
                "remaining_quantity": 0,
                "status": "consumed",
                "version": int(row["version"]) + 1,
            },
        )
        context.insert(
            "pantry_movements",
            {
                "pantry_batch_id": row["id"],
                "movement_type": "adjust",
                "quantity": _sqlite_real(quantity, "leftover correction"),
                "unit": row["unit"],
                "reason": reason,
                "linked_meal_id": meal_id,
                "linked_meal_item_id": None,
                "prior_status": row["status"],
                "created_at": changed_at,
            },
        )


def _deduct_item(
    connection: sqlite3.Connection,
    context: MutationContext,
    meal_id: int,
    meal_item_id: int,
    item: Mapping[str, Any],
    expected_lines: tuple[InventoryDeductionLine, ...],
    *,
    source_text: str,
    committed_at: str,
    deduction_strategy: tuple[str, ...],
) -> None:
    quantity = _payload_decimal(
        item.get("planned_inventory_deduction_weight_g"),
        "planned_inventory_deduction_weight_g",
    )
    name = _required_text(item.get("normalized_name"), "normalized_name")
    inventory_unit = _payload_inventory_unit(item.get("unit"))
    selected = _select_batch_rows(
        _eligible_batches(
            connection,
            name,
            inventory_unit,
            deduction_strategy=deduction_strategy,
        ),
        quantity,
    )
    actual_lines = tuple(
        InventoryDeductionLine(row["batch_code"], selected_quantity, row["unit"])
        for row, selected_quantity in selected
    )
    if actual_lines != expected_lines:
        raise PreviewStaleError("Pantry selection changed after meal preview")
    for row, selected_quantity in selected:
        before = Decimal(str(row["remaining_quantity"]))
        remaining = before - selected_quantity
        if remaining < 0:
            raise PreviewStaleError("Pantry stock is insufficient at commit")
        context.update(
            "pantry_batches",
            row["id"],
            {
                "remaining_quantity": _sqlite_real(
                    remaining, "remaining_quantity"
                ),
                "status": "consumed" if remaining == 0 else row["status"],
                "version": row["version"] + 1,
            },
        )
        context.insert(
            "pantry_movements",
            {
                "pantry_batch_id": row["id"],
                "movement_type": "consume",
                "quantity": _sqlite_real(selected_quantity, "movement quantity"),
                "unit": row["unit"],
                "reason": source_text,
                "linked_meal_id": meal_id,
                "linked_meal_item_id": meal_item_id,
                "prior_status": row["status"],
                "created_at": committed_at,
            },
        )


def _prepared_payload(prepared: _PreparedMeal) -> dict[str, Any]:
    return {
        "confidence": _decimal_payload(prepared.confidence, "confidence"),
        "confirmation_reasons": [
            reason.value for reason in prepared.confirmation_reasons
        ],
        "deduction_strategy": list(prepared.deduction_strategy),
        "items": [
            {
                **_meal_item_payload(item.public),
                "candidates": [
                    _deduction_payload(line) for line in item.candidates
                ],
                "display_order": item.display_order,
                "item_role": item.public.role.value,
                "parent_index": item.parent_index,
                "nutrition_evidence": (
                    _nutrition_evidence_payload(item.nutrition_evidence)
                    if item.nutrition_evidence is not None
                    else None
                ),
                "planned_inventory_deduction_weight_g": (
                    _optional_decimal_payload(
                        item.planned_inventory_deduction_weight_g,
                        "planned_inventory_deduction_weight_g",
                    )
                ),
            }
            for item in prepared.items
        ],
        "location_type": prepared.location_type,
        "intake_fingerprint": prepared.intake_fingerprint,
        "meal_type": prepared.meal_type,
        "occurred_at": prepared.occurred_at,
        "event_timezone": prepared.event_timezone,
        "local_date": prepared.local_date,
        "source_grade": prepared.source_grade,
        "source_text": prepared.source_text,
        "total_calories": _optional_decimal_payload(
            prepared.total_calories, "total_calories"
        ),
        "total_carbohydrate": _optional_decimal_payload(
            prepared.total_carbohydrate, "total_carbohydrate"
        ),
        "total_fat": _optional_decimal_payload(prepared.total_fat, "total_fat"),
        "total_fiber": _optional_decimal_payload(
            prepared.total_fiber, "total_fiber"
        ),
        "total_protein": _optional_decimal_payload(
            prepared.total_protein, "total_protein"
        ),
        "total_sodium": _optional_decimal_payload(
            prepared.total_sodium, "total_sodium"
        ),
        "total_hydration_ml": _optional_decimal_payload(
            prepared.total_hydration_ml, "total_hydration_ml"
        ),
    }


def _intake_fingerprint(payload: Mapping[str, Any]) -> str:
    stored = payload.get("intake_fingerprint")
    if stored is not None:
        if (
            not isinstance(stored, str)
            or len(stored) != 64
            or any(character not in "0123456789abcdef" for character in stored)
        ):
            raise PreviewError("Stored intake fingerprint is invalid")
        return stored

    items = _payload_items(payload)
    identity_items: list[IntakeIdentityItem] = []
    for item_index, item in enumerate(items):
        parent_index = item.get("parent_index")
        parent_name = None
        if parent_index is not None:
            if (
                isinstance(parent_index, bool)
                or not isinstance(parent_index, int)
                or parent_index < 0
                or parent_index >= item_index
            ):
                raise PreviewError("Stored meal item hierarchy is invalid")
            parent_name = _required_text(
                items[parent_index].get("normalized_name"),
                "parent normalized_name",
            )
        identity_items.append(
            IntakeIdentityItem(
                normalized_name=_required_text(
                    item.get("normalized_name"), "normalized_name"
                ),
                amount=_payload_optional_decimal(item.get("amount"), "amount"),
                unit=_optional_text(item.get("unit"), "unit"),
                consumed_weight_g=_payload_optional_decimal(
                    item.get("consumed_weight_g"), "consumed_weight_g"
                ),
                consumed_volume_ml=_payload_optional_decimal(
                    item.get("consumed_volume_ml"), "consumed_volume_ml"
                ),
                consumed_servings=_payload_optional_decimal(
                    item.get("consumed_servings"), "consumed_servings"
                ),
                item_role=_required_text(
                    item.get("item_role"), "item_role"
                ),
                parent_name=parent_name,
            )
        )
    return intake_event_fingerprint(
        IntakeIdentity(
            occurred_at=_required_text(
                payload.get("occurred_at"), "occurred_at"
            ),
            meal_type=_meal_type(payload.get("meal_type")),
            location_type=_location_type(payload.get("location_type")),
            items=tuple(identity_items),
        )
    )


def _draft_intake_fingerprint(draft: MealDraft) -> str:
    nodes = _flatten_items(draft.items)
    identity_items = []
    for node in nodes:
        parent_name = (
            _required_text(
                nodes[node.parent_index].item.normalized_name,
                "parent normalized_name",
            )
            if node.parent_index is not None
            else None
        )
        identity_items.append(
            IntakeIdentityItem(
                normalized_name=_required_text(
                    node.item.normalized_name, "normalized_name"
                ),
                amount=_optional_decimal(node.item.amount, "amount"),
                unit=_optional_text(node.item.unit, "unit"),
                consumed_weight_g=_optional_decimal(
                    node.item.consumed_weight_g, "consumed_weight_g"
                ),
                consumed_volume_ml=_optional_decimal(
                    node.item.consumed_volume_ml, "consumed_volume_ml"
                ),
                consumed_servings=_optional_decimal(
                    node.item.consumed_servings, "consumed_servings"
                ),
                item_role=node.role.value,
                parent_name=parent_name,
            )
        )
    return intake_event_fingerprint(
        IntakeIdentity(
            occurred_at=_timestamp(draft.occurred_at, "occurred_at"),
            meal_type=_meal_type(draft.meal_type),
            location_type=_location_type(draft.location_type),
            items=tuple(identity_items),
        )
    )


def _meal_item_payload(item: MealItem) -> dict[str, Any]:
    return {
        "amount": _optional_decimal_payload(item.amount, "amount"),
        "calories": _optional_decimal_payload(item.calories, "calories"),
        "carbohydrate": _optional_decimal_payload(
            item.carbohydrate, "carbohydrate"
        ),
        "confidence": _decimal_payload(item.confidence, "confidence"),
        "consumed_weight_g": _optional_decimal_payload(
            item.consumed_weight_g, "consumed_weight_g"
        ),
        "consumed_volume_ml": _optional_decimal_payload(
            item.consumed_volume_ml, "consumed_volume_ml"
        ),
        "consumed_servings": _optional_decimal_payload(
            item.consumed_servings, "consumed_servings"
        ),
        "cooking_yield": _optional_decimal_payload(
            item.cooking_yield, "cooking_yield"
        ),
        "deductions": [
            _deduction_payload(line) for line in item.deductions
        ],
        "edible_ratio": _optional_decimal_payload(
            item.edible_ratio, "edible_ratio"
        ),
        "fat": _optional_decimal_payload(item.fat, "fat"),
        "fiber": _optional_decimal_payload(item.fiber, "fiber"),
        "inventory_action": item.inventory_action.value,
        "inventory_deduction_weight_g": _optional_decimal_payload(
            item.inventory_deduction_weight_g,
            "inventory_deduction_weight_g",
        ),
        "normalized_name": item.normalized_name,
        "nutrition_source": item.nutrition_source,
        "protein": _optional_decimal_payload(item.protein, "protein"),
        "raw_name": item.raw_name,
        "raw_weight_g": _optional_decimal_payload(
            item.raw_weight_g, "raw_weight_g"
        ),
        "sodium": _optional_decimal_payload(item.sodium, "sodium"),
        "hydration_ml": _optional_decimal_payload(
            item.hydration_ml, "hydration_ml"
        ),
        "source_grade": item.source_grade,
        "uncertainty": item.uncertainty,
        "unit": item.unit,
        "quantity_estimate": (
            _quantity_estimate_payload(item.quantity_estimate)
            if item.quantity_estimate is not None
            else None
        ),
        "leftover": (
            _leftover_payload(item.leftover)
            if item.leftover is not None
            else None
        ),
    }


def _nutrition_evidence_payload(
    evidence: _PreparedNutritionEvidence,
) -> dict[str, Any]:
    return {
        "basis": evidence.basis.value,
        "input_facts": _facts_payload(evidence.input_facts),
        "scale_factor": _decimal_payload(
            evidence.scale_factor, "nutrition evidence scale_factor"
        ),
        "source_name": evidence.input_facts.source,
        "source_grade": evidence.input_facts.source_grade,
        "dataset_version": evidence.dataset_version,
        "rules_version": evidence.rules_version,
        "portion_evidence": dict(evidence.portion_evidence),
        "calculation_status": evidence.calculation_status,
        "provenance_status": evidence.provenance_status,
        "warnings": list(evidence.warnings),
    }


def _deduction_payload(line: InventoryDeductionLine) -> dict[str, Any]:
    return {
        "batch_code": line.batch_code,
        "quantity": _decimal_payload(line.quantity, "deduction quantity"),
        "unit": line.unit,
    }


def _draft_payload(draft: MealDraft) -> dict[str, Any]:
    return {
        "intent": draft.intent,
        "items": [_draft_item_payload(item) for item in draft.items],
        "location_type": draft.location_type,
        "meal_type": draft.meal_type,
        "occurred_at": _timestamp(draft.occurred_at, "occurred_at"),
        "source_text": draft.source_text,
    }


def _draft_item_payload(item: MealItemDraft) -> dict[str, Any]:
    signals = _item_confidence_signals(item)
    facts = item.nutrition_facts
    return {
        "amount": _optional_decimal_payload(item.amount, "amount"),
        "brand": item.brand,
        "confidence_signals": {
            name: _optional_decimal_payload(getattr(signals, name), name)
            for name in _CONFIDENCE_FIELDS
        },
        "consumed_weight_g": _optional_decimal_payload(
            item.consumed_weight_g, "consumed_weight_g"
        ),
        "consumed_volume_ml": _optional_decimal_payload(
            item.consumed_volume_ml, "consumed_volume_ml"
        ),
        "consumed_servings": _optional_decimal_payload(
            item.consumed_servings, "consumed_servings"
        ),
        "cooking_yield": _optional_decimal_payload(
            item.cooking_yield, "cooking_yield"
        ),
        "edible_ratio": _optional_decimal_payload(
            item.edible_ratio, "edible_ratio"
        ),
        "ingredients": [
            _draft_item_payload(ingredient) for ingredient in item.ingredients
        ],
        "inventory_deduction_weight_g": _optional_decimal_payload(
            item.inventory_deduction_weight_g,
            "inventory_deduction_weight_g",
        ),
        "normalized_name": item.normalized_name,
        "portion_expression": item.portion_expression,
        "quantity_estimate": (
            _quantity_estimate_payload(item.quantity_estimate)
            if item.quantity_estimate is not None
            else None
        ),
        "nutrition_facts": _facts_payload(facts) if facts is not None else None,
        "preparation_losses": [
            {
                "kind": loss.kind,
                "quantity": _decimal_payload(
                    loss.quantity, "preparation loss quantity"
                ),
                "unit": loss.unit,
                "nutrition_facts": _facts_payload(loss.nutrition_facts),
            }
            for loss in item.preparation_losses
        ],
        "nutrition_estimate": (
            _facts_payload(item.nutrition_estimate)
            if item.nutrition_estimate is not None
            else None
        ),
        "nutrition_basis": (
            item.nutrition_basis.value
            if item.nutrition_basis is not None
            else None
        ),
        "nutrition_dataset_version": item.nutrition_dataset_version,
        "raw_name": item.raw_name,
        "raw_weight_g": _optional_decimal_payload(
            item.raw_weight_g, "raw_weight_g"
        ),
        "unit": item.unit,
        "leftover": (
            _leftover_payload(item.leftover)
            if item.leftover is not None
            else None
        ),
    }


def _quantity_estimate_payload(
    value: QuantityEstimate,
) -> dict[str, object]:
    if not isinstance(value, QuantityEstimate):
        raise MealValidationError("quantity_estimate is invalid")
    return {
        "suggested": _decimal_payload(value.suggested, "suggested"),
        "lower": _decimal_payload(value.lower, "lower"),
        "upper": _decimal_payload(value.upper, "upper"),
        "unit": value.unit,
        "evidence_type": value.evidence_type,
        "policy_key": value.policy_key,
        "confirmation_required": value.confirmation_required,
    }


def _leftover_payload(value: prepared_foods.LeftoverDraft) -> dict[str, Any]:
    return {
        "food_name": value.food_name,
        "normalized_name": value.normalized_name,
        "quantity": _decimal_payload(value.quantity, "leftover quantity"),
        "unit": value.unit,
        "storage_location": value.storage_location,
        "expires_at": prepared_foods.leftover_expiry_payload(
            value.expires_at
        ),
    }


def _leftover_from_payload(
    value: Mapping[str, Any],
) -> prepared_foods.LeftoverDraft:
    return prepared_foods.LeftoverDraft(
        food_name=_required_text(value.get("food_name"), "leftover food_name"),
        normalized_name=_required_text(
            value.get("normalized_name"), "leftover normalized_name"
        ),
        quantity=_payload_decimal(
            value.get("quantity"), "leftover quantity"
        ),
        unit=_required_text(value.get("unit"), "leftover unit"),
        storage_location=_required_text(
            value.get("storage_location"), "leftover storage_location"
        ),
        expires_at=_parse_timestamp(
            _required_text(value.get("expires_at"), "leftover expires_at")
        ),
    )


def _mapping_payload(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreviewError(f"Stored {label} must be an object")
    return value


def _string_sequence(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise PreviewError(f"Stored {label} must be an array")
    if any(not isinstance(entry, str) for entry in value):
        raise PreviewError(f"Stored {label} must contain only strings")
    return tuple(value)


def _descendant_nutrition(
    items: Sequence[Mapping[str, Any]], ancestor_index: int
) -> dict[str, Decimal | None]:
    field_map = {
        "calories_kcal": "calories",
        "protein_g": "protein",
        "fat_g": "fat",
        "carbohydrate_g": "carbohydrate",
        "fiber_g": "fiber",
        "sodium_mg": "sodium",
        "hydration_ml": "hydration_ml",
    }
    descendants = [
        item
        for candidate_index, item in enumerate(items)
        if _is_descendant(items, candidate_index, ancestor_index)
        and _meal_item_role(item.get("item_role")) is not MealItemRole.DISH
    ]
    if not descendants:
        descendants = [items[ancestor_index]]
    result: dict[str, Decimal | None] = {}
    for output_field, input_field in field_map.items():
        values = [
            _payload_decimal(item.get(input_field), input_field)
            for item in descendants
            if item.get(input_field) is not None
        ]
        result[output_field] = (
            sum(values, Decimal("0")) if values else None
        )
    return result


def _is_descendant(
    items: Sequence[Mapping[str, Any]],
    candidate_index: int,
    ancestor_index: int,
) -> bool:
    parent = items[candidate_index].get("parent_index")
    while parent is not None:
        if not isinstance(parent, int) or isinstance(parent, bool):
            raise PreviewError("Stored meal item hierarchy is invalid")
        if parent == ancestor_index:
            return True
        if parent < 0 or parent >= candidate_index:
            raise PreviewError("Stored meal item hierarchy is invalid")
        candidate_index = parent
        parent = items[candidate_index].get("parent_index")
    return False


def _facts_payload(
    facts: NutritionFacts | NutritionResult,
) -> dict[str, Any]:
    if not isinstance(facts, (NutritionFacts, NutritionResult)):
        raise MealValidationError(
            "nutrition_facts must be NutritionFacts or NutritionResult"
        )
    return {
        "calories": _optional_decimal_payload(facts.calories, "calories"),
        "carbohydrate": _optional_decimal_payload(
            facts.carbohydrate, "carbohydrate"
        ),
        "fat": _optional_decimal_payload(facts.fat, "fat"),
        "fiber": _optional_decimal_payload(facts.fiber, "fiber"),
        "protein": _optional_decimal_payload(facts.protein, "protein"),
        "sodium": _optional_decimal_payload(facts.sodium, "sodium"),
        "hydration_ml": _optional_decimal_payload(
            facts.hydration_ml, "hydration_ml"
        ),
        "source": facts.source,
        "source_grade": facts.source_grade,
        "uncertainty": facts.uncertainty,
    }


def _preview_from_prepared(
    token: str, expires_at: datetime, prepared: _PreparedMeal
) -> MealPreview:
    return MealPreview(
        token=token,
        expires_at=expires_at,
        occurred_at=_parse_timestamp(prepared.occurred_at),
        meal_type=prepared.meal_type,
        source_text=prepared.source_text,
        location_type=prepared.location_type,
        items=_nested_prepared_items(prepared.items),
        total_calories=prepared.total_calories,
        total_protein=prepared.total_protein,
        total_fat=prepared.total_fat,
        total_carbohydrate=prepared.total_carbohydrate,
        total_fiber=prepared.total_fiber,
        total_sodium=prepared.total_sodium,
        total_hydration_ml=prepared.total_hydration_ml,
        source_grade=prepared.source_grade,
        confidence=prepared.confidence,
        confirmation_reasons=prepared.confirmation_reasons,
    )


def _nested_prepared_items(
    items: tuple[_PreparedItem, ...],
) -> tuple[MealItem, ...]:
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for index, item in enumerate(items):
        if item.parent_index is None:
            roots.append(index)
        else:
            children.setdefault(item.parent_index, []).append(index)

    def build(index: int) -> MealItem:
        child_indices = sorted(
            children.get(index, ()),
            key=lambda child: (items[child].display_order, child),
        )
        return replace(
            items[index].public,
            ingredients=tuple(build(child) for child in child_indices),
        )

    return tuple(
        build(index)
        for index in sorted(
            roots, key=lambda root: (items[root].display_order, root)
        )
    )


def _read_meal(connection: sqlite3.Connection, meal_id: int) -> MealRecord:
    row = connection.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    if row is None:
        raise KeyError("Committed meal no longer exists")
    return _meal_record(connection, row)


def _meal_record(connection: sqlite3.Connection, row: sqlite3.Row) -> MealRecord:
    item_rows = connection.execute(
        """
        SELECT *
        FROM meal_items
        WHERE meal_id = ?
        ORDER BY id
        """,
        (row["id"],),
    ).fetchall()
    flat_items = {
        item_row["id"]: _meal_item_record(connection, row, item_row)
        for item_row in item_rows
    }
    children: dict[int, list[sqlite3.Row]] = {}
    roots: list[sqlite3.Row] = []
    for item_row in item_rows:
        parent_id = item_row["parent_item_id"]
        if parent_id is None:
            roots.append(item_row)
        else:
            children.setdefault(parent_id, []).append(item_row)

    def build(item_row: sqlite3.Row) -> MealItem:
        child_rows = sorted(
            children.get(item_row["id"], ()),
            key=lambda child: (child["display_order"], child["id"]),
        )
        return replace(
            flat_items[item_row["id"]],
            ingredients=tuple(build(child) for child in child_rows),
        )

    items = tuple(
        build(item_row)
        for item_row in sorted(
            roots, key=lambda root: (root["display_order"], root["id"])
        )
    )
    nutritional_items = tuple(
        item
        for item in _flatten_public_items(items)
        if item.role is not MealItemRole.DISH
    )
    return MealRecord(
        occurred_at=_parse_timestamp(row["occurred_at"]),
        meal_type=row["meal_type"],
        source_text=row["source_text"],
        location_type=row["location_type"],
        items=items,
        total_calories=_stored_optional_decimal(
            row["total_calories"], "total_calories"
        ),
        total_protein=_stored_optional_decimal(
            row["total_protein"], "total_protein"
        ),
        total_fat=_stored_optional_decimal(row["total_fat"], "total_fat"),
        total_carbohydrate=_stored_optional_decimal(
            row["total_carbohydrate"], "total_carbohydrate"
        ),
        total_fiber=_stored_optional_decimal(row["total_fiber"], "total_fiber"),
        total_sodium=_stored_optional_decimal(
            row["total_sodium"], "total_sodium"
        ),
        total_hydration_ml=_stored_optional_decimal(
            row["total_hydration_ml"], "total_hydration_ml"
        ),
        source_grade=_combined_grade(
            tuple(item.source_grade for item in nutritional_items)
        ),
        confidence=_stored_decimal(row["confidence"], "confidence"),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        deleted_at=_parse_timestamp(row["deleted_at"])
        if row["deleted_at"] is not None
        else None,
    )


def _meal_item_record(
    connection: sqlite3.Connection,
    meal_row: sqlite3.Row,
    row: sqlite3.Row,
) -> MealItem:
    evidence_row = connection.execute(
        """
        SELECT portion_evidence_json
        FROM meal_item_nutrition_evidence
        WHERE meal_item_id = ?
        """,
        (row["id"],),
    ).fetchone()
    quantity_estimate = None
    portion_expression = None
    if evidence_row is not None:
        portion_evidence = _json_object(
            evidence_row["portion_evidence_json"],
            "stored portion evidence",
        )
        portion_expression = _optional_text(
            portion_evidence.get("portion_expression"),
            "stored portion expression",
        )
        raw_quantity_estimate = portion_evidence.get("quantity_estimate")
        if raw_quantity_estimate is not None:
            quantity_estimate = _quantity_estimate_from_payload(
                _mapping_payload(
                    raw_quantity_estimate,
                    "stored quantity estimate",
                )
            )
    pending = connection.execute(
        """
        SELECT 1
        FROM pending_inventory_links
        WHERE meal_item_id = ? AND status = 'pending'
        LIMIT 1
        """,
        (row["id"],),
    ).fetchone()
    movement_rows = connection.execute(
        """
        SELECT batches.batch_code, movements.quantity, movements.unit
        FROM pantry_movements AS movements
        JOIN pantry_batches AS batches ON batches.id = movements.pantry_batch_id
        WHERE movements.linked_meal_id = ?
          AND movements.transaction_id = ?
          AND movements.movement_type = 'consume'
          AND (
              movements.linked_meal_item_id = ?
              OR (
                  movements.linked_meal_item_id IS NULL
                  AND batches.normalized_name = ?
              )
          )
        ORDER BY movements.id
        """,
        (
            meal_row["id"],
            meal_row["transaction_id"],
            row["id"],
            row["normalized_name"],
        ),
    ).fetchall()
    deductions = tuple(
        InventoryDeductionLine(
            movement["batch_code"],
            Decimal(str(movement["quantity"])),
            movement["unit"],
        )
        for movement in movement_rows
    )
    action = (
        InventoryLinkAction.DEDUCT
        if deductions
        else InventoryLinkAction.PENDING
        if pending is not None
        else InventoryLinkAction.NONE
    )
    return MealItem(
        raw_name=row["raw_name"],
        normalized_name=row["normalized_name"],
        amount=_stored_optional_decimal(row["amount"], "amount"),
        unit=row["unit"],
        consumed_weight_g=_stored_optional_decimal(
            row["consumed_weight_g"], "consumed_weight_g"
        ),
        consumed_volume_ml=_stored_optional_decimal(
            row["consumed_volume_ml"], "consumed_volume_ml"
        ),
        consumed_servings=_stored_optional_decimal(
            row["consumed_servings"], "consumed_servings"
        ),
        raw_weight_g=_stored_optional_decimal(row["raw_weight_g"], "raw_weight_g"),
        inventory_deduction_weight_g=_stored_optional_decimal(
            row["inventory_deduction_weight_g"], "inventory_deduction_weight_g"
        ),
        edible_ratio=_stored_optional_decimal(row["edible_ratio"], "edible_ratio"),
        cooking_yield=_stored_optional_decimal(
            row["cooking_yield"], "cooking_yield"
        ),
        calories=_stored_optional_decimal(row["calories"], "calories"),
        protein=_stored_optional_decimal(row["protein"], "protein"),
        fat=_stored_optional_decimal(row["fat"], "fat"),
        carbohydrate=_stored_optional_decimal(
            row["carbohydrate"], "carbohydrate"
        ),
        fiber=_stored_optional_decimal(row["fiber"], "fiber"),
        sodium=_stored_optional_decimal(row["sodium"], "sodium"),
        hydration_ml=_stored_optional_decimal(
            row["hydration_ml"], "hydration_ml"
        ),
        source_grade=row["source_grade"],
        nutrition_source=row["nutrition_source"],
        uncertainty=row["uncertainty"],
        confidence=_stored_decimal(row["confidence"], "confidence"),
        inventory_action=action,
        deductions=deductions,
        role=_meal_item_role(row["item_role"]),
        leftover=None,
        quantity_estimate=quantity_estimate,
        portion_expression=portion_expression,
    )


def _quantity_estimate_from_payload(
    payload: Mapping[str, Any],
) -> QuantityEstimate:
    confirmation_required = payload.get("confirmation_required")
    if not isinstance(confirmation_required, bool):
        raise MealValidationError(
            "stored quantity estimate confirmation_required is invalid"
        )
    return QuantityEstimate(
        suggested=_payload_decimal(payload.get("suggested"), "suggested"),
        lower=_payload_decimal(payload.get("lower"), "lower"),
        upper=_payload_decimal(payload.get("upper"), "upper"),
        unit=_required_text(payload.get("unit"), "unit"),
        evidence_type=_required_text(
            payload.get("evidence_type"), "evidence_type"
        ),
        policy_key=_required_text(payload.get("policy_key"), "policy_key"),
        confirmation_required=confirmation_required,
    )


def _selected_meal_row(
    connection: sqlite3.Connection,
    occurred_at: str,
    source_text: str,
    *,
    meal_id: int | None = None,
    expected_state: tuple[str, str | None] | None = None,
) -> sqlite3.Row:
    if meal_id is not None:
        if isinstance(meal_id, bool) or not isinstance(meal_id, int):
            raise MealValidationError("internal meal target must be an integer")
        row = connection.execute(
            "SELECT * FROM meals WHERE id = ? AND deleted_at IS NULL",
            (meal_id,),
        ).fetchone()
        if row is None:
            if expected_state is not None:
                raise MealReferenceStaleError("selected meal is stale")
            raise KeyError("No active meal matches the selected workflow reference")
        if expected_state is not None and (
            row["updated_at"], row["deleted_at"]
        ) != expected_state:
            raise MealReferenceStaleError("selected meal is stale")
        return row
    rows = connection.execute(
        """
        SELECT *
        FROM meals
        WHERE occurred_at = ? AND source_text = ? AND deleted_at IS NULL
        ORDER BY id
        """,
        (occurred_at, source_text),
    ).fetchall()
    if not rows:
        raise KeyError("No active meal matches the supplied selector")
    if len(rows) != 1:
        raise MealValidationError("meal selector is ambiguous")
    return rows[0]


def _selector_values(selector: MealSelector) -> tuple[str, str]:
    if not isinstance(selector, MealSelector):
        raise MealValidationError("selector must be MealSelector")
    return (
        _timestamp(selector.occurred_at, "selector.occurred_at"),
        _required_text(selector.source_text, "selector.source_text"),
    )


def _resource_versions(value: str) -> tuple[tuple[int, int], ...]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise PreviewError("Stored resource versions are invalid") from error
    if not isinstance(payload, list):
        raise PreviewError("Stored resource versions are invalid")
    resources: list[tuple[int, int]] = []
    seen: set[int] = set()
    for resource in payload:
        if not isinstance(resource, dict) or set(resource) != {"batch_id", "version"}:
            raise PreviewError("Stored resource versions are invalid")
        batch_id = resource["batch_id"]
        version = resource["version"]
        if (
            isinstance(batch_id, bool)
            or not isinstance(batch_id, int)
            or isinstance(version, bool)
            or not isinstance(version, int)
            or batch_id in seen
        ):
            raise PreviewError("Stored resource versions are invalid")
        seen.add(batch_id)
        resources.append((batch_id, version))
    return tuple(resources)


def _payload_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PreviewError("Stored preview items are invalid")
    return items


def _candidate_lines(
    item: Mapping[str, Any]
) -> tuple[InventoryDeductionLine, ...]:
    candidates = item.get("candidates")
    if not isinstance(candidates, list):
        raise PreviewError("Stored inventory candidates are invalid")
    lines: list[InventoryDeductionLine] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise PreviewError("Stored inventory candidates are invalid")
        batch_code = candidate.get("batch_code")
        if batch_code is not None and not isinstance(batch_code, str):
            raise PreviewError("Stored inventory candidates are invalid")
        lines.append(
            InventoryDeductionLine(
                batch_code=batch_code,
                quantity=_payload_decimal(
                    candidate.get("quantity"), "candidate quantity"
                ),
                unit=_required_text(candidate.get("unit"), "candidate unit"),
            )
        )
    return tuple(lines)


def _inventory_link_action(value: object) -> InventoryLinkAction:
    try:
        return InventoryLinkAction(value)
    except (TypeError, ValueError) as error:
        raise PreviewError("Stored inventory action is invalid") from error


def _meal_item_role(value: object) -> MealItemRole:
    try:
        return MealItemRole(value)
    except (TypeError, ValueError) as error:
        raise PreviewError("Stored meal item role is invalid") from error


def _json_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise PreviewError(f"Stored {label} is invalid") from error
    if not isinstance(payload, dict):
        raise PreviewError(f"Stored {label} is invalid")
    return payload


def _flatten_items(items: tuple[MealItemDraft, ...]) -> tuple[_DraftNode, ...]:
    if not isinstance(items, tuple):
        raise MealValidationError("items must be a tuple")
    result: list[_DraftNode] = []

    def visit(
        siblings: tuple[MealItemDraft, ...], parent_index: int | None
    ) -> None:
        if not isinstance(siblings, tuple):
            raise MealValidationError("ingredients must be a tuple")
        for display_order, item in enumerate(siblings):
            if not isinstance(item, MealItemDraft):
                raise MealValidationError("items must contain MealItemDraft values")
            if not isinstance(item.ingredients, tuple):
                raise MealValidationError("ingredients must be a tuple")
            role = (
                MealItemRole.DISH
                if item.ingredients or item.leftover is not None
                else MealItemRole.INGREDIENT
                if parent_index is not None
                else MealItemRole.FOOD
            )
            item_index = len(result)
            result.append(
                _DraftNode(
                    item=item,
                    role=role,
                    parent_index=parent_index,
                    display_order=display_order,
                )
            )
            if item.ingredients:
                visit(item.ingredients, item_index)

    visit(items, None)
    return tuple(result)


def _nutrient_total(
    items: Sequence[_PreparedItem], field_name: str
) -> Decimal | None:
    values = [
        getattr(item.public, field_name)
        for item in items
    ]
    if not values or any(value is None for value in values):
        return None
    return sum(values, Decimal("0"))


def _flatten_public_items(items: tuple[MealItem, ...]) -> tuple[MealItem, ...]:
    result: list[MealItem] = []
    for item in items:
        result.append(item)
        result.extend(_flatten_public_items(item.ingredients))
    return tuple(result)


def _combined_grade(grades: tuple[str, ...]) -> str:
    if not grades or "unknown" in grades:
        return "unknown"
    try:
        return weakest_grade(*grades)
    except ValueError as error:
        raise MealValidationError(str(error)) from error


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise MealValidationError("cannot average an empty confidence collection")
    with localcontext() as context:
        context.prec = 50
        return sum(values, Decimal("0")) / Decimal(len(values))


def _require_record_draft(draft: MealDraft) -> None:
    if not isinstance(draft, MealDraft):
        raise MealValidationError("draft must be MealDraft")
    _require_record_intent(draft.intent)


def _require_record_intent(intent: str) -> None:
    if intent != "record":
        raise MealValidationError("only intent='record' may write meal data")


def _meal_type(value: object) -> str:
    text = _required_text(value, "meal_type").lower()
    if text not in _MEAL_TYPES:
        raise MealValidationError(f"unknown meal_type: {text!r}")
    return text


def _location_type(value: object) -> str:
    text = _required_text(value, "location_type").lower()
    if text not in _LOCATION_TYPES:
        raise MealValidationError(f"unknown location_type: {text!r}")
    return text


def _source_grade(value: object) -> str:
    if value not in {"A", "B", "C", "D", "unknown"}:
        raise PreviewError("Stored source grade is invalid")
    return str(value)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MealValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise MealValidationError(f"{field_name} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise MealValidationError(
            f"{field_name} must be a finite non-negative Decimal"
        )
    return value


def _control_decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise MealValidationError(f"{field_name} must be a number from zero through one")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        raise MealValidationError(
            f"{field_name} must be a number from zero through one"
        ) from error
    if not number.is_finite() or number < 0 or number > 1:
        raise MealValidationError(f"{field_name} must be a number from zero through one")
    return number


def _optional_control_decimal(
    value: object, field_name: str
) -> Decimal | None:
    return None if value is None else _control_decimal(value, field_name)


def _weight_decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise MealValidationError(f"{field_name} must be a non-negative number")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        raise MealValidationError(
            f"{field_name} must be a non-negative number"
        ) from error
    if not number.is_finite() or number < 0:
        raise MealValidationError(f"{field_name} must be a non-negative number")
    return number


def _preview_expiration_minutes(settings: Settings) -> int:
    value = settings.behavior.inventory.preview_expiration_minutes
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MealValidationError("preview expiration minutes must be a positive integer")
    return value


def _deduction_strategy(settings: Settings) -> tuple[str, ...]:
    try:
        return normalized_deduction_strategy(
            settings.behavior.inventory.deduction_strategy
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise MealValidationError("invalid inventory deduction strategy") from error


def _payload_deduction_strategy(
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    value = payload.get("deduction_strategy")
    if not isinstance(value, list):
        raise PreviewError("Stored preview deduction strategy is invalid")
    try:
        return normalized_deduction_strategy(value)
    except (TypeError, ValueError) as error:
        raise PreviewError("Stored preview deduction strategy is invalid") from error


def _commit_deduction_strategy(value: Sequence[str]) -> tuple[str, ...]:
    try:
        return normalized_deduction_strategy(value)
    except (TypeError, ValueError) as error:
        raise PreviewError("Current deduction strategy is invalid") from error


def _decimal_payload(value: Decimal, field_name: str) -> str:
    try:
        return encode_decimal_text(value, field_name)
    except ValueError as error:
        raise MealValidationError(str(error)) from error


def _optional_decimal_payload(
    value: Decimal | None, field_name: str
) -> str | None:
    return _decimal_payload(value, field_name) if value is not None else None


def _payload_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise PreviewError(f"Stored {field_name} is invalid")
    try:
        return decode_decimal_text(value, field_name)
    except ValueError as error:
        raise PreviewError(f"Stored {field_name} is invalid") from error


def _payload_optional_decimal(
    value: object, field_name: str
) -> Decimal | None:
    return None if value is None else _payload_decimal(value, field_name)


def _canonical_stored_decimal(value: object, field_name: str) -> str | None:
    number = _payload_optional_decimal(value, field_name)
    return encode_decimal_text(number, field_name) if number is not None else None


def _stored_confirmation_reasons(
    value: object,
) -> tuple[ConfirmationReason, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PreviewStaleError("Stored confirmation reasons are invalid")
    try:
        reasons = tuple(ConfirmationReason(reason) for reason in value)
    except (TypeError, ValueError) as error:
        raise PreviewStaleError(
            "Stored confirmation reasons are invalid"
        ) from error
    if len(set(reasons)) != len(reasons):
        raise PreviewStaleError("Stored confirmation reasons are invalid")
    return reasons


def _stored_optional_decimal(
    value: object, field_name: str
) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MealValidationError(f"stored {field_name} must be canonical Decimal text")
    return decode_decimal_text(value, field_name)


def _stored_decimal(value: object, field_name: str) -> Decimal:
    number = _stored_optional_decimal(value, field_name)
    if number is None:
        raise MealValidationError(f"stored {field_name} cannot be null")
    return number


def _sqlite_real(value: Decimal, field_name: str) -> float:
    converted = float(value)
    if not math.isfinite(converted) or (
        value != 0
        and (
            converted == 0
            or (value > 0 and converted < 0)
            or (value < 0 and converted > 0)
        )
    ):
        raise MealValidationError(
            f"{field_name} is not representable as a SQLite REAL"
        )
    return converted


def _token_hash(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise MealValidationError("token must be a non-empty string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _timestamp(value: datetime, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MealValidationError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
