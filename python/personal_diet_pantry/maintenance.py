"""Non-migrating command entry point for backup and read-only self-check."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, TextIO

from . import database
from .backup import create_backup
from .paths import resolve_data_paths
from .self_check import run_self_check


_ACTIONS = frozenset({"backup", "self-check"})


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one maintenance action without initializing or migrating the database."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    output = stdout or sys.stdout
    diagnostics = stderr or sys.stderr
    if len(arguments) != 1 or arguments[0] not in _ACTIONS:
        diagnostics.write("usage: maintenance.py {backup|self-check}\n")
        return 2

    source_root = Path(__file__).resolve().parents[2]
    try:
        data_paths = resolve_data_paths(None, os.environ if env is None else env, None)
        connection = _connect_existing_read_only(data_paths.database)
        try:
            if arguments[0] == "backup":
                backup_path = create_backup(
                    connection,
                    data_paths,
                    label="manual",
                )
                response: dict[str, Any] = {
                    "ok": True,
                    "backup": {"name": backup_path.name},
                }
            else:
                checks = run_self_check(
                    connection,
                    data_paths,
                    source_root / "migrations",
                )
                response = {
                    "ok": True,
                    "checks": [asdict(check) for check in checks],
                }
        finally:
            connection.close()
    except Exception as error:
        diagnostics.write(f"maintenance failed: {error}\n")
        return 1

    output.write(
        json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    output.flush()
    return 0


def _connect_existing_read_only(database_path: Path) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    database.register_database_functions(connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


if __name__ == "__main__":
    raise SystemExit(main())
