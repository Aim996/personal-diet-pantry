"""Validated declarative policy registries for extensible agent behavior."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import yaml

from .models import ConfigurationError, DataPaths
from .paths import validate_owned_path


_REGISTRY_NAMES = (
    "temporal-scopes",
    "quantity-evidence",
    "intent-routes",
    "inventory-relations",
    "report-taxonomy",
    "fact-authority",
)
_ALLOWED_OPERATORS = {
    "temporal-scopes": frozenset(
        {"calendar_unit", "calendar_anchor", "rolling_duration", "local_segment"}
    ),
    "quantity-evidence": frozenset(
        {"bounded_quantity", "confirmed_personal_rule", "package_spec"}
    ),
    "intent-routes": frozenset({"capability_route"}),
    "inventory-relations": frozenset(
        {"inventory_kind", "provenance_relation"}
    ),
    "report-taxonomy": frozenset({"expiry_state"}),
    "fact-authority": frozenset({"formal_source"}),
}
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_REFERENCE_PATTERN = re.compile(
    r"^(?P<registry>[a-z][a-z0-9-]*):(?P<policy>[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+)$"
)
_MAX_ENTRIES_PER_REGISTRY = 256
_MAX_VALUE_DEPTH = 8


@dataclass(frozen=True)
class PolicyEntry:
    """One registered descriptor backed only by validated static data."""

    policy_key: str
    operator: str
    values: Mapping[str, object]
    source: str
    version: int
    overridable: frozenset[str] = frozenset()
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyRegistry:
    """Immutable collection of named policy registries."""

    registries: Mapping[str, tuple[PolicyEntry, ...]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.registries)

    def entries(self, registry_name: str) -> tuple[PolicyEntry, ...]:
        try:
            return self.registries[registry_name]
        except KeyError as error:
            raise ConfigurationError(
                f"unknown registry: {registry_name}"
            ) from error

    def entry(self, registry_name: str, policy_key: str) -> PolicyEntry:
        for entry in self.entries(registry_name):
            if entry.policy_key == policy_key:
                return entry
        raise ConfigurationError(
            f"unknown policy key: {registry_name}:{policy_key}"
        )


def load_policy_registry(
    source_root: Path,
    data_paths: DataPaths,
    *,
    include_overrides: bool = True,
) -> PolicyRegistry:
    """Load shipped policies and an optional validated user override file."""

    source_root = Path(source_root)
    loaded: dict[str, tuple[PolicyEntry, ...]] = {}
    for registry_name in _REGISTRY_NAMES:
        document = _read_yaml(
            source_root / "rules" / f"{registry_name}.yaml"
        )
        loaded[registry_name] = _registry_entries(
            registry_name,
            document,
        )

    if include_overrides:
        override_path = data_paths.root / "config" / "policy-overrides.yaml"
        validate_owned_path(data_paths, override_path)
        if override_path.is_file():
            loaded = _apply_overrides(loaded, _read_yaml(override_path))

    _validate_references(loaded)
    return PolicyRegistry(
        MappingProxyType(
            {name: tuple(loaded[name]) for name in _REGISTRY_NAMES}
        )
    )


def _read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Unable to read policy registry: {path}") from error
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"policy registry must be a mapping: {path}")
    return value


def _registry_entries(
    expected_name: str,
    document: Mapping[str, Any],
) -> tuple[PolicyEntry, ...]:
    if set(document) != {"schema_version", "registry", "entries"}:
        raise ConfigurationError(
            f"policy registry {expected_name} has unsupported top-level fields"
        )
    version = document["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigurationError(
            f"policy registry {expected_name} schema_version must be 1"
        )
    if document["registry"] != expected_name:
        raise ConfigurationError(
            f"policy registry name mismatch: expected {expected_name}"
        )
    raw_entries = document["entries"]
    if (
        not isinstance(raw_entries, Sequence)
        or isinstance(raw_entries, (str, bytes, bytearray))
        or not raw_entries
        or len(raw_entries) > _MAX_ENTRIES_PER_REGISTRY
    ):
        raise ConfigurationError(
            f"policy registry {expected_name} entries must contain 1-{_MAX_ENTRIES_PER_REGISTRY} items"
        )

    entries: list[PolicyEntry] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ConfigurationError(
                f"policy registry {expected_name} entry must be a mapping"
            )
        allowed_fields = {
            "policy_key",
            "operator",
            "values",
            "overridable",
            "references",
        }
        unexpected = set(raw) - allowed_fields
        if unexpected:
            raise ConfigurationError(
                f"policy entry has unsupported fields: {sorted(unexpected)}"
            )
        policy_key = _policy_key(raw.get("policy_key"), "policy_key")
        if policy_key in seen:
            raise ConfigurationError(
                f"duplicate policy key: {expected_name}:{policy_key}"
            )
        seen.add(policy_key)
        operator = raw.get("operator")
        if not isinstance(operator, str) or operator not in _ALLOWED_OPERATORS[expected_name]:
            raise ConfigurationError(
                f"unknown operator for {expected_name}:{policy_key}: {operator!r}"
            )
        values = raw.get("values")
        if not isinstance(values, Mapping):
            raise ConfigurationError(
                f"policy values must be a mapping: {expected_name}:{policy_key}"
            )
        normalized_values = _validated_mapping(values, depth=0)
        overridable = _string_set(raw.get("overridable", ()), "overridable")
        if not overridable <= normalized_values.keys():
            raise ConfigurationError(
                f"overridable fields must exist in values: {expected_name}:{policy_key}"
            )
        references = tuple(
            _reference(value)
            for value in _string_sequence(raw.get("references", ()), "references")
        )
        entries.append(
            PolicyEntry(
                policy_key=policy_key,
                operator=operator,
                values=MappingProxyType(normalized_values),
                source="shipped",
                version=version,
                overridable=frozenset(overridable),
                references=references,
            )
        )
    return tuple(entries)


def _apply_overrides(
    registries: Mapping[str, tuple[PolicyEntry, ...]],
    document: Mapping[str, Any],
) -> dict[str, tuple[PolicyEntry, ...]]:
    if set(document) != {"schema_version", "overrides"}:
        raise ConfigurationError("policy override document has unsupported fields")
    if document["schema_version"] != 1:
        raise ConfigurationError("policy override schema_version must be 1")
    raw_overrides = document["overrides"]
    if not isinstance(raw_overrides, list):
        raise ConfigurationError("policy overrides must be a list")

    mutable = {name: list(entries) for name, entries in registries.items()}
    seen: set[tuple[str, str]] = set()
    for raw in raw_overrides:
        if not isinstance(raw, Mapping):
            raise ConfigurationError("policy override must be a mapping")
        protected = set(raw) - {"registry", "policy_key", "values"}
        if protected:
            raise ConfigurationError(
                f"cannot override protected field: {sorted(protected)[0]}"
            )
        registry_name = raw.get("registry")
        if registry_name not in mutable:
            raise ConfigurationError(f"unknown registry: {registry_name}")
        policy_key = _policy_key(raw.get("policy_key"), "policy_key")
        identity = (str(registry_name), policy_key)
        if identity in seen:
            raise ConfigurationError(
                f"duplicate override: {registry_name}:{policy_key}"
            )
        seen.add(identity)
        entries = mutable[str(registry_name)]
        index = next(
            (
                position
                for position, entry in enumerate(entries)
                if entry.policy_key == policy_key
            ),
            None,
        )
        if index is None:
            raise ConfigurationError(
                f"unknown policy key: {registry_name}:{policy_key}"
            )
        values = raw.get("values")
        if not isinstance(values, Mapping) or not values:
            raise ConfigurationError("policy override values must be a non-empty mapping")
        normalized_values = _validated_mapping(values, depth=0)
        entry = entries[index]
        for key in normalized_values:
            if key not in entry.overridable:
                raise ConfigurationError(
                    f"policy value is not overridable: {registry_name}:{policy_key}:{key}"
                )
        merged = dict(entry.values)
        merged.update(normalized_values)
        entries[index] = replace(
            entry,
            values=MappingProxyType(merged),
            source="user_override",
        )
    return {name: tuple(entries) for name, entries in mutable.items()}


def _validate_references(
    registries: Mapping[str, tuple[PolicyEntry, ...]],
) -> None:
    graph: dict[str, tuple[str, ...]] = {}
    known = {
        f"{registry_name}:{entry.policy_key}"
        for registry_name, entries in registries.items()
        for entry in entries
    }
    for registry_name, entries in registries.items():
        for entry in entries:
            identity = f"{registry_name}:{entry.policy_key}"
            for reference in entry.references:
                if reference not in known:
                    raise ConfigurationError(
                        f"dangling policy reference: {identity} -> {reference}"
                    )
            graph[identity] = entry.references

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identity: str) -> None:
        if identity in visiting:
            raise ConfigurationError(f"cyclic policy reference: {identity}")
        if identity in visited:
            return
        visiting.add(identity)
        for reference in graph[identity]:
            visit(reference)
        visiting.remove(identity)
        visited.add(identity)

    for identity in graph:
        visit(identity)


def _policy_key(value: object, field: str) -> str:
    if not isinstance(value, str) or not _KEY_PATTERN.fullmatch(value):
        raise ConfigurationError(f"{field} must be a namespaced policy key")
    return value


def _reference(value: str) -> str:
    if not _REFERENCE_PATTERN.fullmatch(value):
        raise ConfigurationError(f"invalid policy reference: {value!r}")
    return value


def _string_sequence(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ConfigurationError(f"{field} must be a list of strings")
    if any(not isinstance(item, str) or not item for item in value):
        raise ConfigurationError(f"{field} must be a list of strings")
    return tuple(value)


def _string_set(value: object, field: str) -> set[str]:
    values = _string_sequence(value, field)
    if len(values) != len(set(values)):
        raise ConfigurationError(f"{field} must not contain duplicates")
    return set(values)


def _validated_mapping(
    value: Mapping[object, object],
    *,
    depth: int,
) -> dict[str, object]:
    if depth > _MAX_VALUE_DEPTH:
        raise ConfigurationError("policy values exceed the nesting limit")
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ConfigurationError("policy value keys must be non-empty strings")
        result[raw_key] = _validated_value(raw_value, depth=depth + 1)
    return result


def _validated_value(value: object, *, depth: int) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType(_validated_mapping(value, depth=depth))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 256:
            raise ConfigurationError("policy list exceeds the size limit")
        return tuple(_validated_value(item, depth=depth + 1) for item in value)
    raise ConfigurationError(f"unsupported policy value type: {type(value).__name__}")
