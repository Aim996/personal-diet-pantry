from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

import pytest

from personal_diet_pantry import inventory_matching, nutrition_profiles
from personal_diet_pantry.service import DietService
from personal_diet_pantry.transactions import TransactionManager
from tests.contracts.helpers import recent_operation_handle


_LABEL = {
    "calories_kcal": Decimal("64"),
    "protein_g": Decimal("3.2"),
    "fat_g": Decimal("3.6"),
    "carbohydrate_g": Decimal("4.8"),
    "fiber_g": None,
    "sodium_mg": Decimal("50"),
    "sugar_g": Decimal("4.8"),
}
_MEAL_FACTS = {
    "calories": 1,
    "protein": 0,
    "fat": 0,
    "carbohydrate": 0,
    "fiber": 0,
    "sodium": 0,
    "source": "contract fixture",
    "source_grade": "A",
}


def _pantry(
    service: DietService,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": "pantry", "action": action, "payload": payload}
    )


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": domain, "action": action, "payload": payload}
    )


def _batch_ids(service: DietService, normalized_name: str) -> list[int]:
    return [
        int(row["id"])
        for row in service.connection.execute(
            """
            SELECT id
            FROM pantry_batches
            WHERE normalized_name = ? AND unit = 'ml'
            ORDER BY id
            """,
            (normalized_name,),
        ).fetchall()
    ]


def _add_matching_batch(service: DietService, normalized_name: str) -> int:
    cursor = service.connection.execute(
        """
        INSERT INTO pantry_batches (
            food_name, normalized_name, added_at, initial_quantity,
            remaining_quantity, unit, status, source, version, transaction_id
        ) VALUES (?, ?, '2026-08-02T00:00:00Z', 500, 500, 'ml', 'active',
                  'seed', 1, 'txn_inventory_search_seed')
        """,
        (normalized_name, normalized_name),
    )
    service.connection.commit()
    return int(cursor.lastrowid)


def _add_search_batch(
    service: DietService,
    normalized_name: str,
    *,
    unit: str,
    quantity: str = "1",
) -> int:
    cursor = service.connection.execute(
        """
        INSERT INTO pantry_batches (
            food_name, normalized_name, added_at, initial_quantity,
            remaining_quantity, unit, status, source, version, transaction_id
        ) VALUES (?, ?, '2026-08-02T00:00:00Z', ?, ?, ?, 'active',
                  'seed', 1, 'txn_inventory_search_seed')
        """,
        (normalized_name, normalized_name, quantity, quantity, unit),
    )
    service.connection.commit()
    return int(cursor.lastrowid)


