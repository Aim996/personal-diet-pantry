from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from personal_diet_pantry import reports
from personal_diet_pantry.report_localization import resolve_report_locale
from personal_diet_pantry.service import DietService


def _set_language(service: DietService, language: str) -> None:
    service.settings = replace(
        service.settings,
        profile=replace(service.settings.profile, language=language),
    )


def _build_daily(service: DietService) -> Path:
    result = service.dispatch(
        {
            "domain": "report",
            "action": "daily",
            "payload": {"report_date": "2026-07-29"},
        }
    )
    assert result["ok"] is True
    return service.data_paths.reports / "daily" / "2026-07-29.md"


def test_zh_profile_generates_chinese_daily_report(
    service: DietService,
) -> None:
    text = _build_daily(service).read_text(encoding="utf-8")

    assert text.startswith("# 每日报告")
    assert "营养与饮水" in text
    assert "数据质量" in text
    assert "下一步建议" in text
    assert "Daily Report" not in text
    assert "Personalized Next Steps" not in text


def test_unsupported_language_falls_back_to_english(
    service: DietService,
) -> None:
    _set_language(service, "fr-FR")

    text = _build_daily(service).read_text(encoding="utf-8")

    assert text.startswith("# Daily Report")
    assert resolve_report_locale("fr-FR").code == "en"


def test_locale_boundary_owns_metric_labels_and_messages() -> None:
    zh = resolve_report_locale("zh-CN")
    en = resolve_report_locale("en")

    assert zh.code == "zh-CN"
    assert zh.metric_labels["sodium"] == "钠"
    assert zh.text("no_rows") == "_无。_"
    assert en.metric_labels["sodium"] == "Sodium"


def test_failed_template_render_does_not_replace_previous_report(
    service: DietService,
    tmp_path: Path,
) -> None:
    destination = _build_daily(service)
    before = destination.read_bytes()
    broken_templates = tmp_path / "templates"
    chinese = broken_templates / "zh-CN"
    chinese.mkdir(parents=True)
    (chinese / "daily-report.md").write_text(
        "# {{TITLE}}\n\n{{UNKNOWN_PLACEHOLDER}}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        reports.build_daily_report(
            service.connection,
            service.data_paths,
            service.settings,
            date(2026, 7, 29),
            templates_dir=broken_templates,
        )

    assert destination.read_bytes() == before
