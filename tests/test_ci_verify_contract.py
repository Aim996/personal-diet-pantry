from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_clean_ci_builds_typescript_before_package_content_tests() -> None:
    source = (PROJECT_ROOT / "ci" / "verify.ps1").read_text(encoding="utf-8")

    build = source.index("& npm run build")
    tests = source.index("& node $Vitest run")
    assert build < tests


def test_ci_runs_skill_routing_evaluation_before_full_tests() -> None:
    source = (PROJECT_ROOT / "ci" / "verify.ps1").read_text(encoding="utf-8")

    evaluation = source.index("scripts/lint_skill.py")
    trace = source.index("scripts/validate_behavior_trace.py")
    tests = source.index("& $Python -m pytest")
    assert evaluation < tests
    assert trace < tests


def test_ci_scans_sensitive_content_before_full_tests() -> None:
    source = (PROJECT_ROOT / "ci" / "verify.ps1").read_text(encoding="utf-8")

    scan = source.index("scripts/scan_sensitive_content.py")
    tests = source.index("& $Python -m pytest")
    assert scan < tests


def test_ci_runs_bounded_v070_core_gate_before_full_suite() -> None:
    source = (PROJECT_ROOT / "ci" / "verify.ps1").read_text(encoding="utf-8")
    nodeids = [
        line.strip()
        for line in (PROJECT_ROOT / "contracts" / "v070-core-tests.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(nodeids) == len(set(nodeids))
    assert len(nodeids) <= 36
    required_skill_nodes = {
        "tests/contracts/test_natural_language_trigger_skill_contract.py::test_frontmatter_discovers_natural_personal_diet_intent_without_name",
        "tests/contracts/test_natural_language_trigger_skill_contract.py::test_activation_contract_precedes_readiness_and_applies_full_skill",
        "tests/contracts/test_natural_language_trigger_skill_contract.py::test_bare_number_never_writes_body_weight_without_explicit_wording",
        "tests/contracts/test_natural_language_trigger_skill_contract.py::test_public_reply_contract_hides_internal_implementation",
        "tests/contracts/test_body_weight_skill_contract.py::test_skill_routes_explicit_weight_and_free_status_to_one_direct_write",
    }
    assert required_skill_nodes <= set(nodeids)
    required_closed_loop_nodes = {
        "tests/contracts/test_live_intake_regressions.py::test_cook_six_eggs_eat_two_store_four_is_conservative",
        "tests/contracts/test_pantry_transaction_contracts.py::test_adjust_discard_open_freeze_thaw_transitions",
        "tests/contracts/test_pantry_transaction_contracts.py::test_transaction_undo_redo_restores_exact_pantry_state",
        "tests/contracts/test_body_weight_contracts.py::test_record_uses_system_time_default_kg_and_returns_summary",
        "tests/integration/test_upgrade_e2e.py::test_immutable_v050_database_upgrades_without_data_loss",
    }
    assert required_closed_loop_nodes <= set(nodeids)
    required_v071_nodes = {
        "tests/contracts/test_live_intake_regressions.py::test_ambiguous_raw_milk_cannot_be_hidden_by_exact_normalized_name",
        "tests/contracts/test_live_intake_regressions.py::test_two_liquids_keep_independent_volume_and_basis",
        "tests/contracts/test_meal_water_contracts.py::test_cooking_correction_retires_untouched_old_leftover",
        "tests/contracts/test_meal_water_contracts.py::test_cooking_correction_rejects_consumed_old_leftover",
        "tests/contracts/test_live_intake_regressions.py::test_explicit_fat_trim_reduces_nutrition_and_preserves_raw_deduction",
        "tests/test_progress.py::test_maximum_goal_returns_exact_over_by",
    }
    assert required_v071_nodes <= set(nodeids)
    required_retained_v070_nodes = {
        "tests/contracts/test_live_intake_regressions.py::test_two_small_sweet_potatoes_and_500ml_soy_milk_total",
        "tests/contracts/test_live_intake_regressions.py::test_clear_completed_external_meal_records_without_confirmation",
        "tests/contracts/test_live_intake_regressions.py::test_two_fried_eggs_and_300ml_milk_are_scaled_once",
        "tests/contracts/test_live_intake_regressions.py::test_same_intake_retry_does_not_create_a_second_meal",
        "tests/contracts/test_live_intake_regressions.py::test_semantically_equivalent_intake_retry_replays_existing_meal",
    }
    assert required_retained_v070_nodes <= set(nodeids)
    required_v072_nodes = {
        "tests/contracts/test_inventory_search_contracts.py::test_public_search_returns_bounded_candidates_and_handles",
        "src-tests/schema-size.test.ts",
        "tests/test_tool_contract_generation.py::test_skill_routes_target_the_expected_domain_actions",
        "tests/integration/test_inventory_search_migration.py::test_inventory_search_migration_adds_index_and_product_reference",
    }
    assert required_v072_nodes <= set(nodeids)
    required_v0731_nodes = {
        "tests/contracts/test_live_intake_regressions.py::test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo",
    }
    assert required_v0731_nodes <= set(nodeids)
    required_v0736_nodes = {
        "tests/test_temporal_queries.py::test_same_cross_day_scope_returns_meals_water_and_weights",
        "tests/test_local_time_projection.py::test_public_meal_water_and_weight_project_shanghai_local_time",
        "tests/test_expiring_report_completeness.py::test_expiring_report_includes_all_expired_remaining_batches_and_is_read_only",
        "tests/test_inventory_lineage_projection.py::test_prepared_food_search_projects_only_formal_cooking_relation",
        "tests/test_quantity_resolution.py::test_multiple_estimates_share_one_preview_and_one_final_commit",
        "tests/test_skill_stabilization.py::test_repeated_failure_trace_stops_after_one_diet_call_without_exec_or_files",
    }
    assert required_v0736_nodes <= set(nodeids)
    assert len(nodeids) == 36
    assert "contracts/v070-core-tests.txt" in source.replace("\\", "/")
    assert source.index("$CoreTests") < source.index(
        '& $Python -m pytest -q "--junitxml=$PytestReport"'
    )
