from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.conftest import run_bridge_probe


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_packed_installable_starts_without_source_tree(
    tmp_path: Path,
) -> None:
    npm = shutil.which("npm")
    assert npm is not None
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    packed = _run(
        [
            npm,
            "pack",
            "--json",
            "--pack-destination",
            str(pack_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    archive_name = json.loads(packed.stdout)[0]["filename"]
    archive = pack_dir / archive_name
    assert archive.is_file()

    install_root = tmp_path / "consumer"
    install_root.mkdir()
    (install_root / "package.json").write_text(
        json.dumps(
            {
                "name": "diet-installable-contract",
                "version": "1.0.0",
                "private": True,
            }
        ),
        encoding="utf-8",
    )
    _run(
        [
            npm,
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            str(archive),
        ],
        cwd=install_root,
    )
    installed = (
        install_root
        / "node_modules"
        / "personal-diet-pantry"
    )

    package = json.loads(
        (installed / "package.json").read_text(encoding="utf-8")
    )
    assert package["version"] == "0.8.27"
    assert package["productVersion"] == "0.7.4.27"
    assert (
        installed / "migrations" / "013_intake_data_correctness.sql"
    ).is_file()
    assert (
        installed / "migrations" / "014_session_key_minimization.sql"
    ).is_file()
    assert (
        installed / "migrations" / "015_body_weight_logs.sql"
    ).is_file()
    assert (
        installed / "migrations" / "016_recipe_shopping.sql"
    ).is_file()
    assert (
        installed / "migrations" / "017_cost_waste.sql"
    ).is_file()
    assert (
        installed / "migrations" / "018_privacy_erasure.sql"
    ).is_file()
    assert (
        installed / "migrations" / "019_trusted_workflows.sql"
    ).is_file()
    assert (
        installed / "migrations" / "020_inventory_search.sql"
    ).is_file()
    assert (
        installed
        / "migrations"
        / "021_package_semantics_and_product_operations.sql"
    ).is_file()
    assert (
        installed
        / "control-migrations"
        / "002_maintenance_evidence.sql"
    ).is_file()
    assert (
        installed / "dist" / "generated" / "tool-contracts.js"
    ).is_file()
    assert (
        installed
        / "python"
        / "personal_diet_pantry"
        / "package_semantics.py"
    ).is_file()
    assert (installed / "LICENSE").is_file()
    assert (installed / "UPDATE-v0.7.4.27.zh-CN.md").is_file()
    assert (
        installed
        / "python"
        / "personal_diet_pantry"
        / "body_weight.py"
    ).is_file()
    assert (installed / "templates" / "en" / "daily-report.md").is_file()
    assert (
        installed / "templates" / "zh-CN" / "daily-report.md"
    ).is_file()
    for excluded in ("src", "tests", "src-tests", "contracts"):
        assert not (installed / excluded).exists()

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed / "python")
    imported = _run(
        [
            sys.executable,
            "-c",
            (
                "import personal_diet_pantry as package;"
                "print(package.__version__)"
            ),
        ],
        cwd=installed,
        env=environment,
    )
    assert imported.stdout.strip() == "0.8.27"

    smoke = run_bridge_probe(
        installed,
        tmp_path / "installed-data",
        "smoke",
    )
    assert smoke["scenario"] == "smoke"
    assert smoke["initialized"]["data"]["initialized"] is True
    assert not [
        check
        for check in smoke["self_check"]["data"]["checks"]
        if check["level"] == "FAIL"
    ]
