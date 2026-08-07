"""Shipped YAML defaults, per-user overrides, and validation."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .inventory_order import normalized_deduction_strategy
from .models import (
    AutomationSettings,
    BehaviorSettings,
    ConfigurationError,
    InventorySettings,
    LearningSettings,
    MealLoggingSettings,
    NutritionGoals,
    ProfileSettings,
    Settings,
    WaterSettings,
    WaterUnits,
    frozen_decimal_mapping,
    frozen_mapping,
)
from .timezones import TimezoneConfigurationError, resolve_timezone
from .paths import DataPaths, validate_owned_path

_CONFIG_FILES = ("profile.yaml", "nutrition-goals.yaml", "behavior.yaml", "automation.yaml")
_RULE_FILES = (
    "cooking-yields.yaml",
    "edible-ratios.yaml",
    "food-aliases.yaml",
    "inventory-deduction.yaml",
    "nutrition-foods.yaml",
    "nutrition-source-policy.yaml",
    "portion-estimates.yaml",
    "temporal-scopes.yaml",
    "quantity-evidence.yaml",
    "intent-routes.yaml",
    "inventory-relations.yaml",
    "report-taxonomy.yaml",
    "fact-authority.yaml",
)
_CONFIDENCE_FIELDS = frozenset(
    {
        "source_confidence",
        "name_match_confidence",
        "quantity_confidence",
        "batch_uniqueness",
        "context_consistency",
        "personal_rule_confidence",
    }
)


def load_settings(
    source_root: Path,
    data_paths: DataPaths,
    *,
    include_overrides: bool = True,
) -> Settings:
    """Load shipped YAML defaults and optional data-directory YAML overrides."""

    source_root = Path(source_root)
    loaded = {
        filename: _read_yaml(source_root / "config" / filename)
        for filename in _CONFIG_FILES
    }
    if include_overrides:
        override_root = data_paths.root / "config"
        for filename in _CONFIG_FILES:
            override_path = override_root / filename
            validate_owned_path(data_paths, override_path)
            if override_path.is_file():
                loaded[filename] = _merge(loaded[filename], _read_yaml(override_path))

    return Settings(
        profile=_build_profile(loaded["profile.yaml"]),
        nutrition_goals=_build_nutrition_goals(loaded["nutrition-goals.yaml"]),
        behavior=_build_behavior(loaded["behavior.yaml"]),
        automation=_build_automation(loaded["automation.yaml"]),
    )


def validate_static_rules(source_root: Path) -> None:
    """Validate that all shipped static-rule documents are readable mappings."""

    rules_dir = Path(source_root) / "rules"
    for filename in _RULE_FILES:
        values = _read_yaml(rules_dir / filename)
        if not values:
            raise ConfigurationError(f"Static rule file must not be empty: {filename}")


def validate_automation(settings: Settings) -> None:
    """Validate suggested automation entries without scheduling anything."""

    schedules = settings.automation.suggested_schedules
    if not schedules:
        raise ConfigurationError("automation.suggested_schedules must not be empty")
    for name, expression in schedules.items():
        fields = expression.split()
        if len(fields) != 5 or any(
            not field
            or any(character not in "0123456789*/?,-" for character in field)
            for field in fields
        ):
            raise ConfigurationError(
                f"automation schedule {name!r} must be a five-field cron expression"
            )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to read configuration: {path}") from error
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration must be a mapping: {path}")
    return value


def _merge(defaults: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge copies of two mappings without mutating either input."""

    merged = dict(defaults)
    for key, override in overrides.items():
        default = merged.get(key)
        if isinstance(default, Mapping) and isinstance(override, Mapping):
            merged[key] = _merge(default, override)
        else:
            merged[key] = override
    return merged


def _build_profile(values: Mapping[str, Any]) -> ProfileSettings:
    water = _mapping(values, "default_water_units")
    meal_times = _mapping(values, "default_meal_times")
    try:
        timezone_name = _string(values, "timezone")
        resolve_timezone(timezone_name)
    except TimezoneConfigurationError:
        raise
    except ConfigurationError as error:
        raise TimezoneConfigurationError(str(error)) from error
    return ProfileSettings(
        name=_string(values, "name"),
        timezone=timezone_name,
        language=_string(values, "language"),
        default_meal_times=frozen_mapping({key: _string(meal_times, key) for key in meal_times}),
        default_water_units=WaterUnits(
            cup_ml=_positive_int(water, "cup_ml"),
            glass_ml=_positive_int(water, "glass_ml"),
            bottle_ml=_positive_int(water, "bottle_ml"),
        ),
    )


