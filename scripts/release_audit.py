#!/usr/bin/env python3
"""Audit release consistency without reading or modifying runtime data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Mapping

try:
    from behavior_contract import (
        load_behavior_contract,
        validate_behavior_contract,
    )
except ModuleNotFoundError:
    from scripts.behavior_contract import (
        load_behavior_contract,
        validate_behavior_contract,
    )


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class AuditResult:
    root: Path
    version: str | None
    findings: tuple[AuditFinding, ...]
    checks: Mapping[str, Any]

    @property
    def status(self) -> str:
        if any(item.severity == "error" for item in self.findings):
            return "error"
        if self.findings:
            return "warning"
        return "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root": str(self.root),
            "version": self.version,
            "checks": dict(self.checks),
            "findings": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                }
                for item in self.findings
            ],
        }


def audit_project(root: Path) -> AuditResult:
    """Return deterministic release findings for one source package."""

    root = Path(root).resolve()
    findings: list[AuditFinding] = []
    versions = _versions(root, findings)
    canonical_version = versions.get("package.json")
    product_version = _product_version(root, findings)
    if versions and len(set(versions.values())) > 1:
        findings.append(
            AuditFinding(
                "VERSION_DRIFT",
                "error",
                "package.json, pyproject.toml, and openclaw.plugin.json "
                "must declare the same version",
            )
        )

    python_tests = tuple(sorted((root / "tests").rglob("test_*.py")))
    ts_test_root = root / "src-tests"
    ts_tests = tuple(
        sorted(
            path
            for pattern in ("*.test.ts", "*.spec.ts")
            for path in ts_test_root.rglob(pattern)
        )
    )
    if not python_tests:
        findings.append(
            AuditFinding(
                "MISSING_PYTHON_TESTS",
                "error",
                "The source package has no Python test files",
            )
        )
    if not ts_tests:
        findings.append(
            AuditFinding(
                "MISSING_TS_TESTS",
                "error",
                "The source package has no TypeScript test files",
            )
        )

    excluded = _archive_exclusions(root, findings)
    excluded_tests = sorted({"tests", "src-tests"} & excluded)
    if excluded_tests:
        findings.append(
            AuditFinding(
                "SOURCE_ARCHIVE_EXCLUDES_TESTS",
                "error",
                "The reproducible source archive excludes: "
                + ", ".join(excluded_tests),
            )
        )

    action_contracts = _tool_actions(root, findings)
    for domain, contract in action_contracts.items():
        ts_actions = frozenset(contract["typescript"])
        python_actions = frozenset(contract["python"])
        if ts_actions != python_actions:
            findings.append(
                AuditFinding(
                    "ACTION_CONTRACT_DRIFT",
                    "error",
                    f"{domain} actions differ; "
                    f"TypeScript-only={sorted(ts_actions - python_actions)}, "
                    f"Python-only={sorted(python_actions - ts_actions)}",
                )
            )

    public_behavior_contract = _public_behavior_contract(
        root,
        action_contracts,
        findings,
    )
    dependency_risk_acceptance = _dependency_risk_acceptance(
        root,
        findings,
    )
    release_pipeline = _release_pipeline(root, findings)

    release_version = _release_version(root, findings)
    if (
        product_version is not None
        and release_version is not None
        and release_version != product_version
    ):
        findings.append(
            AuditFinding(
                "RELEASE_VERSION_DRIFT",
                "error",
                f"RELEASE.zh-CN.md describes v{release_version}, "
                f"but package.json productVersion is v{product_version}",
            )
        )

    skill_path = root / "skills" / "personal-diet-pantry" / "SKILL.md"
    skill_lines = _line_count(skill_path, findings, "SKILL.md")
    if skill_lines is not None and skill_lines > 500:
        findings.append(
            AuditFinding(
                "SKILL_TOO_LONG",
                "warning",
                f"SKILL.md has {skill_lines} physical lines; split optional "
                "guidance into references before adding more behavior",
            )
        )

    severity_rank = {"error": 0, "warning": 1}
    findings.sort(key=lambda item: (severity_rank[item.severity], item.code))
    return AuditResult(
        root=root,
        version=canonical_version,
        findings=tuple(findings),
        checks={
            "versions": versions,
            "product_version": product_version,
            "python_test_files": len(python_tests),
            "typescript_test_files": len(ts_tests),
            "source_archive_exclusions": sorted(excluded),
            "action_contracts": action_contracts,
            "public_behavior_contract": public_behavior_contract,
            "dependency_risk_acceptance": dependency_risk_acceptance,
            "release_pipeline": release_pipeline,
            "release_document_version": release_version,
            "skill_physical_lines": skill_lines,
        },
    )


def _versions(
    root: Path,
    findings: list[AuditFinding],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        versions["package.json"] = str(
            json.loads((root / "package.json").read_text(encoding="utf-8"))[
                "version"
            ]
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        findings.append(
            AuditFinding(
                "PACKAGE_VERSION_UNREADABLE",
                "error",
                f"Unable to read package.json version: {type(error).__name__}",
            )
        )
    try:
        versions["openclaw.plugin.json"] = str(
            json.loads(
                (root / "openclaw.plugin.json").read_text(encoding="utf-8")
            )["version"]
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        findings.append(
            AuditFinding(
                "PLUGIN_VERSION_UNREADABLE",
                "error",
                "Unable to read openclaw.plugin.json version: "
                f"{type(error).__name__}",
            )
        )
    try:
        versions["pyproject.toml"] = str(
            tomllib.loads(
                (root / "pyproject.toml").read_text(encoding="utf-8")
            )["project"]["version"]
        )
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        findings.append(
            AuditFinding(
                "PYPROJECT_VERSION_UNREADABLE",
                "error",
                f"Unable to read pyproject.toml version: {type(error).__name__}",
            )
        )
    return versions


def _product_version(
    root: Path,
    findings: list[AuditFinding],
) -> str | None:
    try:
        value = json.loads(
            (root / "package.json").read_text(encoding="utf-8")
        )["productVersion"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("productVersion must be a non-empty string")
        return value
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        findings.append(
            AuditFinding(
                "PRODUCT_VERSION_UNREADABLE",
                "error",
                "Unable to read package.json productVersion: "
                f"{type(error).__name__}",
            )
        )
        return None


def _archive_exclusions(
    root: Path,
    findings: list[AuditFinding],
) -> frozenset[str]:
    path = root / "scripts" / "reproducible_archive.py"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        findings.append(
            AuditFinding(
                "SOURCE_ARCHIVE_POLICY_UNREADABLE",
                "error",
                f"Unable to read source archive policy: {type(error).__name__}",
            )
        )
        return frozenset()
    match = re.search(
        r"_ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES\s*=\s*frozenset\s*\("
        r"(?P<body>.*?)\)\s*",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        findings.append(
            AuditFinding(
                "SOURCE_ARCHIVE_POLICY_UNREADABLE",
                "error",
                "Unable to locate the source archive exclusion policy",
            )
        )
        return frozenset()
    return frozenset(
        re.findall(r"""["']([^"']+)["']""", match.group("body"))
    )


def _tool_actions(
    root: Path,
    findings: list[AuditFinding],
) -> dict[str, dict[str, object]]:
    domains = (
        "meal",
        "water",
        "weight",
        "pantry",
        "transaction",
        "report",
        "system",
    )
    schema_markers = {
        domain: f"export const {domain.title()}ParametersSchema"
        for domain in domains
    }
    schema_end_markers = {
        domain: schema_markers[domains[index + 1]]
        if index + 1 < len(domains)
        else "export const PluginConfigSchema"
        for index, domain in enumerate(domains)
    }
    service_markers = {
        domain: f"_{domain.upper()}_ACTIONS = {{"
        for domain in domains
    }
    service_end_markers = {
        domain: service_markers[domains[index + 1]]
        if index + 1 < len(domains)
        else "_IMPLEMENTED_ACTION_MAPS ="
        for index, domain in enumerate(domains)
    }
    try:
        schema_source = (root / "src" / "schemas.ts").read_text(
            encoding="utf-8"
        )
        service_source = (
            root / "python" / "personal_diet_pantry" / "service.py"
        ).read_text(encoding="utf-8")
        contracts: dict[str, dict[str, object]] = {}
        for domain in domains:
            schema_block = _between(
                schema_source,
                schema_markers[domain],
                schema_end_markers[domain],
            )
            service_block = _between(
                service_source,
                service_markers[domain],
                service_end_markers[domain],
            )
            ts_actions = sorted(
                set(re.findall(r'actionBranch\(\s*"([^"]+)"', schema_block))
                | set(
                    re.findall(
                        rf'{domain}TargetAction\(\s*"([^"]+)"',
                        schema_block,
                    )
                )
            )
            python_actions = sorted(
                set(
                    re.findall(
                        r'^\s*"([^"]+)"\s*:',
                        service_block,
                        flags=re.MULTILINE,
                    )
                )
            )
            contracts[domain] = {
                "typescript": ts_actions,
                "python": python_actions,
                "match": ts_actions == python_actions,
            }
        return contracts
    except (OSError, ValueError) as error:
        findings.append(
            AuditFinding(
                "ACTION_CONTRACTS_UNREADABLE",
                "error",
                f"Unable to extract tool actions: {type(error).__name__}",
            )
        )
        return {}


def _between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def _public_behavior_contract(
    root: Path,
    action_contracts: Mapping[str, Mapping[str, object]],
    findings: list[AuditFinding],
) -> dict[str, object]:
    validation_findings = validate_behavior_contract(root)
    findings.extend(
        AuditFinding(item.code, item.severity, item.message)
        for item in validation_findings
    )
    result: dict[str, object] = {
        "schema_version": None,
        "action_count": 0,
        "matches_typescript": False,
        "matches_python": False,
    }
    if validation_findings:
        return result

    contract = load_behavior_contract(root)
    declared = {
        domain: frozenset(actions)
        for domain, actions in contract.items()
    }
    typescript = {
        domain: frozenset(
            str(action) for action in item.get("typescript", [])
        )
        for domain, item in action_contracts.items()
    }
    python = {
        domain: frozenset(str(action) for action in item.get("python", []))
        for domain, item in action_contracts.items()
    }
    matches_typescript = declared == typescript
    matches_python = declared == python
    if not matches_typescript or not matches_python:
        findings.append(
            AuditFinding(
                "BEHAVIOR_ACTION_DRIFT",
                "error",
                "Public behavior contract must match both TypeScript and "
                "Python public action inventories",
            )
        )
    result.update(
        {
            "schema_version": 1,
            "action_count": sum(len(actions) for actions in contract.values()),
            "matches_typescript": matches_typescript,
            "matches_python": matches_python,
        }
    )
    return result


def _release_version(
    root: Path,
    findings: list[AuditFinding],
) -> str | None:
    path = root / "RELEASE.zh-CN.md"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        findings.append(
            AuditFinding(
                "RELEASE_DOCUMENT_UNREADABLE",
                "error",
                f"Unable to read RELEASE.zh-CN.md: {type(error).__name__}",
            )
        )
        return None
    match = re.search(
        r"^#.*?\bv?(\d+(?:\.\d+){2,}(?:-[0-9A-Za-z.-]+)?)\b",
        source,
        flags=re.MULTILINE,
    )
    if match is None:
        findings.append(
            AuditFinding(
                "RELEASE_VERSION_UNREADABLE",
                "error",
                "The release document heading has no semantic version",
            )
        )
        return None
    return match.group(1)


def _dependency_risk_acceptance(
    root: Path,
    findings: list[AuditFinding],
) -> dict[str, object]:
    path = root / "contracts" / "dependency-risk-acceptance.json"
    result = {"schema_version": None, "accepted_count": 0}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("accepted"), list)
        ):
            raise ValueError("invalid dependency acceptance shape")
        required = {
            "advisory_id",
            "package",
            "severity",
            "dependency_path",
            "isolation_basis",
            "reviewed_on",
            "review_deadline",
        }
        for index, record in enumerate(value["accepted"]):
            if (
                not isinstance(record, dict)
                or set(record) != required
                or not all(
                    isinstance(item, str) and item.strip()
                    for item in record.values()
                )
            ):
                raise ValueError(
                    f"invalid accepted dependency record {index}"
                )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        findings.append(
            AuditFinding(
                "INVALID_DEPENDENCY_RISK_ACCEPTANCE",
                "error",
                (
                    "Unable to validate dependency risk acceptance: "
                    f"{type(error).__name__}"
                ),
            )
        )
        return result
    result.update(
        {
            "schema_version": 1,
            "accepted_count": len(value["accepted"]),
        }
    )
    return result


def _release_pipeline(
    root: Path,
    findings: list[AuditFinding],
) -> dict[str, bool]:
    files = {
        "ci/verify.ps1": root / "ci" / "verify.ps1",
        "scripts/build_release.py": (
            root / "scripts" / "build_release.py"
        ),
        "scripts/validate_dependency_audit.py": (
            root / "scripts" / "validate_dependency_audit.py"
        ),
    }
    status = {name: path.is_file() for name, path in files.items()}
    missing = [name for name, exists in status.items() if not exists]
    if missing:
        findings.append(
            AuditFinding(
                "MISSING_RELEASE_PIPELINE",
                "error",
                "Release pipeline is missing: " + ", ".join(missing),
            )
        )
    return status


def _line_count(
    path: Path,
    findings: list[AuditFinding],
    label: str,
) -> int | None:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError as error:
        findings.append(
            AuditFinding(
                "SKILL_UNREADABLE",
                "error",
                f"Unable to read {label}: {type(error).__name__}",
            )
        )
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    result = audit_project(arguments.root)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
