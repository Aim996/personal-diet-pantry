import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "check_release_ref.py"
    spec = importlib.util.spec_from_file_location("check_release_ref", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps({"productVersion": "0.7.4.28"}), encoding="utf-8"
    )
    return tmp_path


def test_tag_push_must_exactly_match_product_version(tmp_path: Path) -> None:
    module = load_module()
    assert (
        module.validate_release_context(project(tmp_path), "push", "v0.7.4.28")
        == "0.7.4.28"
    )
    with pytest.raises(module.ReleaseRefError, match="v0.7.4.28"):
        module.validate_release_context(project(tmp_path), "push", "v0.7.4.4")


def test_manual_dispatch_is_dry_run_without_tag_requirement(tmp_path: Path) -> None:
    module = load_module()
    assert (
        module.validate_release_context(
            project(tmp_path), "workflow_dispatch", "main"
        )
        == "0.7.4.28"
    )


def test_unknown_event_fails_closed(tmp_path: Path) -> None:
    module = load_module()
    with pytest.raises(module.ReleaseRefError, match="unsupported"):
        module.validate_release_context(
            project(tmp_path), "pull_request", "v0.7.4.28"
        )
