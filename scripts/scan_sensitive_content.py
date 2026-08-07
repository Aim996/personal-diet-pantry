#!/usr/bin/env python3
"""Reject credential-like content and runtime data from release source."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re


_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".tools",
        ".venv",
        "__pycache__",
        "dist",
        "dist-package",
        "node_modules",
        "src-tests",
        "tests",
    }
)
_RUNTIME_NAMES = frozenset(
    {
        ".env",
        "diet.sqlite",
        "health-report.md",
    }
)
_RUNTIME_SUFFIXES = frozenset({".db", ".pem", ".key", ".sqlite", ".sqlite3"})
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_MAX_SCANNED_BYTES = 4 * 1024 * 1024


def _is_excluded_directory(name: str) -> bool:
    return (
        name in _EXCLUDED_DIRECTORIES
        or name == "venv"
        or name.startswith(".venv")
    )


@dataclass(frozen=True)
class Finding:
    code: str
    relative_path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _is_runtime_file(path: Path) -> bool:
    lowered = path.name.lower()
    return (
        lowered in _RUNTIME_NAMES
        or lowered.startswith(".env.")
        or path.suffix.lower() in _RUNTIME_SUFFIXES
    )


def scan_tree(root: Path) -> tuple[Finding, ...]:
    resolved = Path(root).resolve()
    findings: list[Finding] = []
    paths: list[Path] = []
    for directory, names, filenames in os.walk(resolved):
        names[:] = sorted(
            name for name in names if not _is_excluded_directory(name)
        )
        paths.extend(Path(directory) / name for name in sorted(filenames))
    for path in paths:
        relative = path.relative_to(resolved).as_posix()
        if _is_runtime_file(path):
            findings.append(
                Finding(
                    code="RUNTIME_DATA_FILE",
                    relative_path=relative,
                    message=f"runtime data or credential file is not releasable: {relative}",
                )
            )
            continue
        if path.stat().st_size > _MAX_SCANNED_BYTES:
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
            findings.append(
                Finding(
                    code="CREDENTIAL_LIKE_CONTENT",
                    relative_path=relative,
                    message=f"credential-like content found in {relative}",
                )
            )
    return tuple(findings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    findings = scan_tree(root)
    print(
        json.dumps(
            {
                "status": "fail" if findings else "pass",
                "root": str(root.resolve()),
                "findings": [item.as_dict() for item in findings],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
