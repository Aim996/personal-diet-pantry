"""Verified SQLite online backups and explicitly confirmed restores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
from typing import Iterable

from .clock import Clock, system_utc_now
from .database import (
    apply_migrations,
    connect_database,
    register_database_functions,
    validate_database,
)
from .file_io import atomic_write_text
from .models import DataPaths
from .paths import validate_owned_path


RESTORE_REQUIRES_CONFIRMATION = "RESTORE_REQUIRES_CONFIRMATION"
_BACKUP_LABEL = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_BACKUP_TIMESTAMP = r"\d{8}T\d{6}(?:\d{6})?Z"
_LEGACY_MANAGED_BACKUP_NAME = re.compile(
    rf"^backup-(?P<timestamp>{_BACKUP_TIMESTAMP})\.sqlite$"
)
_CURRENT_PLUGIN_BACKUP_NAME = re.compile(
    rf"^(?P<label>[A-Za-z0-9-]+)-"
    rf"(?P<timestamp>{_BACKUP_TIMESTAMP})-"
    r"(?P<token>[0-9a-f]{12})\.sqlite$"
)
_PLUGIN_LIKE_BACKUP_NAME = re.compile(
    rf"^[A-Za-z0-9-]+-{_BACKUP_TIMESTAMP}(?:-.*)?\.sqlite$"
)
_MAX_BACKUP_LABEL_LENGTH = 64


class BackupNameKind(str, Enum):
    """Classification used by health checks and retention policy."""

    RETENTION_MANAGED = "retention_managed"
    PLUGIN_BACKUP = "plugin_backup"
    UNMANAGED = "unmanaged"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class BackupName:
    kind: BackupNameKind
    timestamp: datetime | None = None
    label: str | None = None


class BackupVerificationError(RuntimeError):
    """Raised when a backup checksum or SQLite integrity check fails."""


class RestoreConfirmationRequired(RuntimeError):
    """Raised when a destructive restore was not explicitly confirmed."""

    code = RESTORE_REQUIRES_CONFIRMATION


class RestoreError(RuntimeError):
    """Raised when a confirmed restore cannot produce a healthy database."""

    def __init__(
        self,
        message: str,
        *,
        recovered_connection: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(message)
        self.recovered_connection = recovered_connection


@dataclass(frozen=True)
class RestoreResult:
    """The reopened database and verified snapshot taken before replacement."""

    connection: sqlite3.Connection
    pre_restore_backup: Path


def create_backup(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    *,
    label: str = "backup",
    _clock: Clock | None = None,
) -> Path:
    """Create an online SQLite backup and SHA-256 sidecar."""

    backup_dir = Path(data_paths.backups)
    validate_owned_path(data_paths, backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    clock = _clock or system_utc_now
    timestamp = _timestamp(clock())
    safe_label = _backup_label(label)
    destination = _reserve_backup_destination(
        backup_dir,
        safe_label,
        timestamp,
        data_paths,
    )
    temporary_path: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            dir=backup_dir,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        backup_connection = sqlite3.connect(temporary_path)
        register_database_functions(backup_connection)
        try:
            connection.backup(backup_connection)
            integrity = [
                row[0] for row in backup_connection.execute("PRAGMA integrity_check")
            ]
            if integrity != ["ok"]:
                raise BackupVerificationError(
                    "SQLite backup integrity check failed: " + "; ".join(integrity)
                )
        finally:
            backup_connection.close()
        digest = _sha256(temporary_path)
        validate_owned_path(data_paths, temporary_path)
        validate_owned_path(data_paths, destination)
        os.replace(temporary_path, destination)
        temporary_path = None
        _write_sidecar(destination, digest, data_paths)
        verify_backup(destination, data_paths=data_paths)
        published = True
        apply_backup_retention(data_paths, _clock=clock)
        return destination
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if not published:
            destination.unlink(missing_ok=True)
            destination.with_suffix(
                destination.suffix + ".sha256"
            ).unlink(missing_ok=True)
        raise


def verify_backup(
    backup_path: Path,
    checksum_path: Path | None = None,
    *,
    data_paths: DataPaths | None = None,
) -> bool:
    """Verify the sidecar checksum and SQLite integrity of a backup."""

    source = Path(backup_path)
    sidecar = (
        Path(checksum_path)
        if checksum_path is not None
        else source.with_suffix(source.suffix + ".sha256")
    )
    if data_paths is not None:
        validate_owned_path(data_paths, source)
        validate_owned_path(data_paths, sidecar)
    if not source.is_file():
        raise BackupVerificationError(f"Backup file is missing: {source}")
    if not sidecar.is_file():
        raise BackupVerificationError(f"Backup checksum sidecar is missing: {sidecar}")
    expected = _read_sidecar(sidecar, source.name)
    actual = _sha256(source)
    if not hmac.compare_digest(expected, actual):
        raise BackupVerificationError("Backup checksum does not match its SHA-256 sidecar")
    try:
        connection = sqlite3.connect(source)
        register_database_functions(connection)
        try:
            connection.execute("PRAGMA query_only = ON")
            integrity = [
                row[0] for row in connection.execute("PRAGMA integrity_check")
            ]
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise BackupVerificationError("Backup is not a readable SQLite database") from error
    if integrity != ["ok"]:
        raise BackupVerificationError(
            "Backup SQLite integrity check failed: " + "; ".join(integrity)
        )
    return True


def restore_backup(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    backup_path: Path,
    migrations_dir: Path,
    *,
    confirmed: bool,
    _clock: Clock | None = None,
    additional_connections: Iterable[sqlite3.Connection] = (),
) -> RestoreResult:
    """Validate a staged restore, atomically swap it, and roll back on failure."""

    if confirmed is not True:
        raise RestoreConfirmationRequired(
            "Restore requires explicit confirmation before replacing the active database"
        )
    source = Path(backup_path)
    validate_owned_path(data_paths, source)
    verify_backup(source, data_paths=data_paths)
    pre_restore = create_backup(
        connection,
        data_paths,
        label="pre-restore",
        _clock=_clock,
    )
    verify_backup(pre_restore, data_paths=data_paths)

    database_path = Path(data_paths.database)
    validate_owned_path(data_paths, database_path)
    validate_owned_path(data_paths, database_path.parent)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=database_path.parent,
            prefix=f".{database_path.name}.",
            suffix=".restore.tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        shutil.copyfile(source, temporary_path)
        with temporary_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        staged = connect_database(temporary_path)
        try:
            apply_migrations(staged, Path(migrations_dir))
            failures = [
                result for result in validate_database(staged) if result.level == "FAIL"
            ]
            if not failures:
                from .self_check import run_self_check

                failures = [
                    result
                    for result in run_self_check(
                        staged,
                        data_paths,
                        Path(migrations_dir),
                        source_root=Path(migrations_dir).parent,
                        write_report=False,
                    )
                    if result.level == "FAIL"
                ]
            if failures:
                raise RestoreError(
                    "Staged database failed self-check validation: "
                    + "; ".join(result.message for result in failures)
                )
            staged.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            staged.close()
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    for active in (connection, *tuple(additional_connections)):
        active.close()
    _remove_sqlite_sidecars(data_paths, database_path)
    validate_owned_path(data_paths, temporary_path)
    validate_owned_path(data_paths, database_path)
    os.replace(temporary_path, database_path)
    temporary_path = None
    try:
        restored = connect_database(database_path)
        failures = [
            result for result in validate_database(restored) if result.level == "FAIL"
        ]
        if failures:
            raise RestoreError(
                "Restored database failed validation: "
                + "; ".join(result.message for result in failures)
            )
    except BaseException as restore_error:
        try:
            restored.close()
        except (NameError, sqlite3.Error):
            pass
        _rollback_restore(data_paths, database_path, pre_restore)
        try:
            reopened = connect_database(database_path)
            rollback_failures = [
                result
                for result in validate_database(reopened)
                if result.level == "FAIL"
            ]
            if rollback_failures:
                reopened.close()
                raise RestoreError(
                    "Restore failed and rollback database failed validation"
                ) from restore_error
        except RestoreError:
            raise
        except BaseException as rollback_error:
            raise RestoreError(
                "Restore failed and the pre-restore rollback could not be reopened"
            ) from rollback_error
        raise RestoreError(
            "Restore failed after swap; the pre-restore database was rolled back",
            recovered_connection=reopened,
        ) from restore_error
    return RestoreResult(connection=restored, pre_restore_backup=pre_restore)


def _write_sidecar(
    backup_path: Path,
    digest: str,
    data_paths: DataPaths,
) -> None:
    sidecar = backup_path.with_suffix(backup_path.suffix + ".sha256")
    atomic_write_text(
        sidecar,
        f"{digest}  {backup_path.name}\n",
        encoding="ascii",
        data_paths=data_paths,
    )


def _rollback_restore(
    data_paths: DataPaths,
    database_path: Path,
    pre_restore: Path,
) -> None:
    rollback_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=database_path.parent,
            prefix=f".{database_path.name}.",
            suffix=".rollback.tmp",
            delete=False,
        ) as handle:
            rollback_path = Path(handle.name)
        shutil.copyfile(pre_restore, rollback_path)
        with rollback_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        _remove_sqlite_sidecars(data_paths, database_path)
        validate_owned_path(data_paths, rollback_path)
        validate_owned_path(data_paths, database_path)
        os.replace(rollback_path, database_path)
        rollback_path = None
    finally:
        if rollback_path is not None:
            rollback_path.unlink(missing_ok=True)


def _remove_sqlite_sidecars(data_paths: DataPaths, database_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database_path) + suffix)
        validate_owned_path(data_paths, sidecar)
        sidecar.unlink(missing_ok=True)


def _read_sidecar(sidecar: Path, expected_name: str) -> str:
    try:
        line = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise BackupVerificationError("Backup checksum sidecar is unreadable") from error
    parts = line.split()
    if len(parts) != 2 or parts[1] != expected_name:
        raise BackupVerificationError("Backup checksum sidecar has an invalid format")
    digest = parts[0].casefold()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise BackupVerificationError("Backup checksum sidecar has an invalid SHA-256 digest")
    return digest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Backup timestamps must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    base = utc.strftime("%Y%m%dT%H%M%S")
    if utc.microsecond:
        base += f"{utc.microsecond:06d}"
    return base + "Z"


def _backup_label(value: str) -> str:
    if (
        len(value) > _MAX_BACKUP_LABEL_LENGTH
        or _BACKUP_LABEL.fullmatch(value) is None
    ):
        raise ValueError(
            "Backup label must be 1-64 ASCII alphanumeric segments "
            "separated by single hyphens"
        )
    return value


def _reserve_backup_destination(
    backup_dir: Path,
    label: str,
    timestamp: str,
    data_paths: DataPaths,
    *,
    attempts: int = 128,
) -> Path:
    """Exclusively reserve a unique snapshot and checksum pair."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _ in range(attempts):
        destination = backup_dir / (
            f"{label}-{timestamp}-{secrets.token_hex(6)}.sqlite"
        )
        sidecar = destination.with_suffix(destination.suffix + ".sha256")
        validate_owned_path(data_paths, destination)
        validate_owned_path(data_paths, sidecar)
        try:
            destination_descriptor = os.open(
                destination,
                flags,
                0o600,
            )
        except FileExistsError:
            continue
        try:
            os.close(destination_descriptor)
            try:
                sidecar_descriptor = os.open(sidecar, flags, 0o600)
            except FileExistsError:
                destination.unlink(missing_ok=True)
                continue
            try:
                os.close(sidecar_descriptor)
            except BaseException:
                sidecar.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    raise BackupVerificationError(
        "Unable to reserve a unique backup destination"
    )


