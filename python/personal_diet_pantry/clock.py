"""Internal UTC clock dependency for transactional and safety workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone


Clock = Callable[[], datetime]


def system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


_ACTIVE_CLOCK: ContextVar[Clock] = ContextVar(
    "personal_diet_pantry_internal_clock",
    default=system_utc_now,
)


def utc_now() -> datetime:
    value = _ACTIVE_CLOCK.get()()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Internal clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def utc_text() -> str:
    return (
        utc_now()
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@contextmanager
def use_clock(clock: Clock) -> Iterator[None]:
    token = _ACTIVE_CLOCK.set(clock)
    try:
        yield
    finally:
        _ACTIVE_CLOCK.reset(token)
