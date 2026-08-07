from __future__ import annotations

from personal_diet_pantry.service import DietService


def _daily_report_text(service: DietService) -> str:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "daily",
            "payload": {"report_date": "2026-07-29"},
        }
    )
    assert result["ok"] is True
    return (
        service.data_paths.reports / "daily" / "2026-07-29.md"
    ).read_text(encoding="utf-8")


def test_generated_report_does_not_present_defaults_as_confirmed_goals(
    service: DietService,
) -> None:
    report = _daily_report_text(service)

    assert "配置目标尚未由用户确认。" in report
    assert " / 2000 kcal" not in report
    assert "补水目标" not in report
    assert "已确认目标" not in report


def test_generated_report_uses_targets_after_formal_confirmation(
    service: DietService,
) -> None:
    update = service.dispatch(
        {
            "domain": "system",
            "action": "update_goals",
            "payload": {
                "calories_kcal": 2100,
                "protein_g": 90,
                "fat_g": 65,
                "carbohydrate_g": 260,
                "fiber_g": 30,
                "sodium_mg": 2000,
                "water_ml": 2200,
                "timezone_name": "Asia/Shanghai",
                "source_text": "确认我的每日目标",
            },
        }
    )
    assert update["ok"] is True

    report = _daily_report_text(service)

    assert "配置目标尚未由用户确认。" not in report
    assert " / 2100 kcal" in report