def apply_backup_retention(
    data_paths: DataPaths,
    *,
    daily: int = 30,
    monthly: int = 12,
    _clock: Clock | None = None,
) -> tuple[Path, ...]:
    """Prune ordinary snapshots while preserving safety-labelled backups."""

    backup_dir = validate_owned_path(data_paths, Path(data_paths.backups))
    if not backup_dir.is_dir():
        return ()
    clock = _clock or system_utc_now
    reference_now = clock()
    if reference_now.tzinfo is None or reference_now.utcoffset() is None:
        raise ValueError("Backup retention clock must be timezone-aware")
    reference_now = reference_now.astimezone(timezone.utc)
    ordinary: list[tuple[datetime, Path]] = []
    for path in backup_dir.glob("*.sqlite"):
        validate_owned_path(data_paths, path)
        parsed = managed_backup_timestamp(path)
        if parsed is not None:
            ordinary.append((parsed, path))
    ordinary.sort(key=lambda item: (item[0], item[1].name), reverse=True)

    keep: set[Path] = {
        path for timestamp, path in ordinary if timestamp > reference_now
    }
    eligible = [
        (timestamp, path)
        for timestamp, path in ordinary
        if timestamp <= reference_now
    ]
    if eligible:
        daily_cutoff = reference_now.date() - timedelta(days=max(daily - 1, 0))
        seen_days: dict[str, datetime] = {}
        for timestamp, path in eligible:
            if timestamp.date() < daily_cutoff:
                continue
            day = timestamp.strftime("%Y-%m-%d")
            if day not in seen_days:
                seen_days[day] = timestamp
                keep.add(path)
            elif seen_days[day] == timestamp:
                keep.add(path)

        seen_months: dict[str, datetime] = {}
        for timestamp, path in eligible:
            if timestamp.date() >= daily_cutoff:
                continue
            month = timestamp.strftime("%Y-%m")
            if month not in seen_months and len(seen_months) < monthly:
                seen_months[month] = timestamp
                keep.add(path)
            elif seen_months.get(month) == timestamp:
                keep.add(path)

    removed: list[Path] = []
    for _, path in ordinary:
        if path in keep:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
        removed.append(path)
    return tuple(removed)


