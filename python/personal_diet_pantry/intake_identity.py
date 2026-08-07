"""Canonical identity for one completed meal event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata


@dataclass(frozen=True)
class IntakeIdentityItem:
    """Stable facts that distinguish one consumed item."""

    normalized_name: str
    amount: Decimal | None = None
    unit: str | None = None
    consumed_weight_g: Decimal | None = None
    consumed_volume_ml: Decimal | None = None
    consumed_servings: Decimal | None = None
    item_role: str = "food"
    parent_name: str | None = None


@dataclass(frozen=True)
class IntakeIdentity:
    """Business identity for a single-user local intake event."""

    occurred_at: str
    meal_type: str
    location_type: str
    items: tuple[IntakeIdentityItem, ...]
    source_text: str = ""


def intake_event_fingerprint(identity: IntakeIdentity) -> str:
    """Return the same fingerprint for semantically equivalent retries."""

    if not isinstance(identity, IntakeIdentity):
        raise TypeError("identity must be IntakeIdentity")
    payload = {
        "scope": "single_user_local",
        "occurred_at_minute": _canonical_minute(identity.occurred_at),
        "meal_type": _canonical_text(identity.meal_type),
        "location_type": _canonical_text(identity.location_type),
        "items": sorted(
            (_item_payload(item) for item in identity.items),
            key=lambda value: json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _item_payload(item: IntakeIdentityItem) -> dict[str, str | None]:
    if not isinstance(item, IntakeIdentityItem):
        raise TypeError("items must contain IntakeIdentityItem values")
    return {
        "normalized_name": _canonical_text(item.normalized_name),
        "amount": _canonical_decimal(item.amount),
        "unit": _canonical_optional_text(item.unit),
        "consumed_weight_g": _canonical_decimal(item.consumed_weight_g),
        "consumed_volume_ml": _canonical_decimal(item.consumed_volume_ml),
        "consumed_servings": _canonical_decimal(item.consumed_servings),
        "item_role": _canonical_text(item.item_role),
        "parent_name": _canonical_optional_text(item.parent_name),
    }


def _canonical_minute(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("occurred_at must be an ISO-8601 timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("occurred_at must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    minute = parsed.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return minute.isoformat().replace("+00:00", "Z")


def _canonical_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("identity text fields must be strings")
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _canonical_optional_text(value: str | None) -> str | None:
    return None if value is None else _canonical_text(value)


def _canonical_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("identity quantities must be finite Decimal values")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
