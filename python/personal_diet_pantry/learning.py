"""SQLite-backed adaptive personal rules with auditable evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import json
import sqlite3
from typing import Any, Mapping

from .models import Settings
from .transactions import MutationContext, TransactionManager


class LearningValidationError(ValueError):
    """Raised when a personal-learning request is not safe to persist."""


class RuleType(StrEnum):
    PORTION = "portion"
    WATER_UNIT = "water_unit"
    RECIPE = "recipe"
    HOME_SOURCE = "home_source"
    MEAL_TIME = "meal_time"
    FOOD_ALIAS = "food_alias"
    BATCH_PREFERENCE = "batch_preference"
    NUTRITION_LABEL = "nutrition_label"
    REMINDER = "reminder"
    REPLY_STYLE = "reply_style"


@dataclass(frozen=True)
class LearningResult:
    """A public rule view that deliberately excludes database and confidence details."""

    rule_type: RuleType
    subject: str
    outcome: Mapping[str, Any]
    active: bool


@dataclass(frozen=True)
class PortionRule:
    """A validated, active personal interpretation of one portion expression."""

    amount: Decimal
    unit: str


_DEFAULT_EVIDENCE_COUNT = 3
_DEFAULT_PROMOTION_CONFIDENCE = Decimal("0.8")
_DEFAULT_MAX_WATER_UNIT_ML = 5000
RESERVED_STRUCTURED_SUBJECTS = frozenset(
    {"diet_goals", "nutrition_goals", "water_goal"}
)
_SCHEMA_RULE_TYPES = {
    RuleType.PORTION: "portion",
    RuleType.FOOD_ALIAS: "food_alias",
    RuleType.MEAL_TIME: "meal_type",
    RuleType.HOME_SOURCE: "inventory_link",
    RuleType.BATCH_PREFERENCE: "inventory_link",
    RuleType.WATER_UNIT: "preference",
    RuleType.RECIPE: "preference",
    RuleType.NUTRITION_LABEL: "preference",
    RuleType.REMINDER: "preference",
    RuleType.REPLY_STYLE: "preference",
}


def set_explicit_rule(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    rule_type: RuleType,
    subject: str,
    outcome: Mapping[str, Any],
    source_text: str,
    settings: Settings | None = None,
) -> LearningResult:
    """Make an explicit user preference active immediately at full confidence."""

    normalized_type = _rule_type(rule_type)
    normalized_subject = _subject(subject)
    _reject_reserved_structured_subject(normalized_subject)
    normalized_outcome = _validated_outcome(
        normalized_type, _json_object(outcome, "outcome"), settings
    )
    source = _source_text(source_text)

    def mutate(context: MutationContext) -> sqlite3.Row:
        existing = _current_learning_rule(
            connection, normalized_type, normalized_subject
        )
        now = _utc_now()
        if existing is None:
            rule = context.insert(
                "personal_rules",
                {
                    "rule_type": _storage_rule_type(normalized_type),
                    "subject": normalized_subject,
                    "rule_json": _rule_json(normalized_type, normalized_outcome),
                    "confidence": 1.0,
                    "evidence_count": 1,
                    "source": "explicit_user",
                    "active": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        else:
            rule = context.update(
                "personal_rules",
                existing["id"],
                {
                    "rule_type": _storage_rule_type(normalized_type),
                    "rule_json": _rule_json(normalized_type, normalized_outcome),
                    "confidence": 1.0,
                    "evidence_count": max(1, existing["evidence_count"]),
                    "source": "explicit_user",
                    "active": 1,
                    "updated_at": now,
                },
            )
        context.insert(
            "learning_events",
            {
                "rule_id": rule["id"],
                "event_type": "confirmed",
                "evidence_json": _canonical_json({"outcome": normalized_outcome}),
                "created_at": now,
            },
        )
        return rule

    # The shipped schema classifies durable preferences under ``profile_update``.
    result = manager.execute("profile_update", source, mutate)
    return _public_rule(result.value)


def record_learning_event(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    rule_type: RuleType,
    subject: str,
    evidence: Mapping[str, Any],
    outcome: Mapping[str, Any],
    settings: Settings | None = None,
    source_text: str = "incidental learning",
) -> LearningResult:
    """Record one incidental observation and promote only sufficient consensus."""

    normalized_type = _rule_type(rule_type)
    normalized_subject = _subject(subject)
    _reject_reserved_structured_subject(normalized_subject)
    normalized_evidence = _json_object(evidence, "evidence")
    raw_outcome = _json_object(outcome, "outcome")
    # Older incidental observations used {"grams": ...}.  Keep their
    # auditable history intact; newly persisted and explicit portion rules use
    # the canonical amount/unit outcome enforced below.
    normalized_outcome = (
        raw_outcome
        if normalized_type is RuleType.PORTION and set(raw_outcome) == {"grams"}
        else _validated_outcome(normalized_type, raw_outcome, settings)
    )
    source = _source_text(source_text)
    if (
        settings is not None
        and not settings.behavior.learning.enabled
    ):
        existing = _current_learning_rule(
            connection, normalized_type, normalized_subject
        )
        if existing is not None:
            return _public_rule(existing)
        return LearningResult(
            rule_type=normalized_type,
            subject=normalized_subject,
            outcome=normalized_outcome,
            active=False,
        )
    automatic, evidence_count, threshold = _learning_options(settings)

    def mutate(context: MutationContext) -> sqlite3.Row:
        existing = _current_learning_rule(
            connection, normalized_type, normalized_subject
        )
        now = _utc_now()
        if existing is None:
            rule = context.insert(
                "personal_rules",
                {
                    "rule_type": _storage_rule_type(normalized_type),
                    "subject": normalized_subject,
                    "rule_json": _rule_json(normalized_type, normalized_outcome),
                    "confidence": 1.0,
                    "evidence_count": 0,
                    "source": "incidental",
                    "active": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        else:
            rule = existing
        context.insert(
            "learning_events",
            {
                "rule_id": rule["id"],
                "event_type": "observed",
                "evidence_json": _canonical_json(
                    {"evidence": normalized_evidence, "outcome": normalized_outcome}
                ),
                "created_at": now,
            },
        )

        # Explicit choices are authoritative; observations remain auditable but cannot dilute them.
        if rule["source"] == "explicit_user":
            return rule

        best_outcome, total, confidence = _aggregate_observations(connection, rule["id"])
        was_active = bool(rule["active"])
        is_active = automatic and total >= evidence_count and confidence >= threshold
        updated = context.update(
            "personal_rules",
            rule["id"],
            {
                "rule_json": _rule_json(normalized_type, best_outcome),
                "confidence": float(confidence),
                "evidence_count": total,
                "source": "incidental",
                "active": int(is_active),
                "updated_at": now,
            },
        )
        if is_active != was_active:
            context.insert(
                "learning_events",
                {
                    "rule_id": rule["id"],
                    "event_type": "promoted" if is_active else "demoted",
                    "evidence_json": _canonical_json({"outcome": best_outcome}),
                    "created_at": now,
                },
            )
        return updated

    result = manager.execute("profile_update", source, mutate)
    return _public_rule(result.value)


def find_active_rule(
    connection: sqlite3.Connection,
    *,
    rule_type: RuleType,
    subject: str,
) -> LearningResult | None:
    """Return the current active rule for a canonical type-and-subject key."""

    row = _rule_row(connection, _rule_type(rule_type), _subject(subject), active_only=True)
    return _public_rule(row) if row is not None else None


def learned_water_unit_milliliters(
    connection: sqlite3.Connection, *, subject: str
) -> Decimal | None:
    """Return a validated active water-unit multiplier, when one exists."""

    rule = find_active_rule(
        connection,
        rule_type=RuleType.WATER_UNIT,
        subject=subject,
    )
    if rule is None:
        return None
    value = rule.outcome.get("milliliters")
    if isinstance(value, bool):
        raise LearningValidationError(
            "water-unit milliliters must be a positive finite number"
        )
    try:
        milliliters = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise LearningValidationError(
            "water-unit milliliters must be a positive finite number"
        ) from error
    if not milliliters.is_finite() or milliliters <= 0:
        raise LearningValidationError(
            "water-unit milliliters must be a positive finite number"
        )
    return milliliters


def portion_subject(normalized_name: str, expression: str) -> str:
    """Build the canonical durable key for a food's spoken portion."""

    return f"{_subject(normalized_name)}|{_subject(expression)}"


