from __future__ import annotations

from decimal import Decimal

from personal_diet_pantry.intake_identity import (
    IntakeIdentity,
    IntakeIdentityItem,
    intake_event_fingerprint,
)


def _event(
    *,
    occurred_at: str = "2026-07-30T00:00:15Z",
    source_text: str = "喝了豆浆和吃了红薯",
    items: tuple[IntakeIdentityItem, ...] | None = None,
) -> IntakeIdentity:
    return IntakeIdentity(
        occurred_at=occurred_at,
        meal_type="breakfast",
        location_type="home",
        source_text=source_text,
        items=items
        or (
            IntakeIdentityItem(
                normalized_name="soy milk",
                amount=Decimal("500"),
                unit="ml",
                consumed_volume_ml=Decimal("500"),
            ),
            IntakeIdentityItem(
                normalized_name="sweet potato",
                amount=Decimal("2"),
                unit="piece",
                consumed_weight_g=Decimal("240"),
            ),
        ),
    )


def test_fingerprint_normalizes_time_text_decimal_and_item_order() -> None:
    left = _event()
    right = _event(
        occurred_at="2026-07-30T00:00:45+00:00",
        source_text="换一种说法也不改变已发生事实",
        items=(
            IntakeIdentityItem(
                normalized_name=" SWEET　POTATO ",
                amount=Decimal("2.0"),
                unit="PIECE",
                consumed_weight_g=Decimal("240.0"),
            ),
            IntakeIdentityItem(
                normalized_name=" SOY  MILK ",
                amount=Decimal("500.0"),
                unit="ML",
                consumed_volume_ml=Decimal("500.00"),
            ),
        ),
    )

    assert intake_event_fingerprint(left) == intake_event_fingerprint(right)


def test_fingerprint_changes_when_consumed_quantity_changes() -> None:
    changed = _event(
        items=(
            IntakeIdentityItem(
                normalized_name="soy milk",
                amount=Decimal("300"),
                unit="ml",
                consumed_volume_ml=Decimal("300"),
            ),
        ),
    )

    assert intake_event_fingerprint(_event()) != intake_event_fingerprint(
        changed
    )

