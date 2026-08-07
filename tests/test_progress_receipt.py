from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from personal_diet_pantry.meals import MealItem, MealRecord, QuantityEstimate
from personal_diet_pantry.progress import ProgressMetric
from personal_diet_pantry.progress_receipt import (
    _meal_item_summary,
    render_meal_receipt,
    render_progress_block,
    render_water_receipt,
)


def _metric(
    key: str,
    label: str,
    emoji: str,
    current: str,
    target: str,
    unit: str,
    *,
    increment: str | None = None,
    percent: int,
    bar: str,
    completeness: str = "complete",
    unknown_item_count: int = 0,
) -> ProgressMetric:
    return ProgressMetric(
        key=key,
        label=label,
        emoji=emoji,
        current=Decimal(current),
        target=Decimal(target),
        unit=unit,
        goal_type="maximum" if key in {"calories", "fat", "carbohydrate"} else "minimum",
        increment=Decimal(increment) if increment is not None else None,
        has_unknown=completeness != "complete",
        over_by=None,
        percent=percent,
        bar=bar,
        completeness=completeness,
        unknown_item_count=unknown_item_count,
    )


def test_complete_progress_uses_the_fixed_six_metric_two_line_template() -> None:
    metrics = (
        _metric("calories", "热量", "🔥", "1954.3", "1900", "kcal", increment="111.3", percent=103, bar="██████████"),
        _metric("protein", "蛋白", "🥩", "135.7", "170", "g", increment="26.1", percent=80, bar="████████░░"),
        _metric("fat", "脂肪", "🧈", "85.59", "55", "g", increment="0.09", percent=156, bar="██████████"),
        _metric("carbohydrate", "碳水", "🌾", "163.81", "150", "g", increment="1.11", percent=109, bar="██████████"),
        _metric("fiber", "纤维", "🥬", "12", "30", "g", percent=40, bar="████░░░░░░"),
        _metric("water", "饮水", "💧", "0", "3000", "ml", percent=0, bar="░░░░░░░░░░"),
    )

    assert render_progress_block(metrics) == "\n".join(
        (
            "🔥 热量 ██████████ 103%",
            "🔥1954.3 / 1900 kcal +111.3kcal +6%",
            "🥩 蛋白 ████████░░ 80%",
            "🥩135.7 / 170 g +26.1g +15%",
            "🧈 脂肪 ██████████ 156%",
            "🧈85.59 / 55 g +0.09g +<1%",
            "🌾 碳水 ██████████ 109%",
            "🌾163.81 / 150 g +1.11g +<1%",
            "🥬 纤维 ████░░░░░░ 40%",
            "🥬12 / 30 g",
            "💧 饮水 ░░░░░░░░░░ 0%",
            "💧0ml / 3L",
        )
    )


def test_pure_water_receipt_selects_only_the_water_metric() -> None:
    metrics = (
        _metric(
            "calories",
            "热量",
            "🔥",
            "710",
            "1900",
            "kcal",
            percent=37,
            bar="████░░░░░░",
        ),
        _metric(
            "protein",
            "蛋白",
            "🥩",
            "26.8",
            "170",
            "g",
            percent=16,
            bar="██░░░░░░░░",
        ),
        _metric(
            "water",
            "饮水",
            "💧",
            "137",
            "3000",
            "ml",
            increment="137",
            percent=5,
            bar="█░░░░░░░░░",
        ),
    )

    assert render_water_receipt(137, metrics=metrics) == (
        "已记录！饮水 137ml\n\n"
        "💧 饮水 █░░░░░░░░░ 5%\n"
        "💧137ml / 3L +137ml +5%"
    )


def test_partial_metric_never_presents_missing_nutrition_as_zero() -> None:
    metric = _metric(
        "calories",
        "热量",
        "🔥",
        "900",
        "1900",
        "kcal",
        percent=47,
        bar="█████░░░░░",
        completeness="partial",
        unknown_item_count=1,
    )

    assert render_progress_block((metric,)) == (
        "🔥 热量 █████░░░░░ ≥47%（部分未知）\n"
        "🔥已知至少 900 / 1900 kcal；另有 1 项未知"
    )


