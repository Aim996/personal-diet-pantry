from pathlib import Path

import yaml

from personal_diet_pantry.service import ACTIONS
from scripts.behavior_contract import (
    load_behavior_contract,
    validate_behavior_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_behavior_contract_covers_every_public_action_once() -> None:
    contract = load_behavior_contract(ROOT)
    actual = {domain: set(actions) for domain, actions in ACTIONS.items()}
    declared = {
        domain: set(actions)
        for domain, actions in contract.items()
    }
    assert declared == actual


def test_every_action_declares_operational_semantics() -> None:
    contract = load_behavior_contract(ROOT)
    for domain, actions in contract.items():
        for action, item in actions.items():
            assert item.mode in {"read", "mutation", "maintenance", "derived_file"}
            assert item.confirmation in {
                "none", "conditional", "workflow_handle", "required_true"
            }
            assert item.retry in {
                "safe_read", "operation_receipt", "no_blind_retry"
            }
            assert item.python_test.startswith("tests/")
            assert item.typescript_test.startswith("src-tests/")


def test_invalid_schema_has_stable_finding_code(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "public-behavior.yaml").write_text(
        "schema_version: 2\ndomains: {}\n",
        encoding="utf-8",
    )

    findings = validate_behavior_contract(tmp_path)

    assert [item.code for item in findings] == [
        "INVALID_BEHAVIOR_CONTRACT"
    ]


def test_python_action_drift_has_stable_finding_code(
    tmp_path: Path,
) -> None:
    metadata = {
        "mode": "read",
        "confirmation": "none",
        "retry": "safe_read",
        "python_test": "tests/contracts/test_contract.py",
        "typescript_test": "src-tests/all-actions-schema.test.ts",
    }
    domains = {
        domain: {"declared": dict(metadata)}
        for domain in (
                "meal",
                "water",
                "weight",
                "pantry",
            "transaction",
            "report",
            "system",
        )
    }
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "public-behavior.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "domains": domains},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    package = tmp_path / "python" / "personal_diet_pantry"
    package.mkdir(parents=True)
    (package / "service.py").write_text(
        "\n".join(
            f"_{domain.upper()}_ACTIONS = {{'actual': object()}}"
            for domain in domains
        ),
        encoding="utf-8",
    )

    findings = validate_behavior_contract(tmp_path)

    assert [item.code for item in findings] == ["BEHAVIOR_ACTION_DRIFT"]