def _build_nutrition_goals(values: Mapping[str, Any]) -> NutritionGoals:
    return NutritionGoals(
        calories_kcal=_non_negative_int(values, "calories_kcal"),
        protein_g=_non_negative_int(values, "protein_g"),
        fat_g=_non_negative_int(values, "fat_g"),
        carbohydrate_g=_non_negative_int(values, "carbohydrate_g"),
        fiber_g=_non_negative_int(values, "fiber_g"),
        sodium_mg=_non_negative_int(values, "sodium_mg"),
        water_ml=_non_negative_int(values, "water_ml"),
    )


def _build_behavior(values: Mapping[str, Any]) -> BehaviorSettings:
    inventory = _mapping(values, "inventory")
    learning = _mapping(values, "learning")
    meal_logging = _mapping(values, "meal_logging")
    water = _mapping(values, "water")
    allow_negative_stock = _bool(inventory, "allow_negative_stock")
    if allow_negative_stock:
        raise ConfigurationError("inventory.allow_negative_stock must be false")
    strategy = inventory.get("deduction_strategy")
    if not isinstance(strategy, list):
        raise ConfigurationError("inventory.deduction_strategy must be a list of strings")
    try:
        strategy = normalized_deduction_strategy(strategy)
    except ValueError as error:
        raise ConfigurationError(f"inventory.{error}") from error
    auto_confidence = _decimal_probability(inventory, "auto_deduct_confidence")
    pending_confidence = _decimal_probability(inventory, "pending_link_confidence")
    if pending_confidence > auto_confidence:
        raise ConfigurationError(
            "inventory.pending_link_confidence cannot exceed auto_deduct_confidence"
        )
    configured_weights = _mapping(inventory, "confidence_weights")
    if set(configured_weights) != _CONFIDENCE_FIELDS:
        raise ConfigurationError(
            "inventory.confidence_weights must declare all confidence factors"
        )
    confidence_weights = {
        name: _non_negative_decimal(configured_weights, name)
        for name in _CONFIDENCE_FIELDS
    }
    if sum(confidence_weights.values(), Decimal("0")) <= 0:
        raise ConfigurationError(
            "inventory.confidence_weights must have a positive total"
        )
    return BehaviorSettings(
        inventory=InventorySettings(
            auto_deduct=_bool(inventory, "auto_deduct"),
            ask_below_confidence=_probability(inventory, "ask_below_confidence"),
            allow_negative_stock=allow_negative_stock,
            deduction_strategy=strategy,
            preview_expiration_minutes=_positive_int(
                inventory, "preview_expiration_minutes"
            ),
            auto_deduct_confidence=auto_confidence,
            pending_link_confidence=pending_confidence,
            confidence_weights=frozen_decimal_mapping(confidence_weights),
        ),
        learning=LearningSettings(
            enabled=_bool(learning, "enabled"),
            promotion_evidence_count=_positive_int(learning, "promotion_evidence_count"),
            allow_automatic_promotion=_bool(learning, "allow_automatic_promotion"),
        ),
        meal_logging=MealLoggingSettings(
            infer_meal_type=_bool(meal_logging, "infer_meal_type"),
            infer_portion=_bool(meal_logging, "infer_portion"),
            record_planned_meals=_bool(meal_logging, "record_planned_meals"),
            restaurant_food_deduct_inventory=_bool(meal_logging, "restaurant_food_deduct_inventory"),
        ),
        water=WaterSettings(max_single_entry_ml=_positive_int(water, "max_single_entry_ml")),
    )


def _build_automation(values: Mapping[str, Any]) -> AutomationSettings:
    schedules = _mapping(values, "suggested_schedules")
    return AutomationSettings(
        enabled=_bool(values, "enabled"),
        suggested_schedules=frozen_mapping({key: _string(schedules, key) for key in schedules}),
    )


def _mapping(values: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = values.get(key)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{key} must be a mapping")
    return value


def _string(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str):
        raise ConfigurationError(f"{key} must be a string")
    return value


def _bool(values: Mapping[str, Any], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be a boolean")
    return value


def _positive_int(values: Mapping[str, Any], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{key} must be an integer of at least one")
    return value


def _non_negative_int(values: Mapping[str, Any], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"{key} must be a non-negative integer")
    return value


def _probability(values: Mapping[str, Any], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ConfigurationError(f"{key} must be a number from zero through one")
    return float(value)


def _decimal_probability(values: Mapping[str, Any], key: str) -> Decimal:
    value = _decimal_number(values, key)
    if value < 0 or value > 1:
        raise ConfigurationError(f"{key} must be a number from zero through one")
    return value


def _non_negative_decimal(values: Mapping[str, Any], key: str) -> Decimal:
    value = _decimal_number(values, key)
    if value < 0:
        raise ConfigurationError(f"{key} must be a non-negative number")
    return value


def _decimal_number(values: Mapping[str, Any], key: str) -> Decimal:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ConfigurationError(f"{key} must be a finite number")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ConfigurationError(f"{key} must be a finite number") from error
    if not number.is_finite():
        raise ConfigurationError(f"{key} must be a finite number")
    return number
