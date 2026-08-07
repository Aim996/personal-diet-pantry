from __future__ import annotations

import json
from pathlib import Path

from scripts.lint_skill import lint_skill


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "personal-diet-pantry"
TRACES = ROOT / "tests" / "fixtures" / "traces"
INVARIANTS = ROOT / "docs" / "PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md"


def _trace(name: str) -> dict[str, object]:
    return json.loads((TRACES / name).read_text(encoding="utf-8"))


def test_generalized_stabilization_routes_are_concrete_and_perfect() -> None:
    result = lint_skill(SKILL_ROOT, ROOT / "tests" / "skill-evals" / "routing.yaml")

    assert result.case_count >= 39
    assert result.safety_score == 1
    assert result.overall_score == 1, result.failures


def test_main_workflow_contains_generalized_time_estimation_and_budget_contracts() -> None:
    main = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    time_scope = (SKILL_ROOT / "references" / "time-and-query-scopes.md").read_text(
        encoding="utf-8"
    )
    estimation = (
        SKILL_ROOT / "references" / "estimation-and-confirmation.md"
    ).read_text(encoding="utf-8")
    budget = (
        SKILL_ROOT / "references" / "tool-budget-and-recovery.md"
    ).read_text(encoding="utf-8")

    assert "event status -> requested scope -> sufficient facts -> one route" in main
    assert "negative, planned, hypothetical, or cancelled" in main
    assert "examples are tests, not a closed runtime keyword list" in main
    assert "Runtime reference reads are exactly 0" in main
    assert "Never use Exec, Shell, SQL, Memory Search, file traversal" in main
    assert "all intake domains carried by `diet_meal` in that interval" in time_scope
    assert "Asia/Shanghai" in time_scope
    assert "one combined preview" in estimation
    assert "zero business writes" in estimation
    assert "simple read or write | 1" in budget
    assert "targeted search plus operation | 2" in budget
    assert "same error fingerprint" in budget
    assert "Exec" in budget and "file" in budget


def test_confirmed_v0736_behaviors_are_registered_as_cross_version_invariants() -> None:
    invariants = INVARIANTS.read_text(encoding="utf-8")

    for phrase in (
        "泛化时间范围与查询完整性",
        "不得把“昨晚”或任何单个示例写成特殊分支",
        "模糊数量的估算、预览与确认",
        "先展示可解释的估算并请求一次确认",
        "一次合并预览",
        "有界工具调用、失败熔断与证据边界",
        "不得降级到 Exec、Shell、SQL、文件遍历或报告文件",
    ):
        assert phrase in invariants


def test_repeated_failure_trace_stops_after_one_diet_call_without_exec_or_files() -> None:
    trace = _trace("repeated-diet-failure.json")
    calls = trace["tool_calls"]

    assert len(calls) == 1
    assert calls[0]["tool"].startswith("diet_")
    assert calls[0]["result"]["ok"] is False
    assert trace["terminal"] is True
    assert trace["fallback_attempts"] == []
    serialized = json.dumps(trace, ensure_ascii=False).casefold()
    assert "shell_command" not in serialized
    assert '"tool": "exec"' not in serialized
    assert "read_file" not in serialized


def test_combined_estimate_trace_previews_once_then_commits_once_after_confirmation() -> None:
    trace = _trace("combined-estimate-preview.json")
    calls = trace["tool_calls"]
    previews = [call for call in calls if call["action"] == "preview_record"]
    commits = [call for call in calls if call["action"] == "commit_record"]

    assert len(previews) == 1
    assert len(previews[0]["arguments"]["items"]) == 2
    assert len(previews[0]["result"]["resolution"]["quantity_estimates"]) == 2
    assert trace["database_assertions"]["before"] == trace["database_assertions"][
        "after_preview"
    ]
    assert len(commits) == 1
    assert commits[0]["arguments"]["confirmed"] is True
    assert trace["database_assertions"]["after_commit"] != trace[
        "database_assertions"
    ]["before"]
    assert len(trace["final_replies"]) == 1


def test_negated_vague_intake_trace_short_circuits_before_resolution() -> None:
    trace = _trace("negated-vague-intake.json")

    assert trace["tool_calls"] == []
    assert trace["resolutions"] == []
    assert trace["database_assertions"]["before"] == trace["database_assertions"][
        "after"
    ]
    assert len(trace["final_replies"]) == 1
