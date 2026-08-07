from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = PROJECT_ROOT / "skills" / "personal-diet-pantry" / "SKILL.md"


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_ordinary_pantry_add_does_not_require_production_or_expiry_dates() -> None:
    skill = _skill()

    for phrase in (
        "Production date and expiry are optional for ordinary pantry intake",
        "infer storage location from the food category",
        "estimate expiry from the intake date",
        "mark both inferred facts as estimates",
        "explicit user facts always override those defaults",
    ):
        assert phrase in skill

    assert "If a required expiry fact was missing" not in skill
    assert "pantry supplement -> preview_add -> pure confirmation -> commit_add" not in skill


def test_explicit_weighing_without_a_unit_defaults_to_kg_and_records_directly() -> None:
    skill = _skill()

    assert "Explicit weighing wording plus a plausible number defaults to kilograms" in skill
    assert "record it directly without asking for the unit" in skill
    assert "A bare number without explicit body-weight wording" in skill


def test_unique_same_session_meal_correction_does_not_reenter_preview() -> None:
    skill = _skill()

    assert "correct a uniquely identified recorded meal" in skill
    assert "`diet_meal update` directly" in skill
    assert "later `commit_record` after preview confirmation" not in skill
