"""Reusable recipe profiles and bounded pantry-aware meal candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import sqlite3
from typing import Any, Mapping, Sequence

from .transactions import TransactionManager


class RecipeValidationError(ValueError):
    """Raised when a recipe profile or candidate request is invalid."""


@dataclass(frozen=True)
class RecipeIngredient:
    food_name: str
    normalized_name: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class RecipeProfile:
    name: str
    ingredients: tuple[RecipeIngredient, ...]
    yield_quantity: Decimal
    yield_unit: str
    notes: str | None
    version: int


@dataclass(frozen=True)
class RecipeCandidate:
    name: str
    yield_quantity: Decimal
    yield_unit: str
    pantry_coverage: Decimal
    available_ingredients: tuple[str, ...]
    missing_ingredients: tuple[str, ...]
    expiring_ingredients: tuple[str, ...]
    reasons: tuple[str, ...]
    candidate_only: bool = True


def save_recipe(
    connection: sqlite3.Connection,
    manager: TransactionManager,
    *,
    name: str,
    ingredients: Sequence[Mapping[str, Any]],
    yield_quantity: Decimal,
    yield_unit: str,
    source_text: str,
    now: datetime,
    notes: str | None = None,
) -> RecipeProfile:
    """Create or replace one named recipe profile as an undoable mutation."""

    recipe_name = _text(name, "name", maximum=120)
    normalized_name = recipe_name.casefold()
    normalized_ingredients = _ingredients(ingredients)
    normalized_yield = _positive(yield_quantity, "yield_quantity")
    normalized_unit = _text(yield_unit, "yield_unit", maximum=24)
    normalized_source = _text(source_text, "source_text", maximum=1000)
    normalized_notes = (
        _text(notes, "notes", maximum=500) if notes is not None else None
    )
    timestamp = _timestamp(now)
    stored_ingredients = _ingredients_json(normalized_ingredients)

    def mutate(context):
        existing = connection.execute(
            "SELECT * FROM recipe_profiles WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()
        values = {
            "name": recipe_name,
            "normalized_name": normalized_name,
            "ingredients_json": stored_ingredients,
            "yield_quantity": _sqlite_real(normalized_yield, "yield_quantity"),
            "yield_unit": normalized_unit,
            "notes": normalized_notes,
            "source_text": normalized_source,
            "updated_at": timestamp,
            "deleted_at": None,
        }
        if existing is None:
            row = context.insert(
                "recipe_profiles",
                values | {"created_at": timestamp, "version": 1},
            )
        else:
            row = context.update(
                "recipe_profiles",
                int(existing["id"]),
                values | {"version": int(existing["version"]) + 1},
            )
        return _profile(row)

    return manager.execute("meal_plan", normalized_source, mutate).value


def suggest_recipes(
    connection: sqlite3.Connection,
    *,
    limit: int = 3,
    max_missing_items: int = 2,
    now: datetime,
) -> tuple[RecipeCandidate, ...]:
    """Return at most three deterministic candidates without changing facts."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3:
        raise RecipeValidationError("limit must be an integer from 1 to 3")
    if (
        isinstance(max_missing_items, bool)
        or not isinstance(max_missing_items, int)
        or not 0 <= max_missing_items <= 30
    ):
        raise RecipeValidationError(
            "max_missing_items must be an integer from 0 to 30"
        )
    reference_time = _aware_utc(now)
    pantry_state = _pantry_state(connection, reference_time=reference_time)
    ranked: list[
        tuple[tuple[Decimal, int, datetime, int, str], RecipeCandidate]
    ] = []
    for row in connection.execute(
        """
        SELECT *
        FROM recipe_profiles
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        """
    ):
        profile = _profile(row)
        available: list[str] = []
        missing: list[str] = []
        expiring: list[str] = []
        earliest_expiry = datetime.max.replace(tzinfo=timezone.utc)
        for ingredient in profile.ingredients:
            stored, expiry = pantry_state.get(
                (ingredient.normalized_name, ingredient.unit),
                (Decimal("0"), None),
            )
            if stored >= ingredient.quantity:
                available.append(ingredient.food_name)
                if (
                    expiry is not None
                    and expiry <= reference_time + timedelta(days=3)
                ):
                    expiring.append(ingredient.food_name)
                    earliest_expiry = min(earliest_expiry, expiry)
            else:
                missing.append(ingredient.food_name)
        if len(missing) > max_missing_items:
            continue
        coverage = Decimal(len(available)) / Decimal(len(profile.ingredients))
        reasons = (
            (("优先使用临期食材",) if expiring else ())
            + (
                ("库存食材齐全",)
                if not missing
                else (f"已有{len(available)}种食材", f"还缺{len(missing)}种食材")
            )
        )
        candidate = RecipeCandidate(
            name=profile.name,
            yield_quantity=profile.yield_quantity,
            yield_unit=profile.yield_unit,
            pantry_coverage=coverage,
            available_ingredients=tuple(available),
            missing_ingredients=tuple(missing),
            expiring_ingredients=tuple(expiring),
            reasons=reasons,
        )
        ranked.append(
            (
                (
                    -coverage,
                    -len(expiring),
                    earliest_expiry,
                    len(missing),
                    profile.name.casefold(),
                ),
                candidate,
            )
        )
    ranked.sort(key=lambda item: item[0])
    return tuple(candidate for _, candidate in ranked[:limit])