def test_inventory_matching_excludes_expired_batches_from_available_quantity(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    for expiry, quantity in (
        ("2026-08-03T00:00:00Z", "500"),
        ("2026-08-10T00:00:00Z", "250"),
    ):
        added = _pantry(
            service,
            "add",
            {
                "food_name": "燕麦奶",
                "normalized_name": "oat milk",
                "quantity": quantity,
                "unit": "ml",
                "added_at": "2026-08-01T00:00:00Z",
                "expires_at": expiry,
                "source_text": f"新增燕麦奶 {quantity}ml",
            },
        )
        assert added["ok"] is True

    result = _pantry(
        service,
        "search",
        {"search_text": "oat milk", "unit": "ml"},
    )

    assert result["ok"] is True
    assert len(result["data"]["candidates"]) == 1
    assert Decimal(result["data"]["candidates"][0]["available_quantity"]) == Decimal("250")
    assert result["data"]["candidates"][0]["batch_count"] == 1


def test_completed_meal_truthfully_consumes_an_expired_selected_batch(
    service: DietService,
) -> None:
    added = _pantry(
        service,
        "add",
        {
            "food_name": "水煮蛋",
            "normalized_name": "水煮蛋",
            "quantity": "3",
            "unit": "piece",
            "added_at": "2026-08-01T00:00:00+08:00",
            "expires_at": "2026-08-04T00:00:00+08:00",
            "source_text": "冰箱里有3个水煮蛋",
        },
    )
    assert added["ok"] is True
    search = _pantry(
        service,
        "search",
        {"search_text": "水煮蛋", "unit": "piece"},
    )
    selected = search["data"]["candidates"][0]
    assert selected["availability"] == "expired_only"

    rejected = _dispatch(
        service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-05T20:00:00+08:00",
            "meal_type": "dinner",
            "source_text": "我已经吃了一个过期水煮蛋",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "水煮蛋",
                    "normalized_name": "水煮蛋",
                    "amount": "1",
                    "unit": "piece",
                    "nutrition_facts": _MEAL_FACTS,
                    "nutrition_basis": "consumed_total",
                    "inventory_match_handle": selected["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert rejected["ok"] is False
    assert rejected["error"]["reason"] == "expired_inventory"
    unchanged = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = '水煮蛋'"
    ).fetchone()
    assert Decimal(str(unchanged["remaining_quantity"])) == Decimal("3")

    recorded = _dispatch(
        service,
        "meal",
        "record",
        {
            "_turn_completed_consumption": True,
            "occurred_at": "2026-08-05T20:00:00+08:00",
            "meal_type": "dinner",
            "source_text": "我已经吃了一个过期水煮蛋",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "水煮蛋",
                    "normalized_name": "水煮蛋",
                    "amount": "1",
                    "unit": "piece",
                    "nutrition_facts": _MEAL_FACTS,
                    "nutrition_basis": "consumed_total",
                    "inventory_match_handle": selected["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert recorded["ok"] is True, recorded
    remaining = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = '水煮蛋'"
    ).fetchone()
    assert Decimal(str(remaining["remaining_quantity"])) == Decimal("2")
    movement = service.connection.execute(
        "SELECT movement_type FROM pantry_movements "
        "WHERE pantry_batch_id = ("
        "SELECT id FROM pantry_batches WHERE normalized_name = '水煮蛋'"
        ") ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert movement["movement_type"] == "consume"


def _link_label(
    service: DietService,
    batch_id: int,
    *,
    nutrition: dict[str, Decimal | None] | None = None,
    serving_basis: str = "per_100ml",
    source_grade: str = "B",
) -> None:
    row = service.connection.execute(
        "SELECT normalized_name FROM pantry_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    assert row is not None
    nutrition_profiles.create_and_link_profile(
        service.connection,
        TransactionManager(service.connection),
        pantry_batch_id=batch_id,
        draft=nutrition_profiles.NutritionProfileDraft(
            normalized_name=str(row["normalized_name"]),
            brand="seed",
            product_key="search-contract",
            serving_basis=serving_basis,
            nutrition=nutrition or _LABEL,
            source_text="seed label",
            source_grade=source_grade,
        ),
        linked_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )


def _stored_workflow(
    service: DietService,
    handle: str,
) -> tuple[dict[str, object], list[dict[str, int]]]:
    token_hash = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    row = service.connection.execute(
        """
        SELECT result_json, resource_versions_json
        FROM operation_previews
        WHERE token_hash = ?
        """,
        (token_hash,),
    ).fetchone()
    assert row is not None
    return json.loads(row["result_json"]), json.loads(
        row["resource_versions_json"]
    )


def _add_boxed_product(
    service: DietService,
    *,
    normalized_name: str,
    quantity: str,
    display_quantity: str | None,
    display_unit: str | None = "盒",
    base_quantity_per_display_unit: str | None = "250",
    nutrition_profile: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "food_name": normalized_name,
        "normalized_name": normalized_name,
        "quantity": quantity,
        "unit": "ml",
        "added_at": "2026-08-02T00:00:00Z",
        "expires_at": "2026-08-20T00:00:00Z",
        "source_text": f"新增 {normalized_name}",
    }
    if display_quantity is not None:
        payload.update(
            {
                "display_quantity": display_quantity,
                "display_unit": display_unit,
                "base_quantity_per_display_unit": (
                    base_quantity_per_display_unit
                ),
                "package_hierarchy": [],
            }
        )
    if nutrition_profile is not None:
        payload["nutrition_profile"] = nutrition_profile
    result = _pantry(service, "add", payload)
    assert result["ok"] is True, result


def _seed_inventory(service) -> None:
    connection = service.connection
    connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, status, created_at, source_text
        ) VALUES ('txn_inventory_search_seed', 'pantry_add', 'pending', ?, 'seed')
        """,
        ("2026-08-02T00:00:00Z",),
    )
    rows = [
        (f"无关商品{number}", f"无关商品{number}", "1", "piece")
        for number in range(100)
    ]
    rows.extend(
        [
            ("鸡蛋", "鸡蛋", "12", "piece"),
            ("鸡蛋", "鸡蛋", "20", "piece"),
            ("小象巴氏乳牛奶", "小象巴氏乳", "1000", "ml"),
            ("川象鲜牛奶", "川象鲜牛奶", "900", "ml"),
        ]
    )
    connection.executemany(
        """
        INSERT INTO pantry_batches (
            food_name, normalized_name, added_at, initial_quantity,
            remaining_quantity, unit, status, source, version, transaction_id
        ) VALUES (?, ?, '2026-08-02T00:00:00Z', ?, ?, ?, 'active', 'seed', 1,
                  'txn_inventory_search_seed')
        """,
        [(name, normalized, quantity, quantity, unit) for name, normalized, quantity, unit in rows],
    )
    connection.execute(
        """
        INSERT INTO personal_rules (
            rule_type, subject, rule_json, confidence, evidence_count, source,
            active, created_at, updated_at, transaction_id
        ) VALUES (
            'food_alias', '早餐奶',
            '{"outcome":{"canonical":"川象鲜牛奶"},"rule_type":"food_alias"}',
            1.0, 1, 'explicit_user', 1,
            '2026-08-02T00:00:00Z', '2026-08-02T00:00:00Z',
            'txn_inventory_search_seed'
        )
        """
    )
    connection.commit()


@pytest.fixture
def seeded_service(service):
    _seed_inventory(service)
    return service


def test_search_is_bounded_and_aggregates_same_product(seeded_service):
    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection, "鸡蛋", unit="piece", limit=5
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "鸡蛋"
    assert candidates[0].batch_count == 2
    assert candidates[0].available_quantity == Decimal("32")


def test_search_normalizes_unicode_punctuation_and_internal_whitespace(
    seeded_service,
):
    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection, "鸡，　蛋", unit="piece", limit=5
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "鸡蛋"
    assert candidates[0].match_kind == "exact"


def test_separator_normalized_exact_precedes_learned_alias(seeded_service):
    _add_search_batch(seeded_service, "breakfastmilk", unit="ml")
    _add_search_batch(seeded_service, "target milk", unit="ml")
    seeded_service.connection.execute(
        """
        INSERT INTO personal_rules (
            rule_type, subject, rule_json, confidence, evidence_count, source,
            active, created_at, updated_at, transaction_id
        ) VALUES (
            'food_alias', 'breakfast milk',
            '{"outcome":{"canonical":"target milk"},"rule_type":"food_alias"}',
            1.0, 1, 'explicit_user', 1,
            '2026-08-02T00:00:00Z', '2026-08-02T00:00:00Z',
            'txn_inventory_search_seed'
        )
        """
    )
    seeded_service.connection.commit()

    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "breakfast milk",
        unit="ml",
        limit=5,
    )

    assert [candidate.normalized_name for candidate in candidates[:2]] == [
        "breakfastmilk",
        "target milk",
    ]
    assert [candidate.match_kind for candidate in candidates[:2]] == [
        "exact",
        "learned_alias",
    ]


def test_non_ascii_casefold_difference_remains_an_exact_match(seeded_service):
    _add_search_batch(seeded_service, "äpfel", unit="piece")

    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "ÄPFEL",
        unit="piece",
        limit=1,
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "äpfel"
    assert candidates[0].match_kind == "exact"
    assert candidates[0].match_rank == 0


def test_candidate_separator_normalization_precedes_learned_alias(
    seeded_service,
):
    _add_search_batch(seeded_service, "breakfast milk", unit="ml")
    _add_search_batch(seeded_service, "target milk", unit="ml")
    seeded_service.connection.execute(
        """
        INSERT INTO personal_rules (
            rule_type, subject, rule_json, confidence, evidence_count, source,
            active, created_at, updated_at, transaction_id
        ) VALUES (
            'food_alias', 'breakfastmilk',
            '{"outcome":{"canonical":"target milk"},"rule_type":"food_alias"}',
            1.0, 1, 'explicit_user', 1,
            '2026-08-02T00:00:00Z', '2026-08-02T00:00:00Z',
            'txn_inventory_search_seed'
        )
        """
    )
    seeded_service.connection.commit()

    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "breakfastmilk",
        unit="ml",
        limit=5,
    )

    assert [candidate.normalized_name for candidate in candidates[:2]] == [
        "breakfast milk",
        "target milk",
    ]
    assert [candidate.match_kind for candidate in candidates[:2]] == [
        "exact",
        "learned_alias",
    ]


def test_candidate_non_ascii_casefold_difference_is_exact(seeded_service):
    _add_search_batch(seeded_service, "ÄPFEL", unit="piece")

    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "äpfel",
        unit="piece",
        limit=1,
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "ÄPFEL"
    assert candidates[0].match_kind == "exact"
    assert candidates[0].match_rank == 0


@pytest.mark.parametrize("stored_name", ("（foo）", "ｆoo"))
def test_candidate_side_normalization_precedes_learned_alias(
    seeded_service,
    stored_name,
):
    _add_search_batch(seeded_service, stored_name, unit="piece")
    _add_search_batch(seeded_service, "target item", unit="piece")
    seeded_service.connection.execute(
        """
        INSERT INTO personal_rules (
            rule_type, subject, rule_json, confidence, evidence_count, source,
            active, created_at, updated_at, transaction_id
        ) VALUES (
            'food_alias', 'foo',
            '{"outcome":{"canonical":"target item"},"rule_type":"food_alias"}',
            1.0, 1, 'explicit_user', 1,
            '2026-08-02T00:00:00Z', '2026-08-02T00:00:00Z',
            'txn_inventory_search_seed'
        )
        """
    )
    seeded_service.connection.commit()

    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "foo",
        unit="piece",
        limit=5,
    )

    assert [candidate.normalized_name for candidate in candidates[:2]] == [
        stored_name,
        "target item",
    ]
    assert [candidate.match_kind for candidate in candidates[:2]] == [
        "exact",
        "learned_alias",
    ]


def test_search_returns_distinct_milk_products_without_auto_choice(seeded_service):
    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection, "牛奶", unit="ml", limit=5
    )

    assert {item.normalized_name for item in candidates} == {
        "小象巴氏乳",
        "川象鲜牛奶",
    }


def test_search_uses_learned_alias_before_keyword_expansion(seeded_service):
    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection, "早餐奶", unit="ml", limit=5
    )

    assert len(candidates) == 1
    assert candidates[0].match_kind == "learned_alias"
    assert candidates[0].normalized_name == "川象鲜牛奶"


def test_public_search_expands_static_tomato_alias(seeded_service):
    _add_search_batch(seeded_service, "西红柿", unit="piece", quantity="3")

    result = _pantry(
        seeded_service,
        "search",
        {"search_text": "番茄", "unit": "piece"},
    )

    assert result["ok"] is True, result
    assert [
        (candidate["normalized_name"], candidate["match_kind"])
        for candidate in result["data"]["candidates"]
    ] == [("西红柿", "static_alias")]
    assert result["data"]["candidates"][0]["match_method"] == "static_alias"
    assert result["data"]["candidates"][0]["match_rank"] == 1


def test_public_search_keeps_original_exact_before_learned_alias_target(
    seeded_service,
):
    _add_search_batch(seeded_service, "breakfast milk", unit="ml")
    _add_search_batch(seeded_service, "target milk", unit="ml")
    seeded_service.connection.execute(
        """
        INSERT INTO personal_rules (
            rule_type, subject, rule_json, confidence, evidence_count, source,
            active, created_at, updated_at, transaction_id
        ) VALUES (
            'food_alias', 'breakfast milk',
            '{"outcome":{"canonical":"target milk"},"rule_type":"food_alias"}',
            1.0, 1, 'explicit_user', 1,
            '2026-08-02T00:00:00Z', '2026-08-02T00:00:00Z',
            'txn_inventory_search_seed'
        )
        """
    )
    seeded_service.connection.commit()

    result = _pantry(
        seeded_service,
        "search",
        {"search_text": "breakfast milk", "unit": "ml", "limit": 5},
    )

    assert result["ok"] is True, result
    candidates = result["data"]["candidates"]
    assert [candidate["normalized_name"] for candidate in candidates[:2]] == [
        "breakfast milk",
        "target milk",
    ]
    assert [candidate["match_kind"] for candidate in candidates[:2]] == [
        "exact",
        "learned_alias",
    ]
    assert [candidate["match_rank"] for candidate in candidates[:2]] == [0, 1]


def test_public_search_fills_remaining_limit_with_keyword_candidates(
    seeded_service,
):
    _add_search_batch(seeded_service, "breakfast milk", unit="ml")
    _add_search_batch(seeded_service, "breakfast milk deluxe", unit="ml")

    result = _pantry(
        seeded_service,
        "search",
        {"search_text": "breakfast milk", "unit": "ml", "limit": 2},
    )

    assert result["ok"] is True, result
    candidates = result["data"]["candidates"]
    assert [candidate["normalized_name"] for candidate in candidates] == [
        "breakfast milk",
        "breakfast milk deluxe",
    ]
    assert [candidate["match_method"] for candidate in candidates] == [
        "exact",
        "keyword",
    ]
    assert [candidate["match_rank"] for candidate in candidates] == [0, 2]


def test_public_search_returns_all_distinct_static_alias_products(
    seeded_service,
):
    _add_search_batch(seeded_service, "香菇", unit="piece")
    _add_search_batch(seeded_service, "平菇", unit="piece")

    result = _pantry(
        seeded_service,
        "search",
        {"search_text": "蘑菇", "unit": "piece", "limit": 5},
    )

    assert result["ok"] is True, result
    candidates = result["data"]["candidates"]
    assert {candidate["normalized_name"] for candidate in candidates} == {
        "香菇",
        "平菇",
    }
    assert {candidate["match_kind"] for candidate in candidates} == {
        "static_alias"
    }


def test_large_exact_search_uses_inventory_index_without_match_udf(service):
    service.connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, status, created_at, source_text
        ) VALUES ('txn_inventory_search_seed', 'pantry_add', 'pending', ?, 'seed')
        """,
        ("2026-08-02T00:00:00Z",),
    )
    rows = [
        (
            f"unrelated-{number:05d}",
            f"unrelated-{number:05d}",
            "1",
            "1",
            "piece",
        )
        for number in range(10_000)
    ]
    rows.append(("indexed target", "indexed target", "2", "2", "piece"))
    service.connection.executemany(
        """
        INSERT INTO pantry_batches (
            food_name, normalized_name, added_at, initial_quantity,
            remaining_quantity, unit, status, source, version, transaction_id
        ) VALUES (?, ?, '2026-08-02T00:00:00Z', ?, ?, ?, 'active', 'seed', 1,
                  'txn_inventory_search_seed')
        """,
        rows,
    )
    service.connection.commit()
    udf_calls = 0
    real_match_key = inventory_matching._sqlite_match_key

    def counted_match_key(value):
        nonlocal udf_calls
        udf_calls += 1
        return real_match_key(value)

    inventory_matching._sqlite_match_key = counted_match_key
    try:
        candidates = inventory_matching.search_inventory_candidates(
            service.connection,
            "indexed target",
            unit="piece",
            limit=1,
        )
    finally:
        inventory_matching._sqlite_match_key = real_match_key

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "indexed target"
    assert udf_calls == 0
    plan = service.connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT normalized_name, unit, SUM(remaining_quantity)
        FROM pantry_batches
        WHERE normalized_name = ? COLLATE NOCASE
          AND unit = ? COLLATE NOCASE
          AND status IN ('active', 'opened', 'thawed')
          AND remaining_quantity > 0
        GROUP BY normalized_name, unit
        """,
        ("indexed target", "piece"),
    ).fetchall()
    assert any(
        "idx_pantry_batches_search" in str(row["detail"])
        for row in plan
    )


def test_learned_alias_uses_indexed_equality_without_match_udf(seeded_service):
    udf_calls = 0
    real_match_key = inventory_matching._sqlite_match_key

    def counted_match_key(value):
        nonlocal udf_calls
        udf_calls += 1
        return real_match_key(value)

    inventory_matching._sqlite_match_key = counted_match_key
    try:
        candidates = inventory_matching.search_inventory_candidates(
            seeded_service.connection,
            "早餐奶",
            unit="ml",
            limit=1,
        )
    finally:
        inventory_matching._sqlite_match_key = real_match_key

    assert candidates[0].normalized_name == "川象鲜牛奶"
    assert candidates[0].match_kind == "learned_alias"
    assert udf_calls == 0


def test_normalized_exact_uses_expression_index_without_match_udf(
    seeded_service,
    monkeypatch,
):
    _add_search_batch(seeded_service, "（indexed target）", unit="piece")
    udf_calls = 0
    real_match_key = inventory_matching._sqlite_match_key

    def counted_match_key(value):
        nonlocal udf_calls
        udf_calls += 1
        return real_match_key(value)

    monkeypatch.setattr(
        inventory_matching,
        "_sqlite_match_key",
        counted_match_key,
    )
    candidates = inventory_matching.search_inventory_candidates(
        seeded_service.connection,
        "indexed target",
        unit="piece",
        limit=1,
    )

    assert len(candidates) == 1
    assert candidates[0].normalized_name == "（indexed target）"
    assert candidates[0].match_kind == "exact"
    assert udf_calls == 0
    plan = seeded_service.connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT normalized_name, unit, SUM(remaining_quantity)
        FROM pantry_batches
        WHERE inventory_match_key(normalized_name) = ?
          AND unit = ? COLLATE NOCASE
          AND status IN ('active', 'opened', 'thawed')
          AND remaining_quantity > 0
        GROUP BY normalized_name, unit
        """,
        ("indexedtarget", "piece"),
    ).fetchall()
    assert any(
        "idx_pantry_batches_match_key" in str(row["detail"])
        for row in plan
    )


def test_search_never_materializes_every_inventory_name(seeded_service):
    statements = []
    seeded_service.connection.set_trace_callback(statements.append)

    inventory_matching.search_inventory_candidates(
        seeded_service.connection, "鸡蛋", unit="piece", limit=5
    )

    assert not any(
        "SELECT DISTINCT normalized_name FROM pantry_batches" in sql
        for sql in statements
    )


@pytest.mark.parametrize("limit", [0, 6, True, "5"])
def test_search_rejects_limit_outside_its_bounded_range(service, limit):
    with pytest.raises(ValueError, match="limit must be between 1 and 5"):
        inventory_matching.search_inventory_candidates(
            service.connection, "鸡蛋", limit=limit
        )


def test_public_search_returns_bounded_candidates_and_handles(seeded_service):
    statements = []
    seeded_service.connection.set_trace_callback(statements.append)
    try:
        result = _pantry(
            seeded_service,
            "search",
            {"search_text": "牛奶", "unit": "ml", "limit": 5},
        )
    finally:
        seeded_service.connection.set_trace_callback(None)

    assert result["ok"] is True
    candidates = result["data"]["candidates"]
    assert len(candidates) <= 5
    assert result["data"]["returned_count"] == len(candidates)
    assert all(
        "inventory_match_handle" in item["workflow"] for item in candidates
    )
    assert all("nutrition" not in item for item in candidates)
    assert all(
        item["nutrition_status"] == "not_requested" for item in candidates
    )
    assert all(item["nutrition_available"] is None for item in candidates)
    assert any(
        "pantry_nutrition_links" in statement.casefold()
        for statement in statements
    )
    assert all(
        not {
            "id", "batch_id", "batch_ids", "batches", "source_text",
            "price", "metadata",
        }.intersection(item)
        for item in candidates
    )


def test_product_handle_direct_deduct_uses_selected_sku_without_requery(
    seeded_service,
):
    search = _pantry(
        seeded_service,
        "search",
        {"search_text": "牛奶", "unit": "ml"},
    )
    chosen = search["data"]["candidates"][0]
    before = {
        str(row["normalized_name"]): Decimal(str(row["remaining_quantity"]))
        for row in seeded_service.connection.execute(
            "SELECT normalized_name, remaining_quantity "
            "FROM pantry_batches WHERE unit = 'ml'"
        )
    }

    result = _pantry(
        seeded_service,
        "deduct",
        {
            "inventory_match_handle": chosen["workflow"][
                "inventory_match_handle"
            ],
            "quantity": "100",
            "unit": "ml",
            "source_text": "喝了这瓶牛奶里的100毫升",
        },
    )

    assert result["ok"] is True
    after = {
        str(row["normalized_name"]): Decimal(str(row["remaining_quantity"]))
        for row in seeded_service.connection.execute(
            "SELECT normalized_name, remaining_quantity "
            "FROM pantry_batches WHERE unit = 'ml'"
        )
    }
    chosen_name = str(chosen["normalized_name"])
    assert after[chosen_name] == before[chosen_name] - Decimal("100")
    assert all(
        after[name] == quantity
        for name, quantity in before.items()
        if name != chosen_name
    )


def test_selected_product_handle_avoids_requery_and_deducts_only_chosen_sku(
    seeded_service,
):
    milk_names = [
        str(row["normalized_name"])
        for row in seeded_service.connection.execute(
            "SELECT normalized_name FROM pantry_batches WHERE unit = 'ml' "
            "ORDER BY initial_quantity DESC"
        ).fetchall()
    ]
    assert len(milk_names) == 2
    seeded_service.connection.execute(
        "UPDATE pantry_batches SET initial_quantity = 250, "
        "remaining_quantity = 250, added_at = '2026-08-01T00:00:00Z' "
        "WHERE normalized_name = ?",
        (milk_names[0],),
    )
    seeded_service.connection.execute(
        "UPDATE pantry_batches SET initial_quantity = 200, "
        "remaining_quantity = 200, added_at = '2026-08-01T00:00:00Z' "
        "WHERE normalized_name = ?",
        (milk_names[1],),
    )
    seeded_service.connection.commit()
    _link_label(
        seeded_service,
        _batch_ids(seeded_service, milk_names[0])[0],
        nutrition=_LABEL | {"fiber_g": Decimal("0")},
    )

    search = _dispatch(
        seeded_service,
        "pantry",
        "search",
        {"search_text": "牛奶", "unit": "ml"},
    )
    chosen = next(
        item
        for item in search["data"]["candidates"]
        if Decimal(str(item["available_quantity"])) == Decimal("250")
    )
    unchosen = next(
        item
        for item in search["data"]["candidates"]
        if Decimal(str(item["available_quantity"])) == Decimal("200")
    )

    meal = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "喝了一瓶小瓶牛奶",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "小瓶牛奶",
                    "normalized_name": chosen["normalized_name"],
                    "amount": 250,
                    "unit": "ml",
                    "inventory_match_handle": chosen["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert meal["ok"] is True, meal
    remaining = dict(
        seeded_service.connection.execute(
            "SELECT normalized_name, remaining_quantity FROM pantry_batches "
            "WHERE unit = 'ml'"
        ).fetchall()
    )
    assert remaining[chosen["normalized_name"]] == 0
    assert remaining[unchosen["normalized_name"]] == 200
    stored_item = seeded_service.connection.execute(
        "SELECT raw_name, normalized_name, calories FROM meal_items"
    ).fetchone()
    assert stored_item is not None
    assert stored_item["raw_name"] == "小瓶牛奶"
    assert stored_item["normalized_name"] == chosen["normalized_name"]
    assert stored_item["calories"] == "160"


def test_valid_product_handle_deducts_with_unknown_location_and_undo_restores(
    service: DietService,
) -> None:
    product_name = "京东京造分离乳清蛋白粉"
    added = _pantry(
        service,
        "add",
        {
            "food_name": "京东京造 分离乳清蛋白粉（莓果味）",
            "normalized_name": product_name,
            "quantity": "350",
            "unit": "g",
            "added_at": "2026-08-01T00:00:00+08:00",
            "expires_at": "2027-08-01T00:00:00+08:00",
            "source_text": "蛋白粉还剩350克",
            "storage_location": "pantry",
        },
    )
    assert added["ok"] is True, added
    search = _pantry(
        service,
        "search",
        {"search_text": product_name, "unit": "g"},
    )
    assert search["ok"] is True, search
    selected = search["data"]["candidates"][0]

    recorded = _dispatch(
        service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-05T23:04:00+08:00",
            "meal_type": "snack",
            "source_text": "刚喝了半勺京东京造蛋白粉",
            "location_type": "unknown",
            "items": [
                {
                    "raw_name": "京东京造 分离乳清蛋白粉（莓果味）",
                    "normalized_name": product_name,
                    "amount": "15",
                    "unit": "g",
                    "portion_expression": "半勺｜约15克（估算）",
                    "consumed_weight_g": "15",
                    "inventory_match_handle": selected["workflow"][
                        "inventory_match_handle"
                    ],
                    "nutrition_estimate": _MEAL_FACTS,
                    "nutrition_basis": "per_100g",
                }
            ],
        },
    )
    assert recorded["ok"] is True, recorded
    if recorded["requires_confirmation"]:
        recorded = _dispatch(
            service,
            "meal",
            "commit_record",
            {
                "commit_handle": recorded["data"]["preview"]["workflow"][
                    "commit_handle"
                ],
                "confirmed": True,
            },
        )
    assert recorded["ok"] is True, recorded

    remaining = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = ?",
        (product_name,),
    ).fetchone()
    assert Decimal(str(remaining["remaining_quantity"])) == Decimal("335")
    movement = service.connection.execute(
        "SELECT movement_type, quantity, unit FROM pantry_movements "
        "WHERE pantry_batch_id = ("
        "SELECT id FROM pantry_batches WHERE normalized_name = ?"
        ") ORDER BY id DESC LIMIT 1",
        (product_name,),
    ).fetchone()
    assert movement["movement_type"] == "consume"
    assert Decimal(str(movement["quantity"])) == Decimal("15")
    assert movement["unit"] == "g"

    undone = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": recent_operation_handle(
                service,
                operation="undo",
                operation_type="meal_record",
            )
        },
    )
    assert undone["ok"] is True, undone
    restored = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = ?",
        (product_name,),
    ).fetchone()
    assert Decimal(str(restored["remaining_quantity"])) == Decimal("350")


