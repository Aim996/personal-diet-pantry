from __future__ import annotations

from pathlib import Path
import re

from scripts.lint_skill import lint_skill


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "personal-diet-pantry"
MAIN = SKILL_ROOT / "SKILL.md"
PRODUCT_INVARIANTS = ROOT / "docs" / "PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md"
REFERENCE_NAMES = {
    "meal-and-nutrition.md",
    "cooking-and-leftovers.md",
    "pantry-and-expiry.md",
    "water-and-progress.md",
    "weight-tracking.md",
    "transactions-and-maintenance.md",
    "goals-preferences-learning.md",
    "recipes-shopping-and-cost.md",
    "exports-deletion-and-privacy.md",
    "images-and-structured-extraction.md",
    "reply-style-and-error-boundaries.md",
    "time-and-query-scopes.md",
    "estimation-and-confirmation.md",
    "tool-budget-and-recovery.md",
}


def test_main_skill_is_a_bounded_self_contained_runtime_core() -> None:
    main = MAIN.read_text(encoding="utf-8")
    references = SKILL_ROOT / "references"

    assert len(main.splitlines()) <= 400
    assert len(main.encode("utf-8")) <= 20_000
    assert {path.name for path in references.glob("*.md")} == REFERENCE_NAMES
    for name in REFERENCE_NAMES:
        assert f"(references/{name})" not in main
        reference = (references / name).read_text(encoding="utf-8")
        assert len(reference.splitlines()) >= 12
        assert not re.search(r"\]\((?:\.\./)?references/", reference)
    assert "Runtime reference reads are exactly 0" in main
    assert "runtime agents must not read or route through it" in main


def test_progressive_split_preserves_critical_behavior_contracts() -> None:
    bundle = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (MAIN, *(SKILL_ROOT / "references").glob("*.md"))
    )

    for phrase in (
        "Non-occurrence always wins over weight",
        "`measured_at` 不是公共参数",
        "7日均值：",
        "nutrition_basis",
        "consumed_total",
        "inventory_effects",
        "record_cooking",
        "DATABASE_INTEGRITY_ERROR",
        "zero tool calls",
        "Telegram",
        "WebUI",
        "Supplemental facts are not confirmation",
        "Supplemental facts with explicit final write authorization",
        "do not create a replacement preview",
        "reuse the visible inventory_match_handle with the supplied nutrition facts",
        "preserve the user's raw product wording",
        "fried food with unknown oil",
        "over_by",
        "same turn",
        "do not preflight",
        "reuse the returned handle",
        "one aggregate report call",
        "Preferred capability routes",
        "verified equivalent capability",
        "normalized arguments",
        "diet_pantry search",
        "inventory_match_handle",
        "search before browse",
        "nutrition_mode",
        "full inventory only when the user explicitly asks",
        "food_name + unit + display_quantity + display_unit + base_quantity_per_display_unit",
        "raw_name + normalized_name + amount + unit + inventory_match_handle",
        "validated handle and exact amount bind the meal write and pantry deduction",
        "Missing label nutrients remain unknown",
        "Never add a model estimate only to make a write pass",
        "Production date and expiry are optional for ordinary pantry intake",
        "explicit user facts always override those defaults",
    ):
        assert phrase in bundle

    for obsolete in (
        "retry the same action once",
        "allow at most one correction",
        "genuinely cross-domain intent loads at most two",
    ):
        assert obsolete not in MAIN.read_text(encoding="utf-8")


def test_post_commit_progress_receipt_preserves_the_legacy_renderer() -> None:
    main = MAIN.read_text(encoding="utf-8")
    progress = (
        SKILL_ROOT / "references" / "water-and-progress.md"
    ).read_text(encoding="utf-8")
    reply = (
        SKILL_ROOT / "references" / "reply-style-and-error-boundaries.md"
    ).read_text(encoding="utf-8")
    invariants = PRODUCT_INVARIANTS.read_text(encoding="utf-8")

    for phrase in (
        "the final successful tool result",
        "six metrics in fixed order",
        "every metric uses exactly two lines",
        "never reuse any field from an earlier receipt",
    ):
        assert phrase in main.casefold()

    for phrase in (
        "Never reuse totals or a progress snapshot from an earlier turn",
        "Do not call `diet_report progress` merely to rebuild a successful write receipt",
        "The first line contains only Emoji, name, bar, and percentage.",
        "an optional current-turn increment",
        "increment_percentage = round(increment / target * 100)",
        "positive increments below 1% use `+<1%`",
        "Do not wrap increments in parentheses.",
        "do not generate unsolicited judgment",
        "Over-target percentages remain real",
        "Fiber with an explicitly unknown current",
        "Water always uses current milliliters and target liters",
        "Do not add a progress-summary heading such as `📊 今日进度：`",
    ):
        assert phrase in progress

    assert "The compact renderer is two lines:" not in progress
    assert "Use short bars only when they remain legible." not in progress
    assert "Success receipts should usually fit in two or three lines." not in reply
    assert "后续迭代不得静默删除或弱化" in invariants
    assert "不得为了重建回执额外调用 `diet_report progress`" in invariants
    assert "每项固定两行" in invariants


