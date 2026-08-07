"""Policy-backed temporal descriptors shared by all query domains."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .models import ConfigurationError
from .policies import PolicyEntry, PolicyRegistry
from .timezones import localize_datetime, resolve_timezone, utc_text


_WINDOW_FIELDS = (
    "occurred_on",
    "calendar_window",
    "rolling_window",
    "local_range",
    "natural_window",
)
_MAX_CALENDAR_OFFSET = 10_000
_MAX_ROLLING_VALUE = Decimal("10000")


class TemporalValidationError(ValueError):
    """A temporal query descriptor cannot define one safe UTC window."""


@dataclass(frozen=True)
class ResolvedWindow:
    """One resolved, half-open time window with UTC and local projections."""

    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    timezone_name: str
    window_type: str
    unit: str | None
    segment: str | None
    complete: bool

    def public_scope(self) -> dict[str, object]:
        result: dict[str, object] = {
            "window_type": self.window_type,
            "start_utc": utc_text(self.start_utc),
            "end_utc": utc_text(self.end_utc),
            "start_local": self.start_local.isoformat(),
            "end_local": self.end_local.isoformat(),
            "timezone": self.timezone_name,
            "complete": self.complete,
        }
        if self.unit is not None:
            result["unit"] = self.unit
        if self.segment is not None:
            result["segment"] = self.segment
        return result


def resolve_query_window(
    payload: Mapping[str, object],
    *,
    now: datetime,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ResolvedWindow | None:
    """Resolve exactly one optional descriptor in the profile timezone."""

    if not isinstance(payload, Mapping):
        raise TemporalValidationError("query payload must be an object")
    now_utc = _aware_utc(now, "now")
    zone = resolve_timezone(timezone_name)
    now_local = now_utc.astimezone(zone)
    selected = [field for field in _WINDOW_FIELDS if payload.get(field) is not None]
    if not selected:
        return None
    if len(selected) != 1:
        raise TemporalValidationError(
            "exactly one temporal query mode may be supplied"
        )

    field = selected[0]
    if field == "occurred_on":
        day = _calendar_date(payload[field], "occurred_on")
        start_local = localize_datetime(
            datetime.combine(day, time.min), timezone_name
        )
        end_local = localize_datetime(
            datetime.combine(day + timedelta(days=1), time.min),
            timezone_name,
        )
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        return ResolvedWindow(
            start_utc=start_utc,
            end_utc=end_utc,
            start_local=start_local,
            end_local=end_local,
            timezone_name=timezone_name,
            window_type="occurred_on",
            unit="day",
            segment=None,
            complete=end_utc <= now_utc,
        )
    if field == "calendar_window":
        return _calendar_window(
            _mapping(payload[field], field),
            now_utc=now_utc,
            now_local=now_local,
            timezone_name=timezone_name,
            policies=policies,
        )
    if field == "rolling_window":
        return _rolling_window(
            _mapping(payload[field], field),
            now_utc=now_utc,
            timezone_name=timezone_name,
            policies=policies,
        )
    if field == "local_range":
        return _local_range(
            _mapping(payload[field], field),
            now_utc=now_utc,
            timezone_name=timezone_name,
        )
    return _natural_window(
        _mapping(payload[field], field),
        now_utc=now_utc,
        now_local=now_local,
        timezone_name=timezone_name,
        policies=policies,
    )


def _natural_window(
    descriptor: Mapping[str, object],
    *,
    now_utc: datetime,
    now_local: datetime,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ResolvedWindow:
    """Resolve a verbatim natural calendar phrase through declarative aliases."""

    _exact_fields(descriptor, required=("text",), optional=())
    raw_text = descriptor.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip() or len(raw_text) > 500:
        raise TemporalValidationError(
            "natural_window.text must be non-empty text up to 500 characters"
        )
    text = "".join(raw_text.casefold().split())
    anchors: list[tuple[int, int, str, int]] = []
    segments: list[tuple[int, int, str]] = []
    for entry in policies.entries("temporal-scopes"):
        aliases = _policy_aliases(entry)
        if entry.operator == "calendar_anchor":
            unit = entry.values.get("unit")
            offset = entry.values.get("offset")
            if unit not in {"day", "week", "month"} or isinstance(offset, bool) or not isinstance(offset, int):
                raise TemporalValidationError(
                    f"invalid calendar anchor policy: {entry.policy_key}"
                )
            for alias in aliases:
                position = text.find(alias.casefold())
                if position >= 0:
                    anchors.append((position, -len(alias), unit, offset))
        elif entry.operator == "local_segment":
            segment = entry.policy_key.rsplit(".", 1)[-1]
            for alias in aliases:
                position = text.find(alias.casefold())
                if position >= 0:
                    segments.append((-len(alias), position, segment))
    if not anchors:
        explicit = _explicit_calendar_segment_window(
            text,
            segment=min(segments)[2] if segments else None,
            now_utc=now_utc,
            now_local=now_local,
            timezone_name=timezone_name,
            policies=policies,
        )
        if explicit is not None:
            return explicit
        raise TemporalValidationError(
            "natural time expression needs a registered calendar anchor"
        )
    _, _, unit, offset = min(anchors)
    segment = min(segments)[2] if segments else None
    if segment is not None and unit != "day":
        raise TemporalValidationError(
            "natural time segments require a day calendar anchor"
        )
    resolved = _calendar_window(
        {
            "unit": unit,
            "offset": offset,
            **({"segment": segment} if segment is not None else {}),
        },
        now_utc=now_utc,
        now_local=now_local,
        timezone_name=timezone_name,
        policies=policies,
    )
    return replace(resolved, window_type="natural_window")


_EXPLICIT_CALENDAR_DATE = re.compile(
    r"(?:(?P<year>\d{4})年)?(?:(?P<month>\d{1,2})月)?"
    r"(?P<day>\d{1,2})(?:日|号)"
)

_CHINESE_TIME_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_EXPLICIT_LOCAL_TIME = re.compile(
    r"(?P<period>凌晨|清晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里)?"
    r"(?P<hour>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})(?:点|时)"
    r"(?:(?P<half>半)|(?P<minute>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})(?:分)?)?"
)


def _chinese_time_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "十" not in value:
        if len(value) != 1 or value not in _CHINESE_TIME_DIGITS:
            raise TemporalValidationError("natural time expression contains an invalid clock time")
        return _CHINESE_TIME_DIGITS[value]
    if value.count("十") != 1:
        raise TemporalValidationError("natural time expression contains an invalid clock time")
    left, right = value.split("十", 1)
    tens = 1 if left == "" else _CHINESE_TIME_DIGITS.get(left)
    ones = 0 if right == "" else _CHINESE_TIME_DIGITS.get(right)
    if tens is None or ones is None:
        raise TemporalValidationError("natural time expression contains an invalid clock time")
    return tens * 10 + ones


def _explicit_time_after_date(
    text: str,
    date_match: re.Match[str],
    *,
    boundary: int,
) -> time | None:
    """Read one explicit clock time next to one calendar date."""

    tail = text[date_match.end():boundary]
    match = _EXPLICIT_LOCAL_TIME.search(tail)
    if match is None:
        return None
    hour = _chinese_time_number(match.group("hour"))
    if match.group("half") is not None:
        minute = 30
    elif match.group("minute") is not None:
        minute = _chinese_time_number(match.group("minute"))
    else:
        minute = 0
    period = match.group("period")
    if period in {"凌晨", "清晨", "早上", "早晨", "上午"}:
        if hour == 12:
            hour = 0
        elif hour > 12:
            raise TemporalValidationError("natural time expression contains an invalid morning clock time")
    elif period == "中午":
        if 1 <= hour <= 10:
            hour += 12
        elif hour not in {11, 12}:
            raise TemporalValidationError("natural time expression contains an invalid noon clock time")
    elif period in {"下午", "傍晚", "晚上", "夜里"}:
        if 1 <= hour < 12:
            hour += 12
        elif hour != 12:
            raise TemporalValidationError("natural time expression contains an invalid evening clock time")
    elif not 0 <= hour <= 23:
        raise TemporalValidationError("natural time expression contains an invalid clock time")
    if not 0 <= minute <= 59:
        raise TemporalValidationError("natural time expression contains an invalid clock time")
    return time(hour, minute)


def _explicit_calendar_segment_window(
    text: str,
    *,
    segment: str | None,
    now_utc: datetime,
    now_local: datetime,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ResolvedWindow | None:
    """Resolve explicit calendar dates with endpoint clocks or a local segment."""

    matches = list(_EXPLICIT_CALENDAR_DATE.finditer(text))
    if not matches:
        return None
    explicit_start = (
        _explicit_time_after_date(text, matches[0], boundary=matches[1].start())
        if len(matches) >= 2
        else None
    )
    explicit_end = (
        _explicit_time_after_date(
            text,
            matches[1],
            boundary=(matches[2].start() if len(matches) > 2 else len(text)),
        )
        if len(matches) >= 2
        else None
    )
    if segment is None:
        if explicit_start is None or explicit_end is None:
            return None
        start_time = explicit_start
        end_time = explicit_end
        cross_day = False
    else:
        segment_entry = _policy(
            policies, f"segment.{segment}", "local_segment"
        )
        start_time = _policy_time(segment_entry, "start")
        end_time = _policy_time(segment_entry, "end")
        cross_day = segment_entry.values.get("cross_day")
        if not isinstance(cross_day, bool):
            raise TemporalValidationError(
                f"invalid cross_day policy value: {segment_entry.policy_key}"
            )
        if explicit_start is not None and explicit_end is not None:
            start_time = explicit_start
            end_time = explicit_end

    first = matches[0]
    start_year = int(first.group("year") or now_local.year)
    start_month = int(first.group("month") or now_local.month)
    start_day = _safe_date(start_year, start_month, int(first.group("day")))

    if len(matches) == 1:
        end_day = start_day + timedelta(days=1) if cross_day else start_day
    else:
        second = matches[1]
        end_year = int(second.group("year") or start_day.year)
        end_month = int(second.group("month") or start_day.month)
        end_number = int(second.group("day"))
        end_day = _safe_date(end_year, end_month, end_number)
        if end_day < start_day and second.group("year") is None:
            if second.group("month") is None:
                end_year, end_month = _next_month(start_day.year, start_day.month)
                end_day = _safe_date(end_year, end_month, end_number)
            else:
                end_day = _safe_date(start_day.year + 1, end_month, end_number)
        if end_day == start_day and cross_day:
            end_day += timedelta(days=1)

    start_local = localize_datetime(
        datetime.combine(start_day, start_time), timezone_name
    )
    end_local = localize_datetime(
        datetime.combine(end_day, end_time), timezone_name
    )
    return _finish_window(
        start_local,
        end_local,
        now_utc=now_utc,
        timezone_name=timezone_name,
        window_type="natural_window",
        unit="day",
        segment=segment,
    )


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as error:
        raise TemporalValidationError(
            "natural time expression contains an invalid calendar date"
        ) from error


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _policy_aliases(entry: PolicyEntry) -> tuple[str, ...]:
    aliases = entry.values.get("aliases", ())
    if isinstance(aliases, (str, bytes)) or not isinstance(aliases, Sequence):
        raise TemporalValidationError(
            f"invalid aliases policy value: {entry.policy_key}"
        )
    normalized = tuple(alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip())
    if len(normalized) != len(aliases):
        raise TemporalValidationError(
            f"invalid aliases policy value: {entry.policy_key}"
        )
    return normalized


def _calendar_window(
    descriptor: Mapping[str, object],
    *,
    now_utc: datetime,
    now_local: datetime,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ResolvedWindow:
    _exact_fields(descriptor, required=("unit",), optional=("offset", "segment"))
    unit = _identifier(descriptor.get("unit"), "calendar_window.unit")
    entry = _policy(policies, f"calendar.{unit}", "calendar_unit")
    configured_unit = entry.values.get("unit")
    if configured_unit != unit:
        raise TemporalValidationError(
            f"calendar policy unit mismatch: {entry.policy_key}"
        )
    offset = descriptor.get("offset", 0)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not -_MAX_CALENDAR_OFFSET <= offset <= _MAX_CALENDAR_OFFSET
    ):
        raise TemporalValidationError(
            "calendar_window.offset must be an integer from -10000 to 10000"
        )

    start_day, end_day = _calendar_days(
        unit,
        now_local.date(),
        offset,
        week_start=entry.values.get("week_start"),
    )
    segment_value = descriptor.get("segment")
    segment = (
        _identifier(segment_value, "calendar_window.segment")
        if segment_value is not None
        else None
    )
    if segment is None or segment == "full":
        if segment == "full":
            _policy(policies, "segment.full", "local_segment")
        start_local = localize_datetime(
            datetime.combine(start_day, time.min), timezone_name
        )
        end_local = localize_datetime(
            datetime.combine(end_day, time.min), timezone_name
        )
    else:
        if unit != "day":
            raise TemporalValidationError(
                "local time segments currently require calendar unit day"
            )
        segment_entry = _policy(
            policies, f"segment.{segment}", "local_segment"
        )
        start_time = _policy_time(segment_entry, "start")
        end_time = _policy_time(segment_entry, "end")
        cross_day = segment_entry.values.get("cross_day")
        if not isinstance(cross_day, bool):
            raise TemporalValidationError(
                f"invalid cross_day policy value: {segment_entry.policy_key}"
            )
        start_local = localize_datetime(
            datetime.combine(start_day, start_time), timezone_name
        )
        segment_end_day = start_day + timedelta(days=1) if cross_day else start_day
        end_local = localize_datetime(
            datetime.combine(segment_end_day, end_time), timezone_name
        )

    return _finish_window(
        start_local,
        end_local,
        now_utc=now_utc,
        timezone_name=timezone_name,
        window_type="calendar_window",
        unit=unit,
        segment=segment,
    )


def _rolling_window(
    descriptor: Mapping[str, object],
    *,
    now_utc: datetime,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ResolvedWindow:
    _exact_fields(descriptor, required=("value", "unit"), optional=())
    unit = _identifier(descriptor.get("unit"), "rolling_window.unit")
    entry = _policy(policies, f"duration.{unit}", "rolling_duration")
    seconds = entry.values.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        raise TemporalValidationError(
            f"invalid duration policy value: {entry.policy_key}"
        )
    try:
        value = Decimal(str(descriptor.get("value")))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise TemporalValidationError(
            "rolling_window.value must be a positive number"
        ) from error
    if not value.is_finite() or value <= 0 or value > _MAX_ROLLING_VALUE:
        raise TemporalValidationError(
            "rolling_window.value must be greater than 0 and at most 10000"
        )
    duration_seconds = value * Decimal(str(seconds))
    if duration_seconds != duration_seconds.to_integral_value():
        raise TemporalValidationError(
            "rolling window must resolve to a whole number of seconds"
        )
    start_utc = now_utc - timedelta(seconds=int(duration_seconds))
    zone = resolve_timezone(timezone_name)
    return ResolvedWindow(
        start_utc=start_utc,
        end_utc=now_utc,
        start_local=start_utc.astimezone(zone),
        end_local=now_utc.astimezone(zone),
        timezone_name=timezone_name,
        window_type="rolling_window",
        unit=unit,
        segment=None,
        complete=True,
    )


def _local_range(
    descriptor: Mapping[str, object],
    *,
    now_utc: datetime,
    timezone_name: str,
) -> ResolvedWindow:
    _exact_fields(descriptor, required=("start", "end"), optional=())
    start_local = localize_datetime(
        _naive_datetime(descriptor.get("start"), "local_range.start"),
        timezone_name,
    )
    end_local = localize_datetime(
        _naive_datetime(descriptor.get("end"), "local_range.end"),
        timezone_name,
    )
    return _finish_window(
        start_local,
        end_local,
        now_utc=now_utc,
        timezone_name=timezone_name,
        window_type="local_range",
        unit=None,
        segment=None,
    )


def _finish_window(
    start_local: datetime,
    nominal_end_local: datetime,
    *,
    now_utc: datetime,
    timezone_name: str,
    window_type: str,
    unit: str | None,
    segment: str | None,
) -> ResolvedWindow:
    start_utc = start_local.astimezone(timezone.utc)
    nominal_end_utc = nominal_end_local.astimezone(timezone.utc)
    if nominal_end_utc <= start_utc:
        raise TemporalValidationError("temporal window end must be after start")
    if start_utc > now_utc:
        raise TemporalValidationError("temporal window starts in the future")
    complete = nominal_end_utc <= now_utc
    end_utc = nominal_end_utc if complete else now_utc
    if end_utc <= start_utc:
        raise TemporalValidationError("temporal window is empty at trusted now")
    zone = resolve_timezone(timezone_name)
    return ResolvedWindow(
        start_utc=start_utc,
        end_utc=end_utc,
        start_local=start_utc.astimezone(zone),
        end_local=end_utc.astimezone(zone),
        timezone_name=timezone_name,
        window_type=window_type,
        unit=unit,
        segment=segment,
        complete=complete,
    )


def _calendar_days(
    unit: str,
    anchor: date,
    offset: int,
    *,
    week_start: object,
) -> tuple[date, date]:
    if unit == "day":
        start = anchor + timedelta(days=offset)
        return start, start + timedelta(days=1)
    if unit == "week":
        if isinstance(week_start, bool) or not isinstance(week_start, int) or not 1 <= week_start <= 7:
            raise TemporalValidationError("calendar week_start must be from 1 to 7")
        start = anchor - timedelta(days=(anchor.isoweekday() - week_start) % 7)
        start += timedelta(weeks=offset)
        return start, start + timedelta(weeks=1)
    if unit == "month":
        start = _shift_month(date(anchor.year, anchor.month, 1), offset)
        return start, _shift_month(start, 1)
    raise TemporalValidationError(f"unsupported calendar unit: {unit}")


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, month_zero = divmod(month_index, 12)
    if not 1 <= year <= 9999:
        raise TemporalValidationError("calendar month offset is out of range")
    return date(year, month_zero + 1, 1)


def _policy(
    policies: PolicyRegistry,
    policy_key: str,
    operator: str,
) -> PolicyEntry:
    try:
        entry = policies.entry("temporal-scopes", policy_key)
    except ConfigurationError as error:
        raise TemporalValidationError(
            f"unknown temporal policy identifier: {policy_key.rsplit('.', 1)[-1]}"
        ) from error
    if entry.operator != operator:
        raise TemporalValidationError(
            f"temporal policy operator mismatch: {policy_key}"
        )
    return entry


def _policy_time(entry: PolicyEntry, field: str) -> time:
    raw = entry.values.get(field)
    if not isinstance(raw, str):
        raise TemporalValidationError(
            f"invalid {field} policy value: {entry.policy_key}"
        )
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as error:
        raise TemporalValidationError(
            f"invalid {field} policy value: {entry.policy_key}"
        ) from error
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise TemporalValidationError(
            f"invalid {field} policy value: {entry.policy_key}"
        )
    return parsed


def _calendar_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        raise TemporalValidationError(f"{field} must be a calendar date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise TemporalValidationError(
                f"{field} must be an ISO calendar date"
            ) from error
    raise TemporalValidationError(f"{field} must be an ISO calendar date")


def _naive_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TemporalValidationError(
            f"{field} must be a local ISO date-time without timezone"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise TemporalValidationError(
            f"{field} must be a local ISO date-time without timezone"
        ) from error
    if parsed.tzinfo is not None:
        raise TemporalValidationError(
            f"{field} must not include a timezone offset"
        )
    return parsed


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalValidationError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TemporalValidationError(f"{field} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, object],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> None:
    missing = set(required) - set(value)
    if missing:
        raise TemporalValidationError(
            f"temporal descriptor is missing {sorted(missing)[0]}"
        )
    unexpected = set(value) - set(required) - set(optional)
    if unexpected:
        raise TemporalValidationError(
            f"unknown temporal descriptor field: {sorted(unexpected)[0]}"
        )


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TemporalValidationError(f"{field} must be a policy identifier")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 64
        or not normalized[0].isalpha()
        or any(not (character.islower() or character.isdigit() or character in "_-") for character in normalized)
    ):
        raise TemporalValidationError(f"{field} must be a policy identifier")
    return normalized
