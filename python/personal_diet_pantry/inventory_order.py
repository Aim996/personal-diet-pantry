"""Validated, deterministic inventory deduction ordering."""

from __future__ import annotations

from collections.abc import Sequence


DEFAULT_DEDUCTION_STRATEGY = (
    "opened_first",
    "earliest_expiry",
    "earliest_added",
)
SUPPORTED_DEDUCTION_STRATEGIES = frozenset(
    {
        "opened_first",
        "earliest_expiry",
        "earliest_added",
        "newest_added",
    }
)
_ORDER_FRAGMENTS = {
    "opened_first": ("CASE WHEN opened_at IS NULL THEN 1 ELSE 0 END",),
    "earliest_expiry": (
        "CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END",
        "expires_at",
    ),
    "earliest_added": ("added_at",),
    "newest_added": ("added_at DESC",),
}


def normalized_deduction_strategy(
    strategy: Sequence[str] | None,
) -> tuple[str, ...]:
    """Return one non-empty strategy containing only unique supported keys."""

    values = DEFAULT_DEDUCTION_STRATEGY if strategy is None else tuple(strategy)
    if (
        not values
        or any(not isinstance(value, str) or not value for value in values)
        or any(value not in SUPPORTED_DEDUCTION_STRATEGIES for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(
            "deduction_strategy must be a non-empty list of unique supported keys"
        )
    return values


def deduction_order_sql(strategy: Sequence[str] | None) -> str:
    """Build safe ORDER BY expressions from validated constant fragments."""

    fragments = [
        fragment
        for key in normalized_deduction_strategy(strategy)
        for fragment in _ORDER_FRAGMENTS[key]
    ]
    fragments.append("id")
    return ", ".join(fragments)
