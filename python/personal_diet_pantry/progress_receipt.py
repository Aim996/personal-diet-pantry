"""Deterministic user-facing receipts built from committed ledger snapshots."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence

from .meals import MealRecord
from .prepared_foods import InventoryEffect
from .progress import ProgressMetric


def render_meal_receipt(
    meal: MealRecord,
    *,
    inventory_effects: Sequence[InventoryEffect],
    metrics: Sequence[ProgressMetric],
    goals_confirmed: bool = True,
    verb: str = "记录",
) -> str:
    """Render one meal commit without asking the model to reconstruct facts."""

    item_text = "、".join(_meal_item_summary(item) for item in meal.items)
    nutrition_text = (
        f"｜{_number(meal.total_calories)} kcal"
        if meal.total_calories is not None
        else "｜营养待补充"
    )
    sections = [f"已{verb}！{item_text}{nutrition_text}"]
    inventory_text = _inventory_summary(inventory_effects)
    if inventory_text:
        sections.append(inventory_text)
    sections.append(_progress_or_goal_notice(metrics, goals_confirmed=goals_confirmed))
    return "\n\n".join(section for section in sections if section)


def render_water_receipt(
    amount_ml: int,
    *,
    metrics: Sequence[ProgressMetric],
    goals_confirmed: bool = True,
    verb: str = "记录",
) -> str:
    sections = [f"已{verb}！饮水 {_number(Decimal(amount_ml))}ml"]
    water_metrics = tuple(metric for metric in metrics if metric.key == "water")
    sections.append(
        _progress_or_goal_notice(water_metrics, goals_confirmed=goals_confirmed)
    )
    return "\n\n".join(section for section in sections if section)


def render_progress_block(metrics: Sequence[ProgressMetric]) -> str:
    """Render every metric as exactly two lines in the fixed legacy layout."""

    lines: list[str] = []
    for metric in metrics:
        if metric.target is None:
            continue
        lines.extend(_metric_lines(metric))
    return "\n".join(lines)


def _progress_or_goal_notice(
    metrics: Sequence[ProgressMetric], *, goals_confirmed: bool
) -> str:
    if not goals_confirmed:
        return "目标尚未确认，本次已记录；暂不生成进度条。"
    return render_progress_block(metrics)


def _metric_lines(metric: ProgressMetric) -> tuple[str, str]:
    target = metric.target
    if target is None:
        raise ValueError("confirmed progress metric requires a target")
    unknown_count = max(metric.unknown_item_count, 1 if metric.has_unknown else 0)
    if metric.completeness == "unknown":
        return (
            f"{metric.emoji} {metric.label} ░░░░░░░░░░ 未知",
            f"{metric.emoji}未知 / {_display_target(target, metric.unit)}；{unknown_count} 项未知",
        )
    if metric.completeness == "partial" or metric.has_unknown:
        percent_text = f"≥{metric.percent or 0}%"
        return (
            f"{metric.emoji} {metric.label} {metric.bar or '░░░░░░░░░░'} {percent_text}（部分未知）",
            (
                f"{metric.emoji}已知至少 {_number(metric.current or Decimal('0'))} / "
                f"{_display_target(target, metric.unit)}；另有 {unknown_count} 项未知"
            ),
        )
    return (
        f"{metric.emoji} {metric.label} {metric.bar or '░░░░░░░░░░'} {metric.percent or 0}%",
        _complete_value_line(metric, target),
    )


def _complete_value_line(metric: ProgressMetric, target: Decimal) -> str:
    current = metric.current or Decimal("0")
    if metric.unit == "ml":
        value_text = (
            f"{_display_water(current, prefer_liters=False)} / "
            f"{_display_water(target, prefer_liters=True)}"
        )
    else:
        value_text = f"{_number(current)} / {_number(target)} {metric.unit}"
    increment_text = _increment_text(metric.increment, target, metric.unit)
    return f"{metric.emoji}{value_text}{increment_text}"


def _increment_text(
    increment: Decimal | None, target: Decimal, unit: str
) -> str:
    if increment is None or increment == 0:
        return ""
    sign = "+" if increment > 0 else "-"
    magnitude = abs(increment)
    ratio = magnitude * 100 / target if target > 0 else Decimal("0")
    if Decimal("0") < ratio < Decimal("1"):
        percent_text = "<1%"
    else:
        percent_text = (
            f"{int(ratio.quantize(Decimal('1'), rounding=ROUND_HALF_UP))}%"
        )
    return f" {sign}{_number(magnitude)}{unit} {sign}{percent_text}"


def _meal_item_summary(item) -> str:
    portion_expression = getattr(item, "portion_expression", None)
    if isinstance(portion_expression, str):
        normalized_expression = portion_expression.strip()
        has_explicit_measure = re.search(
            r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百半两约]+)\s*"
            r"(?:千克|公斤|克|kg|g|毫升|ml)",
            normalized_expression,
            flags=re.IGNORECASE,
        )
        if normalized_expression and (
            "可食部" in normalized_expression or has_explicit_measure is not None
        ):
            return f"{item.raw_name} {normalized_expression}"
    amount = ""
    if item.amount is not None:
        amount = f" {_number(item.amount)}{item.unit or ''}"
    elif item.consumed_weight_g is not None:
        amount = f" {_number(item.consumed_weight_g)}克"
    weight = ""
    if item.amount is not None and item.consumed_weight_g is not None:
        amount_is_same_weight = (
            (item.unit or "").casefold() in {"g", "克"}
            and item.amount == item.consumed_weight_g
        )
        if not amount_is_same_weight:
            weight = f" {_number(item.consumed_weight_g)}克"
    estimate_mark = "（估算）" if item.quantity_estimate is not None else ""
    return f"{item.raw_name}{amount}{weight}{estimate_mark}"


def _inventory_summary(effects: Iterable[InventoryEffect]) -> str:
    lines = []
    for effect in effects:
        quantity = f"{_number(effect.quantity)}{effect.unit}"
        if effect.direction == "decrease":
            remaining = (
                "已用完"
                if effect.cleared
                else f"剩 {_number(effect.remaining_quantity or Decimal('0'))}{effect.unit}"
            )
            lines.append(f"{effect.food_name}{remaining}（-{quantity}）")
        elif effect.direction == "increase":
            if not effect.prepared and effect.remaining_quantity is not None:
                remaining = (
                    f"剩 {_number(effect.remaining_quantity)}{effect.unit}"
                )
                lines.append(f"{effect.food_name}{remaining}（+{quantity}）")
            else:
                location = (
                    f"，{effect.storage_location}"
                    if effect.storage_location
                    else ""
                )
                lines.append(f"{effect.food_name}新增 {quantity}{location}")
    return f"📦 库存变动：{'；'.join(lines)}" if lines else ""


def _display_target(value: Decimal, unit: str) -> str:
    if unit == "ml":
        return _display_water(value, prefer_liters=True)
    return f"{_number(value)} {unit}"


def _display_water(value: Decimal, *, prefer_liters: bool) -> str:
    if prefer_liters and value >= 1000 and value % 1000 == 0:
        return f"{_number(value / 1000)}L"
    return f"{_number(value)}ml"


def _number(value: Decimal) -> str:
    normalized = value.normalize() if value != 0 else Decimal("0")
    return format(normalized, "f")
