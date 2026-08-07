#!/usr/bin/env python3
"""Deterministically validate progressive-disclosure Skill routing fixtures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PUBLIC_TOOLS = {
    "diet_meal",
    "diet_water",
    "diet_weight",
    "diet_pantry",
    "diet_transaction",
    "diet_report",
    "diet_system",
}
DOMAINS = {
    "meal",
    "water",
    "weight",
    "pantry",
    "transaction",
    "report",
    "system",
    "zero-write",
}
WRITE_EXPECTATIONS = {"zero", "read", "write", "preview"}


@dataclass(frozen=True)
class EvaluationResult:
    case_count: int
    passed_count: int
    domain_coverage: set[str]
    safety_score: float
    overall_score: float
    failures: tuple[str, ...]


def _string_list(value: Any, field: str, case_id: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{case_id}: {field} must be a list of strings")
    return value


def _contains_all(haystack: str, needles: list[str]) -> bool:
    normalized = haystack.casefold()
    return all(needle.casefold() in normalized for needle in needles)


def evaluate_skill(skill_root: Path, cases_path: Path) -> EvaluationResult:
    """Evaluate case schema, one-level routing, safety, and referenced contracts."""

    skill_root = Path(skill_root)
    main_path = skill_root / "SKILL.md"
    main = main_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8"))
    cases = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(cases, list):
        raise ValueError("evaluation file must contain a cases list")

    failures: list[str] = []
    passed = 0
    safety_total = 0
    safety_passed = 0
    seen_ids: set[str] = set()
    coverage: set[str] = set()

    for index, case in enumerate(cases, start=1):
        case_failures: list[str] = []
        if not isinstance(case, dict):
            failures.append(f"case-{index}: case must be a mapping")
            continue

        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            case_id = f"case-{index}"
            case_failures.append(f"{case_id}: id must be a non-empty string")
        elif case_id in seen_ids:
            case_failures.append(f"{case_id}: duplicate id")
        seen_ids.add(case_id)

        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            case_failures.append(f"{case_id}: prompt must be non-empty")

        domain = case.get("expected_domain")
        if domain not in DOMAINS:
            case_failures.append(f"{case_id}: unknown expected_domain {domain!r}")
        else:
            coverage.add(domain)

        expectation = case.get("write_expectation")
        if expectation not in WRITE_EXPECTATIONS:
            case_failures.append(f"{case_id}: invalid write_expectation {expectation!r}")

        try:
            allowed = set(_string_list(case.get("allowed_tools"), "allowed_tools", case_id))
            forbidden = set(
                _string_list(case.get("forbidden_tools"), "forbidden_tools", case_id)
            )
            reference_contains = _string_list(
                case.get("reference_contains"), "reference_contains", case_id
            )
            reply_contains = _string_list(
                case.get("reply_contains"), "reply_contains", case_id
            )
        except ValueError as error:
            case_failures.append(str(error))
            allowed, forbidden, reference_contains, reply_contains = set(), set(), [], []

        unknown_tools = (allowed | forbidden) - PUBLIC_TOOLS
        if unknown_tools:
            case_failures.append(f"{case_id}: unknown tools {sorted(unknown_tools)}")
        if allowed & forbidden:
            case_failures.append(f"{case_id}: allowed_tools overlap forbidden_tools")
        if not reference_contains:
            case_failures.append(f"{case_id}: reference_contains must not be empty")
        if not reply_contains:
            case_failures.append(f"{case_id}: reply_contains must not be empty")

        reference_name = case.get("expected_reference")
        if reference_name == "SKILL.md":
            reference_text = main
        elif isinstance(reference_name, str):
            reference_path = skill_root / "references" / reference_name
            route = f"(references/{reference_name})"
            if route not in main:
                case_failures.append(f"{case_id}: reference is not linked from SKILL.md")
            if reference_path.is_file():
                reference_text = reference_path.read_text(encoding="utf-8")
            else:
                reference_text = ""
                case_failures.append(f"{case_id}: reference does not exist")
        else:
            reference_text = ""
            case_failures.append(f"{case_id}: expected_reference must be a filename")

        if reference_contains and not _contains_all(reference_text, reference_contains):
            missing = [
                phrase
                for phrase in reference_contains
                if phrase.casefold() not in reference_text.casefold()
            ]
            case_failures.append(f"{case_id}: missing reference contract {missing}")

        is_safety_case = expectation == "zero" or bool(forbidden)
        if is_safety_case:
            safety_total += 1
            safe = not (allowed & forbidden)
            if expectation == "zero":
                safe = safe and not allowed and forbidden == PUBLIC_TOOLS
            if safe:
                safety_passed += 1
            else:
                case_failures.append(f"{case_id}: unsafe tool boundary")

        if case_failures:
            failures.extend(case_failures)
        else:
            passed += 1

    count = len(cases)
    return EvaluationResult(
        case_count=count,
        passed_count=passed,
        domain_coverage=coverage,
        safety_score=(safety_passed / safety_total) if safety_total else 1.0,
        overall_score=(passed / count) if count else 0.0,
        failures=tuple(failures),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_skill(args.skill, args.cases)
    print(
        f"{result.passed_count}/{result.case_count} cases; "
        f"safety={result.safety_score:.3f}; overall={result.overall_score:.3f}"
    )
    for failure in result.failures:
        print(f"- {failure}")
    return 0 if result.overall_score == 1 and result.safety_score == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
