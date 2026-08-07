#!/usr/bin/env python3
"""Validate a sanitized normal-path OpenClaw behavior trace."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


SEARCH_TEMPLATE = {"search_text", "unit", "nutrition_mode"}
MEAL_ITEM_TEMPLATE = {
    "raw_name",
    "normalized_name",
    "amount",
    "unit",
    "inventory_match_handle",
}
MANUAL_NUTRITION_FIELDS = {
    "nutrition_facts",
    "nutrition_estimate",
    "nutrition_basis",
    "nutrition_dataset_version",
    "consumed_weight_g",
    "consumed_volume_ml",
    "consumed_servings",
}
HANDLE_PATTERN = re.compile(r"^[0-9a-f]{48}$")


@dataclass(frozen=True)
class TraceValidationResult:
    ok: bool
    search_count: int
    meal_record_count: int
    failures: tuple[str, ...]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validate_behavior_trace(path: Path) -> TraceValidationResult:
    """Check one de-identified trace for the trusted packaged-pantry loop."""

    failures: list[str] = []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return TraceValidationResult(False, 0, 0, (f"invalid trace JSON: {error}",))

    trace = _mapping(raw)
    if not trace:
        return TraceValidationResult(False, 0, 0, ("trace root must be a mapping",))

    if trace.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    for field in ("scenario_id", "user_input"):
        if not isinstance(trace.get(field), str) or not trace[field].strip():
            failures.append(f"{field} must be a non-empty string")
    elapsed_ms = trace.get("elapsed_ms")
    if not isinstance(elapsed_ms, int) or isinstance(elapsed_ms, bool) or elapsed_ms <= 0:
        failures.append("elapsed_ms must be a positive integer")

    raw_calls = trace.get("tool_calls")
    calls = _calls(raw_calls)
    if not isinstance(raw_calls, list) or len(calls) != len(raw_calls):
        failures.append("tool_calls must be a list of mappings")

    searches = [
        call
        for call in calls
        if call.get("tool") == "diet_pantry" and call.get("action") == "search"
    ]
    records = [
        call
        for call in calls
        if call.get("tool") == "diet_meal" and call.get("action") == "record"
    ]
    if len(searches) != 1:
        failures.append("normal path must contain exactly one pantry search")
    if len(records) != 1:
        failures.append("normal path must contain exactly one meal record")
    if len(calls) != 2 or calls[:1] != searches or calls[1:2] != records:
        failures.append("normal path tool order must be pantry search then meal record")

    search_handle: str | None = None
    if searches:
        search = searches[0]
        search_args = _mapping(search.get("arguments"))
        missing = SEARCH_TEMPLATE - search_args.keys()
        if missing:
            failures.append(f"pantry search is missing fields: {sorted(missing)}")
        if search_args.get("nutrition_mode") != "none":
            failures.append("normal pantry search must use nutrition_mode none")
        search_result = _mapping(search.get("result"))
        candidates = search_result.get("candidates")
        if search_result.get("ok") is not True or not isinstance(candidates, list) or len(candidates) != 1:
            failures.append("pantry search must return exactly one successful candidate")
        else:
            candidate = _mapping(candidates[0])
            for field in (
                "remaining_display_quantity",
                "display_unit",
                "base_quantity_per_display_unit",
            ):
                if field not in candidate:
                    failures.append(f"pantry candidate is missing {field}")
            search_handle = _mapping(candidate.get("workflow")).get(
                "inventory_match_handle"
            )
            if not isinstance(search_handle, str) or not HANDLE_PATTERN.fullmatch(search_handle):
                failures.append("inventory_match_handle must be 48 lowercase hex characters")

    if records:
        record = records[0]
        arguments = _mapping(record.get("arguments"))
        items = arguments.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            failures.append("meal record must contain exactly one item mapping")
        else:
            item = items[0]
            missing = MEAL_ITEM_TEMPLATE - item.keys()
            if missing:
                failures.append(f"meal item is missing fields: {sorted(missing)}")
            manual = MANUAL_NUTRITION_FIELDS & item.keys()
            if manual:
                failures.append(
                    f"normal inventory path must not include manual nutrition fields: {sorted(manual)}"
                )
            if item.get("amount") != "1" or item.get("unit") != "盒":
                failures.append("meal record must preserve the user's 1 盒 display quantity")
            if search_handle is not None and item.get("inventory_match_handle") != search_handle:
                failures.append("meal record must reuse the unchanged search handle")
        result = _mapping(record.get("result"))
        if result.get("ok") is not True:
            failures.append("meal record result must be successful")

    replies = trace.get("final_replies")
    if (
        not isinstance(replies, list)
        or len(replies) != 1
        or not isinstance(replies[0], str)
        or not replies[0].strip()
    ):
        failures.append("trace must contain exactly one non-empty final reply")

    assertions = _mapping(trace.get("database_assertions"))
    before = _mapping(assertions.get("before"))
    after = _mapping(assertions.get("after"))
    after_undo = _mapping(assertions.get("after_undo"))
    if before.get("pantry_remaining_ml") != "500" or before.get("active_meals") != 0:
        failures.append("before state must contain 500 ml and zero active meals")
    if (
        after.get("pantry_remaining_ml") != "250"
        or after.get("active_meals") != 1
        or after.get("meal_item_amount") != "250"
        or after.get("meal_item_unit") != "ml"
        or after.get("fiber_g", object()) is not None
        or after.get("sodium_mg", object()) is not None
    ):
        failures.append("after state must prove conversion, deduction, and unknown nutrients")
    if (
        after_undo.get("pantry_remaining_ml") != before.get("pantry_remaining_ml")
        or after_undo.get("active_meals") != before.get("active_meals")
        or after_undo.get("nutrition_evidence_rows")
        != before.get("nutrition_evidence_rows")
    ):
        failures.append("after_undo state must restore pantry, meal, and evidence counts")

    return TraceValidationResult(
        ok=not failures,
        search_count=len(searches),
        meal_record_count=len(records),
        failures=tuple(failures),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    result = validate_behavior_trace(args.trace)
    print(
        f"searches={result.search_count}; meal_records={result.meal_record_count}; "
        f"status={'PASS' if result.ok else 'FAIL'}"
    )
    for failure in result.failures:
        print(f"- {failure}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
