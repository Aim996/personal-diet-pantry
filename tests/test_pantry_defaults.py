from __future__ import annotations

from datetime import datetime, timezone

from personal_diet_pantry.pantry_defaults import resolve_pantry_defaults


ADDED_AT = datetime(2026, 8, 7, 11, 20, tzinfo=timezone.utc)


def test_explicit_storage_location_wins_and_is_normalized() -> None:
    defaults = resolve_pantry_defaults(
        food_name="苹果",
        source_text="刚买了俩苹果，放冰箱了",
        added_at=ADDED_AT,
        storage_location="冰箱",
        expires_at=None,
    )

    assert defaults.storage_location == "冷藏"
    assert defaults.storage_location_source == "user"
    assert defaults.expires_at > ADDED_AT
    assert defaults.expiry_source == "estimated"


def test_storage_is_inferred_by_food_kind() -> None:
    frozen = resolve_pantry_defaults(
        food_name="速冻水饺",
        source_text="买了两袋速冻水饺",
        added_at=ADDED_AT,
        storage_location=None,
        expires_at=None,
    )
    dry = resolve_pantry_defaults(
        food_name="大米",
        source_text="买了袋大米",
        added_at=ADDED_AT,
        storage_location=None,
        expires_at=None,
    )
    yogurt = resolve_pantry_defaults(
        food_name="酸奶",
        source_text="买了两盒酸奶",
        added_at=ADDED_AT,
        storage_location=None,
        expires_at=None,
    )

    assert (frozen.storage_location, frozen.storage_location_source) == (
        "冷冻",
        "inferred",
    )
    assert (dry.storage_location, dry.storage_location_source) == (
        "常温",
        "inferred",
    )
    assert (yogurt.storage_location, yogurt.storage_location_source) == (
        "冷藏",
        "inferred",
    )


def test_explicit_expiry_is_never_replaced_by_an_estimate() -> None:
    explicit = datetime(2026, 8, 20, 15, 59, 59, tzinfo=timezone.utc)
    defaults = resolve_pantry_defaults(
        food_name="酸奶",
        source_text="酸奶8月20日到期",
        added_at=ADDED_AT,
        storage_location=None,
        expires_at=explicit,
    )

    assert defaults.expires_at == explicit
    assert defaults.expiry_source == "user"


def test_user_location_overrides_food_kind_inference() -> None:
    defaults = resolve_pantry_defaults(
        food_name="酸奶",
        source_text="这盒酸奶先放常温",
        added_at=ADDED_AT,
        storage_location="常温",
        expires_at=None,
    )

    assert defaults.storage_location == "常温"
    assert defaults.storage_location_source == "user"
