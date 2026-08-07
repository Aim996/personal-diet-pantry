from __future__ import annotations

import importlib.util
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script() -> ModuleType:
    path = PROJECT_ROOT / "scripts" / "cold_backup.py"
    assert path.is_file(), "cold_backup.py must exist"
    spec = importlib.util.spec_from_file_location("cold_backup", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cold_backup"] = module
    spec.loader.exec_module(module)
    return module


def _database(path: Path, value: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES (?)", (value,))
        connection.commit()


def _value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("SELECT value FROM evidence").fetchone()[0])


def _artifact_snapshot(path: Path) -> dict[str, bytes | None]:
    return {
        suffix: (
            candidate.read_bytes()
            if candidate.exists()
            else None
        )
        for suffix in ("", "-wal", "-shm", "-journal")
        for candidate in (Path(str(path) + suffix),)
    }


def _directory_file_snapshot(path: Path) -> dict[str, bytes]:
    return {
        candidate.name: candidate.read_bytes()
        for candidate in path.iterdir()
        if candidate.is_file()
    }


def _leave_hot_rollback_journal(path: Path) -> Path:
    worker = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=DELETE")
connection.execute("PRAGMA synchronous=FULL")
connection.execute("PRAGMA cache_size=1")
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "UPDATE evidence SET value = ?",
    ("uncommitted-" + "x" * 1_000_000,),
)
os._exit(0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", worker, str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    journal = path.with_name(path.name + "-journal")
    assert journal.is_file()
    assert journal.stat().st_size > 512
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    return journal


def test_backup_includes_committed_data_still_in_wal(tmp_path: Path) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "diet.sqlite.pre-v0.7.2"

    with sqlite3.connect(source) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO evidence VALUES ('committed-in-wal')")
        writer.commit()
        assert source.with_name("diet.sqlite-wal").stat().st_size > 0

        digest = cold_backup.backup_database(source, destination)

    assert len(digest) == 64
    assert _value(destination) == "committed-in-wal"
    with sqlite3.connect(destination) as copied:
        assert copied.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_backup_refuses_existing_target_without_changing_it(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "diet.sqlite.pre-v0.7.2"
    _database(source, "source")
    destination.write_bytes(b"existing-backup")

    with pytest.raises(cold_backup.ColdBackupError, match="already exists"):
        cold_backup.backup_database(source, destination)

    assert destination.read_bytes() == b"existing-backup"
    assert _value(source) == "source"


def test_backup_refuses_existing_target_journal_without_changing_it(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "diet.sqlite.pre-v0.7.2"
    journal = destination.with_name(destination.name + "-journal")
    _database(source, "source")
    journal.write_bytes(b"existing-journal")

    with pytest.raises(cold_backup.ColdBackupError, match="already exists"):
        cold_backup.backup_database(source, destination)

    assert not destination.exists()
    assert journal.read_bytes() == b"existing-journal"
    assert _value(source) == "source"


@pytest.mark.parametrize("suffix", ("", "-wal", "-shm", "-journal"))
def test_backup_rejects_source_artifact_destinations_before_any_write(
    tmp_path: Path,
    suffix: str,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = Path(str(source) + suffix)
    _database(source, "source")
    before = _artifact_snapshot(source)

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.backup_database(source, destination)

    assert _artifact_snapshot(source) == before
    assert _value(source) == "source"


def test_backup_rejects_lexical_alias_of_source_sidecar_before_any_write(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    lexical_component = tmp_path / "unused-component"
    lexical_component.mkdir()
    destination = lexical_component / ".." / "diet.sqlite-wal"
    _database(source, "source")
    before = _artifact_snapshot(source)

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.backup_database(source, destination)

    assert _artifact_snapshot(source) == before
    assert _value(source) == "source"


def test_backup_rejects_hardlink_identity_across_derived_artifacts(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "backup.sqlite"
    destination_wal = Path(str(destination) + "-wal")
    _database(source, "source")
    try:
        os.link(source, destination_wal)
    except OSError as error:
        pytest.skip(f"hard links are unavailable: {error}")
    source_before = source.read_bytes()
    hardlink_before = destination_wal.read_bytes()

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.backup_database(source, destination)

    assert source.read_bytes() == source_before
    assert destination_wal.read_bytes() == hardlink_before
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path alias")
def test_backup_rejects_extended_alias_of_source_sidecar_before_any_write(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    _database(source, "source")
    destination = Path("\\\\?\\" + str(Path(str(source) + "-wal").resolve()))
    before = _artifact_snapshot(source)

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.backup_database(source, destination)

    assert _artifact_snapshot(source) == before
    assert _value(source) == "source"


@pytest.mark.skipif(os.name != "nt", reason="Win32 filename aliases")
@pytest.mark.parametrize("suffix", ("", "-wal", "-shm", "-journal"))
@pytest.mark.parametrize("trailing", (".", " "), ids=("dot", "space"))
def test_backup_rejects_win32_trailing_alias_before_any_write(
    tmp_path: Path,
    suffix: str,
    trailing: str,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = Path(f"{source}{suffix}{trailing}")
    canonical = Path(f"{source}{suffix}")
    _database(source, "source")
    artifacts_before = _artifact_snapshot(source)
    directory_before = _directory_file_snapshot(tmp_path)
    error = None
    digest = None

    try:
        digest = cold_backup.backup_database(source, destination)
    except cold_backup.ColdBackupError as current_error:
        error = current_error

    canonical_exists = os.path.lexists(canonical)
    same_file = (
        canonical_exists
        and os.path.lexists(destination)
        and os.path.samefile(canonical, destination)
    )
    assert error is not None, (
        "Win32 alias unexpectedly produced a verified backup: "
        f"digest={digest}, canonical={canonical.name}, "
        f"canonical_exists={canonical_exists}, samefile={same_file}"
    )
    assert "artifacts overlap" in str(error)
    assert _artifact_snapshot(source) == artifacts_before
    assert _directory_file_snapshot(tmp_path) == directory_before
    assert _value(source) == "source"


@pytest.mark.skipif(os.name != "nt", reason="Win32 filename aliases")
@pytest.mark.parametrize("trailing", (".", " "), ids=("dot", "space"))
def test_backup_rejects_win32_trailing_alias_in_ancestor_component(
    tmp_path: Path,
    trailing: str,
) -> None:
    cold_backup = _load_script()
    canonical_parent = tmp_path / "inventory"
    canonical_parent.mkdir()
    source = canonical_parent / "diet.sqlite"
    destination = tmp_path / f"inventory{trailing}" / "diet.sqlite-wal"
    _database(source, "source")
    artifacts_before = _artifact_snapshot(source)
    directory_before = _directory_file_snapshot(canonical_parent)

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.backup_database(source, destination)

    assert _artifact_snapshot(source) == artifacts_before
    assert _directory_file_snapshot(canonical_parent) == directory_before
    assert _value(source) == "source"


@pytest.mark.skipif(os.name != "nt", reason="Win32 filename aliases")
@pytest.mark.parametrize("trailing", (".", " "), ids=("dot", "space"))
@pytest.mark.parametrize("extended", (False, True), ids=("ordinary", "extended"))
def test_win32_artifact_key_collapses_trailing_alias_in_every_component(
    tmp_path: Path,
    trailing: str,
    extended: bool,
) -> None:
    cold_backup = _load_script()
    canonical = tmp_path / "missing-parent" / "diet.sqlite-wal"
    alias = tmp_path / f"missing-parent{trailing}" / f"diet.sqlite-wal{trailing}"
    if extended:
        alias = Path("\\\\?\\" + str(alias.absolute()))

    assert cold_backup._artifact_key(alias) == cold_backup._artifact_key(canonical)


def test_backup_allows_non_alias_destination_after_disjoint_check(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "safe-cold-backup.sqlite"
    _database(source, "source")
    source_before = _artifact_snapshot(source)

    digest = cold_backup.backup_database(source, destination)

    assert digest == cold_backup._sha256(destination)
    assert _value(destination) == "source"
    assert _artifact_snapshot(source) == source_before


def test_restore_quarantines_current_database_and_sidecars(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    active.with_name("diet.sqlite-wal").write_bytes(b"current-wal")
    active.with_name("diet.sqlite-shm").write_bytes(b"current-shm")

    digest = cold_backup.restore_database(backup, active, quarantine)

    assert len(digest) == 64
    assert _value(active) == "pre-upgrade"
    assert quarantine.with_name(quarantine.name + "-wal").read_bytes() == b"current-wal"
    assert quarantine.with_name(quarantine.name + "-shm").read_bytes() == b"current-shm"
    assert not active.with_name("diet.sqlite-wal").exists()
    assert not active.with_name("diet.sqlite-shm").exists()
    assert _value(quarantine) == "migrated"


def test_restore_quarantines_hot_journal_before_first_open(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    cold_backup.backup_database(source, active)
    with closing(sqlite3.connect(active)) as connection:
        connection.execute("UPDATE evidence SET value = 'migrated'")
        connection.commit()
    hot_journal = _leave_hot_rollback_journal(active)
    journal_probe = tmp_path / "journal-probe.sqlite"
    shutil.copyfile(active, journal_probe)
    shutil.copyfile(
        hot_journal,
        journal_probe.with_name(journal_probe.name + "-journal"),
    )
    with closing(sqlite3.connect(journal_probe)) as probed:
        assert probed.execute("SELECT value FROM evidence").fetchone()[0] == (
            "migrated"
        )
        assert probed.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    cold_backup.restore_database(backup, active, quarantine)

    with closing(sqlite3.connect(active)) as restored:
        assert restored.execute("SELECT value FROM evidence").fetchone()[0] == (
            "pre-upgrade"
        )
        assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert not hot_journal.exists()
    assert quarantine.with_name(quarantine.name + "-journal").is_file()


def test_restore_refuses_existing_quarantine_journal_before_changing_active(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    quarantine_journal = quarantine.with_name(quarantine.name + "-journal")
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    active_bytes = active.read_bytes()
    quarantine_journal.write_bytes(b"existing-quarantine-journal")

    with pytest.raises(cold_backup.ColdBackupError, match="already exists"):
        cold_backup.restore_database(backup, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert quarantine_journal.read_bytes() == b"existing-quarantine-journal"
    assert not quarantine.exists()


def test_restore_refuses_existing_quarantine_without_changing_current_files(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    active_bytes = active.read_bytes()
    wal = active.with_name(active.name + "-wal")
    shm = active.with_name(active.name + "-shm")
    wal.write_bytes(b"current-wal")
    shm.write_bytes(b"current-shm")
    quarantine.write_bytes(b"existing-quarantine")

    with pytest.raises(cold_backup.ColdBackupError, match="already exists"):
        cold_backup.restore_database(backup, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert wal.read_bytes() == b"current-wal"
    assert shm.read_bytes() == b"current-shm"
    assert quarantine.read_bytes() == b"existing-quarantine"


def test_restore_rejects_bad_backup_without_changing_current_files(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    backup = tmp_path / "bad-backup.sqlite"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    backup.write_bytes(b"not a sqlite database")
    _database(active, "migrated")
    active_bytes = active.read_bytes()
    wal = active.with_name(active.name + "-wal")
    shm = active.with_name(active.name + "-shm")
    wal.write_bytes(b"current-wal")
    shm.write_bytes(b"current-shm")

    with pytest.raises(cold_backup.ColdBackupError, match="valid SQLite"):
        cold_backup.restore_database(backup, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert wal.read_bytes() == b"current-wal"
    assert shm.read_bytes() == b"current-shm"
    assert not quarantine.exists()
    assert not tuple(tmp_path.glob(".diet.sqlite.restore-*.tmp"))


def test_restore_rolls_back_files_if_quarantine_stops_partway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    active_bytes = active.read_bytes()
    wal = active.with_name(active.name + "-wal")
    shm = active.with_name(active.name + "-shm")
    wal.write_bytes(b"current-wal")
    shm.write_bytes(b"current-shm")
    real_move = cold_backup._move_no_overwrite
    calls = 0

    def fail_second_move(move_source: Path, move_destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise cold_backup.ColdBackupError("injected sidecar move failure")
        real_move(move_source, move_destination)

    monkeypatch.setattr(cold_backup, "_move_no_overwrite", fail_second_move)

    with pytest.raises(cold_backup.ColdBackupError, match="injected"):
        cold_backup.restore_database(backup, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert wal.read_bytes() == b"current-wal"
    assert shm.read_bytes() == b"current-shm"
    assert not quarantine.exists()


def test_backup_reports_original_failure_when_cleanup_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "diet.sqlite"
    destination = tmp_path / "diet.sqlite.pre-v0.7.2"
    _database(source, "source")

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise cold_backup.ColdBackupError("injected SQLite copy failure")

    def fail_cleanup(_path: Path) -> None:
        raise PermissionError("injected Windows lock")

    monkeypatch.setattr(cold_backup, "_copy_database", fail_copy)
    monkeypatch.setattr(cold_backup, "_cleanup_created_database", fail_cleanup)

    with pytest.raises(
        cold_backup.ColdBackupError,
        match="injected SQLite copy failure.*incomplete target remains",
    ):
        cold_backup.backup_database(source, destination)

    assert destination.exists()


def test_restore_preserves_original_failure_when_candidate_cleanup_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    active_bytes = active.read_bytes()

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise cold_backup.ColdBackupError("original restore failure")

    def fail_cleanup(_path: Path) -> None:
        raise PermissionError("candidate cleanup lock")

    monkeypatch.setattr(cold_backup, "_copy_database", fail_copy)
    monkeypatch.setattr(cold_backup, "_cleanup_created_database", fail_cleanup)

    with pytest.raises(
        cold_backup.ColdBackupError,
        match="original restore failure.*incomplete restore candidate remains",
    ):
        cold_backup.restore_database(backup, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert not quarantine.exists()


def test_restore_reports_completed_state_when_candidate_cleanup_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")

    def fail_cleanup(_path: Path) -> None:
        raise PermissionError("candidate cleanup lock")

    monkeypatch.setattr(cold_backup, "_cleanup_created_database", fail_cleanup)

    with pytest.raises(
        cold_backup.ColdBackupError,
        match="restore completed.*candidate residue may remain",
    ):
        cold_backup.restore_database(backup, active, quarantine)

    assert _value(active) == "pre-upgrade"
    assert _value(quarantine) == "migrated"


def test_restore_rejects_overlapping_derived_artifacts_without_changes(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    source = tmp_path / "pre-upgrade.sqlite"
    backup = tmp_path / "diet.sqlite.pre-v0.7.2"
    active = tmp_path / "diet.sqlite"
    quarantine = active.with_name(active.name + "-wal")
    _database(source, "pre-upgrade")
    cold_backup.backup_database(source, backup)
    _database(active, "migrated")
    backup_bytes = backup.read_bytes()
    active_bytes = active.read_bytes()

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.restore_database(backup, active, quarantine)

    assert backup.read_bytes() == backup_bytes
    assert active.read_bytes() == active_bytes
    assert not quarantine.exists()
    assert not quarantine.with_name(quarantine.name + "-wal").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path alias")
def test_restore_rejects_extended_path_alias_without_changes(
    tmp_path: Path,
) -> None:
    cold_backup = _load_script()
    active = tmp_path / "diet.sqlite"
    quarantine = tmp_path / "diet.sqlite.v0.7.2-quarantine"
    _database(active, "migrated")
    active_bytes = active.read_bytes()
    extended_alias = Path("\\\\?\\" + str(active.resolve()))

    with pytest.raises(cold_backup.ColdBackupError, match="artifacts.*overlap"):
        cold_backup.restore_database(extended_alias, active, quarantine)

    assert active.read_bytes() == active_bytes
    assert not quarantine.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended UNC alias")
def test_artifact_key_normalizes_lowercase_extended_unc() -> None:
    cold_backup = _load_script()
    ordinary = Path(r"\\invalid.example\share\diet.sqlite-wal")
    extended = Path(r"\\?\unc\invalid.example\share\diet.sqlite-wal")

    assert cold_backup._artifact_key(extended) == cold_backup._artifact_key(
        ordinary
    )
