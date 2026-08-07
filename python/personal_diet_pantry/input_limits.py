"""Layered limits for all untrusted JSON request values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


MAX_JSON_DEPTH = 32
MAX_STRING_LENGTH = 16 * 1024
MAX_COLLECTION_MEMBERS = 1000
MAX_TOTAL_ITEMS = MAX_COLLECTION_MEMBERS
MAX_MEAL_ITEMS = 100
MAX_INGREDIENT_LEVELS = 8
MAX_INGREDIENT_CHILDREN = 50


class InputLimitError(ValueError):
    """Raised before business parsing when a request exceeds a public limit."""


def validate_json_value(value: Any, *, depth: int = 1) -> None:
    """Validate generic JSON depth, string, and immediate collection limits."""

    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise InputLimitError("request string exceeds the maximum length")
        return
    if isinstance(value, Mapping):
        if depth > MAX_JSON_DEPTH:
            raise InputLimitError("request JSON exceeds the maximum nesting depth")
        if len(value) > MAX_COLLECTION_MEMBERS:
            raise InputLimitError("request object has too many members")
        for key, item in value.items():
            if not isinstance(key, str):
                raise InputLimitError("request object keys must be strings")
            if len(key) > MAX_STRING_LENGTH:
                raise InputLimitError("request object key exceeds the maximum length")
            validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        if depth > MAX_JSON_DEPTH:
            raise InputLimitError("request JSON exceeds the maximum nesting depth")
        if len(value) > MAX_COLLECTION_MEMBERS:
            raise InputLimitError("request array has too many items")
        for item in value:
            validate_json_value(item, depth=depth + 1)


def validate_meal_payload(value: Mapping[str, Any]) -> None:
    """Apply stricter top-level meal and recursive ingredient limits."""

    items = value.get("items")
    if items is None and isinstance(value.get("dish"), Mapping):
        ingredients = value["dish"].get("ingredients")
        if not isinstance(ingredients, Sequence) or isinstance(
            ingredients, (str, bytes, bytearray)
        ):
            return
        if len(ingredients) > MAX_MEAL_ITEMS:
            raise InputLimitError("meal has too many top-level items")
        remaining = MAX_TOTAL_ITEMS - 1
        for ingredient in ingredients:
            if isinstance(ingredient, Mapping):
                used = _validate_ingredient_item(
                    ingredient, level=1, remaining=remaining
                )
                remaining -= used
        return
    if not isinstance(items, Sequence) or isinstance(
        items, (str, bytes, bytearray)
    ):
        return
    if len(items) > MAX_MEAL_ITEMS:
        raise InputLimitError("meal has too many top-level items")
    remaining = MAX_TOTAL_ITEMS
    for item in items:
        if isinstance(item, Mapping):
            used = _validate_ingredient_item(
                item, level=1, remaining=remaining
            )
            remaining -= used


def _validate_ingredient_item(
    value: Mapping[str, Any], *, level: int, remaining: int
) -> int:
    if remaining < 1:
        raise InputLimitError("meal has too many total items")
    if level > MAX_INGREDIENT_LEVELS:
        raise InputLimitError("ingredient tree exceeds the maximum depth")
    used = 1
    ingredients = value.get("ingredients", ())
    if not isinstance(ingredients, Sequence) or isinstance(
        ingredients, (str, bytes, bytearray)
    ):
        return used
    if len(ingredients) > MAX_INGREDIENT_CHILDREN:
        raise InputLimitError("ingredient item has too many children")
    for ingredient in ingredients:
        if isinstance(ingredient, Mapping):
            child_count = _validate_ingredient_item(
                ingredient,
                level=level + 1,
                remaining=remaining - used,
            )
            used += child_count
    return used
