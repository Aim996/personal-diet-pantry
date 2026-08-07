from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_validator() -> ModuleType:
    path = PROJECT_ROOT / "scripts" / "validate_skill.py"
    assert path.is_file(), "validate_skill.py must exist"
    spec = importlib.util.spec_from_file_location("validate_skill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_skill"] = module
    spec.loader.exec_module(module)
    return module


def _skill(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_skill_validator_accepts_utf8_frontmatter(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    skill = _skill(
        tmp_path / "skill" / "SKILL.md",
        (
            "---\n"
            "name: 食序管家\n"
            "description: 记录餐食与库存\n"
            "---\n\n"
            "# 食序管家\n"
        ),
    )

    assert validator.validate_skill(skill) == ()


def test_skill_validator_rejects_missing_or_malformed_frontmatter(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    missing = _skill(
        tmp_path / "missing" / "SKILL.md",
        "# Missing\n",
    )
    malformed = _skill(
        tmp_path / "malformed" / "SKILL.md",
        "---\nname: valid\n---\n# Missing description\n",
    )

    assert any(
        item.severity == "error"
        for item in validator.validate_skill(missing)
    )
    assert any(
        item.severity == "error"
        for item in validator.validate_skill(malformed)
    )


def test_skill_validator_rejects_missing_relative_reference(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    skill = _skill(
        tmp_path / "skill" / "SKILL.md",
        (
            "---\n"
            "name: example\n"
            "description: Example skill\n"
            "---\n\n"
            "Read [the guide](references/missing.md).\n"
        ),
    )

    findings = validator.validate_skill(skill)

    assert [item.code for item in findings] == [
        "MISSING_SKILL_REFERENCE"
    ]


def test_skill_validator_warns_over_500_lines(
    tmp_path: Path,
) -> None:
    validator = _load_validator()
    skill = _skill(
        tmp_path / "skill" / "SKILL.md",
        (
            "---\n"
            "name: long-skill\n"
            "description: Long but structurally valid\n"
            "---\n"
            + "\n".join(f"line {index}" for index in range(501))
            + "\n"
        ),
    )

    findings = validator.validate_skill(skill)

    assert [item.code for item in findings] == [
        "SKILL_TOO_LONG"
    ]
    assert findings[0].severity == "warning"


def test_current_skill_is_structurally_valid() -> None:
    validator = _load_validator()
    skill = (
        PROJECT_ROOT
        / "skills"
        / "personal-diet-pantry"
        / "SKILL.md"
    )

    findings = validator.validate_skill(skill)

    assert not [
        item for item in findings if item.severity == "error"
    ]
    assert validator.main([str(skill)]) == 0