def test_selected_piece_handle_supplies_omitted_unit_for_inventory_deduction(
    seeded_service,
    monkeypatch,
):
    seeded_service.connection.execute(
        """
        INSERT INTO pantry_batches (
            food_name, normalized_name, added_at, initial_quantity,
            remaining_quantity, unit, status, source, version, transaction_id
        ) VALUES (
            '鸡蛋', '鸡蛋', '2026-08-02T00:00:00Z', 100, 100, 'g',
            'active', 'seed', 1, 'txn_inventory_search_seed'
        )
        """
    )
    seeded_service.connection.commit()
    search = _pantry(
        seeded_service,
        "search",
        {"search_text": "鸡蛋", "unit": "piece"},
    )
    chosen = search["data"]["candidates"][0]
    fuzzy_calls = 0

    def unexpected_fuzzy_resolution(*_args, **_kwargs):
        nonlocal fuzzy_calls
        fuzzy_calls += 1
        raise AssertionError("verified handles must bypass fuzzy resolution")

    monkeypatch.setattr(
        inventory_matching,
        "resolve_meal_inventory_name",
        unexpected_fuzzy_resolution,
    )

    result = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "吃了库存里的一个鸡蛋",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "鸡蛋",
                    "normalized_name": "鸡蛋",
                    "consumed_weight_g": 1,
                    "inventory_deduction_weight_g": 1,
                    "nutrition_basis": "per_100g",
                    "nutrition_facts": _MEAL_FACTS,
                    "inventory_match_handle": chosen["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert result["ok"] is True, result
    remaining = dict(
        seeded_service.connection.execute(
            """
            SELECT unit, SUM(remaining_quantity)
            FROM pantry_batches
            WHERE normalized_name = '鸡蛋'
            GROUP BY unit
            """
        ).fetchall()
    )
    assert remaining == {"g": 100, "piece": 31}
    assert {effect["unit"] for effect in result["data"]["inventory_effects"]} == {
        "piece"
    }
    assert fuzzy_calls == 0


def test_selected_piece_handle_accepts_equivalent_supplied_unit_alias(
    seeded_service,
):
    search = _pantry(
        seeded_service,
        "search",
        {"search_text": "鸡蛋", "unit": "piece"},
    )
    chosen = search["data"]["candidates"][0]

    result = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "吃了库存里的一个鸡蛋",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "鸡蛋",
                    "normalized_name": "鸡蛋",
                    "amount": 1,
                    "unit": "pieces",
                    "consumed_weight_g": 50,
                    "nutrition_basis": "per_100g",
                    "nutrition_facts": _MEAL_FACTS,
                    "inventory_match_handle": chosen["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert result["ok"] is True, result
    assert {effect["unit"] for effect in result["data"]["inventory_effects"]} == {
        "piece"
    }


@pytest.mark.parametrize("handle_state", ("nonexistent", "wrong_operation"))
def test_well_formed_unusable_product_handles_fail_closed(
    seeded_service,
    handle_state,
):
    search = _pantry(
        seeded_service,
        "search",
        {"search_text": "鸡蛋", "unit": "piece"},
    )
    chosen = search["data"]["candidates"][0]
    handle = chosen["workflow"]["inventory_match_handle"]
    if handle_state == "nonexistent":
        handle = "wfh_" + "a" * 43
    else:
        seeded_service.connection.execute(
            """
            UPDATE operation_previews
            SET operation_type = 'pantry_batch_reference'
            WHERE operation_type = 'pantry_product_reference'
            """
        )
        seeded_service.connection.commit()
    before = tuple(
        seeded_service.connection.execute(
            """
            SELECT id, remaining_quantity, version
            FROM pantry_batches
            ORDER BY id
            """
        ).fetchall()
    )

    result = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "吃了库存里的一个鸡蛋",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "鸡蛋",
                    "normalized_name": "鸡蛋",
                    "amount": 1,
                    "unit": "piece",
                    "inventory_match_handle": handle,
                }
            ],
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert tuple(
        seeded_service.connection.execute(
            """
            SELECT id, remaining_quantity, version
            FROM pantry_batches
            ORDER BY id
            """
        ).fetchall()
    ) == before


