from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.generate_tool_contracts import (
    generated_outputs,
    load_default_actions,
    load_skill_routes,
    load_tool_contract,
)


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ROUTES = {
    "meal_record": ("meal", "record"),
    "cooking_record": ("meal", "record_cooking"),
    "prepared_meal_record": ("meal", "record_prepared"),
    "water_record": ("water", "record"),
    "weight_record": ("weight", "record"),
    "pantry_search": ("pantry", "search"),
    "pantry_add": ("pantry", "add"),
    "pantry_deduct": ("pantry", "deduct"),
    "pantry_discard": ("pantry", "discard"),
    "recent_operations": ("transaction", "get_recent"),
    "undo": ("transaction", "undo"),
    "redo": ("transaction", "redo"),
    "daily_progress": ("report", "progress"),
    "self_check": ("system", "self_check"),
}


def test_tools_contract_is_the_complete_seven_domain_action_source() -> None:
    contract = load_tool_contract(ROOT / "contracts" / "tools.yaml")

    assert tuple(contract) == (
        "meal",
        "water",
        "weight",
        "pantry",
        "transaction",
        "report",
        "system",
    )
    assert sum(len(domain.actions) for domain in contract.values()) == 75
    assert contract["system"].actions["restore"].confirmation == "required_true"
    assert contract["meal"].actions["record"].handler == "_meal_record"
    assert contract["pantry"].actions["search"].handler == "_pantry_search"
    assert contract["weight"].tool == "diet_weight"


def test_daily_surface_includes_the_pantry_add_preview_commit_seam() -> None:
    contract_path = ROOT / "contracts" / "tools.yaml"
    contract = load_tool_contract(contract_path)
    actions = load_default_actions(contract_path, contract)

    assert sum(len(values) for values in actions.values()) == 42
    assert "preview_add" in actions["pantry"]
    assert "commit_add" in actions["pantry"]


def test_generated_contract_files_are_current() -> None:
    outputs = generated_outputs(ROOT)

    assert set(outputs) == {
        ROOT / "contracts" / "public-behavior.yaml",
        ROOT / "src" / "generated" / "tool-contracts.ts",
        ROOT
        / "python"
        / "personal_diet_pantry"
        / "generated_tool_contracts.py",
        ROOT / "docs" / "GENERATED-ACTIONS.zh-CN.md",
    }
    for path, expected in outputs.items():
        assert path.read_text(encoding="utf-8") == expected


def test_formal_mutations_come_from_tools_contract() -> None:
    contract = load_tool_contract(ROOT / "contracts" / "tools.yaml")
    mutations = {
        (domain_name, action_name)
        for domain_name, domain in contract.items()
        for action_name, action in domain.actions.items()
        if action.mode == "mutation"
    }

    assert ("meal", "record") in mutations
    assert ("weight", "record") in mutations
    assert ("system", "backup") not in mutations
    assert ("system", "maintenance_status") not in mutations


def test_skill_routes_target_the_expected_domain_actions() -> None:
    routes = load_skill_routes(ROOT / "contracts" / "tools.yaml")

    assert {
        name: (route.domain, route.action)
        for name, route in routes.items()
    } == EXPECTED_ROUTES


def test_skill_routes_reject_unknown_action_target(tmp_path: Path) -> None:
    contract_path = ROOT / "contracts" / "tools.yaml"
    malformed_path = tmp_path / "tools.yaml"
    malformed_path.write_text(
        contract_path.read_text(encoding="utf-8")
        .replace(
            "  self_check: {domain: system, action: self_check}",
            "  self_check: {domain: system, action: self_check}\n"
            "  invalid_route: {domain: meal, action: absent}",
        ),
        encoding="utf-8",
    )

    try:
        load_skill_routes(malformed_path)
    except ValueError as error:
        assert str(error) == "skill_routes.invalid_route targets an unknown action"
    else:
        raise AssertionError("unknown route target must be rejected")


def test_typescript_routes_quote_non_identifier_names(tmp_path: Path) -> None:
    source = (ROOT / "contracts" / "tools.yaml").read_text(encoding="utf-8")
    contract_path = tmp_path / "contracts" / "tools.yaml"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        source.replace("  meal_record:", "  meal-record:").replace(
            "  water_record:",
            "  1record:",
        ),
        encoding="utf-8",
    )

    routes = load_skill_routes(contract_path)
    assert "meal-record" in routes
    assert "1record" in routes

    output_path = tmp_path / "src" / "generated" / "tool-contracts.ts"
    output = generated_outputs(tmp_path)[output_path]
    syntax_check = subprocess.run(
        [
            "node",
            "-e",
            "const ts=require('typescript');let source='';"
            "process.stdin.setEncoding('utf8');"
            "process.stdin.on('data',chunk=>source+=chunk);"
            "process.stdin.on('end',()=>{"
            "const file=ts.createSourceFile('tool-contracts.ts',source,"
            "ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);"
            "if(file.parseDiagnostics.length){"
            "console.error(file.parseDiagnostics.map(d=>d.messageText).join('\\n'));"
            "process.exit(1);}});",
        ],
        cwd=ROOT,
        input=output,
        capture_output=True,
        text=True,
        check=False,
    )

    assert syntax_check.returncode == 0, syntax_check.stderr
    assert "  'meal-record': ['meal', 'record']" in output
    assert "  '1record': ['water', 'record']" in output
