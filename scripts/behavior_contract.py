#!/usr/bin/env python3
"""Load and validate the public behavior contract without runtime data access."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


_DOMAINS = (
    "meal",
    "water",
    "weight",
    "pantry",
    "transaction",
    "report",
    "system",
)
_MODES = frozenset({"read", "mutation", "maintenance", "derived_file"})
_CONFIRMATIONS = frozenset(
    {"none", "conditional", "workflow_handle", "required_true"}
)
_RETRIES = frozenset({"safe_read", "operation_receipt", "no_blind_retry"})


@dataclass(frozen=True)
class ActionContract:
    mode: str
    confirmation: str
    retry: str
    python_test: str
    typescript_test: str


@dataclass(frozen=True)
class ContractFinding:
    code: str
    severity: str
    message: str


def load_behavior_contract(
    root: Path,
) -> dict[str, dict[str, ActionContract]]:
    path = Path(root) / "contracts" / "public-behavior.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("public behavior schema_version must be 1")
    domains = raw.get("domains")
    if not isinstance(domains, dict):
        raise ValueError("public behavior domains must be a mapping")
    return {
        str(domain): {
            str(action): ActionContract(**_metadata(metadata))
            for action, metadata in _actions(actions).items()
        }
        for domain, actions in domains.items()
    }


def validate_behavior_contract(root: Path) -> tuple[ContractFinding, ...]:
    root = Path(root)
    path = root / "contracts" / "public-behavior.yaml"
    if not path.is_file():
        return (
            ContractFinding(
                "MISSING_BEHAVIOR_CONTRACT",
                "error",
                "contracts/public-behavior.yaml is required",
            ),
        )

    try:
        contract = load_behavior_contract(root)
        _validate_metadata(contract)
        actual = _python_actions(root)
    except (OSError, SyntaxError, TypeError, ValueError, yaml.YAMLError) as error:
        return (
            ContractFinding(
                "INVALID_BEHAVIOR_CONTRACT",
                "error",
                "Unable to validate public behavior contract: "
                f"{type(error).__name__}: {error}",
            ),
        )

    declared = {
        domain: frozenset(actions)
        for domain, actions in contract.items()
    }
    if declared != actual:
        details = []
        for domain in sorted(set(declared) | set(actual)):
            declared_actions = declared.get(domain, frozenset())
            actual_actions = actual.get(domain, frozenset())
            if declared_actions != actual_actions:
                details.append(
                    f"{domain}: missing={sorted(actual_actions - declared_actions)}, "
                    f"extra={sorted(declared_actions - actual_actions)}"
                )
        return (
            ContractFinding(
                "BEHAVIOR_ACTION_DRIFT",
                "error",
                "Public behavior contract differs from Python actions; "
                + "; ".join(details),
            ),
        )
    return _validate_test_references(root, contract)


def _metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("action metadata must be a mapping")
    expected = {
        "mode",
        "confirmation",
        "retry",
        "python_test",
        "typescript_test",
    }
    if set(value) != expected or not all(
        isinstance(item, str) for item in value.values()
    ):
        raise ValueError(
            "action metadata must contain exactly the five string fields"
        )
    return dict(value)


def _actions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("domain actions must be a mapping")
    return value


def _validate_metadata(
    contract: dict[str, dict[str, ActionContract]],
) -> None:
    if tuple(contract) != _DOMAINS:
        raise ValueError(
            "domains must be ordered as meal, water, weight, pantry, "
            "transaction, report, system"
        )
    for domain, actions in contract.items():
        if not actions:
            raise ValueError(f"{domain} must declare at least one action")
        for action, item in actions.items():
            if not action:
                raise ValueError(f"{domain} contains an empty action")
            if item.mode not in _MODES:
                raise ValueError(f"{domain}.{action} has invalid mode")
            if item.confirmation not in _CONFIRMATIONS:
                raise ValueError(
                    f"{domain}.{action} has invalid confirmation"
                )
            if item.retry not in _RETRIES:
                raise ValueError(f"{domain}.{action} has invalid retry")
            _validate_test_path(
                item.python_test,
                "tests/",
                ".py",
                f"{domain}.{action}.python_test",
            )
            _validate_test_path(
                item.typescript_test,
                "src-tests/",
                ".ts",
                f"{domain}.{action}.typescript_test",
            )


def _validate_test_path(
    value: str,
    prefix: str,
    suffix: str,
    label: str,
) -> None:
    base, separator, node_id = value.partition("::")
    path = PurePosixPath(base)
    if (
        not base.startswith(prefix)
        or not base.endswith(suffix)
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in base
        or (
            separator
            and (
                suffix != ".py"
                or not node_id.startswith("test_")
                or not node_id.replace("_", "").isalnum()
            )
        )
        or value.count("::") > 1
    ):
        raise ValueError(f"{label} is not a safe project-relative path")


def _python_actions(root: Path) -> dict[str, frozenset[str]]:
    source = (
        root / "python" / "personal_diet_pantry" / "service.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    result: dict[str, frozenset[str]] = {}
    expected_names = {
        f"_{domain.upper()}_ACTIONS": domain for domain in _DOMAINS
    }
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in expected_names:
            continue
        if not isinstance(node.value, ast.Dict):
            raise ValueError(f"{target.id} must be a dictionary literal")
        keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(
                key.value, str
            ):
                raise ValueError(f"{target.id} keys must be string literals")
            keys.append(key.value)
        result[expected_names[target.id]] = frozenset(keys)
    if set(result) != set(_DOMAINS):
        raise ValueError("unable to extract all seven Python action domains")
    return {domain: result[domain] for domain in _DOMAINS}


def _validate_test_references(
    root: Path,
    contract: dict[str, dict[str, ActionContract]],
) -> tuple[ContractFinding, ...]:
    findings: list[ContractFinding] = []
    checked: set[tuple[str, str]] = set()
    for domain, actions in contract.items():
        for action, item in actions.items():
            for kind, reference in (
                ("Python", item.python_test),
                ("TypeScript", item.typescript_test),
            ):
                key = (kind, reference)
                if key in checked:
                    continue
                checked.add(key)
                base, _, node_id = reference.partition("::")
                path = root / PurePosixPath(base)
                exists = path.is_file()
                if exists and kind == "Python":
                    exists = bool(node_id) and _python_test_exists(
                        path,
                        node_id,
                    )
                if not exists:
                    findings.append(
                        ContractFinding(
                            "MISSING_BEHAVIOR_TEST",
                            "error",
                            (
                                f"{kind} behavior test does not exist for "
                                f"{domain}.{action}: {reference}"
                            ),
                        )
                    )
    return tuple(findings)


def _python_test_exists(path: Path, node_id: str) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == node_id
        for node in tree.body
    )
