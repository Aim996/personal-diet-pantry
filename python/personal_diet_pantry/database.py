"""SQLite connection, migration, and baseline integrity utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Literal


class MigrationError(RuntimeError):
    """Raised when migrations cannot safely run on the supplied connection."""


class MigrationChecksumError(MigrationError):
    """Raised when an applied migration no longer has its recorded contents."""


@dataclass(frozen=True)
class CheckResult:
    """The outcome of a database health check."""

    code: str
    level: Literal["PASS", "WARN", "FAIL"]
    message: str
    repairable: bool


_MIGRATION_NAME = re.compile(r"^(?P<version>\d+)_(?P<name>.+\.sql)$")


def _normalize_lf(contents: bytes) -> bytes:
    """Normalize CRLF line endings without changing other bytes."""

    return contents.replace(b"\r\n", b"\n")


def migration_checksum(contents: bytes) -> str:
    """Return the canonical LF-normalized SHA-256 checksum for migration contents."""

    return hashlib.sha256(_normalize_lf(contents)).hexdigest()


def migration_checksums(contents: bytes) -> set[str]:
    """Return checksums accepted for raw, LF-normalized, and CRLF migration contents."""

    lf_contents = _normalize_lf(contents)
    crlf_contents = lf_contents.replace(b"\n", b"\r\n")
    return {
        hashlib.sha256(contents).hexdigest(),
        hashlib.sha256(lf_contents).hexdigest(),
        hashlib.sha256(crlf_contents).hexdigest(),
    }


def connect_database(path: Path) -> sqlite3.Connection:
    """Open a SQLite database with the consistency settings used by this plugin."""

    connection = sqlite3.connect(path)
    register_database_functions(connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def apply_migrations(connection: sqlite3.Connection, migrations_dir: Path) -> None:
    """Apply numbered SQL migrations once, rejecting changed applied files."""

    register_database_functions(connection)
    if connection.in_transaction:
        raise MigrationError("Cannot apply migrations while the connection has an active transaction")

    migrations = _find_migrations(migrations_dir)
    migrations_by_version = {migration.version: migration for migration in migrations}
    try:
        connection.execute("BEGIN IMMEDIATE")
        applied = _applied_migrations(connection)

        for version, record in applied.items():
            migration = migrations_by_version.get(version)
            if migration is None:
                raise MigrationChecksumError(
                    f"Applied migration version {version} is missing from the migration directory"
                )
            if record.filename != migration.filename:
                raise MigrationChecksumError(
                    f"Applied migration {record.filename} is missing from the migration directory"
                )
            if record.checksum not in migration.accepted_checksums:
                raise MigrationChecksumError(
                    f"Migration {migration.filename} checksum does not match its applied checksum"
                )

        for migration in migrations:
            if migration.version not in applied:
                _apply_migration(connection, migration)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def has_pending_migrations(
    connection: sqlite3.Connection,
    migrations_dir: Path,
) -> bool:
    """Return whether a valid existing schema is behind shipped migrations."""

    migrations = _find_migrations(Path(migrations_dir))
    applied = _applied_migrations(connection)
    by_version = {migration.version: migration for migration in migrations}
    for version, record in applied.items():
        migration = by_version.get(version)
        if (
            migration is None
            or record.filename != migration.filename
            or record.checksum not in migration.accepted_checksums
        ):
            raise MigrationChecksumError(
                f"Applied migration version {version} does not match shipped migrations"
            )
    return any(migration.version not in applied for migration in migrations)


def validate_database(connection: sqlite3.Connection) -> list[CheckResult]:
    """Return the core SQLite integrity and foreign-key check results."""

    integrity_rows = list(connection.execute("PRAGMA integrity_check"))
    integrity_messages = [row[0] for row in integrity_rows]
    integrity_ok = integrity_messages == ["ok"]
    foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))

    return [
        CheckResult(
            code="integrity_check",
            level="PASS" if integrity_ok else "FAIL",
            message="SQLite integrity check passed" if integrity_ok else "; ".join(integrity_messages),
            repairable=False,
        ),
        CheckResult(
            code="foreign_key_check",
            level="PASS" if not foreign_key_rows else "FAIL",
            message="Foreign-key check passed"
            if not foreign_key_rows
            else f"Found {len(foreign_key_rows)} foreign-key violation(s)",
            repairable=False,
        ),
    ]


def register_database_functions(connection: sqlite3.Connection) -> None:
    """Register deterministic functions referenced by the persisted schema."""

    from .inventory_matching import register_inventory_match_key

    register_inventory_match_key(connection)


@dataclass(frozen=True)
class _Migration:
    version: int
    filename: str
    checksum: str
    accepted_checksums: frozenset[str]
    sql: str


@dataclass(frozen=True)
class _AppliedMigration:
    filename: str
    checksum: str


def _find_migrations(migrations_dir: Path) -> list[_Migration]:
    migrations: list[_Migration] = []
    seen_versions: set[int] = set()
    for path in migrations_dir.iterdir():
        if not path.is_file():
            continue
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        version = int(match["version"])
        if version in seen_versions:
            raise ValueError(f"Duplicate migration version {version}")
        contents = path.read_bytes()
        migrations.append(
            _Migration(
                version=version,
                filename=path.name,
                checksum=migration_checksum(contents),
                accepted_checksums=frozenset(migration_checksums(contents)),
                sql=contents.decode("utf-8"),
            )
        )
        seen_versions.add(version)
    return sorted(migrations, key=lambda migration: migration.version)


def _applied_migrations(connection: sqlite3.Connection) -> dict[int, _AppliedMigration]:
    schema_migrations_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if schema_migrations_exists is None:
        return {}
    return {
        row["version"]: _AppliedMigration(filename=row["name"], checksum=row["checksum"])
        for row in connection.execute("SELECT version, name, checksum FROM schema_migrations")
    }


def _apply_migration(connection: sqlite3.Connection, migration: _Migration) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _execute_script(connection, migration.sql)
    connection.execute(
        """
        INSERT INTO schema_migrations (version, name, applied_at, checksum)
        VALUES (?, ?, ?, ?)
        """,
        (migration.version, migration.filename, timestamp, migration.checksum),
    )


def _execute_script(connection: sqlite3.Connection, script: str) -> None:
    statement_parts: list[str] = []
    for character in script:
        statement_parts.append(character)
        if character == ";":
            statement = "".join(statement_parts)
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement_parts.clear()
    final_statement = "".join(statement_parts).strip()
    if final_statement:
        connection.execute(final_statement)
