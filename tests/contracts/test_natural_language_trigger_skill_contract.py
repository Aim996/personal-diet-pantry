from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = (
    PROJECT_ROOT
    / "skills"
    / "personal-diet-pantry"
    / "SKILL.md"
)
REPLY_PATH = (
    PROJECT_ROOT
    / "skills"
    / "personal-diet-pantry"
    / "references"
    / "reply-style-and-error-boundaries.md"
)


def _skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter(skill: str) -> dict[str, str]:
    marker, raw, _body = skill.split("---", maxsplit=2)
    assert marker == ""
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict)
    return loaded


def _activation(skill: str) -> str:
    start = skill.index("## Natural-language activation")
    end = skill.index("## Readiness")
    assert start < end
    return skill[start:end]


def test_frontmatter_discovers_natural_personal_diet_intent_without_name() -> None:
    skill = _skill()
    frontmatter = _frontmatter(skill)

    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "personal-diet-pantry"
    description = frontmatter["description"]
    assert description.startswith("Use when ")
    for phrase in (
        "meals",
        "nutritious drinks",
        "plain water",
        "cooking or leftovers",
        "pantry stock or expiry",
        "nutrition plans or reports",
        "goals or preferences",
        "undo/redo",
        "body weight",
        "diet_*",
        "without naming the Skill",
        "writing/translation",
        "generic knowledge",
        "image requests",
        "code/examples",
        "events explicitly not done",
    ):
        assert phrase in description


def test_activation_contract_precedes_readiness_and_applies_full_skill() -> None:
    activation = _activation(_skill())

    for phrase in (
        "complete intent",
        "Chinese colloquial wording",
        "omitted subjects",
        "spoken quantities",
        "typos",
        "incomplete grammar",
        "clearly completed",
        "Plans, hypotheticals, denials, and non-occurrence",
        "zero write calls",
        "the rest of this Skill remains mandatory",
        "Telegram",
        "WebUI",
    ):
        assert phrase in activation


def test_activation_keeps_all_seven_typed_tool_domains() -> None:
    skill = _skill()

    for tool in (
        "diet_meal",
        "diet_water",
        "diet_weight",
        "diet_pantry",
        "diet_transaction",
        "diet_report",
        "diet_system",
    ):
        assert f"`{tool}`" in skill


def test_activation_routes_through_capability_scoped_readiness() -> None:
    skill = _skill()

    assert "## Preferred capability routes" in skill
    assert "readiness is per required capability" in skill.casefold()
    assert "all seven tools must exist" not in skill


def test_meal_time_defaults_use_the_trusted_clock_without_overriding_user_time() -> None:
    skill = _skill()

    for phrase in (
        "`diet_meal record`, `preview_record`, and `record_cooking`",
        "omitted `occurred_at`",
        "trusted system clock",
        "explicit time",
        "Never invent `context.now`",
    ):
        assert phrase in skill


def test_bare_number_never_writes_body_weight_without_explicit_wording() -> None:
    skill = _skill()

    assert "A bare number without explicit body-weight wording" in skill
    assert "must not create a body-weight record" in skill
    assert "Non-occurrence always wins over weight" in skill


def test_public_reply_contract_hides_internal_implementation() -> None:
    skill = _skill()
    reply = REPLY_PATH.read_text(encoding="utf-8")

    for phrase in (
        "Never show tool names",
        "Never expose those diagnostics",
        "no internal identifier, path, credential, stack trace",
    ):
        assert phrase in skill
    for phrase in (
        "Do not narrate tool selection, handlers, scaling mechanics, retries",
        "avoid command syntax and engineering jargon",
    ):
        assert phrase in reply


def test_triggering_change_does_not_grow_the_runtime_skill() -> None:
    assert len(_skill().splitlines()) <= 724


def test_clear_count_intake_and_unique_correction_are_direct_but_vague_intake_is_not() -> None:
    skill = _skill()

    for phrase in (
        "one corn or one sausage",
        "record directly in the same turn",
        "natural count and the estimated gram weight",
        "open-ended vague quantity",
        "zero-write preview",
        "handle-bound correction",
        "update directly and atomically",
        "never ask for a second confirmation",
        "exactly one of `nutrition_facts` or `nutrition_estimate`",
        "Pure water remains compact",
        "Preserve the user's natural classifier",
        "inedible core, peel, shell, bone, or pit",
        "gross whole-item weight",
        "edible portion",
        "可食部（玉米粒）约90克（估算）",
        "complete `portion_expression`",
        "does not infer an edible-part label",
        "A/B evidence uses `nutrition_facts`",
        "C/D evidence uses `nutrition_estimate`",
        "one numbered row per returned meal",
        "identical-looking records remain separate",
    ):
        assert phrase in skill

    assert "If the wording has no safe mapping, ask one short question" not in skill
    normalized_skill = " ".join(skill.casefold().split())
    for phrase in (
        "meal type and location are analytical labels",
        "must not block a clear completed intake",
        "omit them",
        "`other` and `unknown`",
    ):
        assert phrase.casefold() in normalized_skill


def test_v073_routes_inventory_consumption_through_the_meal_transaction() -> None:
    skill = _skill()

    for phrase in (
        "packaged pantry intake",
        "completed pantry food or nutritious drink consumption",
        "non-intake product use or discard",
        "prepared leftover eaten",
        "calendar expiry date",
        "`diet_pantry search` → `inventory_match_handle` → `diet_meal record`",
        "`nutrition_mode: \"summary\"`",
        "keep the user's package amount and unit unchanged",
        "`amount: 1`, `unit: 盒`",
        "do not call `diet_pantry query` after the successful search",
        "`diet_pantry search` → `diet_pantry deduct` or `discard`",
        "`prepared_food_handle` → `diet_meal record_prepared`",
        "never call pantry deduct separately for completed consumption",
        "omit unknown nutrition properties",
        "`hydration_ml`",
        "never send `hydration`",
    ):
        assert phrase in skill


def test_current_meal_deletion_is_not_routed_as_historical_transaction_undo() -> None:
    skill = _skill()

    for phrase in (
        "explicit whole current meal-record deletion",
        "call `diet_meal delete` once",
        "do not call `diet_transaction undo`",
        "same-session meal handle",
        "query only meal candidates",
    ):
        assert phrase in skill


def test_v073_recovery_and_inventory_rules_are_explicit() -> None:
    skill = _skill()

    for phrase in (
        "Multiple physical batches of one product are not product ambiguity",
        "Let the tool convert package display units",
        "Prefer `expiry_date`",
        "Never repeat an unchanged failure fingerprint",
    ):
        assert phrase in skill
