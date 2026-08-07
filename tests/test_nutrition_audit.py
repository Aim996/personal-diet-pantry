from __future__ import annotations

from personal_diet_pantry.nutrition_audit import audit_nutrition

from tests.contracts.helpers import recorded_meal


def test_audit_flags_hydration_above_consumed_volume(service) -> None:
    recorded_meal(service)
    service.connection.execute(
        """
        UPDATE meal_items
        SET consumed_volume_ml = '500', hydration_ml = '2375'
        WHERE id = (
            SELECT id FROM meal_items ORDER BY id LIMIT 1
        )
        """
    )
    service.connection.commit()

    findings = audit_nutrition(service.connection)

    assert any(
        finding.code == "hydration_exceeds_volume"
        for finding in findings
    )


def test_audit_flags_complete_meal_without_evidence(service) -> None:
    recorded_meal(service)
    service.connection.execute(
        "DELETE FROM meal_item_nutrition_evidence"
    )
    service.connection.execute(
        """
        UPDATE meals
        SET nutrition_calculation_status = 'unverified',
            nutrition_provenance_status = 'untraceable'
        """
    )
    service.connection.commit()

    findings = audit_nutrition(service.connection)
    codes = {finding.code for finding in findings}

    assert "missing_nutrition_basis" in codes
    assert "complete_but_unverified" in codes


def test_audit_does_not_mutate_business_rows(service) -> None:
    recorded_meal(service)
    before = service.connection.total_changes

    audit_nutrition(service.connection)

    assert service.connection.total_changes == before


def test_partial_meal_known_lower_bound_is_not_a_total_mismatch(
    service,
) -> None:
    recorded_meal(service)
    service.connection.execute(
        """
        INSERT INTO meal_items (
            meal_id, parent_item_id, item_role, display_order,
            raw_name, normalized_name, amount, unit,
            source_grade, confidence, transaction_id
        )
        SELECT id, NULL, 'food', 1,
               'unknown side', 'unknown side', '1', 'serving',
               'unknown', '0.5', transaction_id
        FROM meals
        LIMIT 1
        """
    )
    service.connection.execute(
        """
        UPDATE meals
        SET nutrition_status = 'partial',
            nutrition_missing_fields_json = '["calories","protein","fat","carbohydrate","fiber","sodium"]'
        """
    )
    service.connection.commit()

    findings = audit_nutrition(service.connection)

    assert not any(
        finding.code == "meal_total_mismatch"
        for finding in findings
    )