@pytest.mark.parametrize(
    "identity_override",
    [
        {"normalized_name": "different milk"},
        {"unit": "g"},
    ],
    ids=["name", "unit"],
)
def test_selected_product_handle_rejects_identity_mismatch(
    seeded_service,
    identity_override,
):
    search = _dispatch(
        seeded_service,
        "pantry",
        "search",
        {"search_text": "牛奶", "unit": "ml"},
    )
    chosen = search["data"]["candidates"][0]
    item = {
        "raw_name": "小瓶牛奶",
        "normalized_name": chosen["normalized_name"],
        "amount": 250,
        "unit": "ml",
        "inventory_match_handle": chosen["workflow"][
            "inventory_match_handle"
        ],
    } | identity_override

    result = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "喝了一瓶小瓶牛奶",
            "location_type": "home",
            "items": [item],
        },
    )

    assert result["ok"] is False
    if "unit" in identity_override:
        assert result["error"] == {
            "code": "INVALID_INPUT",
            "message": "The request is invalid",
            "field": "items[0].unit",
            "reason": "incompatible",
            "expected": (
                "the base unit or verified display unit from pantry search"
            ),
            "retryable": True,
        }
        return
    assert result["error"] == {
        "code": "INVALID_INPUT",
        "message": "The request is invalid",
        "field": "items[0].inventory_match_handle",
        "reason": "identity_mismatch",
        "expected": "the normalized_name and unit returned with this handle",
        "retryable": True,
    }


