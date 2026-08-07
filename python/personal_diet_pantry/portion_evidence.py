"""Small, deterministic normalization for user-visible portion evidence."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Iterable


_EDIBLE_WEIGHT = re.compile(
    r"可食部\s*[（(]\s*"
    r"(?P<label>[\u3400-\u9fffA-Za-z0-9·\- ]{1,20})"
    r"\s*[）)]\s*"
    r"(?P<approx>约|大约|大概|估计|估算|差不多)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:克|g)",
    flags=re.IGNORECASE,
)
_GRAM_VALUE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:克|g)", re.IGNORECASE)
_GRAM_EXPRESSION = re.compile(
    r"^\s*(?:(?:约|大约|大概|估计|估算|差不多)\s*)?"
    r"\d+(?:\.\d+)?\s*(?:克|g)"
    r"(?:\s*(?:左右|（估算）|\(估算\)))?\s*$",
    re.IGNORECASE,
)
_APPROXIMATE_WORDS = ("约", "大约", "大概", "估计", "估算", "差不多", "左右", "可能")
_ESTIMATE_MARKERS = _APPROXIMATE_WORDS + ("（估算）", "(估算)")
_EXPLICIT_MEASUREMENT_OBJECTS = (
    "可食部",
    "毛重",
    "总重",
    "净重",
    "带芯",
    "带皮",
    "带骨",
    "连壳",
    "带壳",
    "去皮",
    "去骨",
    "去壳",
    "果肉",
    "果仁",
)
_MEASURE_UNITS = frozenset(
    {
        "g",
        "克",
        "kg",
        "千克",
        "公斤",
        "ml",
        "毫升",
        "l",
        "升",
    }
)


def normalize_portion_expression(
    *,
    portion_expression: str | None,
    amount: Decimal | None,
    unit: str | None,
    consumed_weight_g: Decimal | None,
    source_text: str,
    quantity_estimated: bool,
    nutrition_source: str | None,
    nutrition_uncertainty: str | None,
) -> str | None:
    """Keep count, edible object, weight, and estimate state in one expression.

    The function consumes only evidence already present in the request. It does not
    infer food anatomy from a product name and therefore cannot turn a missing label
    into a fabricated edible part.
    """

    expression = _clean(portion_expression)
    if (
        amount is None
        or amount <= 0
        or unit is None
        or not unit.strip()
        or unit.strip().casefold() in _MEASURE_UNITS
        or consumed_weight_g is None
        or consumed_weight_g <= 0
    ):
        return expression

    count = f"{_number(amount)}{unit.strip()}"
    weight = _number(consumed_weight_g)
    user_exact_weight = _source_declares_exact_weight(source_text, consumed_weight_g)
    edible_evidence = _matching_edible_evidence(
        (expression, nutrition_uncertainty, nutrition_source),
        consumed_weight_g,
        allow_stale_label=user_exact_weight,
    )
    if edible_evidence is not None:
        label, evidence_is_estimated = edible_evidence
        if user_exact_weight:
            return f"{count}｜可食部（{label}）{weight}克"
        if evidence_is_estimated or quantity_estimated:
            return f"{count}｜可食部（{label}）约{weight}克（估算）"
        return f"{count}｜可食部（{label}）{weight}克"

    if user_exact_weight and (
        quantity_estimated or _contains_estimate_marker(expression)
    ):
        return f"{count} {weight}克"
    return expression


def inherit_previous_portion_expression(
    *,
    portion_expression: str | None,
    previous_portion_expression: str | None,
    consumed_weight_g: Decimal | None,
    source_text: str,
) -> str | None:
    """Reuse a committed measurement object for a weight-only correction.

    This helper never derives anatomy from the food name.  It only exposes the
    last committed expression to ``normalize_portion_expression`` when the new
    turn supplies an exact gram value without replacing the measurement object.
    Explicit raw/edible/object wording in either the new expression or source
    remains authoritative and prevents inheritance.
    """

    current = _clean(portion_expression)
    previous = _clean(previous_portion_expression)
    if (
        current is None
        or previous is None
        or consumed_weight_g is None
        or consumed_weight_g <= 0
        or _GRAM_EXPRESSION.fullmatch(current) is None
        or not _source_declares_exact_weight(source_text, consumed_weight_g)
        or _declares_measurement_object(current)
        or _declares_measurement_object(source_text)
    ):
        return current
    return previous


def _matching_edible_evidence(
    values: Iterable[str | None],
    expected_weight: Decimal,
    *,
    allow_stale_label: bool,
) -> tuple[str, bool] | None:
    stale_labels: set[str] = set()
    for value in values:
        text = _clean(value)
        if text is None:
            continue
        for match in _EDIBLE_WEIGHT.finditer(text):
            try:
                candidate = Decimal(match.group("value"))
            except InvalidOperation:
                continue
            label = " ".join(match.group("label").split())
            if not label:
                continue
            stale_labels.add(label)
            if candidate != expected_weight:
                continue
            evidence_is_estimated = bool(match.group("approx")) or _contains_estimate_marker(
                text[match.start() : min(len(text), match.end() + 12)]
            )
            return label, evidence_is_estimated
    if allow_stale_label and len(stale_labels) == 1:
        return next(iter(stale_labels)), True
    return None


def _source_declares_exact_weight(source_text: str, expected_weight: Decimal) -> bool:
    text = source_text.strip()
    for match in _GRAM_VALUE.finditer(text):
        try:
            candidate = Decimal(match.group("value"))
        except InvalidOperation:
            continue
        if candidate != expected_weight:
            continue
        nearby = text[max(0, match.start() - 8) : min(len(text), match.end() + 4)]
        if any(marker in nearby for marker in _APPROXIMATE_WORDS):
            continue
        return True
    return False


def _contains_estimate_marker(value: str | None) -> bool:
    return bool(value) and any(marker in value for marker in _ESTIMATE_MARKERS)


def _declares_measurement_object(value: str | None) -> bool:
    return bool(value) and any(marker in value for marker in _EXPLICIT_MEASUREMENT_OBJECTS)


def _clean(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _number(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")
