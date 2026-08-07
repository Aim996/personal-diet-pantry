from __future__ import annotations

from collections.abc import Iterator
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from personal_diet_pantry.service import DietService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_bridge_probe(
    project_root: Path,
    data_dir: Path,
    scenario: str,
) -> dict[str, object]:
    """Run the built JavaScript bridge against one isolated data directory."""

    environment = os.environ.copy()
    environment["PYTHON"] = sys.executable
    completed = subprocess.run(
        [
            "node",
            str(PROJECT_ROOT / "scripts" / "bridge_probe.mjs"),
            str(Path(project_root).resolve()),
            str(Path(data_dir).resolve()),
            scenario,
        ],
        check=True,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.stdout.count("\n") == 1
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


@pytest.fixture
def service(tmp_path: Path) -> Iterator[DietService]:
    instance = DietService(
        PROJECT_ROOT,
        plugin_config={"dataDir": str(tmp_path / "data")},
        env={},
    )
    try:
        yield instance
    finally:
        instance.close()
