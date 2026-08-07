#!/usr/bin/env python3
"""Create and restore fail-closed SQLite cold backups."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile


class ColdBackupError(RuntimeError):
    """Raised when a cold backup operation cannot complete safely."""


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _require_regular_file(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ColdBackupError(f"{label} does not exist") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ColdBackupError(f"{label} must be a regular file")


def _require_directory(path: Path, *, label: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise ColdBackupError(f"{label} must be an existing directory")


def _sidecars(path: Path) -> tuple[Path, Path]:
    return (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    )


def _database_artifacts(path: Path) -> tuple[Path, ...]:
    return (path, *_sidecars(path), path.with_name(path.name + "-journal"))


def _artifact_key(path: Path) -> str:
    resolved = os.fspath(path.resolve(strict=False))
    if os.name != "nt":
        return os.path.normcase(resolved)
    return _win32_artifact_key(resolved)


def _win32_artifact_key(resolved: str) -> str:
    folded = resolved.casefold()
    extended_unc = "\\\\?\\unc\\"
    extended = "\\\\?\\"
    if folded.startswith(extended_unc):
        resolved = "\\\\" + resolved[len(extended_unc) :]
    elif folded.startswith(extended):
        resolved = resolved[len(extended) :]
    components = resolved.replace("/", "\\").split("\\")
    collapsed = "\\".join(component.rstrip(" .") for component in components)
    return os.path.normcase(os.path.normpath(collapsed)).casefold()


def _same_existing_file(first: Path, second: Path) -> bool:
    if not os.path.lexists(first) or not os.path.lexists(second):
        return False
    try:
        return os.path.samefile(first, second)
    except OSError as error:
        raise ColdBackupError("unable to compare artifact file identity") from error


def _require_disjoint_artifacts(*named_paths: tuple[str, Path]) -> None:
    seen: list[tuple[str, str, Path]] = []
    for label, path in named_paths:
        for artifact in _database_artifacts(path):
            key = _artifact_key(artifact)
            for previous_key, previous_label, previous_path in seen:
                if key == previous_key or _same_existing_file(
                    artifact,
                    previous_path,
                ):
                    raise ColdBackupError(
                        f"{previous_label} and {label} artifacts overlap: "
                        f"{artifact.name}"
                    )
            seen.append((key, label, artifact))


def _ensure_absent(paths: tuple[Path, ...], *, label: str) -> None:
    for path in paths:
        if os.path.lexists(path):
            raise ColdBackupError(f"{label} already exists: {path.name}")


def _reserve_database(path: Path) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)


def _quick_check(path: Path, *, label: str) -> None:
    try:
        with closing(_read_only(path)) as connection:
            results = [
                str(row[0]) for row in connection.execute("PRAGMA quick_check")
            ]
    except sqlite3.Error as error:
        raise ColdBackupError(f"{label} is not a valid SQLite database") from error
    if results != ["ok"]:
        raise ColdBackupError(f"{label} failed SQLite quick_check")


def _copy_database(source: Path, destination: Path) -> None:
    try:
        with closing(_read_only(source)) as input_database:
            with closing(sqlite3.connect(destination)) as output_database:
                input_database.backup(output_database)
    except sqlite3.Error as error:
        raise ColdBackupError("SQLite backup API failed") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_created_database(path: Path) -> None:
    for candidate in _database_artifacts(path):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def backup_database(source: Path, destination: Path) -> str:
    """Create a verified SQLite backup without overwriting a target."""

    source = Path(source)
    destination = Path(destination)
    _require_regular_file(source, label="source database")
    _require_directory(destination.parent, label="backup directory")
    _require_disjoint_artifacts(
        ("source database", source),
        ("cold backup target", destination),
    )
    _ensure_absent(
        _database_artifacts(destination),
        label="cold backup target",
    )

    created = False
    try:
        _reserve_database(destination)
        created = True
        _copy_database(source, destination)
        _quick_check(destination, label="cold backup")
        return _sha256(destination)
    except FileExistsError as error:
        raise ColdBackupError("cold backup target already exists") from error
    except Exception as error:
        if created:
            try:
                _cleanup_created_database(destination)
            except OSError as cleanup_error:
                raise ColdBackupError(
                    f"{error}; incomplete target remains: {destination.name}"
                ) from cleanup_error
        raise


def _move_no_overwrite(source: Path, destination: Path) -> None:
    """Move one regular file via a no-clobber hard link."""

    _require_regular_file(source, label="file to quarantine")
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError as error:
        raise ColdBackupError(
            f"quarantine target already exists: {destination.name}"
        ) from error
    except OSError as error:
        raise ColdBackupError(
            f"unable to create quarantine file: {destination.name}"
        ) from error
    try:
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


def restore_database(backup: Path, active: Path, quarantine: Path) -> str:
    """Validate a backup, quarantine current files, and restore it."""

    backup = Path(backup)
    active = Path(active)
    quarantine = Path(quarantine)
    _require_regular_file(backup, label="cold backup")
    _require_regular_file(active, label="active database")
    _require_directory(active.parent, label="active database directory")
    if quarantine.parent.resolve() != active.parent.resolve():
        raise ColdBackupError("quarantine must be in the active database directory")
    _require_disjoint_artifacts(
        ("cold backup", backup),
        ("active database", active),
        ("quarantine", quarantine),
    )

    active_paths = _database_artifacts(active)
    quarantine_paths = _database_artifacts(quarantine)
    _ensure_absent(quarantine_paths, label="quarantine target")
    for sidecar in active_paths[1:]:
        if os.path.lexists(sidecar):
            _require_regular_file(sidecar, label="active SQLite sidecar")
    _quick_check(backup, label="cold backup")

    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{active.name}.restore-",
        suffix=".tmp",
        dir=active.parent,
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    moved: list[tuple[Path, Path]] = []
    try:
        _copy_database(backup, candidate)
        _quick_check(candidate, label="restored candidate")
        digest = _sha256(candidate)

        try:
            for source, destination in zip(
                active_paths,
                quarantine_paths,
                strict=True,
            ):
                if source.exists():
                    _move_no_overwrite(source, destination)
                    moved.append((source, destination))
            _move_no_overwrite(candidate, active)
        except Exception as error:
            rollback_error: Exception | None = None
            for source, destination in reversed(moved):
                try:
                    _move_no_overwrite(destination, source)
                except Exception as current_rollback_error:
                    rollback_error = current_rollback_error
                    break
            if rollback_error is not None:
                raise ColdBackupError(
                    f"{error}; quarantine rollback failed and restore stopped"
                ) from rollback_error
            raise
    except Exception as error:
        try:
            _cleanup_created_database(candidate)
        except OSError as cleanup_error:
            raise ColdBackupError(
                f"{error}; incomplete restore candidate remains: "
                f"{candidate.name} (cleanup failed: {cleanup_error})"
            ) from error
        raise
    try:
        _cleanup_created_database(candidate)
    except OSError as cleanup_error:
        raise ColdBackupError(
            "restore completed; candidate cleanup failed and candidate "
            f"residue may remain: {candidate.name}"
        ) from cleanup_error
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or restore a verified SQLite cold backup. Stop the target "
            "instance before running this command."
        )
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--source", required=True, type=Path)
    backup_parser.add_argument("--destination", required=True, type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup", required=True, type=Path)
    restore_parser.add_argument("--active", required=True, type=Path)
    restore_parser.add_argument("--quarantine", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.operation == "backup":
            digest = backup_database(arguments.source, arguments.destination)
            print(f"Cold backup verified (SHA-256 {digest})")
        else:
            digest = restore_database(
                arguments.backup,
                arguments.active,
                arguments.quarantine,
            )
            print(f"Restored database verified (SHA-256 {digest})")
    except (ColdBackupError, OSError) as error:
        print(f"Cold backup operation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
