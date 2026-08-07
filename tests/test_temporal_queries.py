from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from personal_diet_pantry.service import DietService
from personal_diet_pantry.policies import PolicyEntry, PolicyRegistry
from personal_diet_pantry.temporal import (
    TemporalValidationError,
    resolve_query_window,
)
from personal_diet_pantry.timezones import utc_text

from tests.contracts.helpers import complete_meal_payload, recorded_meal


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


@pytest.fixture
def temporal_service(tmp_path: Path):
    clock = MutableClock(datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc))
    instance = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
        _clock=clock,
    )
    try:
        yield instance, clock
    finally:
        instance.close()


@pytest.mark.parametrize(
    ("descriptor", "start", "end", "complete"),
    [
        (
            {
                "calendar_window": {
                    "unit": "day",
                    "offset": -1,
                    "segment": "night",
                }
            },
            "2026-08-03T10:00:00Z",
            "2026-08-03T18:00:00Z",
            True,
        ),
        (
            {"rolling_window": {"value": 3, "unit": "hour"}},
            "2026-08-03T16:00:00Z",
            "2026-08-03T19:00:00Z",
            True,
        ),
    ],
)
def test_resolve_query_window_uses_profile_timezone_and_trusted_now(
    temporal_service,
    descriptor: dict[str, object],
    start: str,
    end: str,
    complete: bool,
) -> None:
    service, clock = temporal_service

    resolved = resolve_query_window(
        descriptor,
        now=clock.current,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == start
    assert utc_text(resolved.end_utc) == end
    assert resolved.complete is complete


@pytest.mark.parametrize(
    "expression",
    (
        "我睡醒有点断片，昨晚到今天凌晨都吃了啥来着？",
        "昨儿夜里到天亮前，我有吃过啥吗？",
    ),
)
def test_equivalent_natural_overnight_phrases_use_one_policy_window(
    temporal_service,
    expression: str,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        {"natural_window": {"text": expression}},
        now=datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T10:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T22:00:00Z"
    assert resolved.segment == "overnight"


def test_explicit_calendar_overnight_phrase_uses_the_same_policy_window(
    temporal_service,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        {"natural_window": {"text": "8月3号晚上到4号天亮前"}},
        now=datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T10:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T22:00:00Z"
    assert resolved.segment == "overnight"
    assert resolved.complete is True


@pytest.mark.parametrize(
    "expression",
    (
        "8月3号傍晚6点到4号早上6点，我有吃过啥吗？",
        "8月3号傍晚六点到4号早上六点，我有吃过啥吗？",
        "8月3号18点到4号6点，我有吃过啥吗？",
    ),
)
def test_explicit_calendar_endpoint_times_override_segment_defaults(
    temporal_service,
    expression: str,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        {"natural_window": {"text": expression}},
        now=datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert resolved.start_local.isoformat() == "2026-08-03T18:00:00+08:00"
    assert resolved.end_local.isoformat() == "2026-08-04T06:00:00+08:00"
    assert utc_text(resolved.start_utc) == "2026-08-03T10:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T22:00:00Z"
    assert resolved.complete is True


@pytest.mark.parametrize(
    ("expression", "now", "start_local", "end_local"),
    (
        (
            "8月31号晚上11点到1号凌晨1点",
            datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
            "2026-08-31T23:00:00+08:00",
            "2026-09-01T01:00:00+08:00",
        ),
        (
            "2026年12月31号晚上11点到2027年1月1号凌晨1点",
            datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc),
            "2026-12-31T23:00:00+08:00",
            "2027-01-01T01:00:00+08:00",
        ),
    ),
)
def test_explicit_calendar_endpoint_times_preserve_calendar_rollover(
    temporal_service,
    expression: str,
    now: datetime,
    start_local: str,
    end_local: str,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        {"natural_window": {"text": expression}},
        now=now,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert resolved.start_local.isoformat() == start_local
    assert resolved.end_local.isoformat() == end_local


@pytest.mark.parametrize(
    ("expression", "now", "start", "end"),
    (
        (
            "8月31号晚上到1号天亮前",
            datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
            "2026-08-31T10:00:00Z",
            "2026-08-31T22:00:00Z",
        ),
        (
            "2026年12月31号晚上到2027年1月1号天亮前",
            datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc),
            "2026-12-31T10:00:00Z",
            "2026-12-31T22:00:00Z",
        ),
    ),
)
def test_explicit_calendar_overnight_phrase_generalizes_across_boundaries(
    temporal_service,
    expression: str,
    now: datetime,
    start: str,
    end: str,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        {"natural_window": {"text": expression}},
        now=now,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == start
    assert utc_text(resolved.end_utc) == end
    assert resolved.segment == "overnight"


@pytest.mark.parametrize(
    ("expression", "start", "end", "unit"),
    (
        ("昨天吃了什么", "2026-08-02T16:00:00Z", "2026-08-03T16:00:00Z", "day"),
        ("上周吃得怎么样", "2026-07-26T16:00:00Z", "2026-08-02T16:00:00Z", "week"),
        ("上个月饮食情况", "2026-06-30T16:00:00Z", "2026-07-31T16:00:00Z", "month"),
    ),
)
def test_natural_calendar_phrases_are_policy_driven_not_night_only(
    temporal_service,
    expression: str,
    start: str,
    end: str,
    unit: str,
) -> None:
    service, _ = temporal_service
    resolved = resolve_query_window(
        {"natural_window": {"text": expression}},
        now=datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == start
    assert utc_text(resolved.end_utc) == end
    assert resolved.unit == unit


def test_current_calendar_window_is_capped_at_trusted_now(
    temporal_service,
) -> None:
    service, clock = temporal_service
    clock.current = datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc)

    resolved = resolve_query_window(
        {"calendar_window": {"unit": "day", "offset": 0}},
        now=clock.current,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T16:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T17:00:00Z"
    assert resolved.complete is False


def test_legacy_explicit_date_keeps_its_full_calendar_bounds(
    temporal_service,
) -> None:
    service, clock = temporal_service
    clock.current = datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc)

    resolved = resolve_query_window(
        {"occurred_on": "2026-08-04"},
        now=clock.current,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T16:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-04T16:00:00Z"
    assert resolved.complete is False


def test_temporal_modes_are_mutually_exclusive_and_policy_keys_are_validated(
    temporal_service,
) -> None:
    service, clock = temporal_service

    with pytest.raises(TemporalValidationError, match="exactly one"):
        resolve_query_window(
            {
                "occurred_on": "2026-08-03",
                "rolling_window": {"value": 3, "unit": "hour"},
            },
            now=clock.current,
            timezone_name="Asia/Shanghai",
            policies=service.policies,
        )


def test_new_registered_segment_uses_existing_operator_without_new_branch(
    temporal_service,
) -> None:
    service, clock = temporal_service
    registries = dict(service.policies.registries)
    registries["temporal-scopes"] = registries["temporal-scopes"] + (
        PolicyEntry(
            policy_key="segment.post_workout",
            operator="local_segment",
            values=MappingProxyType(
                {"start": "15:00", "end": "17:00", "cross_day": False}
            ),
            source="test_registration",
            version=1,
        ),
    )
    extended = PolicyRegistry(MappingProxyType(registries))

    resolved = resolve_query_window(
        {
            "calendar_window": {
                "unit": "day",
                "offset": -1,
                "segment": "post_workout",
            }
        },
        now=clock.current,
        timezone_name="Asia/Shanghai",
        policies=extended,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T07:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T09:00:00Z"

    with pytest.raises(TemporalValidationError, match="unknown"):
        resolve_query_window(
            {"calendar_window": {"unit": "day", "offset": 0, "segment": "post_workout"}},
            now=clock.current,
            timezone_name="Asia/Shanghai",
            policies=service.policies,
        )


def test_explicit_local_range_is_interpreted_in_profile_timezone(
    temporal_service,
) -> None:
    service, clock = temporal_service

    resolved = resolve_query_window(
        {
            "local_range": {
                "start": "2026-08-03T22:00:00",
                "end": "2026-08-04T02:00:00",
            }
        },
        now=clock.current,
        timezone_name="Asia/Shanghai",
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == "2026-08-03T14:00:00Z"
    assert utc_text(resolved.end_utc) == "2026-08-03T18:00:00Z"


@pytest.mark.parametrize(
    ("timezone_name", "now", "descriptor", "start", "end"),
    [
        (
            "Asia/Shanghai",
            datetime(2026, 8, 4, 3, 0, tzinfo=timezone.utc),
            {"calendar_window": {"unit": "week", "offset": -1}},
            "2026-07-26T16:00:00Z",
            "2026-08-02T16:00:00Z",
        ),
        (
            "Asia/Shanghai",
            datetime(2026, 8, 4, 3, 0, tzinfo=timezone.utc),
            {"calendar_window": {"unit": "month", "offset": -1}},
            "2026-06-30T16:00:00Z",
            "2026-07-31T16:00:00Z",
        ),
        (
            "America/New_York",
            datetime(2026, 3, 9, 5, 0, tzinfo=timezone.utc),
            {"calendar_window": {"unit": "day", "offset": -1}},
            "2026-03-08T05:00:00Z",
            "2026-03-09T04:00:00Z",
        ),
    ],
)
def test_calendar_units_use_local_calendar_and_dst_boundaries(
    temporal_service,
    timezone_name: str,
    now: datetime,
    descriptor: dict[str, object],
    start: str,
    end: str,
) -> None:
    service, _ = temporal_service

    resolved = resolve_query_window(
        descriptor,
        now=now,
        timezone_name=timezone_name,
        policies=service.policies,
    )

    assert resolved is not None
    assert utc_text(resolved.start_utc) == start
    assert utc_text(resolved.end_utc) == end


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (
            "2026-03-08T02:30:00",
            "2026-03-08T04:00:00",
            "does not exist",
        ),
        (
            "2026-11-01T01:30:00",
            "2026-11-01T03:00:00",
            "ambiguous",
        ),
    ],
)
def test_local_range_rejects_dst_gaps_and_ambiguous_wall_times(
    temporal_service,
    start: str,
    end: str,
    message: str,
) -> None:
    service, _ = temporal_service

    with pytest.raises(ValueError, match=message):
        resolve_query_window(
            {"local_range": {"start": start, "end": end}},
            now=datetime(2026, 12, 1, tzinfo=timezone.utc),
            timezone_name="America/New_York",
            policies=service.policies,
        )


def test_same_cross_day_scope_returns_meals_water_and_weights(
    temporal_service,
) -> None:
    service, clock = temporal_service
    dinner = deepcopy(complete_meal_payload())
    dinner.update(
        occurred_at="2026-08-03T22:37:00+08:00",
        meal_type="dinner",
        source_text="晚餐",
    )
    snack = deepcopy(complete_meal_payload())
    snack.update(
        occurred_at="2026-08-04T00:15:00+08:00",
        meal_type="snack",
        source_text="蛋白粉",
    )
    recorded_meal(service, payload=dinner)
    recorded_meal(service, payload=snack)

    water_result = service.dispatch(
        {
            "domain": "water",
            "action": "record",
            "payload": {
                "amount": 350,
                "unit": "ml",
                "occurred_at": "2026-08-04T01:00:00+08:00",
                "source_text": "夜间饮水",
            },
        }
    )
    assert water_result["ok"] is True

    clock.current = datetime(2026, 8, 3, 17, 25, tzinfo=timezone.utc)
    weight_result = service.dispatch(
        {
            "domain": "weight",
            "action": "record",
            "payload": {"weight": 80, "unit": "kg", "status_note": "睡前"},
        }
    )
    assert weight_result["ok"] is True
    clock.current = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)

    descriptor = {
        "calendar_window": {
            "unit": "day",
            "offset": -1,
            "segment": "night",
        }
    }
    responses = {
        domain: service.dispatch(
            {"domain": domain, "action": "query", "payload": descriptor}
        )
        for domain in ("meal", "water", "weight")
    }

    assert all(result["ok"] is True for result in responses.values())
    scopes = [result["data"]["scope"] for result in responses.values()]
    assert [scope["start_utc"] for scope in scopes] == [
        "2026-08-03T10:00:00Z"
    ] * 3
    assert [scope["end_utc"] for scope in scopes] == [
        "2026-08-03T18:00:00Z"
    ] * 3
    assert len(responses["meal"]["data"]["meals"]) == 2
    assert responses["water"]["data"]["summary"]["total_ml"] == 350
    assert len(responses["weight"]["data"]["summary"]["records"]) == 1


def test_natural_overnight_queries_return_the_same_meal_set_as_explicit_range(
    temporal_service,
) -> None:
    service, clock = temporal_service
    for occurred_at, source_text, meal_type in (
        ("2026-08-03T20:20:00+08:00", "晚餐一", "dinner"),
        ("2026-08-03T22:37:00+08:00", "蛋白粉", "snack"),
        ("2026-08-04T01:25:00+08:00", "凌晨加餐", "snack"),
        ("2026-08-04T05:59:00+08:00", "天亮前加餐", "snack"),
        ("2026-08-04T06:00:00+08:00", "边界外早餐", "breakfast"),
    ):
        payload = deepcopy(complete_meal_payload())
        payload.update(
            occurred_at=occurred_at,
            source_text=source_text,
            meal_type=meal_type,
        )
        recorded_meal(service, payload=payload)
    clock.current = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)

    scopes = (
        {"natural_window": {"text": "昨晚到今天凌晨都吃了啥"}},
        {"natural_window": {"text": "昨儿夜里到天亮前吃过啥"}},
        {"natural_window": {"text": "8月3号晚上到4号天亮前"}},
        {
            "local_range": {
                "start": "2026-08-03T18:00:00",
                "end": "2026-08-04T06:00:00",
            }
        },
    )
    result_sets = []
    for scope in scopes:
        result = service.dispatch(
            {"domain": "meal", "action": "query", "payload": scope}
        )
        assert result["ok"] is True
        result_sets.append(
            [meal["source_text"] for meal in result["data"]["meals"]]
        )

    assert result_sets[0] == result_sets[1] == result_sets[2] == result_sets[3]
    assert result_sets[0] == ["晚餐一", "蛋白粉", "凌晨加餐", "天亮前加餐"]
