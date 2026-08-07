"""Deterministic Markdown reports derived only from formal SQLite rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import ceil
from pathlib import Path
import sqlite3

from .file_io import atomic_write_text
from .derived_file_leases import (
    DerivedFileLeaseManager,
    LeaseOwnerToken,
    manager_for,
)
from .goal_profiles import load_goal_profile
from .models import ConfigurationError, DataPaths, NutritionGoals, Settings
from .pantry import PantryBatch
from .policies import PolicyRegistry
from .progress import aggregate_period
from .report_localization import ReportLocale, resolve_report_locale
from .timezones import local_date, localize_datetime, resolve_timezone, utc_text


_NUTRITION_FIELDS = (
    ("total_calories", "calories", "kcal", "calories_kcal"),
    ("total_protein", "protein", "g", "protein_g"),
    ("total_fat", "fat", "g", "fat_g"),
    (
        "total_carbohydrate",
        "carbohydrate",
        "g",
        "carbohydrate_g",
    ),
    ("total_fiber", "fiber", "g", "fiber_g"),
    ("total_sodium", "sodium", "mg", "sodium_mg"),
)
_TEMPLATE_NAMES = {
    "daily": "daily-report.md",
    "weekly": "weekly-report.md",
    "monthly": "monthly-report.md",
}
_RULE_CATEGORIES = {
    "food_alias": "rule_food_alias",
    "portion": "rule_portion",
    "meal_type": "rule_meal_type",
    "inventory_link": "rule_inventory_link",
    "preference": "rule_preference",
}


def describe_expiry(
    expires_at: datetime | None,
    now: datetime,
) -> dict[str, str]:
    """Describe saved expiry relative to a trusted current time without writes."""

    if expires_at is None:
        return {
            "expiry_state": "missing",
            "expiry_display": "待补保质期",
        }
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("expiry calculation requires timezone-aware datetimes")
    remaining_seconds = (
        expires_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    ).total_seconds()
    day_seconds = timedelta(days=1).total_seconds()
    if remaining_seconds == 0:
        return {"expiry_state": "expired", "expiry_display": "刚到期"}
    if remaining_seconds > 0:
        state = "usable" if remaining_seconds > day_seconds else "expiring_soon"
        if remaining_seconds >= day_seconds:
            display = f"剩余{int(remaining_seconds // day_seconds)}天"
        else:
            display = f"剩余{max(1, ceil(remaining_seconds / 3600))}小时"
        return {"expiry_state": state, "expiry_display": display}

    elapsed_seconds = -remaining_seconds
    if elapsed_seconds >= day_seconds:
        display = f"已过期{int(elapsed_seconds // day_seconds)}天"
    else:
        display = f"已过期{max(1, ceil(elapsed_seconds / 3600))}小时"
    return {"expiry_state": "expired", "expiry_display": display}


@dataclass(frozen=True)
class ExpiringInventoryCollection:
    """One complete, stable, read-only expiry collection."""

    items: tuple[tuple[PantryBatch, dict[str, str]], ...]
    state_counts: dict[str, int]
    range: dict[str, object]
    complete: bool = True


def collect_expiring_inventory(
    batches: tuple[PantryBatch, ...],
    *,
    now: datetime,
    report_date: date,
    within_days: int,
    timezone_name: str,
    policies: PolicyRegistry,
) -> ExpiringInventoryCollection:
    """Collect all expired stock plus non-expired stock inside the cutoff."""

    if (
        isinstance(within_days, bool)
        or not isinstance(within_days, int)
        or within_days <= 0
    ):
        raise ValueError("within_days must be a positive integer")
    taxonomy = _expiry_taxonomy(policies)
    cutoff = report_date + timedelta(days=within_days)
    collected: list[tuple[PantryBatch, dict[str, str]]] = []
    for batch in batches:
        if batch.remaining_quantity <= 0 or batch.expires_at is None:
            continue
        description = describe_expiry(batch.expires_at, now)
        state = description["expiry_state"]
        policy = taxonomy.get(state)
        if policy is None:
            raise ConfigurationError(
                f"expiry state is not registered: {state}"
            )
        include = False
        if state == "expired":
            include = policy.get("include_past_without_lower_bound") is True
        elif policy.get("apply_future_cutoff") is True:
            include = local_date(batch.expires_at, timezone_name) <= cutoff
        if include:
            collected.append((batch, description))

    collected.sort(
        key=lambda item: (
            item[0].expires_at.astimezone(timezone.utc),
            item[0].normalized_name,
            item[0].batch_code or "",
            item[0].food_name,
        )
    )
    counts = {state: 0 for state in taxonomy}
    for _, description in collected:
        counts[description["expiry_state"]] += 1

    zone_start = localize_datetime(
        datetime.combine(report_date, time.min), timezone_name
    )
    zone_end = localize_datetime(
        datetime.combine(cutoff + timedelta(days=1), time.min),
        timezone_name,
    )
    return ExpiringInventoryCollection(
        items=tuple(collected),
        state_counts=counts,
        range={
            "timezone_name": timezone_name,
            "expired_lower_bound": None,
            "future_start_local": zone_start.isoformat(),
            "future_end_local": zone_end.isoformat(),
            "future_start_utc": utc_text(zone_start),
            "future_end_utc": utc_text(zone_end),
            "end_exclusive": True,
        },
    )


def _expiry_taxonomy(
    policies: PolicyRegistry,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for entry in policies.entries("report-taxonomy"):
        if entry.operator != "expiry_state":
            raise ConfigurationError(
                f"unsupported report taxonomy operator: {entry.operator}"
            )
        public_name = entry.values.get("public_name")
        if not isinstance(public_name, str) or not public_name:
            raise ConfigurationError(
                f"report taxonomy public_name is invalid: {entry.policy_key}"
            )
        if public_name in result:
            raise ConfigurationError(
                f"duplicate report taxonomy public_name: {public_name}"
            )
        result[public_name] = dict(entry.values)
    required = {"expired", "expiring_soon", "usable"}
    if not required <= result.keys():
        raise ConfigurationError("report taxonomy is missing a required expiry state")
    return {state: result[state] for state in sorted(required)}


@dataclass(frozen=True)
class _Period:
    kind: str
    start: date
    end: date
    identifier: str

    @property
    def day_count(self) -> int:
        return (self.end - self.start).days


def build_daily_report(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    settings: Settings,
    report_date: date,
    *,
    templates_dir: Path | None = None,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
) -> Path:
    """Build one daily report and atomically replace its generated Markdown."""

    period = _daily_period(_as_date(report_date))
    return _build_report(
        connection,
        data_paths,
        settings,
        period,
        templates_dir,
        lease_owner=lease_owner,
        lease_manager=lease_manager,
    )


def build_weekly_report(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    settings: Settings,
    report_date: date,
    *,
    templates_dir: Path | None = None,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
) -> Path:
    """Build the ISO week containing ``report_date``."""

    anchor = _as_date(report_date)
    start = anchor - timedelta(days=anchor.weekday())
    iso_year, iso_week, _ = start.isocalendar()
    end = start + timedelta(days=7)
    period = _Period(
        kind="weekly",
        start=start,
        end=end,
        identifier=f"{iso_year}-W{iso_week:02d}",
    )
    return _build_report(
        connection,
        data_paths,
        settings,
        period,
        templates_dir,
        lease_owner=lease_owner,
        lease_manager=lease_manager,
    )


def build_monthly_report(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    settings: Settings,
    report_date: date,
    *,
    templates_dir: Path | None = None,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
) -> Path:
    """Build the calendar month containing ``report_date``."""

    anchor = _as_date(report_date)
    start = anchor.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    identifier = start.strftime("%Y-%m")
    period = _Period(
        kind="monthly",
        start=start,
        end=end,
        identifier=identifier,
    )
    return _build_report(
        connection,
        data_paths,
        settings,
        period,
        templates_dir,
        lease_owner=lease_owner,
        lease_manager=lease_manager,
    )


def _daily_period(report_date: date) -> _Period:
    return _Period(
        kind="daily",
        start=report_date,
        end=report_date + timedelta(days=1),
        identifier=report_date.isoformat(),
    )


def _build_report(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    settings: Settings,
    period: _Period,
    templates_dir: Path | None,
    *,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
) -> Path:
    manager = lease_manager or manager_for(data_paths)
    with manager.shared_publisher(owner=lease_owner):
        return _build_report_owned(
            connection,
            data_paths,
            settings,
            period,
            templates_dir,
        )


def _build_report_owned(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    settings: Settings,
    period: _Period,
    templates_dir: Path | None,
) -> Path:
    locale = resolve_report_locale(settings.profile.language)
    goal_profile = load_goal_profile(connection)
    start_utc, end_utc = _utc_bounds(period, goal_profile.timezone_name)
    meals = connection.execute(
        """
        SELECT id, occurred_at, meal_type, source_text, location_type,
               total_calories, total_protein, total_fat,
               total_carbohydrate, total_fiber, total_sodium
        FROM meals
        WHERE deleted_at IS NULL AND occurred_at >= ? AND occurred_at < ?
        ORDER BY occurred_at, id
        """,
        (start_utc, end_utc),
    ).fetchall()
    water = connection.execute(
        """
        SELECT id, occurred_at, amount_ml, source_text
        FROM water_logs
        WHERE deleted_at IS NULL AND occurred_at >= ? AND occurred_at < ?
        ORDER BY occurred_at, id
        """,
        (start_utc, end_utc),
    ).fetchall()
    movements = connection.execute(
        """
        SELECT pm.id, pm.created_at, pm.movement_type, pm.quantity, pm.unit,
               pm.reason, pb.food_name, pb.batch_code
        FROM pantry_movements AS pm
        JOIN pantry_batches AS pb ON pb.id = pm.pantry_batch_id
        WHERE pm.created_at >= ? AND pm.created_at < ?
        ORDER BY pm.created_at, pm.id
        """,
        (start_utc, end_utc),
    ).fetchall()
    expiry_end = _local_midnight_utc(
        period.end + timedelta(days=7),
        goal_profile.timezone_name,
    )
    expiring = connection.execute(
        """
        SELECT id, expires_at, food_name, batch_code, remaining_quantity, unit
        FROM pantry_batches
        WHERE expires_at IS NOT NULL
          AND expires_at >= ?
          AND expires_at < ?
          AND remaining_quantity > 0
          AND status NOT IN ('discarded', 'expired', 'consumed')
        ORDER BY expires_at, id
        """,
        (start_utc, expiry_end),
    ).fetchall()
    weak_estimates = connection.execute(
        """
        SELECT mi.id, m.occurred_at, mi.raw_name, mi.source_grade,
               mi.nutrition_source, mi.uncertainty
        FROM meal_items AS mi
        JOIN meals AS m ON m.id = mi.meal_id
        WHERE m.deleted_at IS NULL
          AND m.occurred_at >= ?
          AND m.occurred_at < ?
          AND mi.source_grade IN ('C', 'D', 'unknown')
        ORDER BY m.occurred_at, mi.id, mi.display_order
        """,
        (start_utc, end_utc),
    ).fetchall()
    pending_links = connection.execute(
        """
        SELECT pil.id, m.occurred_at, mi.raw_name
        FROM pending_inventory_links AS pil
        JOIN meal_items AS mi ON mi.id = pil.meal_item_id
        JOIN meals AS m ON m.id = mi.meal_id
        WHERE pil.status = 'pending'
          AND m.deleted_at IS NULL
          AND m.occurred_at >= ?
          AND m.occurred_at < ?
        ORDER BY m.occurred_at, pil.id
        """,
        (start_utc, end_utc),
    ).fetchall()
    active_rule = connection.execute(
        """
        SELECT rule_type
        FROM personal_rules
        WHERE active = 1
        ORDER BY rule_type, id
        LIMIT 1
        """
    ).fetchone()

    aggregate = aggregate_period(
        connection, start_utc=start_utc, end_utc=end_utc
    )
    totals = {
        "total_calories": aggregate.calories,
        "total_protein": aggregate.protein,
        "total_fat": aggregate.fat,
        "total_carbohydrate": aggregate.carbohydrate,
        "total_fiber": aggregate.fiber,
        "total_sodium": aggregate.sodium,
    }
    total_water = aggregate.water_total_ml
    substitutions = {
        "TITLE": locale.text(
            f"title_{period.kind}",
            identifier=period.identifier,
            start=period.start.isoformat(),
            end=(period.end - timedelta(days=1)).isoformat(),
        ),
        "TOTALS": _format_totals(
            totals,
            total_water,
            goal_profile.goals,
            period.day_count,
            goals_confirmed=goal_profile.confirmed,
            known_minimum=aggregate.known_minimum,
            incomplete_meal_count=aggregate.incomplete_meal_count,
            unknown_fields=aggregate.unknown_fields,
            locale=locale,
        ),
        "MEALS": _format_meals(meals, locale),
        "WATER": _format_water(water, locale),
        "PANTRY_MOVEMENTS": _format_movements(movements, locale),
        "EXPIRING_BATCHES": _format_expiring(expiring, locale),
        "WEAK_ESTIMATES": _format_weak_estimates(weak_estimates, locale),
        "PENDING_LINKS": _format_pending_links(pending_links, locale),
        "PERSONALIZED_NEXT_STEPS": _personalized_next_steps(
            totals=totals,
            total_water=total_water,
            goals=goal_profile.goals,
            days=period.day_count,
            goals_confirmed=goal_profile.confirmed,
            has_meals=bool(meals),
            has_activity=bool(
                meals
                or water
                or movements
                or expiring
                or weak_estimates
                or pending_links
                or active_rule
            ),
            expiring_count=len(expiring),
            pending_count=len(pending_links),
            weak_count=len(weak_estimates),
            active_rule=active_rule,
            unknown_fields=aggregate.unknown_fields,
            locale=locale,
        ),
    }
    template_root = (
        Path(templates_dir)
        if templates_dir is not None
        else Path(__file__).resolve().parents[2] / "templates"
    )
    template = (
        template_root / locale.code / _TEMPLATE_NAMES[period.kind]
    ).read_text(encoding="utf-8")
    rendered = _render(template, substitutions)
    destination = data_paths.reports / period.kind / f"{period.identifier}.md"
    atomic_write_text(destination, rendered, data_paths=data_paths)
    return destination


def _format_totals(
    totals: dict[str, Decimal],
    water_ml: Decimal,
    goals: NutritionGoals,
    days: int,
    *,
    goals_confirmed: bool,
    known_minimum: bool,
    incomplete_meal_count: int,
    unknown_fields: frozenset[str],
    locale: ReportLocale,
) -> str:
    lines = []
    for field, label_key, unit, goal_field in _NUTRITION_FIELDS:
        label = locale.metric_labels[label_key]
        qualifier = (
            locale.text("known_lower_bound_qualifier")
            if field.removeprefix("total_") in unknown_fields
            else ""
        )
        current = _decimal_text(totals[field])
        if goals_confirmed:
            goal = getattr(goals, goal_field) * days
            lines.append(
                locale.text(
                    "metric_confirmed",
                    label=label,
                    current=current,
                    goal=goal,
                    unit=unit,
                    qualifier=qualifier,
                )
            )
        else:
            lines.append(
                locale.text(
                    "metric_unconfirmed",
                    label=label,
                    current=current,
                    unit=unit,
                    qualifier=qualifier,
                )
            )
    if goals_confirmed:
        lines.append(
            locale.text(
                "water_confirmed",
                current=_decimal_text(water_ml),
                goal=goals.water_ml * days,
            )
        )
    else:
        lines.append(
            locale.text(
                "water_unconfirmed",
                current=_decimal_text(water_ml),
            )
        )
        lines.append(locale.text("targets_unconfirmed"))
    if known_minimum:
        lines.append(
            locale.text("known_minimum", count=incomplete_meal_count)
        )
    return "\n".join(lines)


def _format_meals(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    lines = []
    for row in rows:
        nutrition = locale.list_text(
            [
                locale.text(
                    "nutrition_unknown",
                    unit=unit,
                    label=locale.metric_labels[label_key],
                )
                if row[field] is None
                else locale.text(
                    "nutrition_known",
                    value=_decimal_text(_stored_decimal(row[field])),
                    unit=unit,
                    label=locale.metric_labels[label_key],
                )
                for field, label_key, unit, _ in _NUTRITION_FIELDS
            ]
        )
        lines.append(
            locale.text(
                "meal_line",
                occurred_at=row["occurred_at"],
                meal_type=row["meal_type"],
                source_text=row["source_text"],
                location_type=row["location_type"],
                nutrition=nutrition,
            )
        )
    return "\n".join(lines)


def _format_water(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    return "\n".join(
        locale.text(
            "water_line",
            occurred_at=row["occurred_at"],
            amount_ml=row["amount_ml"],
            source_text=row["source_text"],
        )
        for row in rows
    )


def _format_movements(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    lines = []
    for row in rows:
        batch = (
            locale.text("batch_suffix", batch_code=row["batch_code"])
            if row["batch_code"]
            else ""
        )
        reason = (
            locale.text("reason_suffix", reason=row["reason"])
            if row["reason"]
            else ""
        )
        lines.append(
            locale.text(
                "movement_line",
                created_at=row["created_at"],
                movement_type=row["movement_type"],
                quantity=_decimal_text(Decimal(str(row["quantity"]))),
                unit=row["unit"],
                food_name=row["food_name"],
                batch=batch,
                reason=reason,
            )
        )
    return "\n".join(lines)


def _format_expiring(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    return "\n".join(
        locale.text(
            "expiring_line",
            expires_at=row["expires_at"],
            food_name=row["food_name"],
            batch_code=(
                row["batch_code"]
                or locale.text("unlabelled_batch")
            ),
            quantity=_decimal_text(
                Decimal(str(row["remaining_quantity"]))
            ),
            unit=row["unit"],
        )
        for row in rows
    )


def _format_weak_estimates(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    lines = []
    for row in rows:
        details = "; ".join(
            str(value)
            for value in (row["nutrition_source"], row["uncertainty"])
            if value
        )
        suffix = (
            locale.text("details_suffix", details=details)
            if details
            else ""
        )
        lines.append(
            locale.text(
                "weak_line",
                occurred_at=row["occurred_at"],
                raw_name=row["raw_name"],
                source_grade=row["source_grade"],
                details=suffix,
            )
        )
    return "\n".join(lines)


def _format_pending_links(
    rows: list[sqlite3.Row],
    locale: ReportLocale,
) -> str:
    if not rows:
        return locale.text("no_rows")
    return "\n".join(
        locale.text(
            "pending_line",
            occurred_at=row["occurred_at"],
            raw_name=row["raw_name"],
        )
        for row in rows
    )


def _personalized_next_steps(
    *,
    totals: dict[str, Decimal],
    total_water: Decimal,
    goals: NutritionGoals,
    days: int,
    goals_confirmed: bool,
    has_meals: bool,
    has_activity: bool,
    expiring_count: int,
    pending_count: int,
    weak_count: int,
    active_rule: sqlite3.Row | None,
    unknown_fields: frozenset[str],
    locale: ReportLocale,
) -> str:
    if not has_activity:
        return locale.text("advice_no_activity")

    advice: list[str] = []
    if unknown_fields:
        labels = [
            locale.metric_labels[label_key]
            for field, label_key, _, _ in _NUTRITION_FIELDS
            if field.removeprefix("total_") in unknown_fields
        ]
        advice.append(
            locale.text(
                "advice_unknown",
                labels=locale.list_text(labels),
            )
        )
    water_goal = goals.water_ml * days
    if goals_confirmed and water_goal > 0 and total_water < water_goal:
        advice.append(locale.text("advice_hydration"))

    deviation = (
        _largest_goal_deviation(totals, goals, days, unknown_fields=unknown_fields)
        if goals_confirmed and has_meals
        else None
    )
    if deviation is not None:
        advice.append(
            locale.text(
                "advice_deviation",
                label=locale.metric_labels[deviation],
            )
        )

    if expiring_count:
        noun = locale.text(
            "expiring_noun_one"
            if expiring_count == 1
            else "expiring_noun_many"
        )
        advice.append(
            locale.text(
                "advice_expiring",
                count=expiring_count,
                noun=noun,
            )
        )

    if pending_count or weak_count:
        advice.append(locale.text("advice_pending_weak"))

    if active_rule is not None:
        category_key = _RULE_CATEGORIES.get(str(active_rule["rule_type"]))
        if category_key is not None:
            advice.append(
                locale.text(
                    "advice_active_rule",
                    category=locale.text(category_key),
                )
            )

    return (
        "\n".join(advice[:3])
        if advice
        else locale.text("advice_continue")
    )


def _largest_goal_deviation(
    totals: dict[str, Decimal],
    goals: NutritionGoals,
    days: int,
    *,
    unknown_fields: frozenset[str] = frozenset(),
) -> str | None:
    deviations: list[tuple[Decimal, int, str]] = []
    for index, (field, label_key, _, goal_field) in enumerate(
        _NUTRITION_FIELDS
    ):
        if field.removeprefix("total_") in unknown_fields:
            continue
        goal = Decimal(getattr(goals, goal_field) * days)
        if goal <= 0:
            continue
        difference = abs(totals[field] - goal) / goal
        if difference >= Decimal("0.2"):
            deviations.append((difference, -index, label_key))
    return max(deviations)[2] if deviations else None


def _render(template: str, substitutions: dict[str, str]) -> str:
    rendered = template
    for name, value in substitutions.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("Report template contains an unknown placeholder")
    return rendered.rstrip() + "\n"


def _stored_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if not isinstance(value, str):
        raise ValueError("Formal nutrition values must use exact TEXT decimals")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("Formal nutrition values must be finite and non-negative")
    return number


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _utc_bounds(period: _Period, timezone_name: str) -> tuple[str, str]:
    return (
        _local_midnight_utc(period.start, timezone_name),
        _local_midnight_utc(period.end, timezone_name),
    )


def _local_midnight_utc(day: date, timezone_name: str) -> str:
    zone = resolve_timezone(timezone_name)
    value = datetime.combine(day, time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    return _utc_text(value)


def _utc_text(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_date(value: date) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("report_date must be a date")
    return value
