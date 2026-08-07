#!/usr/bin/env python3
"""Generate stable action inventories from contracts/tools.yaml."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DOMAIN_ORDER = (
    "meal",
    "water",
    "weight",
    "pantry",
    "transaction",
    "report",
    "system",
)
ACTION_FIELDS = (
    "mode",
    "confirmation",
    "retry",
    "python_test",
    "typescript_test",
)
MODES = frozenset({"read", "mutation", "maintenance", "derived_file"})
CONFIRMATIONS = frozenset(
    {"none", "conditional", "workflow_handle", "required_true"}
)
RETRIES = frozenset({"safe_read", "operation_receipt", "no_blind_retry"})


@dataclass(frozen=True)
class ActionContract:
    handler: str
    mode: str
    confirmation: str
    retry: str
    python_test: str
    typescript_test: str


@dataclass(frozen=True)
class DomainContract:
    tool: str
    input_schema: str
    actions: Mapping[str, ActionContract]


@dataclass(frozen=True)
class SkillRouteContract:
    domain: str
    action: str


def load_tool_contract(path: Path) -> dict[str, DomainContract]:
    """Load and validate the single action contract source."""

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("tools contract must be a mapping")
    if raw.get("schema_version") != 1 or raw.get("contract_version") != 1:
        raise ValueError("unsupported tools contract version")
    tools = _mapping(raw.get("tools"), "tools")
    handlers = _mapping(raw.get("handlers"), "handlers")
    domains = _mapping(raw.get("domains"), "domains")
    if tuple(domains) != DOMAIN_ORDER:
        raise ValueError("tools contract domains have an invalid order")
    if set(tools) != set(DOMAIN_ORDER) or set(handlers) != set(DOMAIN_ORDER):
        raise ValueError("tools and handlers must cover all seven domains")

    result: dict[str, DomainContract] = {}
    for domain_name in DOMAIN_ORDER:
        tool = _mapping(tools[domain_name], f"tools.{domain_name}")
        if set(tool) != {"name", "input_schema"}:
            raise ValueError(f"tools.{domain_name} has unsupported fields")
        action_values = _mapping(domains[domain_name], f"domains.{domain_name}")
        handler_values = _mapping(
            handlers[domain_name],
            f"handlers.{domain_name}",
        )
        if set(action_values) != set(handler_values):
            raise ValueError(
                f"handlers.{domain_name} differs from its action inventory"
            )
        actions: dict[str, ActionContract] = {}
        for action_name, metadata_value in action_values.items():
            metadata = _mapping(
                metadata_value,
                f"domains.{domain_name}.{action_name}",
            )
            if tuple(metadata) != ACTION_FIELDS:
                raise ValueError(
                    f"{domain_name}.{action_name} metadata fields are invalid"
                )
            values = {
                field: _text(metadata[field], f"{domain_name}.{action_name}.{field}")
                for field in ACTION_FIELDS
            }
            if values["mode"] not in MODES:
                raise ValueError(f"{domain_name}.{action_name} mode is invalid")
            if values["confirmation"] not in CONFIRMATIONS:
                raise ValueError(
                    f"{domain_name}.{action_name} confirmation is invalid"
                )
            if values["retry"] not in RETRIES:
                raise ValueError(f"{domain_name}.{action_name} retry is invalid")
            actions[str(action_name)] = ActionContract(
                handler=_handler(
                    handler_values[action_name],
                    f"handlers.{domain_name}.{action_name}",
                ),
                **values,
            )
        result[domain_name] = DomainContract(
            tool=_text(tool["name"], f"tools.{domain_name}.name"),
            input_schema=_text(
                tool["input_schema"],
                f"tools.{domain_name}.input_schema",
            ),
            actions=actions,
        )
    return result


def load_skill_routes(
    path: Path,
    domains: Mapping[str, DomainContract] | None = None,
) -> dict[str, SkillRouteContract]:
    """Load routes and ensure every target exists in the action contract."""

    validated_domains = load_tool_contract(path) if domains is None else domains
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("tools contract must be a mapping")
    route_values = _mapping(raw.get("skill_routes"), "skill_routes")
    routes: dict[str, SkillRouteContract] = {}
    for route_name, route_value in route_values.items():
        route = _mapping(route_value, f"skill_routes.{route_name}")
        if set(route) != {"domain", "action"}:
            raise ValueError(f"skill_routes.{route_name} has unsupported fields")
        domain = _text(route["domain"], f"skill_routes.{route_name}.domain")
        action = _text(route["action"], f"skill_routes.{route_name}.action")
        if domain not in validated_domains or action not in validated_domains[domain].actions:
            raise ValueError(f"skill_routes.{route_name} targets an unknown action")
        routes[str(route_name)] = SkillRouteContract(domain=domain, action=action)
    return routes


def load_default_actions(
    path: Path,
    domains: Mapping[str, DomainContract] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Load the daily manifest without removing internal compatibility actions."""

    validated_domains = load_tool_contract(path) if domains is None else domains
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("tools contract must be a mapping")
    values = _mapping(raw.get("default_actions"), "default_actions")
    if tuple(values) != DOMAIN_ORDER:
        raise ValueError("default_actions domains have an invalid order")
    result: dict[str, tuple[str, ...]] = {}
    for domain in DOMAIN_ORDER:
        raw_actions = values[domain]
        if (
            not isinstance(raw_actions, list)
            or not raw_actions
            or any(not isinstance(action, str) or not action for action in raw_actions)
        ):
            raise ValueError(f"default_actions.{domain} must be a non-empty string list")
        actions = tuple(raw_actions)
        if len(actions) != len(set(actions)):
            raise ValueError(f"default_actions.{domain} contains duplicates")
        unknown = set(actions) - set(validated_domains[domain].actions)
        if unknown:
            raise ValueError(
                f"default_actions.{domain} contains unknown action {sorted(unknown)[0]}"
            )
        result[domain] = actions
    if sum(len(actions) for actions in result.values()) != 42:
        raise ValueError("default_actions must contain exactly 42 actions")
    return result


