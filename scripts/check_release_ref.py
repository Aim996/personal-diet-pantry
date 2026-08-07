#!/usr/bin/env python3
"""Fail closed unless a release event matches the product version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


class ReleaseRefError(RuntimeError):
    """Raised when an event cannot safely build or publish a release."""


def validate_release_context(
    project_root: Path,
    event_name: str,
    ref_name: str,
) -> str:
    try:
        package = json.loads(
            (project_root / "package.json").read_text(encoding="utf-8")
        )
        product_version = package["productVersion"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ReleaseRefError("cannot read productVersion from package.json") from error
    if not isinstance(product_version, str) or not product_version.strip():
        raise ReleaseRefError("package.json productVersion must be a non-empty string")
    if event_name == "workflow_dispatch":
        return product_version
    if event_name != "push":
        raise ReleaseRefError(f"unsupported release event: {event_name}")
    expected = f"v{product_version}"
    if ref_name != expected:
        raise ReleaseRefError(f"release ref must be {expected}, got {ref_name}")
    return product_version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref-name", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        version = validate_release_context(
            args.project_root.resolve(), args.event_name, args.ref_name
        )
    except ReleaseRefError as error:
        print(f"release context rejected: {error}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
