from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = PROJECT_ROOT / "skills" / "personal-diet-pantry" / "SKILL.md"


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_ordinary_pantry_add_does_not_require_production_or_expiry_dates() -> None:
    skill = _skill()

    for phrase in (
        "普通入库不强制生产日期或保质期",
        "根据食物类别推断冷藏、冷冻或常温",
        "从入库日估算到期日",
        "回执必须标明这是估算",
        "用户明确事实始终覆盖默认值",
    ):
        assert phrase in skill

    assert "If a required expiry fact was missing" not in skill
    assert "pantry supplement -> preview_add -> pure confirmation -> commit_add" not in skill


def test_explicit_weighing_without_a_unit_defaults_to_kg_and_records_directly() -> None:
    skill = _skill()

    assert "明确说“刚称了106.8”时合理默认 kg" in skill
    assert "直接记录，不要求重复确认" in skill
    assert "完全孤立的数字不能写体重" in skill


def test_unique_same_session_meal_correction_does_not_reenter_preview() -> None:
    skill = _skill()

    assert "成功写入返回的唯一餐食句柄直接 `update`" in skill
    assert "同一会话没有唯一句柄时，只查询一次候选" in skill
    assert "later `commit_record` after preview confirmation" not in skill