def learned_portion(
    connection: sqlite3.Connection, normalized_name: str, expression: str
) -> PortionRule | None:
    """Return an active, validated portion rule for the given food and expression."""

    rule = find_active_rule(
        connection,
        rule_type=RuleType.PORTION,
        subject=portion_subject(normalized_name, expression),
    )
    if rule is None:
        return None
    try:
        outcome = _validated_outcome(RuleType.PORTION, rule.outcome, None)
    except LearningValidationError:
        # A pre-v0.5.0 row may use the old {"grams": ...} representation.
        # It must never override a canonical preference or break meal intake.
        return None
    return PortionRule(amount=Decimal(str(outcome["amount"])), unit=str(outcome["unit"]))


def learned_food_alias(
    connection: sqlite3.Connection, requested_name: str
) -> str | None:
    """Return the active canonical food name selected by the user, if any."""

    rule = find_active_rule(
        connection, rule_type=RuleType.FOOD_ALIAS, subject=requested_name
    )
    if rule is None:
        return None
    try:
        outcome = _validated_outcome(RuleType.FOOD_ALIAS, rule.outcome, None)
    except LearningValidationError:
        # Legacy malformed aliases must not make otherwise safe inventory
        # resolution fail, nor take precedence over its normal fallbacks.
        return None
    return str(outcome["canonical"])


