"""Persistent, undoable nutrition goals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Literal

from .models import NutritionGoals
from .timezones import resolve_timezone
from .transactions import TransactionManager


GoalSource = Literal["configuration_default", "user_confirmed"]


@dataclass(frozen=True)
class GoalProfile:
    goals: NutritionGoals
    timezone_name: str
    updated_at: datetime
    goal_source: GoalSource
    confirmed_at: datetime | None

    @property
    def confirmed(self) -> bool:
        return self.goal_source == "user_confirmed"


def ensure_goal_profile(
    connection: sqlite3.Connection, defaults: NutritionGoals, timezone_name: str, now: datetime
) -> GoalProfile:
    if connection.execute("SELECT count(*) FROM nutrition_goal_profiles").fetchone()[0] == 0:
        _validate(defaults, timezone_name)
        connection.execute(
            """INSERT INTO nutrition_goal_profiles
            (id, calories_kcal, protein_g, fat_g, carbohydrate_g, fiber_g,
             sodium_mg, water_ml, timezone_name, updated_at, goal_source,
             confirmed_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'configuration_default', NULL)""",
            (*_values(defaults), timezone_name.strip(), _timestamp(now)),
        )
        connection.commit()
    return load_goal_profile(connection)


def load_goal_profile(connection: sqlite3.Connection) -> GoalProfile:
    row = connection.execute("SELECT * FROM nutrition_goal_profiles WHERE id = 1").fetchone()
    if row is None:
        raise LookupError("nutrition goal profile has not been initialized")
    return GoalProfile(
        NutritionGoals(*(int(row[field]) for field in _GOAL_FIELDS)),
        row["timezone_name"],
        datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
        row["goal_source"],
        (
            datetime.fromisoformat(row["confirmed_at"].replace("Z", "+00:00"))
            if row["confirmed_at"] is not None
            else None
        ),
    )


def public_provenance(profile: GoalProfile) -> dict[str, object]:
    return {
        "goals_confirmed": profile.confirmed,
        "goal_source": profile.goal_source,
        "confirmed_at": (
            _timestamp(profile.confirmed_at)
            if profile.confirmed_at is not None
            else None
        ),
    }


def update_goal_profile(
    connection: sqlite3.Connection, manager: TransactionManager, draft: NutritionGoals,
    source_text: str, now: datetime, *, timezone_name: str | None = None,
) -> GoalProfile:
    current = load_goal_profile(connection)
    target_timezone = timezone_name if timezone_name is not None else current.timezone_name
    _validate(draft, target_timezone)
    updated_at = _timestamp(now)

    def mutate(context):
        context.update("nutrition_goal_profiles", 1, {
            **dict(zip(_GOAL_FIELDS, _values(draft), strict=True)),
            "timezone_name": target_timezone.strip(),
            "updated_at": updated_at,
            "goal_source": "user_confirmed",
            "confirmed_at": updated_at,
        })
        return load_goal_profile(connection)

    return manager.execute("profile_update", source_text, mutate).value


_GOAL_FIELDS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "fiber_g", "sodium_mg", "water_ml")


def _values(goals: NutritionGoals) -> tuple[int, ...]:
    return tuple(int(getattr(goals, field)) for field in _GOAL_FIELDS)


def _validate(goals: NutritionGoals, timezone_name: str) -> None:
    if any(value <= 0 for value in _values(goals)):
        raise ValueError("all nutrition goals must be positive")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("timezone_name is required")
    resolve_timezone(timezone_name.strip())


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