def generated_outputs(root: Path) -> dict[Path, str]:
    """Return every generated file and its exact expected contents."""

    root = Path(root)
    contract = load_tool_contract(root / "contracts" / "tools.yaml")
    routes = load_skill_routes(root / "contracts" / "tools.yaml", contract)
    default_actions = load_default_actions(
        root / "contracts" / "tools.yaml", contract
    )
    return {
        root / "contracts" / "public-behavior.yaml": _public_behavior(contract, routes, default_actions),
        root / "src" / "generated" / "tool-contracts.ts": _typescript(contract, routes, default_actions),
        root
        / "python"
        / "personal_diet_pantry"
        / "generated_tool_contracts.py": _python(contract, routes, default_actions),
        root / "docs" / "GENERATED-ACTIONS.zh-CN.md": _documentation(contract, routes, default_actions),
    }


def write_outputs(root: Path) -> None:
    for path, contents in generated_outputs(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")


def check_outputs(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path, expected in generated_outputs(root).items()
        if not path.is_file() or path.read_text(encoding="utf-8") != expected
    )


def _public_behavior(
    contract: Mapping[str, DomainContract],
    routes: Mapping[str, SkillRouteContract],
    default_actions: Mapping[str, tuple[str, ...]],
) -> str:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "default_actions": dict(default_actions),
        "domains": {},
    }
    for domain_name, domain in contract.items():
        payload["domains"][domain_name] = {
            action_name: {
                field: getattr(action, field)
                for field in ACTION_FIELDS
            }
            for action_name, action in domain.actions.items()
        }
    payload["skill_routes"] = {
        route_name: {"domain": route.domain, "action": route.action}
        for route_name, route in routes.items()
    }
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )


def _typescript(
    contract: Mapping[str, DomainContract],
    routes: Mapping[str, SkillRouteContract],
    default_actions: Mapping[str, tuple[str, ...]],
) -> str:
    tools = ",\n".join(
        f'  {domain!r}: {spec.tool!r}'
        for domain, spec in sorted(contract.items())
    )
    actions = ",\n".join(
        "  "
        + repr(domain)
        + ": ["
        + ", ".join(repr(action) for action in spec.actions)
        + "]"
        for domain, spec in contract.items()
    )
    defaults = ",\n".join(
        "  "
        + repr(domain)
        + ": ["
        + ", ".join(repr(action) for action in default_actions[domain])
        + "]"
        for domain in DOMAIN_ORDER
    )
    mutations = [
        f"{domain}.{action_name}"
        for domain, spec in contract.items()
        for action_name, action in spec.actions.items()
        if action.mode == "mutation"
    ]
    mutation_text = ",\n".join(f"  {value!r}" for value in mutations)
    route_text = ",\n".join(
        f"  {route_name!r}: [{route.domain!r}, {route.action!r}]"
        for route_name, route in routes.items()
    )
    return (
        "/* Generated by scripts/generate_tool_contracts.py; do not edit. */\n"
        f"export const TOOL_NAMES = {{\n{tools}\n}} as const;\n\n"
        f"export const TOOL_ACTIONS = {{\n{actions}\n}} as const;\n\n"
        f"export const DEFAULT_TOOL_ACTIONS = {{\n{defaults}\n}} as const;\n\n"
        "export const FORMAL_MUTATION_ACTIONS = [\n"
        f"{mutation_text}\n"
        "] as const;\n\n"
        "export const SKILL_ROUTES = {\n"
        f"{route_text}\n"
        "} as const;\n"
    )


