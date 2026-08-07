"""Resolve meal nutrition without turning unknown values into zeroes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

from .nutrition import NutritionResult, weakest_grade


CORE_FIELDS = (
    "calories",
    "protein",
    "fat",
    "carbohydrate",
    "fiber",
    "sodium",
)


@dataclass(frozen=True)
class NutritionResolution:
    result: NutritionResult
    status: Literal["complete", "partial", "incomplete"]
    missing_fields: Sequence[str]


class NutritionEstimateRequired(ValueError):
    def __init__(self, item_index: int, missing_fields: Sequence[str]) -> None:
        super().__init__("complete nutrition estimate required")
        self.item_index = item_index
        self.missing_fields = tuple(missing_fields)


def merge_sources(*sources: NutritionResult | None) -> NutritionResolution:
    """Fill each field from the first source which knows it, in source order."""

    available = tuple(source for source in sources if source is not None)
    values: dict[str, Decimal | None] = {}
    used: list[NutritionResult] = []
    for field in (*CORE_FIELDS, "hydration_ml"):
        selected = next(
            (
                (source, getattr(source, field))
                for source in available
                if getattr(source, field) is not None
            ),
            None,
        )
        values[field] = selected[1] if selected is not None else None
        if selected is not None:
            used.append(selected[0])
    missing = tuple(field for field in CORE_FIELDS if values[field] is None)
    status: Literal["complete", "partial", "incomplete"] = (
        "complete" if not missing else "incomplete" if len(missing) == len(CORE_FIELDS) else "partial"
    )
    distinct_used = tuple(dict.fromkeys(used))
    return NutritionResolution(
        NutritionResult(
            **values,
            source=" + ".join(dict.fromkeys(source.source for source in distinct_used)) or "unresolved",
            source_grade=(
                weakest_grade(*(source.source_grade for source in distinct_used))
                if distinct_used
                else "D"
            ),
            uncertainty="; ".join(
                dict.fromkeys(
                    source.uncertainty
                    for source in distinct_used
                    if source.uncertainty is not None
                )
            ) or None,
        ),
        status,
        missing,
    )
