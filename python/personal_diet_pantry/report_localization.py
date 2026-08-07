"""Centralized language resources for generated Markdown reports."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ReportLocale:
    code: str
    metric_labels: Mapping[str, str]
    messages: Mapping[str, str]

    def text(self, key: str, **values: object) -> str:
        return self.messages[key].format(**values)

    def list_text(self, values: Sequence[str]) -> str:
        if not values:
            return ""
        if self.code == "zh-CN":
            return "、".join(values)
        if len(values) == 1:
            return values[0]
        if len(values) == 2:
            return " and ".join(values)
        return ", ".join(values[:-1]) + f", and {values[-1]}"


_EN = ReportLocale(
    code="en",
    metric_labels=MappingProxyType(
        {
            "calories": "Calories",
            "protein": "Protein",
            "fat": "Fat",
            "carbohydrate": "Carbohydrate",
            "fiber": "Fiber",
            "sodium": "Sodium",
            "water": "Water",
        }
    ),
    messages=MappingProxyType(
        {
            "title_daily": "Daily Report — {identifier}",
            "title_weekly": (
                "Weekly Report — {identifier} ({start} to {end})"
            ),
            "title_monthly": "Monthly Report — {identifier}",
            "no_rows": "_None._",
            "known_lower_bound_qualifier": (
                " (known lower bound; incomplete meal nutrition)"
            ),
            "metric_confirmed": (
                "- {label}: {current} / {goal} {unit}{qualifier}"
            ),
            "metric_unconfirmed": (
                "- {label}: {current} {unit}{qualifier}"
            ),
            "water_confirmed": "- Water: {current} / {goal} ml",
            "water_unconfirmed": "- Water: {current} ml",
            "targets_unconfirmed": (
                "- Configured targets are not user-confirmed."
            ),
            "known_minimum": (
                "- Known minimum: yes ({count} incomplete meal records)"
            ),
            "nutrition_unknown": "unknown {unit} {label}",
            "nutrition_known": "{value} {unit} {label}",
            "meal_line": (
                "- {occurred_at} — {meal_type} — {source_text} "
                "({location_type}) — {nutrition}"
            ),
            "water_line": "- {occurred_at} — {amount_ml} ml — {source_text}",
            "batch_suffix": " ({batch_code})",
            "reason_suffix": " — {reason}",
            "movement_line": (
                "- {created_at} — {movement_type} {quantity} {unit} "
                "{food_name}{batch}{reason}"
            ),
            "expiring_line": (
                "- {expires_at} — {food_name} (batch {batch_code}) — "
                "{quantity} {unit} remaining"
            ),
            "unlabelled_batch": "unlabelled",
            "weak_line": (
                "- {occurred_at} — {raw_name} — grade {source_grade}{details}"
            ),
            "details_suffix": " — {details}",
            "pending_line": (
                "- {occurred_at} — {raw_name} — "
                "pending link needs confirmation"
            ),
            "advice_no_activity": (
                "- Log a meal or water entry when convenient so the next "
                "report can offer more tailored next steps."
            ),
            "advice_unknown": (
                "- Treat {labels} totals as known lower bounds and complete "
                "the missing meal nutrition before evaluating those totals."
            ),
            "advice_hydration": (
                "- Add water entries gradually through the next period to "
                "move closer to your hydration goal."
            ),
            "advice_deviation": (
                "- Review your {label} plan for the next period and adjust "
                "it toward the confirmed goal."
            ),
            "advice_expiring": (
                "- Plan to use or check {count} expiring pantry {noun} soon."
            ),
            "expiring_noun_one": "batch",
            "expiring_noun_many": "batches",
            "advice_pending_weak": (
                "- Review pending pantry links and weak-grade meal estimates "
                "before relying on them."
            ),
            "advice_active_rule": (
                "- Keep your active {category} in mind when planning the "
                "next period."
            ),
            "advice_continue": (
                "- Continue logging meals and water consistently, then "
                "review the next report for trends."
            ),
            "rule_food_alias": "food alias",
            "rule_portion": "portion preference",
            "rule_meal_type": "meal timing preference",
            "rule_inventory_link": "pantry preference",
            "rule_preference": "personal preference",
        }
    ),
)


_ZH_CN = ReportLocale(
    code="zh-CN",
    metric_labels=MappingProxyType(
        {
            "calories": "热量",
            "protein": "蛋白质",
            "fat": "脂肪",
            "carbohydrate": "碳水化合物",
            "fiber": "膳食纤维",
            "sodium": "钠",
            "water": "饮水",
        }
    ),
    messages=MappingProxyType(
        {
            "title_daily": "每日报告 — {identifier}",
            "title_weekly": "每周报告 — {identifier}（{start} 至 {end}）",
            "title_monthly": "每月报告 — {identifier}",
            "no_rows": "_无。_",
            "known_lower_bound_qualifier": "（已知下限；餐次营养不完整）",
            "metric_confirmed": (
                "- {label}：{current} / {goal} {unit}{qualifier}"
            ),
            "metric_unconfirmed": "- {label}：{current} {unit}{qualifier}",
            "water_confirmed": "- 饮水：{current} / {goal} ml",
            "water_unconfirmed": "- 饮水：{current} ml",
            "targets_unconfirmed": "- 配置目标尚未由用户确认。",
            "known_minimum": "- 已知下限：是（{count} 条餐次营养不完整）",
            "nutrition_unknown": "{label}未知（{unit}）",
            "nutrition_known": "{label} {value} {unit}",
            "meal_line": (
                "- {occurred_at} — {meal_type} — {source_text}"
                "（{location_type}）— {nutrition}"
            ),
            "water_line": "- {occurred_at} — {amount_ml} ml — {source_text}",
            "batch_suffix": "（批次 {batch_code}）",
            "reason_suffix": " — {reason}",
            "movement_line": (
                "- {created_at} — {movement_type} {quantity} {unit} "
                "{food_name}{batch}{reason}"
            ),
            "expiring_line": (
                "- {expires_at} — {food_name}（批次 {batch_code}）— "
                "剩余 {quantity} {unit}"
            ),
            "unlabelled_batch": "未标记",
            "weak_line": (
                "- {occurred_at} — {raw_name} — 来源等级 "
                "{source_grade}{details}"
            ),
            "details_suffix": " — {details}",
            "pending_line": (
                "- {occurred_at} — {raw_name} — 库存关联待确认"
            ),
            "advice_no_activity": (
                "- 方便时记录一餐或一次饮水，下一份报告才能给出更贴合"
                "当前数据的建议。"
            ),
            "advice_unknown": (
                "- 将{labels}视为已知下限；补齐缺失的餐次营养后，再评估"
                "这些汇总值。"
            ),
            "advice_hydration": (
                "- 下一周期可分次补记饮水，逐步接近已确认的补水目标。"
            ),
            "advice_deviation": (
                "- 检查下一周期的{label}安排，并向已确认目标调整。"
            ),
            "advice_expiring": "- 尽快使用或检查 {count} 个临期库存批次。",
            "expiring_noun_one": "批次",
            "expiring_noun_many": "批次",
            "advice_pending_weak": (
                "- 在依赖相关数据前，先检查待确认的库存关联和低等级营养"
                "估算。"
            ),
            "advice_active_rule": (
                "- 规划下一周期时，继续考虑已启用的{category}。"
            ),
            "advice_continue": (
                "- 继续稳定记录餐食与饮水，并在下一份报告中观察趋势。"
            ),
            "rule_food_alias": "食物别名规则",
            "rule_portion": "份量偏好",
            "rule_meal_type": "用餐时间偏好",
            "rule_inventory_link": "库存偏好",
            "rule_preference": "个人偏好",
        }
    ),
)


_LOCALES = MappingProxyType({"en": _EN, "zh-CN": _ZH_CN})


def resolve_report_locale(language: str) -> ReportLocale:
    return _LOCALES.get(language, _LOCALES["en"])
