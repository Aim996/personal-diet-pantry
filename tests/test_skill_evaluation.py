from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from scripts.validate_behavior_trace import validate_behavior_trace


ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "tests" / "fixtures" / "traces" / "packaged-soy-one-box.json"


def _trace() -> dict[str, object]:
    return json.loads(TRACE.read_text(encoding="utf-8"))


def test_packaged_soy_trace_proves_one_search_one_record_and_reversible_state() -> None:
    result = validate_behavior_trace(TRACE)

    assert result.ok, result.failures
    assert result.search_count == 1
    assert result.meal_record_count == 1


def test_trace_rejects_manual_nutrition_in_the_normal_inventory_path(tmp_path: Path) -> None:
    trace = deepcopy(_trace())
    trace["tool_calls"][1]["arguments"]["items"][0]["nutrition_facts"] = {
        "calories_kcal": "80"
    }
    path = tmp_path / "manual-nutrition.json"
    path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

    result = validate_behavior_trace(path)

    assert not result.ok
    assert any("manual nutrition" in failure for failure in result.failures)


def test_trace_rejects_a_second_normal_record_attempt(tmp_path: Path) -> None:
    trace = deepcopy(_trace())
    trace["tool_calls"].append(deepcopy(trace["tool_calls"][1]))
    path = tmp_path / "duplicate-record.json"
    path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")

    result = validate_behavior_trace(path)

    assert not result.ok
    assert any("exactly one meal record" in failure for failure in result.failures)