def _pantry_state(
    connection: sqlite3.Connection,
    *,
    reference_time: datetime,
) -> dict[tuple[str, str], tuple[Decimal, datetime | None]]:
    """Return only inventory that is still edible at the recommendation instant."""

    rows = connection.execute(
        """
        SELECT
            normalized_name,
            unit,
            sum(remaining_quantity) AS quantity,
            min(expires_at) AS earliest_expiry
        FROM pantry_batches
        WHERE status IN ('active', 'opened', 'frozen', 'thawed')
          AND remaining_quantity > 0
          AND (expires_at IS NULL OR expires_at > ?)
        GROUP BY normalized_name, unit
        """,
        (_timestamp(reference_time),),
    )
    return {
        (str(row["normalized_name"]).casefold(), str(row["unit"])): (
            Decimal(str(row["quantity"])),
            (
                _datetime(str(row["earliest_expiry"]))
                if row["earliest_expiry"] is not None
                else None
            ),
        )
        for row in rows
    }


def _profile(row: sqlite3.Row) -> RecipeProfile:
    raw = json.loads(str(row["ingredients_json"]))
    if not isinstance(raw, list):
        raise RecipeValidationError("stored recipe ingredients are invalid")
    return RecipeProfile(
        name=str(row["name"]),
        ingredients=tuple(_ingredient(item) for item in raw),
        yield_quantity=Decimal(str(row["yield_quantity"])),
        yield_unit=str(row["yield_unit"]),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        version=int(row["version"]),
    )


def _ingredients(values: Sequence[Mapping[str, Any]]) -> tuple[RecipeIngredient, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise RecipeValidationError("ingredients must be an array")
    if not 1 <= len(values) <= 30:
        raise RecipeValidationError("ingredients must contain 1 to 30 items")
    return tuple(_ingredient(value) for value in values)


def _ingredient(value: Mapping[str, Any]) -> RecipeIngredient:
    if not isinstance(value, Mapping):
        raise RecipeValidationError("each ingredient must be an object")
    food_name = _text(value.get("food_name"), "food_name", maximum=120)
    normalized_value = value.get("normalized_name")
    normalized_name = (
        _text(normalized_value, "normalized_name", maximum=120).casefold()
        if normalized_value is not None
        else food_name.casefold()
    )
    return RecipeIngredient(
        food_name=food_name,
        normalized_name=normalized_name,
        quantity=_positive(value.get("quantity"), "quantity"),
        unit=_text(value.get("unit"), "unit", maximum=24),
    )


def _ingredients_json(values: Sequence[RecipeIngredient]) -> str:
    return json.dumps(
        [
            {
                "food_name": item.food_name,
                "normalized_name": item.normalized_name,
                "quantity": format(item.quantity, "f"),
                "unit": item.unit,
            }
            for item in values
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecipeValidationError(f"{field} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise RecipeValidationError(f"{field} is too long")
    return normalized


def _positive(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise RecipeValidationError(f"{field} must be positive")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RecipeValidationError(f"{field} must be positive") from None
    if not number.is_finite() or number <= 0:
        raise RecipeValidationError(f"{field} must be positive")
    _sqlite_real(number, field)
    return number


def _sqlite_real(value: Decimal, field: str) -> float:
    converted = float(value)
    if not Decimal(str(converted)).is_finite():
        raise RecipeValidationError(f"{field} is not representable")
    return converted


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RecipeValidationError("now must be timezone-aware")
    return (
        _aware_utc(value)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RecipeValidationError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecipeValidationError("stored expiry is invalid") from error
    if parsed.tzinfo is None:
        raise RecipeValidationError("stored expiry is invalid")
    return parsed.astimezone(timezone.utc)
