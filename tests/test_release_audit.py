from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    assert path.is_file(), f"{path.name} must exist"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _minimal_project(
    root: Path,
    *,
    package_version: str,
    pyproject_version: str,
    product_version: str | None = None,
) -> Path:
    root.mkdir()
    product_version = product_version or package_version
    (root / "package.json").write_text(
        json.dumps(
            {
                "version": package_version,
                "productVersion": product_version,
            }
        ),
        encoding="utf-8",
    )
    (root / "openclaw.plugin.json").write_text(
        json.dumps({"version": package_version}),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nversion = "{pyproject_version}"\n',
        encoding="utf-8",
    )
    (root / "RELEASE.zh-CN.md").write_text(
        f"# v{product_version} 发布说明\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "schemas.ts").write_text(
        'export const ReportParametersSchema = boundedActionUnion([\n'
        '  actionBranch("progress", {}),\n'
        "]);\n"
        "export const SystemParametersSchema = boundedActionUnion([]);\n",
        encoding="utf-8",
    )
    package = root / "python" / "personal_diet_pantry"
    package.mkdir(parents=True)
    (package / "service.py").write_text(
        '_REPORT_ACTIONS = {\n    "progress": handler,\n}\n'
        "_SYSTEM_ACTIONS = {}\n",
        encoding="utf-8",
    )
    skill = root / "skills" / "personal-diet-pantry"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "reproducible_archive.py").write_text(
        '_ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES = frozenset('
        '{"node_modules", "tests", "src-tests"})\n',
        encoding="utf-8",
    )
    return root


def test_audit_detects_missing_tests_and_version_drift(
    tmp_path: Path,
) -> None:
    release_audit = _load_script("release_audit")
    project = _minimal_project(
        tmp_path / "project",
        package_version="0.5.0",
        pyproject_version="0.6.0",
    )

    result = release_audit.audit_project(project)

    codes = {finding.code for finding in result.findings}
    assert {
        "VERSION_DRIFT",
        "MISSING_PYTHON_TESTS",
        "MISSING_TS_TESTS",
        "MISSING_BEHAVIOR_CONTRACT",
        "SOURCE_ARCHIVE_EXCLUDES_TESTS",
    } <= codes


def test_source_archive_policy_keeps_test_suites() -> None:
    archive = _load_script("reproducible_archive")

    assert "tests" not in archive._ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES
    assert "src-tests" not in archive._ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES


def test_audit_compares_release_heading_to_product_version(
    tmp_path: Path,
) -> None:
    release_audit = _load_script("release_audit")
    project = _minimal_project(
        tmp_path / "project",
        package_version="0.7.0",
        pyproject_version="0.7.0",
        product_version="0.7.0",
    )
    (project / "RELEASE.zh-CN.md").write_text(
        "# v0.6.1.5.2 发布说明\n",
        encoding="utf-8",
    )

    result = release_audit.audit_project(project)

    assert "RELEASE_VERSION_DRIFT" in {
        finding.code for finding in result.findings
    }


def test_candidate_has_no_release_audit_errors() -> None:
    release_audit = _load_script("release_audit")

    result = release_audit.audit_project(PROJECT_ROOT)

    assert [item.code for item in result.findings if item.severity == "error"] == []


def test_candidate_audits_all_seven_tool_action_contracts() -> None:
    release_audit = _load_script("release_audit")

    result = release_audit.audit_project(PROJECT_ROOT)
    contracts = result.checks["action_contracts"]

    assert set(contracts) == {
        "meal",
        "water",
        "weight",
        "pantry",
        "transaction",
        "report",
        "system",
    }
    assert all(contract["match"] is True for contract in contracts.values())
    behavior = result.checks["public_behavior_contract"]
    assert behavior["schema_version"] == 1
    assert behavior["action_count"] == 77
    assert behavior["matches_typescript"] is True
    assert behavior["matches_python"] is True


def test_audit_rejects_a_missing_behavior_test_reference(
    tmp_path: Path,
) -> None:
    release_audit = _load_script("release_audit")
    project = tmp_path / "project"
    shutil.copytree(
        PROJECT_ROOT,
        project,
        ignore=shutil.ignore_patterns(
            "node_modules",
            ".pytest_cache",
            ".superpowers",
            "__pycache__",
            "*.pyc",
        ),
    )
    contract_path = project / "contracts" / "public-behavior.yaml"
    source = contract_path.read_text(encoding="utf-8")
    contract_path.write_text(
        source.replace(
                (
                    "tests/contracts/test_meal_water_contracts.py::"
                    "test_meal_preview_commit_requires_real_handle"
                ),
            (
                "tests/contracts/missing_contract_test.py::"
                "test_missing"
            ),
            1,
        ),
        encoding="utf-8",
    )

    result = release_audit.audit_project(project)

    assert "MISSING_BEHAVIOR_TEST" in {
        finding.code for finding in result.findings
    }