def managed_backup_timestamp(path: Path) -> datetime | None:
    """Parse only legacy and current ordinary backup filenames."""

    parsed = parse_backup_name(path)
    if parsed.kind is not BackupNameKind.RETENTION_MANAGED:
        return None
    return parsed.timestamp


def parse_backup_name(path: Path) -> BackupName:
    """Classify plugin snapshots separately from unrelated SQLite files."""

    legacy = _LEGACY_MANAGED_BACKUP_NAME.fullmatch(path.name)
    if legacy is not None:
        timestamp = _parse_backup_timestamp(legacy["timestamp"])
        return BackupName(
            (
                BackupNameKind.RETENTION_MANAGED
                if timestamp is not None
                else BackupNameKind.MALFORMED
            ),
            timestamp=timestamp,
            label="backup",
        )

    current = _CURRENT_PLUGIN_BACKUP_NAME.fullmatch(path.name)
    if current is not None:
        label = current["label"]
        timestamp = _parse_backup_timestamp(current["timestamp"])
        if (
            len(label) > _MAX_BACKUP_LABEL_LENGTH
            or _BACKUP_LABEL.fullmatch(label) is None
            or timestamp is None
        ):
            return BackupName(BackupNameKind.MALFORMED)
        kind = (
            BackupNameKind.RETENTION_MANAGED
            if label == "backup"
            else BackupNameKind.PLUGIN_BACKUP
        )
        return BackupName(kind, timestamp=timestamp, label=label)

    if _PLUGIN_LIKE_BACKUP_NAME.fullmatch(path.name) is not None:
        return BackupName(BackupNameKind.MALFORMED)
    return BackupName(BackupNameKind.UNMANAGED)


def _parse_backup_timestamp(stamp: str) -> datetime | None:
    for pattern in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(stamp, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