def _python(
    contract: Mapping[str, DomainContract],
    routes: Mapping[str, SkillRouteContract],
    default_actions: Mapping[str, tuple[str, ...]],
) -> str:
    action_lines = []
    handler_lines = []
    policy_lines = []
    mutation_lines = []
    route_lines = []
    default_lines = []
    for domain, spec in contract.items():
        action_values = ", ".join(repr(value) for value in spec.actions)
        action_lines.append(
            f"    {domain!r}: frozenset(({action_values},)),"
        )
        default_values = ", ".join(repr(value) for value in default_actions[domain])
        default_lines.append(
            f"    {domain!r}: ({default_values}{',' if len(default_actions[domain]) == 1 else ''}),"
        )
        handlers = ", ".join(
            f"{action!r}: {metadata.handler!r}"
            for action, metadata in spec.actions.items()
        )
        handler_lines.append(f"    {domain!r}: {{{handlers}}},")
        for action_name, action in spec.actions.items():
            policy_lines.append(
                "    "
                f"({domain!r}, {action_name!r}): "
                f"({action.mode!r}, {action.confirmation!r}, {action.retry!r}),"
            )
            if action.mode == "mutation":
                mutation_lines.append(
                    f"        ({domain!r}, {action_name!r}),"
                )
    for route_name, route in routes.items():
        route_lines.append(
            f"    {route_name!r}: ({route.domain!r}, {route.action!r}),"
        )
    return (
        '"""Generated action metadata; do not edit directly."""\n\n'
        "ACTIONS = {\n"
        + "\n".join(action_lines)
        + "\n}\n\n"
        "DEFAULT_ACTIONS = {\n"
        + "\n".join(default_lines)
        + "\n}\n\n"
        "ACTION_HANDLER_NAMES = {\n"
        + "\n".join(handler_lines)
        + "\n}\n\n"
        "ACTION_POLICIES = {\n"
        + "\n".join(policy_lines)
        + "\n}\n\n"
        "FORMAL_MUTATION_ACTIONS = frozenset(\n"
        "    {\n"
        + "\n".join(mutation_lines)
        + "\n    }\n"
        ")\n\n"
        "SKILL_ROUTES = {\n"
        + "\n".join(route_lines)
        + "\n}\n"
    )


def _documentation(
    contract: Mapping[str, DomainContract],
    routes: Mapping[str, SkillRouteContract],
    default_actions: Mapping[str, tuple[str, ...]],
) -> str:
    lines = [
        "# 食序管家生成动作索引",
        "",
        "> 本页由 `contracts/tools.yaml` 生成，请勿手工编辑。",
        "",
        "日常默认清单固定为 40 个动作；其余动作仅保留内部兼容。",
        "",
        "| 领域 | 默认动作 |",
        "| --- | --- |",
        *(
            f"| `{domain}` | "
            + ", ".join(f"`{action}`" for action in default_actions[domain])
            + " |"
            for domain in DOMAIN_ORDER
        ),
        "",
        "| 工具 | 领域 | 动作 | 模式 | 确认 | 重试 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for domain_name, domain in contract.items():
        for action_name, action in domain.actions.items():
            lines.append(
                f"| `{domain.tool}` | `{domain_name}` | `{action_name}` | "
                f"`{action.mode}` | `{action.confirmation}` | `{action.retry}` |"
            )
    lines.extend(
        [
            "",
            "## Skill capability routes",
            "",
            "| Route | Domain | Action |",
            "| --- | --- | --- |",
        ]
    )
    for route_name, route in routes.items():
        lines.append(
            f"| `{route_name}` | `{route.domain}` | `{route.action}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{label} must be a string-keyed mapping")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _handler(value: Any, label: str) -> str:
    handler = _text(value, label)
    if not handler.startswith("_") or not handler.replace("_", "").isalnum():
        raise ValueError(f"{label} must be a private Python function name")
    return handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        stale = check_outputs(arguments.root)
        if stale:
            for path in stale:
                print(path.relative_to(arguments.root))
            return 1
        return 0
    write_outputs(arguments.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