def test_expired_selected_product_handle_is_stale(seeded_service):
    search = _dispatch(
        seeded_service,
        "pantry",
        "search",
        {"search_text": "牛奶", "unit": "ml"},
    )
    chosen = search["data"]["candidates"][0]
    seeded_service.connection.execute(
        "UPDATE operation_previews SET created_at = '1999-01-01T00:00:00Z', "
        "expires_at = '2000-01-01T00:00:00Z' "
        "WHERE operation_type = 'pantry_product_reference'"
    )
    seeded_service.connection.commit()

    result = _dispatch(
        seeded_service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-02T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "喝了一瓶小瓶牛奶",
            "location_type": "home",
            "items": [
                {
                    "raw_name": "小瓶牛奶",
                    "normalized_name": chosen["normalized_name"],
                    "amount": 250,
                    "unit": "ml",
                    "inventory_match_handle": chosen["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "STALE_PREVIEW"


def test_summary_returns_one_uniform_linked_snapshot(seeded_service):
    normalized_name = "小象巴氏乳"
    _link_label(seeded_service, _batch_ids(seeded_service, normalized_name)[0])

    result = _pantry(
        seeded_service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "summary",
        },
    )

    assert result["ok"] is True
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "uniform"
    assert candidate["nutrition_available"] is True
    assert set(candidate["nutrition"]) == {
        "serving_basis",
        "source_grade",
        "calories_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "fiber_g",
        "sodium_mg",
    }
    assert "sugar_g" not in candidate["nutrition"]


def test_full_returns_one_uniform_linked_snapshot(seeded_service):
    normalized_name = "小象巴氏乳"
    _link_label(seeded_service, _batch_ids(seeded_service, normalized_name)[0])

    result = _pantry(
        seeded_service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "full",
        },
    )

    assert result["ok"] is True, result
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "uniform"
    assert candidate["nutrition_available"] is True
    assert candidate["nutrition"]["serving_basis"] == "per_100ml"
    assert candidate["nutrition"]["source_grade"] == "B"
    assert candidate["nutrition"]["sugar_g"] == "4.8"


@pytest.mark.parametrize(
    "stored_snapshot",
    (
        "{not-json",
        "[]",
    ),
    ids=("corrupted", "non-object"),
)
def test_public_full_search_rejects_invalid_stored_snapshot(
    seeded_service,
    stored_snapshot,
):
    normalized_name = "小象巴氏乳"
    _link_label(seeded_service, _batch_ids(seeded_service, normalized_name)[0])
    seeded_service.connection.execute("PRAGMA ignore_check_constraints = ON")
    try:
        seeded_service.connection.execute(
            "UPDATE pantry_nutrition_links SET nutrition_snapshot_json = ?",
            (stored_snapshot,),
        )
        seeded_service.connection.commit()
    finally:
        seeded_service.connection.execute("PRAGMA ignore_check_constraints = OFF")
    workflow_count = seeded_service.connection.execute(
        "SELECT COUNT(*) FROM operation_previews"
    ).fetchone()[0]

    result = _pantry(
        seeded_service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "full",
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["message"] == "The request is invalid"
    assert seeded_service.connection.execute(
        "SELECT COUNT(*) FROM operation_previews"
    ).fetchone()[0] == workflow_count


def test_mixed_batch_labels_are_not_silently_merged(seeded_service):
    normalized_name = "小象巴氏乳"
    first_batch_id = _batch_ids(seeded_service, normalized_name)[0]
    second_batch_id = _add_matching_batch(seeded_service, normalized_name)
    _link_label(seeded_service, first_batch_id)
    _link_label(
        seeded_service,
        second_batch_id,
        nutrition=_LABEL | {"calories_kcal": Decimal("72")},
    )

    result = _pantry(
        seeded_service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "full",
        },
    )

    assert result["ok"] is True
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "mixed"
    assert candidate["nutrition_available"] is False
    assert "nutrition" not in candidate


def test_partially_linked_batches_report_partial_without_nutrition(
    seeded_service,
):
    normalized_name = "小象巴氏乳"
    first_batch_id = _batch_ids(seeded_service, normalized_name)[0]
    _add_matching_batch(seeded_service, normalized_name)
    _link_label(seeded_service, first_batch_id)

    result = _pantry(
        seeded_service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "full",
        },
    )

    assert result["ok"] is True
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "partial"
    assert candidate["nutrition_available"] is False
    assert "nutrition" not in candidate


def test_search_projects_uniform_package_and_binds_private_snapshot(service):
    normalized_name = "小象无糖豆浆"
    _add_boxed_product(
        service,
        normalized_name=normalized_name,
        quantity="500",
        display_quantity="2",
        nutrition_profile={
            "normalized_name": normalized_name,
            "serving_basis": "per_100ml",
            "nutrition": {
                "calories_kcal": "33",
                "protein_g": "3.5",
                "fat_g": "1.8",
                "carbohydrate_g": "2",
                "fiber_g": None,
                "sodium_mg": None,
            },
            "source_text": "包装营养标签",
            "source_grade": "A",
        },
    )

    result = _pantry(
        service,
        "search",
        {
            "search_text": "豆浆",
            "unit": "ml",
            "nutrition_mode": "none",
        },
    )

    assert result["ok"] is True, result
    candidate = result["data"]["candidates"][0]
    assert candidate["remaining_display_quantity"] == "2"
    assert candidate["display_unit"] == "盒"
    assert candidate["base_quantity_per_display_unit"] == "250"
    assert candidate["package_hierarchy"] == []
    assert candidate["nutrition_status"] == "not_requested"
    assert "nutrition" not in candidate

    handle = candidate["workflow"]["inventory_match_handle"]
    assert re.fullmatch(r"wfh_[a-z0-9_-]+", handle)
    stored, versions = _stored_workflow(service, handle)
    assert stored["normalized_name"] == normalized_name
    assert stored["base_unit"] == "ml"
    assert stored["package"] == {
        "status": "uniform",
        "display_unit": "盒",
        "base_quantity_per_display_unit": "250",
        "package_hierarchy": [],
    }
    assert stored["nutrition"]["status"] == "uniform"
    assert stored["nutrition"]["serving_basis"] == "per_100ml"
    assert stored["nutrition"]["source_grade"] == "A"
    assert stored["nutrition"]["snapshot"]["fiber_g"] is None
    assert stored["nutrition"]["snapshot"]["sodium_mg"] is None
    assert len(versions) == 1
    assert versions[0]["batch_id"] > 0
    assert versions[0]["version"] == 1


@pytest.mark.parametrize(
    "package_rows, expected_status",
    (
        ((None,), "none"),
        (("250", None), "partial"),
        (("250", "300"), "mixed"),
    ),
    ids=("none", "partial", "mixed"),
)
def test_package_projection_never_guesses_non_uniform_packages(
    service,
    package_rows,
    expected_status,
):
    normalized_name = f"测试{expected_status}包装饮品"
    for index, factor in enumerate(package_rows):
        _add_boxed_product(
            service,
            normalized_name=normalized_name,
            quantity=factor or "250",
            display_quantity=("1" if factor is not None else None),
            base_quantity_per_display_unit=factor,
        )

    result = _pantry(
        service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "none",
        },
    )

    assert result["ok"] is True, result
    candidate = result["data"]["candidates"][0]
    assert "remaining_display_quantity" not in candidate
    assert "display_unit" not in candidate
    assert "base_quantity_per_display_unit" not in candidate
    assert "package_hierarchy" not in candidate

    stored, versions = _stored_workflow(
        service,
        candidate["workflow"]["inventory_match_handle"],
    )
    assert stored["package"] == {"status": expected_status}
    assert len(versions) == len(package_rows)