def test_estimated_count_meal_receipt_exposes_the_estimated_edible_weight() -> None:
    item = MealItem(
        raw_name="玉米",
        normalized_name="corn",
        amount=Decimal("1"),
        unit="个",
        consumed_weight_g=Decimal("90"),
        consumed_volume_ml=None,
        consumed_servings=None,
        raw_weight_g=None,
        inventory_deduction_weight_g=None,
        edible_ratio=None,
        cooking_yield=None,
        calories=Decimal("88"),
        protein=Decimal("3.3"),
        fat=Decimal("1.4"),
        carbohydrate=Decimal("19"),
        fiber=Decimal("2.2"),
        sodium=None,
        hydration_ml=None,
        source_grade="estimated",
        nutrition_source="standard food estimate",
        uncertainty="size varies",
        confidence=Decimal("0.7"),
        inventory_action="none",
        deductions=(),
        quantity_estimate=None,
        portion_expression="1个｜可食部（玉米粒）约90克（估算）",
    )
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    meal = MealRecord(
        occurred_at=now,
        meal_type="snack",
        source_text="吃了个玉米",
        location_type="home",
        items=(item,),
        total_calories=Decimal("88"),
        total_protein=Decimal("3.3"),
        total_fat=Decimal("1.4"),
        total_carbohydrate=Decimal("19"),
        total_fiber=Decimal("2.2"),
        total_sodium=None,
        total_hydration_ml=None,
        source_grade="estimated",
        confidence=Decimal("0.7"),
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    receipt = render_meal_receipt(meal, inventory_effects=(), metrics=())

    assert receipt == "已记录！玉米 1个｜可食部（玉米粒）约90克（估算）｜88 kcal"


def test_legacy_short_portion_expression_keeps_count_weight_fallback() -> None:
    class LegacyItem:
        raw_name = "玉米"
        portion_expression = "一个"
        amount = Decimal("1")
        unit = "个"
        consumed_weight_g = Decimal("90")
        quantity_estimate = object()

    assert _meal_item_summary(LegacyItem()) == "玉米 1个 90克（估算）"


def test_exact_count_meal_receipt_keeps_both_count_and_weight() -> None:
    item = MealItem(
        raw_name="火腿肠",
        normalized_name="sausage",
        amount=Decimal("1"),
        unit="根",
        consumed_weight_g=Decimal("80"),
        consumed_volume_ml=None,
        consumed_servings=None,
        raw_weight_g=None,
        inventory_deduction_weight_g=None,
        edible_ratio=None,
        cooking_yield=None,
        calories=Decimal("135.68"),
        protein=Decimal("6.4"),
        fat=Decimal("10.24"),
        carbohydrate=Decimal("3.84"),
        fiber=Decimal("0"),
        sodium=None,
        hydration_ml=None,
        source_grade="estimated",
        nutrition_source="standard food estimate",
        uncertainty="brand varies",
        confidence=Decimal("0.8"),
        inventory_action="none",
        deductions=(),
        quantity_estimate=None,
    )
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    meal = MealRecord(
        occurred_at=now,
        meal_type="snack",
        source_text="刚才那根火腿肠其实是80克",
        location_type="home",
        items=(item,),
        total_calories=Decimal("135.68"),
        total_protein=Decimal("6.4"),
        total_fat=Decimal("10.24"),
        total_carbohydrate=Decimal("3.84"),
        total_fiber=Decimal("0"),
        total_sodium=None,
        total_hydration_ml=None,
        source_grade="estimated",
        confidence=Decimal("0.8"),
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    receipt = render_meal_receipt(meal, inventory_effects=(), metrics=())

    assert receipt == "已记录！火腿肠 1根 80克｜135.68 kcal"


def test_progress_percent_is_not_artificially_capped_at_999() -> None:
    from personal_diet_pantry.progress import _percent

    assert _percent(Decimal("1200"), Decimal("100")) == 1200