def forget_rule(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    rule_type: RuleType,
    subject: str,
    source_text: str,
) -> LearningResult:
    """Deactivate a rule through the journal so the choice can be undone safely."""

    normalized_type = _rule_type(rule_type)
    normalized_subject = _subject(subject)
    source = _source_text(source_text)

    def mutate(context: MutationContext) -> sqlite3.Row:
        row = _rule_row(connection, normalized_type, normalized_subject, active_only=True)
        if row is None:
            raise KeyError("No active personal rule matches the supplied selector")
        now = _utc_now()
        updated = context.update(
            "personal_rules", row["id"], {"active": 0, "updated_at": now}
        )
        context.insert(
            "learning_events",
            {
                "rule_id": row["id"],
                "event_type": "rejected",
                "evidence_json": _canonical_json(
                    {"reason": "forgotten", "outcome": _public_rule(row).outcome}
                ),
                "created_at": now,
            },
        )
        return updated

    result = manager.execute("profile_update", source, mutate)
    return _public_rule(result.value)


def list_rules(
    connection: sqlite3.Connection, *, include_inactive: bool = False
) -> tuple[LearningResult, ...]:
    """List active rules, or all stored rules when explicitly requested."""

    rows = connection.execute(
        "SELECT * FROM personal_rules "
        + ("" if include_inactive else "WHERE active = 1 ")
        + "ORDER BY rule_type, subject, id"
    ).fetchall()
    return tuple(_public_rule(row) for row in rows)


def _aggregate_observations(
    connection: sqlite3.Connection, rule_id: int
) -> tuple[Mapping[str, Any], int, Decimal]:
    rows = connection.execute(
        """
        SELECT evidence_json
        FROM learning_events
        WHERE rule_id = ?
          AND event_type = 'observed'
          AND id > COALESCE(
              (
                  SELECT MAX(id)
                  FROM learning_events
                  WHERE rule_id = ? AND event_type = 'rejected'
              ),
              0
          )
        ORDER BY id
        """,
        (rule_id, rule_id),
    ).fetchall()
    outcomes: dict[str, tuple[Mapping[str, Any], int]] = {}
    for row in rows:
        payload = _stored_json_object(row["evidence_json"])
        outcome = _json_object(payload.get("outcome"), "stored outcome")
        key = _canonical_json(outcome)
        prior = outcomes.get(key)
        outcomes[key] = (outcome, (prior[1] if prior else 0) + 1)
    if not outcomes:
        raise LearningValidationError("A learning rule has no observations")
    best_key, (best_outcome, winning_count) = min(
        outcomes.items(), key=lambda item: (-item[1][1], item[0])
    )
    del best_key
    total = len(rows)
    return best_outcome, total, Decimal(winning_count) / Decimal(total)


