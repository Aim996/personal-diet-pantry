#!/usr/bin/env python3
"""Validate the packaged Skill without user-global dependencies."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import yaml


_MARKDOWN_LINK = re.compile(
    r"\[[^\]]*\]\((?P<target>[^)\s]+)"
    r"(?:\s+[\"'][^\"']*[\"'])?\)"
)


@dataclass(frozen=True)
class SkillFinding:
    code: str
    severity: str
    message: str


def validate_skill(skill_path: Path) -> tuple[SkillFinding, ...]:
    """Return deterministic structural findings for one SKILL.md."""

    path = Path(skill_path).resolve()
    findings: list[SkillFinding] = []
    try:
        source = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        return (
            SkillFinding(
                "SKILL_UNREADABLE",
                "error",
                f"Unable to read Skill: {type(error).__name__}",
            ),
        )

    frontmatter = _frontmatter(source)
    if frontmatter is None:
        findings.append(
            SkillFinding(
                "INVALID_SKILL_FRONTMATTER",
                "error",
                "SKILL.md must start with YAML frontmatter",
            )
        )
    else:
        try:
            metadata = yaml.safe_load(frontmatter)
        except yaml.YAMLError:
            metadata = None
        if (
            not isinstance(metadata, dict)
            or not _nonempty_text(metadata.get("name"))
            or not _nonempty_text(metadata.get("description"))
        ):
            findings.append(
                SkillFinding(
                    "INVALID_SKILL_FRONTMATTER",
                    "error",
                    (
                        "Skill frontmatter requires nonempty name and "
                        "description"
                    ),
                )
            )

    for reference in sorted(set(_relative_references(source))):
        target = (path.parent / reference).resolve()
        try:
            target.relative_to(path.parent)
        except ValueError:
            findings.append(
                SkillFinding(
                    "UNSAFE_SKILL_REFERENCE",
                    "error",
                    f"Skill reference escapes its package: {reference}",
                )
            )
            continue
        if not target.is_file():
            findings.append(
                SkillFinding(
                    "MISSING_SKILL_REFERENCE",
                    "error",
                    f"Skill reference does not exist: {reference}",
                )
            )

    line_count = len(source.splitlines())
    if line_count > 500:
        findings.append(
            SkillFinding(
                "SKILL_TOO_LONG",
                "warning",
                f"SKILL.md has {line_count} physical lines",
            )
        )
    return tuple(findings)


def _frontmatter(source: str) -> str | None:
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        return None
    return "\n".join(lines[1:end])


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _relative_references(source: str) -> tuple[str, ...]:
    references: list[str] = []
    for match in _MARKDOWN_LINK.finditer(source):
        raw = unquote(match.group("target"))
        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc or raw.startswith("#"):
            continue
        candidate = parsed.path.replace("\\", "/")
        if not candidate or candidate.startswith("/"):
            continue
        references.append(candidate)
    return tuple(references)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "skill",
        nargs="?",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "skills"
            / "personal-diet-pantry"
            / "SKILL.md"
        ),
    )
    arguments = parser.parse_args(argv)
    findings = validate_skill(arguments.skill)
    print(
        json.dumps(
            {
                "status": (
                    "error"
                    if any(item.severity == "error" for item in findings)
                    else "warning"
                    if findings
                    else "pass"
                ),
                "findings": [asdict(item) for item in findings],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(any(item.severity == "error" for item in findings))


if __name__ == "__main__":
    raise SystemExit(main())
