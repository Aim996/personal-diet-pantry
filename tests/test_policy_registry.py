from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from personal_diet_pantry.models import ConfigurationError
from personal_diet_pantry.paths import resolve_data_paths
from personal_diet_pantry.policies import load_policy_registry


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REGISTRIES = {
    "temporal-scopes",
    "quantity-evidence",
    "intent-routes",
    "inventory-relations",
    "report-taxonomy",
    "fact-authority",
}


def _data_paths(tmp_path: Path):
    return resolve_data_paths(
        {"dataDir": str(tmp_path / "data")},
        {},
        None,
    )


def _write_overrides(tmp_path: Path, overrides: list[dict[str, object]]) -> None:
    path = tmp_path / "data" / "config" / "policy-overrides.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "overrides": overrides},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_policy_registry_loads_every_shipped_registry(tmp_path: Path) -> None:
    registry = load_policy_registry(ROOT, _data_paths(tmp_path))

    assert set(registry.names) == EXPECTED_REGISTRIES
    night = registry.entry("temporal-scopes", "segment.night")
    assert night.operator == "local_segment"
    assert night.values["start"] == "18:00"
    assert night.values["end"] == "02:00"
    assert night.source == "shipped"
    assert registry.entry(
        "intent-routes", "route.simple_read"
    ).values["budget"] == 1


def test_policy_registry_applies_only_declared_overridable_values(
    tmp_path: Path,
) -> None:
    _write_overrides(
        tmp_path,
        [
            {
                "registry": "temporal-scopes",
                "policy_key": "segment.night",
                "values": {"start": "19:00", "end": "03:00"},
            }
        ],
    )

    registry = load_policy_registry(ROOT, _data_paths(tmp_path))

    night = registry.entry("temporal-scopes", "segment.night")
    assert night.values["start"] == "19:00"
    assert night.values["end"] == "03:00"
    assert night.source == "user_override"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            [
                {
                    "registry": "temporal-scopes",
                    "policy_key": "segment.night",
                    "operator": "execute_user_expression",
                    "values": {"start": "19:00"},
                }
            ],
            "cannot override protected field",
        ),
        (
            [
                {
                    "registry": "temporal-scopes",
                    "policy_key": "segment.night",
                    "values": {"start": "19:00"},
                },
                {
                    "registry": "temporal-scopes",
                    "policy_key": "segment.night",
                    "values": {"start": "20:00"},
                },
            ],
            "duplicate override",
        ),
        (
            [
                {
                    "registry": "unknown-registry",
                    "policy_key": "segment.night",
                    "values": {"start": "19:00"},
                }
            ],
            "unknown registry",
        ),
        (
            [
                {
                    "registry": "temporal-scopes",
                    "policy_key": "segment.night",
                    "values": {"not_overridable": "value"},
                }
            ],
            "is not overridable",
        ),
    ],
)
def test_policy_registry_rejects_unsafe_or_conflicting_overrides(
    tmp_path: Path,
    overrides: list[dict[str, object]],
    message: str,
) -> None:
    _write_overrides(tmp_path, overrides)

    with pytest.raises(ConfigurationError, match=message):
        load_policy_registry(ROOT, _data_paths(tmp_path))


def test_policy_registry_rejects_unknown_operator_in_shipped_rules(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    rules = source_root / "rules"
    rules.mkdir(parents=True)
    for name in EXPECTED_REGISTRIES:
        original = yaml.safe_load(
            (ROOT / "rules" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        if name == "temporal-scopes":
            original["entries"][0]["operator"] = "execute_user_expression"
        (rules / f"{name}.yaml").write_text(
            yaml.safe_dump(original, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    with pytest.raises(ConfigurationError, match="unknown operator"):
        load_policy_registry(source_root, _data_paths(tmp_path))


def test_policy_registry_rejects_dangling_references(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    rules = source_root / "rules"
    rules.mkdir(parents=True)
    for name in EXPECTED_REGISTRIES:
        original = yaml.safe_load(
            (ROOT / "rules" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        if name == "intent-routes":
            original["entries"][0]["references"] = [
                "fact-authority:missing.policy"
            ]
        (rules / f"{name}.yaml").write_text(
            yaml.safe_dump(original, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    with pytest.raises(ConfigurationError, match="dangling policy reference"):
        load_policy_registry(source_root, _data_paths(tmp_path))
