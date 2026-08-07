from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    PROJECT_ROOT
    / "skills"
    / "personal-diet-pantry"
    / "SKILL.md"
)


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_routes_explicit_weight_and_free_status_to_one_direct_write() -> None:
    skill = _skill()

    assert "`diet_weight`" in skill
    assert (
        '`diet_weight(action="record", weight=104.6, unit="kg", '
        'status_note="空腹")`'
    ) in skill
    assert "体重写入需要明确的称重语义或重量单位" in skill
    assert "完全孤立的数字不能写体重" in skill


def test_skill_uses_only_system_time_and_never_supplies_measurement_time() -> None:
    skill = _skill()

    assert "测量时间由工具读取系统当前时间" in skill
    assert "`measured_at` 不是公共参数" in skill
    assert "不要根据用户文字补传时间" in skill


def test_skill_shapes_optional_average_and_trend_reply() -> None:
    skill = _skill()

    assert "7日均值：" in skill
    assert "趋势：7日均下降 ⬇️0.5 kg" in skill
    assert "没有 `trend` 时省略趋势行" in skill
    assert "不要展示工具名" in skill
    assert "数据库 ID、事务 ID 或工作流句柄" in skill