def _latest_rejection_id(
    connection: sqlite3.Connection, rule_id: int
) -> int | None:
    row = connection.execute(
        """
        SELECT MAX(id)
        FROM learning_events
        WHERE rule_id = ? AND event_type = 'rejected'
        """,
        (rule_id,),
    ).fetchone()
    return row[0] if row is not None else None


def _rule_row(
    connection: sqlite3.Connection,
    rule_type: RuleType,
    subject: str,
    *,
    active_only: bool = False,
) -> sqlite3.Row | None:
    clauses = ["rule_type = ?", "subject = ?"]
    values: list[object] = [_storage_rule_type(rule_type), subject]
    if active_only:
        clauses.append("active = 1")
    rows = connection.execute(
        f"SELECT * FROM personal_rules WHERE {' AND '.join(clauses)} ORDER BY id DESC",
        values,
    ).fetchall()
    matching = [row for row in rows if _stored_rule_type(row) == rule_type]
    if len(matching) > 1:
        raise LearningValidationError("Multiple personal rules share the same canonical key")
    return matching[0] if matching else None


def _current_learning_rule(
    connection: sqlite3.Connection,
    rule_type: RuleType,
    subject: str,
) -> sqlite3.Row | None:
    rows = connection.execute(
        """
        SELECT *
        FROM personal_rules
        WHERE rule_type = ? AND subject = ?
        ORDER BY id DESC
        """,
        (_storage_rule_type(rule_type), subject),
    ).fetchall()
    row = next((item for item in rows if _stored_rule_type(item) == rule_type), None)
    if row is None:
        return None
    if bool(row["active"]) or _latest_rejection_id(connection, row["id"]) is None:
        return row
    return None


def _public_rule(row: sqlite3.Row) -> LearningResult:
    payload = _stored_json_object(row["rule_json"])
    rule_type = _rule_type(payload.get("rule_type"))
    outcome = _json_object(payload.get("outcome"), "stored outcome")
    return LearningResult(
        rule_type=rule_type,
        subject=_subject(row["subject"]),
        outcome=outcome,
        active=bool(row["active"]),
    )


def _stored_rule_type(row: sqlite3.Row) -> RuleType:
    return _rule_type(_stored_json_object(row["rule_json"]).get("rule_type"))


def _rule_json(rule_type: RuleType, outcome: Mapping[str, Any]) -> str:
    return _canonical_json({"outcome": outcome, "rule_type": rule_type.value})


def _storage_rule_type(rule_type: RuleType) -> str:
    return _SCHEMA_RULE_TYPES[rule_type]


def _learning_options(settings: Settings | None) -> tuple[bool, int, Decimal]:
    if settings is None:
        return True, _DEFAULT_EVIDENCE_COUNT, _DEFAULT_PROMOTION_CONFIDENCE
    try:
        learning = settings.behavior.learning
        automatic = bool(learning.enabled and learning.allow_automatic_promotion)
        count = learning.promotion_evidence_count
    except AttributeError as error:
        raise LearningValidationError("settings must provide behavior.learning") from error
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise LearningValidationError("promotion evidence count must be a positive integer")
    configured_threshold = getattr(
        learning,
        "promotion_min_confidence",
        getattr(learning, "minimum_confidence", _DEFAULT_PROMOTION_CONFIDENCE),
    )
    return automatic, count, _confidence(configured_threshold)


