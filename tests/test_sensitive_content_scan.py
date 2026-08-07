from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script() -> ModuleType:
    path = PROJECT_ROOT / "scripts" / "scan_sensitive_content.py"
    assert path.is_file(), f"{path.name} must exist"
    spec = importlib.util.spec_from_file_location("scan_sensitive_content", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_sensitive_content"] = module
    spec.loader.exec_module(module)
    return module


def test_sensitive_content_scan_detects_credentials_and_runtime_data(
    tmp_path: Path,
) -> None:
    scanner = _load_script()
    (tmp_path / "README.md").write_text(
        "token=sk-" + "a" * 28,
        encoding="utf-8",
    )
    (tmp_path / "diet.sqlite").write_bytes(b"not-a-real-database")

    findings = scanner.scan_tree(tmp_path)

    assert {item.code for item in findings} == {
        "CREDENTIAL_LIKE_CONTENT",
        "RUNTIME_DATA_FILE",
    }
    assert all("sk-" not in item.message for item in findings)


def test_sensitive_content_scan_ignores_controlled_test_fixtures(
    tmp_path: Path,
) -> None:
    scanner = _load_script()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "fixture.py").write_text(
        'FAKE = "sk-' + "b" * 28 + '"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("clean\n", encoding="utf-8")

    assert scanner.scan_tree(tmp_path) == ()


def test_sensitive_content_scan_excludes_tool_venv_but_not_source_secrets(
    tmp_path: Path,
) -> None:
    scanner = _load_script()
    for environment_name in (".venv", ".venv-ci", ".tools", "venv"):
        tool_environment = (
            tmp_path / environment_name / "Lib" / "site-packages"
        )
        tool_environment.mkdir(parents=True)
        (tool_environment / "cacert.pem").write_text(
            "-----BEGIN PRIVATE KEY-----\ntooling-only\n",
            encoding="utf-8",
        )
    source = tmp_path / "python" / "personal_diet_pantry"
    source.mkdir(parents=True)
    (source / "release-input.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nsource-secret\n",
        encoding="utf-8",
    )
    (source / "settings.py").write_text(
        'TOKEN = "sk-' + "c" * 28 + '"\n',
        encoding="utf-8",
    )

    findings = scanner.scan_tree(tmp_path)

    assert {(item.code, item.relative_path) for item in findings} == {
        (
            "RUNTIME_DATA_FILE",
            "python/personal_diet_pantry/release-input.pem",
        ),
        (
            "CREDENTIAL_LIKE_CONTENT",
            "python/personal_diet_pantry/settings.py",
        ),
    }
