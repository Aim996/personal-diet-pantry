from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    PROJECT_ROOT
    / "skills"
    / "personal-diet-pantry"
    / "SKILL.md"
)
REPLY_PATH = (
    PROJECT_ROOT
    / "skills"
    / "personal-diet-pantry"
    / "references"
    / "reply-style-and-error-boundaries.md"
)


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter(skill: str) -> dict[str, str]:
    marker, raw, _body = skill.split("---", maxsplit=2)
    assert marker == ""
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict)
    return loaded


def _activation(skill: str) -> str:
    start = skill.index("## 先理解，再行动")
    end = skill.index("## 能力地图")
    assert start < end
    return skill[start:end]


def test_frontmatter_discovers_natural_personal_diet_intent_without_name() -> None:
    skill = _skill()
    frontmatter = _frontmatter(skill)

    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "personal-diet-pantry"
    description = frontmatter["description"]
    assert description.startswith("Use when ")
    for phrase in (
        "meals",
        "nutritious drinks",
        "plain water",
        "cooking or leftovers",
        "pantry stock or expiry",
        "nutrition plans or reports",
        "goals or preferences",
        "undo/redo",
        "body weight",
        "diet_*",
        "without naming the Skill",
        "writing/translation",
        "generic knowledge",
        "image requests",
        "code/examples",
        "events explicitly not done",
    ):
        assert phrase in description


def test_activation_contract_precedes_readiness_and_applies_full_skill() -> None:
    activation = _activation(_skill())

    for phrase in (
        "整句话和可见上下文",
        "口语、错别字、省略主语、自然单位",
        "不是封闭短语表",
        "已经发生、计划、假设、否定、取消",
        "否定、没发生、未来计划和他人行为优先于",
        "保持零写入",
        "渠道变化不能成为绕过 Skill 的理由",
        "Telegram",
        "WebUI",
    ):
        assert phrase in activation


def test_activation_keeps_all_seven_typed_tool_domains() -> None:
    skill = _skill()

    for tool in (
        "diet_meal",
        "diet_water",
        "diet_weight",
        "diet_pantry",
        "diet_transaction",
        "diet_report",
        "diet_system",
    ):
        assert f"`{tool}`" in skill


def test_activation_routes_through_capability_scoped_readiness() -> None:
    skill = _skill()

    assert "## 能力地图" in skill
    assert "readiness is per required capability" in skill.casefold()
    assert "Use exactly one primary route" not in skill
    assert "all seven tools must exist" not in skill


def test_meal_time_defaults_use_the_trusted_clock_without_overriding_user_time() -> None:
    skill = _skill()

    for phrase in (
        "`record`、`preview_record`、`record_cooking`",
        "省略 `occurred_at`",
        "可信系统时钟",
        "用户提供了可解析时间才显式传时间",
        "不能伪造 `context.now`",
    ):
        assert phrase in skill


def test_bare_number_never_writes_body_weight_without_explicit_wording() -> None:
    skill = _skill()

    assert "完全孤立的数字不能写体重" in skill
    assert "应问单位或含义" in skill
    assert "否定、没发生、未来计划和他人行为优先于" in skill


def test_public_reply_contract_hides_internal_implementation() -> None:
    skill = _skill()
    reply = REPLY_PATH.read_text(encoding="utf-8")

    for phrase in (
        "不要展示工具名、内部诊断、路径、凭证、堆栈",
        "源码文件、数据库 ID、事务 ID 或工作流句柄",
    ):
        assert phrase in skill
    for phrase in (
        "Do not narrate tool selection, handlers, scaling mechanics, retries",
        "avoid command syntax and engineering jargon",
    ):
        assert phrase in reply


def test_triggering_change_does_not_grow_the_runtime_skill() -> None:
    assert len(_skill().splitlines()) <= 724


def test_clear_count_intake_and_unique_correction_are_direct_but_vague_intake_is_not() -> None:
    skill = _skill()

    for phrase in (
        "一个玉米、一根火腿肠",
        "清楚、已发生且信息足够的单一事实，直接写入",
        "自然计数、计量对象、可食重量和估算标记",
        "一点、一些、几口、几粒、一小把",
        "保持零业务写入",
        "成功写入返回的唯一餐食句柄直接 `update`",
        "不要删除重建，也不要尝试多套参数",
        "只发送 `nutrition_facts` 或 `nutrition_estimate` 其中一个",
        "纯水保持简洁",
        "“吃了个玉米”保留 `1个`",
        "不把玉米芯算成摄入",
        "带芯总重",
        "可食重量",
        "可食部（玉米粒）约90克（估算）",
        "完整 `portion_expression`",
        "插件不会根据食物名称自行补出可食部标签",
        "A/B 级标签或数据库事实用 `nutrition_facts`",
        "C/D 级估算用 `nutrition_estimate`",
        "逐条编号回答",
        "外观相同的记录仍是独立事实",
    ):
        assert phrase in skill

    assert "If the wording has no safe mapping, ask one short question" not in skill
    normalized_skill = " ".join(skill.casefold().split())
    for phrase in (
        "餐次和地点只是分析标签",
        "不应阻止清楚的摄入事实",
        "未给出则省略",
        "`other` 和 `unknown`",
    ):
        assert phrase.casefold() in normalized_skill


def test_v073_routes_inventory_consumption_through_the_meal_transaction() -> None:
    skill = _skill()

    for phrase in (
        "普通入库不强制生产日期或保质期",
        "吃掉库存食品或营养饮料",
        "`diet_pantry deduct` 只用于明确的非食用消耗",
        "食用加工剩菜必须使用真实的 `prepared_food_handle`",
        "日历到期日时优先使用 `expiry_date`",
        "`inventory_match_handle`",
        "`nutrition_mode: \"summary\"`",
        "保留用户的包装数量和单位",
        "`amount: 1`、`unit: 盒`",
        "成功搜索后不要再调用 `diet_pantry query`",
        "不再额外调用 Pantry 扣减",
        "未知营养字段保持缺失",
        "`hydration_ml`",
        "不得发送 `hydration`",
    ):
        assert phrase in skill


def test_current_meal_deletion_is_not_routed_as_historical_transaction_undo() -> None:
    skill = _skill()

    for phrase in (
        "整条当前餐食删除不是事务撤销",
        "已验证的同会话 `meal_handle`",
        "查询一次候选后再操作",
        "不能遍历旧事务或猜目标",
    ):
        assert phrase in skill


def test_v073_recovery_and_inventory_rules_are_explicit() -> None:
    skill = _skill()

    for phrase in (
        "多个物理批次不是商品歧义",
        "包装显示单位的换算交给工具完成",
        "优先使用 `expiry_date`",
        "相同失败指纹不能原样重试",
    ):
        assert phrase in skill
