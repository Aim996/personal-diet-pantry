from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re

from tests.conftest import run_bridge_probe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")
_PRIVATE_COMPACT_KEYS = {
    "absolutepath",
    "databaseid",
    "mealid",
    "batchid",
    "recordid",
    "transactionid",
    "originaltransactionid",
    "previewtoken",
    "semanticfingerprint",
    "sourcesessionkey",
    "sourcemodel",
    "testrunid",
}


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            compact = str(key).lower().replace("_", "").replace("-", "")
            assert compact not in _PRIVATE_COMPACT_KEYS
            assert not str(key).lower().endswith("_id")
            assert not str(key).lower().endswith("_token")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for child in value:
            _assert_public(child)
    elif isinstance(value, str):
        assert _ABSOLUTE_PATH.match(value) is None


def test_built_bridge_runs_full_fresh_database_scenario(
    tmp_path: Path,
) -> None:
    result = run_bridge_probe(
        PROJECT_ROOT,
        tmp_path / "bridge-data",
        "full",
    )

    assert result["scenario"] == "full"
    assert result["progress"]["data"]["goals_confirmed"] is False
    assert (
        result["progress"]["data"]["goal_source"]
        == "configuration_default"
    )
    assert (
        result["goals"]["data"]["goal_profile"]["goals_confirmed"]
        is True
    )
    assert (
        result["goals"]["data"]["goal_profile"]["goal_source"]
        == "user_confirmed"
    )
    assert result["water"]["data"]["record"]["amount_ml"] == 300
    assert result["weight"]["data"]["record"]["weight_kg"] == "105"
    assert result["weight"]["data"]["record"]["status_note"] == "空腹"
    assert result["weight"]["data"]["record"]["measured_at"]
    assert (
        result["weight"]["data"]["summary"]["seven_day_average_kg"]
        == "105.0"
    )
    assert result["insights"]["data"]["period"]["kind"] == "weekly"
    assert result["insights"]["data"]["goals_confirmed"] is True
    assert result["report"]["data"]["report"]["kind"] == "daily"
    assert result["report_contains_chinese"] is True
    _assert_public(result)