def test_natural_package_handle_derives_per_serving_nutrition_and_undo_restores(
    service: DietService,
) -> None:
    normalized_name = "UAT19原味燕麦奶"
    _add_boxed_product(
        service,
        normalized_name=normalized_name,
        quantity="500",
        display_quantity="2",
        nutrition_profile={
            "normalized_name": normalized_name,
            "serving_basis": "per_serving",
            "nutrition": {
                "calories_kcal": "120",
                "protein_g": "4",
                "fat_g": "3",
                "carbohydrate_g": "18",
                "fiber_g": "2",
                "sodium_mg": "100",
            },
            "source_text": "包装标注每盒营养",
            "source_grade": "A",
        },
    )
    search = _pantry(
        service,
        "search",
        {"search_text": normalized_name, "nutrition_mode": "summary"},
    )
    assert search["ok"] is True, search
    selected = search["data"]["candidates"][0]

    recorded = _dispatch(
        service,
        "meal",
        "record",
        {
            "_turn_completed_consumption": True,
            "occurred_at": "2026-08-06T23:20:00+08:00",
            "meal_type": "other",
            "source_text": "刚喝了一盒库存里的UAT19原味燕麦奶",
            "location_type": "unknown",
            "items": [
                {
                    "raw_name": "UAT19原味燕麦奶 500ml（2盒×250ml）",
                    "normalized_name": normalized_name,
                    "amount": "1",
                    "unit": "盒",
                    "inventory_match_handle": selected["workflow"][
                        "inventory_match_handle"
                    ],
                }
            ],
        },
    )

    assert recorded["ok"] is True, recorded
    item = recorded["data"]["meal"]["items"][0]
    assert item["consumed_volume_ml"] == "250"
    assert item["consumed_servings"] == "1"
    assert item["calories"] == "120"
    assert recorded["data"]["meal"]["total_calories"] == "120"
    assert "剩 250ml（-250ml）" in recorded["data"]["rendered_receipt"]

    undone = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": recent_operation_handle(
                service,
                operation="undo",
                operation_type="meal_record",
            )
        },
    )
    assert undone["ok"] is True, undone
    remaining = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE lower(normalized_name) = lower(?)",
        (normalized_name,),
    ).fetchone()
    assert remaining is not None, {
        "undo": undone,
        "batches": [
            dict(row)
            for row in service.connection.execute(
                "SELECT id, normalized_name, remaining_quantity, status "
                "FROM pantry_batches ORDER BY id"
            ).fetchall()
        ],
    }
    assert Decimal(str(remaining["remaining_quantity"])) == Decimal("500")