def _validated_outcome(
    rule_type: RuleType,
    outcome: Mapping[str, Any],
    settings: Settings | None,
) -> Mapping[str, Any]:
    if rule_type == RuleType.PORTION:
        if set(outcome) != {"amount", "unit"}:
            raise LearningValidationError(
                "portion outcome must contain only amount and unit"
            )
        amount = outcome["amount"]
        if isinstance(amount, bool):
            raise LearningValidationError("portion amount must be a positive finite number")
        try:
            parsed_amount = Decimal(str(amount))
        except (InvalidOperation, ValueError) as error:
            raise LearningValidationError("portion amount must be a positive finite number") from error
        if not parsed_amount.is_finite() or parsed_amount <= 0:
            raise LearningValidationError("portion amount must be a positive finite number")
        if not isinstance(outcome["unit"], str) or not outcome["unit"].strip():
            raise LearningValidationError("portion unit must be non-empty text")
        return outcome
    if rule_type == RuleType.FOOD_ALIAS:
        if set(outcome) != {"canonical"}:
            raise LearningValidationError(
                "food-alias outcome must contain only canonical"
            )
        if not isinstance(outcome["canonical"], str) or not outcome["canonical"].strip():
            raise LearningValidationError("food-alias canonical must be non-empty text")
        return outcome
    if rule_type != RuleType.WATER_UNIT:
        return outcome
    if set(outcome) != {"milliliters"}:
        raise LearningValidationError(
            "water-unit outcome must contain only milliliters"
        )
    value = outcome["milliliters"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LearningValidationError(
            "water-unit milliliters must be a positive finite number"
        )
    try:
        milliliters = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise LearningValidationError(
            "water-unit milliliters must be a positive finite number"
        ) from error
    maximum = _max_water_unit_milliliters(settings)
    if (
        not milliliters.is_finite()
        or milliliters <= 0
        or milliliters > maximum
    ):
        raise LearningValidationError(
            "water-unit milliliters must be positive, finite, and within the configured maximum"
        )
    return outcome


def _max_water_unit_milliliters(settings: Settings | None) -> Decimal:
    if settings is None:
        return Decimal(_DEFAULT_MAX_WATER_UNIT_ML)
    try:
        configured = settings.behavior.water.max_single_entry_ml
    except AttributeError as error:
        raise LearningValidationError("settings must provide behavior.water") from error
    if isinstance(configured, bool) or not isinstance(configured, int) or configured < 1:
        raise LearningValidationError(
            "water single-entry maximum must be a positive integer"
        )
    return Decimal(configured)


def _confidence(value: object) -> Decimal:
    if isinstance(value, bool):
        raise LearningValidationError("promotion confidence must be a number from zero through one")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise LearningValidationError("promotion confidence must be a number from zero through one") from error
    if not number.is_finite() or number < 0 or number > 1:
        raise LearningValidationError("promotion confidence must be a number from zero through one")
    return number


def _rule_type(value: object) -> RuleType:
    try:
        return RuleType(value)
    except (TypeError, ValueError) as error:
        raise LearningValidationError("unsupported personal rule type") from error


def _subject(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningValidationError("subject must be a non-empty string")
    return " ".join(value.split()).casefold()


def _reject_reserved_structured_subject(subject: str) -> None:
    if subject in RESERVED_STRUCTURED_SUBJECTS:
        raise LearningValidationError(
            "Structured diet goals must be updated through system.update_goals"
        )


def _source_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningValidationError("source_text must be a non-empty string")
    return value.strip()


def _json_object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LearningValidationError(f"{label} must be a mapping")
    try:
        encoded = _canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise LearningValidationError(f"{label} must be JSON-compatible") from error
    if not isinstance(decoded, dict):
        raise LearningValidationError(f"{label} must be a mapping")
    return decoded


def _stored_json_object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, str):
        raise LearningValidationError("stored personal rule data is invalid")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise LearningValidationError("stored personal rule data is invalid") from error
    if not isinstance(payload, dict):
        raise LearningValidationError("stored personal rule data is invalid")
    return payload


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
