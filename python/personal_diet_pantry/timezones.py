"""IANA timezone helpers for user-facing calendar behavior."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ConfigurationError


class TimezoneConfigurationError(ConfigurationError):
    """A profile timezone cannot safely define local calendar behavior."""


def resolve_timezone(name: str) -> tzinfo:
    """Resolve one configured IANA timezone or raise a stable config error."""

    if not isinstance(name, str) or not name.strip():
        raise TimezoneConfigurationError(
            "profile timezone must be an IANA timezone name"
        )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise TimezoneConfigurationError(
            "profile timezone is unavailable; install the tzdata package"
        ) from error


def parse_utc_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid ISO timestamp: {value!r}") from error
    else:
        raise TypeError("timestamp must be an ISO string or datetime")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def local_datetime(value: datetime | str, timezone_name: str) -> datetime:
    return parse_utc_timestamp(value).astimezone(resolve_timezone(timezone_name))


def local_date(value: datetime | str, timezone_name: str) -> date:
    return local_datetime(value, timezone_name).date()


def local_expiry_end(value: date, timezone_name: str) -> datetime:
    """Return the final local second of a user-supplied calendar day."""

    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("expiry_date must be a date")
    zone = resolve_timezone(timezone_name)
    next_day = datetime.combine(
        value + timedelta(days=1),
        time.min,
        tzinfo=zone,
    )
    return next_day - timedelta(seconds=1)


def local_calendar_date(value: datetime | str, timezone_name: str) -> str:
    """Render a timestamp as its calendar date in the configured timezone."""

    return local_date(value, timezone_name).isoformat()


def local_day_utc_bounds(day: date, timezone_name: str) -> tuple[str, str]:
    if isinstance(day, datetime) or not isinstance(day, date):
        raise TypeError("day must be a date")
    zone = resolve_timezone(timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(
        date.fromordinal(day.toordinal() + 1), time.min, tzinfo=zone
    ).astimezone(timezone.utc)
    return utc_text(start), utc_text(end)


def localize_datetime(value: datetime, timezone_name: str) -> datetime:
    """Attach an IANA zone while rejecting DST gaps and ambiguous wall times."""

    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("local date-time must not include a timezone")
    zone = resolve_timezone(timezone_name)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        round_trip = (
            candidate.astimezone(timezone.utc)
            .astimezone(zone)
            .replace(tzinfo=None)
        )
        if round_trip == value and all(
            existing.utcoffset() != candidate.utcoffset()
            for existing in candidates
        ):
            candidates.append(candidate)
    if not candidates:
        raise ValueError("local date-time does not exist in the configured timezone")
    if len(candidates) > 1:
        raise ValueError("local date-time is ambiguous in the configured timezone")
    return candidates[0]


def utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
