"""Privacy classification, secret redaction, and portable-output checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


class PrivacyError(ValueError):
    """Raised when data cannot safely cross a public portability boundary."""


P0_PUBLIC = frozenset({"product_version", "contract_version", "schema_version"})
P1_DERIVED = frozenset({"nutrition_status", "record_counts", "quality_summary"})
P2_PERSONAL = frozenset(
    {
        "source_text",
        "status_note",
        "food_name",
        "weight_g",
        "occurred_at",
    }
)
P3_FORBIDDEN_KEYS = frozenset(
    {
        "id",
        "transaction_id",
        "source_session_key",
        "source_session_hash",
        "source_model",
        "test_run_id",
        "preview_token",
        "token",
        "absolute_path",
        "api_key",
        "authorization",
        "password",
        "secret",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|password|secret)\s*[:=]\s*\S{8,}"
    ),
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_REDACTED = "[redacted]"


def scrub_export_value(value: Any) -> tuple[Any, int]:
    """Redact P3-looking values while preserving ordinary personal facts."""

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        redactions = 0
        for raw_key, child in value.items():
            key = str(raw_key)
            compact = key.lower().replace("-", "_")
            if (
                compact in P3_FORBIDDEN_KEYS
                or compact.endswith("_id")
                or compact.endswith("_token")
            ):
                redactions += 1
                continue
            scrubbed, count = scrub_export_value(child)
            output[key] = scrubbed
            redactions += count
        return output, redactions
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        output_list: list[Any] = []
        redactions = 0
        for child in value:
            scrubbed, count = scrub_export_value(child)
            output_list.append(scrubbed)
            redactions += count
        return output_list, redactions
    if isinstance(value, str):
        scrubbed = value
        count = 0
        for pattern in _SECRET_PATTERNS:
            scrubbed, replacements = pattern.subn(_REDACTED, scrubbed)
            count += replacements
        return scrubbed, count
    return value, 0


def assert_portable_payload(value: Any) -> None:
    """Reject private keys, secret-like strings, and excessive nesting."""

    _assert_portable(value, depth=0)


def _assert_portable(value: Any, *, depth: int) -> None:
    if depth > 16:
        raise PrivacyError("portable data nesting exceeds the safe limit")
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            compact = key.lower().replace("-", "_")
            if (
                compact in P3_FORBIDDEN_KEYS
                or compact.endswith("_id")
                or compact.endswith("_token")
            ):
                raise PrivacyError("portable data contains a private field")
            _assert_portable(child, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for child in value:
            _assert_portable(child, depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > 10_000:
            raise PrivacyError("portable string exceeds the safe limit")
        if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
            raise PrivacyError("portable data contains secret-like content")


def csv_safe(value: str) -> str:
    """Prevent spreadsheet formula execution when a CSV cell is opened."""

    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