def test_inventory_match_handle_becomes_stale_when_bound_batch_changes(service):
    normalized_name = "句柄失效测试豆浆"
    _add_boxed_product(
        service,
        normalized_name=normalized_name,
        quantity="500",
        display_quantity="2",
    )
    search = _pantry(
        service,
        "search",
        {
            "search_text": normalized_name,
            "unit": "ml",
            "nutrition_mode": "none",
        },
    )
    chosen = search["data"]["candidates"][0]
    service.connection.execute(
        """
        UPDATE pantry_batches
        SET remaining_quantity = 250, version = version + 1
        WHERE normalized_name = ?
        """,
        (normalized_name,),
    )
    service.connection.commit()

    result = _dispatch(
        service,
        "meal",
        "record",
        {
            "occurred_at": "2026-08-03T08:00:00+08:00",
            "meal_type": "breakfast",
            "source_text": "喝了一盒豆浆",
            "location_type": "home",
            "items": [
                {
                    "raw_name": normalized_name,
                    "normalized_name": normalized_name,
                    "amount": "250",
                    "unit": "ml",
                    "inventory_match_handle": chosen["workflow"][
                        "inventory_match_handle"
                    ],
                    "nutrition_facts": _MEAL_FACTS,
                    "nutrition_basis": "consumed_total",
                }
            ],
        },
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "STALE_PREVIEW"