def test_readiness_and_disclosure_are_capability_scoped() -> None:
    main = MAIN.read_text(encoding="utf-8")

    assert "readiness is per required capability" in main.casefold()
    assert "Runtime reference reads are exactly 0" in main
    assert "do not open `references/`" in main


def test_pantry_nutrition_disclosure_requires_explicit_full_label_request() -> None:
    pantry = (
        SKILL_ROOT / "references" / "pantry-and-expiry.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "Allowed `nutrition_mode` values are exactly `none | summary | full`.",
        "`full` is allowed only when the user explicitly asks for the complete/full nutrition label.",
        "A single-field nutrition question must not silently promote `summary` to `full`.",
        "If `summary` omits that field, say it is unavailable and ask whether to load the full label.",
    ):
        assert phrase in pantry


def test_inventory_lookup_wording_matches_public_search_and_handle_contract() -> None:
    main = MAIN.read_text(encoding="utf-8")
    pantry = (
        SKILL_ROOT / "references" / "pantry-and-expiry.md"
    ).read_text(encoding="utf-8")

    assert (
        "Use `query` with `normalized_name` only when the canonical name is already known."
        in main
    )
    assert (
        "To locate the user's original wording, use `search` with `search_text`."
        in main
    )
    assert "downstream action" in pantry
    assert "downstream meal item" not in pantry


def test_skill_routing_evaluation_is_complete_and_perfect() -> None:
    result = lint_skill(
        SKILL_ROOT,
        ROOT / "tests" / "skill-evals" / "routing.yaml",
    )

    assert result.case_count >= 34
    assert result.domain_coverage == {
        "meal",
        "water",
        "weight",
        "pantry",
        "transaction",
        "report",
        "system",
        "zero-write",
    }
    assert result.safety_score == 1
    assert result.overall_score >= 0.98


def test_bare_number_is_not_a_body_weight_write_contract() -> None:
    main = MAIN.read_text(encoding="utf-8")

    assert "body weight including a reasonable standalone number" not in main
    assert '`diet_weight(action="record", weight=105, unit="kg")`' not in main
    assert "A bare number without explicit body-weight wording" in main


def test_v073_details_stay_in_their_single_required_references() -> None:
    pantry = (SKILL_ROOT / "references" / "pantry-and-expiry.md").read_text(
        encoding="utf-8"
    )
    cooking = (
        SKILL_ROOT / "references" / "cooking-and-leftovers.md"
    ).read_text(encoding="utf-8")
    meal = (SKILL_ROOT / "references" / "meal-and-nutrition.md").read_text(
        encoding="utf-8"
    )
    recovery = (
        SKILL_ROOT / "references" / "reply-style-and-error-boundaries.md"
    ).read_text(encoding="utf-8")

    assert "inventory_match_handle" in pantry
    assert "product identity" in pantry
    assert "package display unit" in pantry
    assert "expiry_date" in pantry
    assert "prepared_food_handle" in cooking
    assert "record_prepared" in cooking
    assert "never recalculate" in cooking
    assert "record_prepared" in meal
    assert "write_committed" in recovery
    assert "preview_ready" in recovery
    assert "no_op" in recovery
    assert "failed" in recovery


def test_image_reference_requires_confirmed_router_projection() -> None:
    text = (
        SKILL_ROOT / "references" / "images-and-structured-extraction.md"
    ).read_text(encoding="utf-8")
    raw_images = re.compile(
        r"Raw images are interpreted only by `image-intake-router`\. This Skill does not perform image\n"
        r"recognition, OCR, or its own extraction",
    )
    unconfirmed = re.compile(
        r"The router's unconfirmed projections are candidates and make zero write calls\. The initial\n"
        r"image turn, a question about its preview, and any corrected or revised preview remain\n"
        r"unconfirmed; do not call a pantry write tool until the latest router preview is confirmed\.",
    )
    confirmed_items = re.compile(
        r"After confirmation, only `diet_projection\.items` may call `diet_pantry\(action=\"add\"\)`\.",
    )
    excluded_payloads = re.compile(
        r"`item_audit`,\n`excluded_items`, and `uncertain_items` explain the router preview only: do not pass them to\n"
        r"any tool and make zero write calls for them\. An empty `items` array also makes zero writes\.",
    )

    for rule in (raw_images, unconfirmed, confirmed_items, excluded_payloads):
        assert rule.search(text)

    positions = [
        text.index("Raw images are interpreted only by"),
        text.index("The router's unconfirmed projections"),
        text.index("After confirmation, only `diet_projection.items`"),
        text.index("`item_audit`,"),
    ]
    assert positions == sorted(positions)
