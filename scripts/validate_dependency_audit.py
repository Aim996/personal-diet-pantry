#!/usr/bin/env python3
"""Validate exact, time-bounded acceptance of development advisories."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any


_AUDITED_SEVERITIES = frozenset(
    {"low", "moderate", "high", "critical"}
)
_ADVISORY_ID = re.compile(r"^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$")
_ACCEPTANCE_FIELDS = frozenset(
    {
        "advisory_id",
        "package",
        "severity",
        "dependency_path",
        "isolation_basis",
        "reviewed_on",
        "review_deadline",
    }
)


@dataclass(frozen=True)
class DependencyFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class _Advisory:
    advisory_id: str
    package: str
    severity: str
    dependency_path: str

    @property
    def key(self) -> tuple[str, str]:
        return self.advisory_id, self.dependency_path


def validate_dependency_audit(
    audit_path: Path,
    acceptance_path: Path,
    *,
    today: date | None = None,
) -> tuple[DependencyFinding, ...]:
    """Return release-blocking findings for one npm audit and contract."""

    effective_today = today or date.today()
    try:
        audit = _object(
            json.loads(Path(audit_path).read_text(encoding="utf-8-sig")),
            "npm audit",
        )
        acceptance = _object(
            json.loads(
                Path(acceptance_path).read_text(encoding="utf-8-sig")
            ),
            "dependency acceptance",
        )
        advisories = _blocking_advisories(audit)
        accepted, contract_findings = _accepted_records(
            acceptance,
            today=effective_today,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return (
            DependencyFinding(
                "INVALID_DEPENDENCY_AUDIT",
                "error",
                (
                    "Unable to validate dependency audit evidence: "
                    f"{type(error).__name__}: {error}"
                ),
            ),
        )

    findings = list(contract_findings)
    advisory_by_key = {item.key: item for item in advisories}
    for advisory in advisories:
        record = accepted.get(advisory.key)
        if record is None:
            findings.append(
                DependencyFinding(
                    "UNACCEPTED_DEPENDENCY_ADVISORY",
                    "error",
                    (
                        f"{advisory.severity} {advisory.advisory_id} in "
                        f"{advisory.dependency_path} is not accepted"
                    ),
                )
            )
            continue
        if (
            record["package"] != advisory.package
            or record["severity"] != advisory.severity
        ):
            findings.append(
                DependencyFinding(
                    "DEPENDENCY_ACCEPTANCE_MISMATCH",
                    "error",
                    (
                        f"Acceptance metadata does not match "
                        f"{advisory.advisory_id} at "
                        f"{advisory.dependency_path}"
                    ),
                )
            )
    for key in sorted(set(accepted) - set(advisory_by_key)):
        findings.append(
            DependencyFinding(
                "STALE_DEPENDENCY_ACCEPTANCE",
                "error",
                (
                    f"Acceptance no longer matches the audit: "
                    f"{key[0]} at {key[1]}"
                ),
            )
        )
    return tuple(findings)


def _blocking_advisories(
    audit: dict[str, Any],
) -> tuple[_Advisory, ...]:
    vulnerabilities = _object(
        audit.get("vulnerabilities", {}),
        "vulnerabilities",
    )
    advisories: list[_Advisory] = []
    for package, raw in vulnerabilities.items():
        vulnerability = _object(raw, f"vulnerability {package}")
        nodes = vulnerability.get("nodes", [])
        if not isinstance(nodes, list) or not all(
            isinstance(node, str) and node
            for node in nodes
        ):
            raise ValueError(f"{package}.nodes must be nonempty paths")
        via = vulnerability.get("via", [])
        if not isinstance(via, list):
            raise ValueError(f"{package}.via must be an array")
        for raw_advisory in via:
            if not isinstance(raw_advisory, dict):
                continue
            severity = raw_advisory.get("severity")
            if severity not in _AUDITED_SEVERITIES:
                continue
            advisory_id = _advisory_id(raw_advisory)
            advisory_package = raw_advisory.get("name", package)
            if not isinstance(advisory_package, str):
                raise ValueError("advisory package must be text")
            advisories.extend(
                _Advisory(
                    advisory_id,
                    advisory_package,
                    severity,
                    node.replace("\\", "/"),
                )
                for node in nodes
            )
    unique = {item.key: item for item in advisories}
    return tuple(unique[key] for key in sorted(unique))


def _advisory_id(advisory: dict[str, Any]) -> str:
    url = advisory.get("url")
    candidate = (
        str(url).rstrip("/").rsplit("/", 1)[-1]
        if isinstance(url, str)
        else ""
    )
    if not _ADVISORY_ID.fullmatch(candidate):
        raise ValueError("blocking advisory must have a GHSA identifier")
    return candidate


def _accepted_records(
    acceptance: dict[str, Any],
    *,
    today: date,
) -> tuple[
    dict[tuple[str, str], dict[str, str]],
    tuple[DependencyFinding, ...],
]:
    if acceptance.get("schema_version") != 1:
        raise ValueError("acceptance schema_version must be 1")
    raw_records = acceptance.get("accepted")
    if not isinstance(raw_records, list):
        raise ValueError("accepted must be an array")
    records: dict[tuple[str, str], dict[str, str]] = {}
    findings: list[DependencyFinding] = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict) or set(raw) != _ACCEPTANCE_FIELDS:
            raise ValueError(
                f"accepted[{index}] must contain the exact contract fields"
            )
        if not all(
            isinstance(value, str) and value.strip()
            for value in raw.values()
        ):
            raise ValueError(
                f"accepted[{index}] fields must be nonempty text"
            )
        record = {key: str(value).strip() for key, value in raw.items()}
        if (
            not _ADVISORY_ID.fullmatch(record["advisory_id"])
            or record["severity"] not in _AUDITED_SEVERITIES
            or not record["dependency_path"].startswith("node_modules/")
            or "\\" in record["dependency_path"]
        ):
            raise ValueError(f"accepted[{index}] has invalid identifiers")
        reviewed = date.fromisoformat(record["reviewed_on"])
        deadline = date.fromisoformat(record["review_deadline"])
        if reviewed > today:
            raise ValueError(
                f"accepted[{index}].reviewed_on cannot be in the future"
            )
        if deadline < reviewed:
            raise ValueError(
                f"accepted[{index}].review_deadline precedes review"
            )
        if deadline < today:
            findings.append(
                DependencyFinding(
                    "EXPIRED_DEPENDENCY_ACCEPTANCE",
                    "error",
                    (
                        f"{record['advisory_id']} acceptance expired on "
                        f"{deadline.isoformat()}"
                    ),
                )
            )
        key = (record["advisory_id"], record["dependency_path"])
        if key in records:
            raise ValueError(f"duplicate acceptance for {key[0]} at {key[1]}")
        records[key] = record
    return records, tuple(findings)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--acceptance", required=True, type=Path)
    arguments = parser.parse_args(argv)
    findings = validate_dependency_audit(
        arguments.audit,
        arguments.acceptance,
    )
    print(
        json.dumps(
            {
                "status": "error" if findings else "pass",
                "findings": [asdict(item) for item in findings],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
