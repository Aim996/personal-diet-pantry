"""Immutable configuration and path models shared by the business core."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class DataPaths:
    root: Path
    database: Path
    control: Path
    maintenance_database: Path
    backups: Path
    exports: Path
    imports: Path
    reports: Path
    cache: Path
    health_report: Path


@dataclass(frozen=True)
class WaterUnits:
    cup_ml: int
    glass_ml: int
    bottle_ml: int


@dataclass(frozen=True)
class ProfileSettings:
    name: str
    timezone: str
    language: str
    default_meal_times: Mapping[str, str]
    default_water_units: WaterUnits


@dataclass(frozen=True)
class NutritionGoals:
    calories_kcal: int
    protein_g: int
    fat_g: int
    carbohydrate_g: int
    fiber_g: int
    sodium_mg: int
    water_ml: int


@dataclass(frozen=True)
class InventorySettings:
    auto_deduct: bool
    ask_below_confidence: float
    allow_negative_stock: bool
    deduction_strategy: tuple[str, ...]
    preview_expiration_minutes: int
    auto_deduct_confidence: Decimal
    pending_link_confidence: Decimal
    confidence_weights: Mapping[str, Decimal]


@dataclass(frozen=True)
class LearningSettings:
    enabled: bool
    promotion_evidence_count: int
    allow_automatic_promotion: bool


@dataclass(frozen=True)
class MealLoggingSettings:
    infer_meal_type: bool
    infer_portion: bool
    record_planned_meals: bool
    restaurant_food_deduct_inventory: bool


@dataclass(frozen=True)
class WaterSettings:
    max_single_entry_ml: int


@dataclass(frozen=True)
class BehaviorSettings:
    inventory: InventorySettings
    learning: LearningSettings
    meal_logging: MealLoggingSettings
    water: WaterSettings


@dataclass(frozen=True)
class AutomationSettings:
    enabled: bool
    suggested_schedules: Mapping[str, str]


@dataclass(frozen=True)
class Settings:
    profile: ProfileSettings
    nutrition_goals: NutritionGoals
    behavior: BehaviorSettings
    automation: AutomationSettings

    @property
    def default_water_units(self) -> WaterUnits:
        return self.profile.default_water_units


def frozen_mapping(values: Mapping[str, str]) -> Mapping[str, str]:
    """Return an immutable shallow copy suitable for a frozen model."""

    return MappingProxyType(dict(values))


def frozen_decimal_mapping(
    values: Mapping[str, Decimal],
) -> Mapping[str, Decimal]:
    """Return an immutable confidence-weight mapping."""

    return MappingProxyType(dict(values))
