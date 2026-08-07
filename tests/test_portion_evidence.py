from decimal import Decimal

from personal_diet_pantry.portion_evidence import (
    inherit_previous_portion_expression,
    normalize_portion_expression,
)


def test_edible_estimate_is_made_explicit_without_food_name_rules() -> None:
    assert normalize_portion_expression(
        portion_expression="1个",
        amount=Decimal("1"),
        unit="个",
        consumed_weight_g=Decimal("90"),
        source_text="吃了个玉米",
        quantity_estimated=False,
        nutrition_source="中国食物成分表常见估算：煮甜玉米可食部",
        nutrition_uncertainty="可食部（玉米粒）约90克，为估算值",
    ) == "1个｜可食部（玉米粒）约90克（估算）"


def test_user_exact_weight_keeps_edible_object_but_removes_estimate() -> None:
    assert normalize_portion_expression(
        portion_expression="1个 80克（估算）",
        amount=Decimal("1"),
        unit="个",
        consumed_weight_g=Decimal("80"),
        source_text="其实是80克",
        quantity_estimated=True,
        nutrition_source="中国食物成分表常见估算：煮甜玉米可食部",
        nutrition_uncertainty="可食部（玉米粒）约80克，为估算值",
    ) == "1个｜可食部（玉米粒）80克"


def test_exact_correction_keeps_one_stale_edible_label_without_keeping_old_weight() -> None:
    assert normalize_portion_expression(
        portion_expression="1个｜可食部（玉米粒）约90克（估算）",
        amount=Decimal("1"),
        unit="个",
        consumed_weight_g=Decimal("80"),
        source_text="其实是80克",
        quantity_estimated=True,
        nutrition_source="中国食物成分表常见估算：煮甜玉米可食部",
        nutrition_uncertainty="可食部（玉米粒）约90克，为估算值",
    ) == "1个｜可食部（玉米粒）80克"


def test_exact_correction_does_not_choose_between_conflicting_edible_labels() -> None:
    assert normalize_portion_expression(
        portion_expression="1份 80克（估算）",
        amount=Decimal("1"),
        unit="份",
        consumed_weight_g=Decimal("80"),
        source_text="其实是80克",
        quantity_estimated=True,
        nutrition_source="可食部（果肉）约90克",
        nutrition_uncertainty="可食部（果仁）约90克",
    ) == "1份 80克"


def test_user_approximate_weight_remains_an_estimate() -> None:
    assert normalize_portion_expression(
        portion_expression="1个 80克（估算）",
        amount=Decimal("1"),
        unit="个",
        consumed_weight_g=Decimal("80"),
        source_text="其实大概80克",
        quantity_estimated=True,
        nutrition_source="常见食物估算",
        nutrition_uncertainty="可食部（果肉）约80克，为估算值",
    ) == "1个｜可食部（果肉）约80克（估算）"


def test_exact_generic_count_weight_drops_only_the_old_quantity_estimate() -> None:
    assert normalize_portion_expression(
        portion_expression="1根 80克（估算）",
        amount=Decimal("1"),
        unit="根",
        consumed_weight_g=Decimal("80"),
        source_text="刚才那根其实是80克",
        quantity_estimated=True,
        nutrition_source="常见火腿肠每100克估算",
        nutrition_uncertainty="品牌和配方会有差异",
    ) == "1根 80克"


def test_no_safe_edible_evidence_does_not_invent_a_food_part() -> None:
    assert normalize_portion_expression(
        portion_expression="1盒",
        amount=Decimal("1"),
        unit="盒",
        consumed_weight_g=Decimal("250"),
        source_text="吃了一盒",
        quantity_estimated=False,
        nutrition_source="包装标签",
        nutrition_uncertainty=None,
    ) == "1盒"


def test_exact_weight_only_correction_inherits_committed_measurement_object() -> None:
    assert inherit_previous_portion_expression(
        portion_expression="80克",
        previous_portion_expression="1个｜可食部（玉米粒）约90克（估算）",
        consumed_weight_g=Decimal("80"),
        source_text="其实是80克",
    ) == "1个｜可食部（玉米粒）约90克（估算）"


def test_explicit_total_weight_never_inherits_old_edible_object() -> None:
    assert inherit_previous_portion_expression(
        portion_expression="带芯总重80克",
        previous_portion_expression="1个｜可食部（玉米粒）约90克（估算）",
        consumed_weight_g=Decimal("80"),
        source_text="其实是带芯总重80克",
    ) == "带芯总重80克"


def test_approximate_correction_does_not_inherit_as_exact() -> None:
    assert inherit_previous_portion_expression(
        portion_expression="大概80克",
        previous_portion_expression="1个｜可食部（玉米粒）约90克（估算）",
        consumed_weight_g=Decimal("80"),
        source_text="其实大概80克",
    ) == "大概80克"
