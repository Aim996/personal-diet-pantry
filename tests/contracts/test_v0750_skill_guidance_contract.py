from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_has_no_positive_phrase_router() -> None:
    assert not (ROOT / "src" / "direct-write-policy.ts").exists()
    index = (ROOT / "src" / "index.ts").read_text(encoding="utf-8")
    assert "classifyDirectWrite" not in index
    assert "directWriteInstruction" not in index


def test_skill_points_without_prescribing_a_closed_positive_route() -> None:
    skill = (
        ROOT / "skills" / "personal-diet-pantry" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "## 能力地图" in skill
    assert "普通正向输入" in skill
    assert "封闭短语表" in skill
    assert "Use exactly one primary route" not in skill
