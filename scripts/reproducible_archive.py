#!/usr/bin/env python3
"""Create a deterministic source archive from the committed Git HEAD tree."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import io
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
import tempfile


_ARCHIVE_MODES = {"100644": 0o644, "100755": 0o755}
_ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES = frozenset(
    {"node_modules", ".pytest_cache", "dist-package"}
)
_SENSITIVE_TRACKED_SUFFIXES = frozenset({".key", ".pem"})
_REPARSE_POINT = 0x400
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
    | {f"com{number}" for number in ("¹", "²", "³")}
    | {f"lpt{number}" for number in ("¹", "²", "³")}
)
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('?*|<>"')


class ArchiveError(RuntimeError):
    """Raised when package inputs or archive destinations are unsafe."""


@dataclass(frozen=True)
class ArchiveManifest:
    commit_sha: str
    members: tuple[str, ...]


def _git(package_root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", os.fspath(package_root), *arguments],
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArchiveError("Unable to read the package Git object database") from error


def _is_symlink_or_reparse(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _validate_existing_path(path: Path, *, label: str) -> None:
    if _is_symlink_or_reparse(path):
        raise ArchiveError(f"{label} must not be a symlink or reparse point: {path}")


def _validate_descendant(
    root: Path,
    candidate: Path,
    *,
    label: str,
) -> Path:
    lexical_root = Path(os.path.abspath(root))
    lexical_candidate = Path(os.path.abspath(candidate))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError as error:
        raise ArchiveError(f"{label} escapes its allowed root") from error
    current = lexical_root
    _validate_existing_path(current, label=label)
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            _validate_existing_path(current, label=label)
    if lexical_candidate.resolve(strict=False).is_relative_to(
        lexical_root.resolve(strict=True)
    ):
        return lexical_candidate
    raise ArchiveError(f"{label} escapes its allowed root")


def _validate_member_name(
    name: str,
    prefix_parts: tuple[str, ...],
) -> tuple[str, ...]:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(
            character in _WINDOWS_FORBIDDEN_CHARACTERS
            for character in name
        )
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in name
        )
    ):
        raise ArchiveError(
            f"Tracked member name is unsafe across platforms: {name!r}"
        )
    parts = tuple(name.split("/"))
    if (
        any(part in ("", ".", "..") for part in parts)
        or len(parts) <= len(prefix_parts)
        or parts[: len(prefix_parts)] != prefix_parts
    ):
        raise ArchiveError(
            f"Tracked member escapes or duplicates the package: {name!r}"
        )
    _windows_member_key(parts)
    return parts


def _windows_member_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for part in parts:
        if part.endswith((".", " ")):
            raise ArchiveError(
                "Tracked member component has a Windows-unsafe trailing "
                f"dot or space: {part!r}"
            )
        windows_part = part.rstrip(" .")
        device_stem = windows_part.split(".", 1)[0].casefold()
        if device_stem in _WINDOWS_DEVICE_NAMES:
            raise ArchiveError(
                f"Tracked member component is a Windows device name: {part!r}"
            )
        normalized.append(windows_part.casefold())
    return tuple(normalized)


def _record_windows_nodes(
    parts: tuple[str, ...],
    nodes: dict[
        tuple[str, ...],
        tuple[tuple[str, ...], str],
    ],
) -> None:
    windows_key = _windows_member_key(parts)
    for length in range(1, len(parts) + 1):
        normalized_prefix = windows_key[:length]
        original_prefix = parts[:length]
        node_kind = "file" if length == len(parts) else "directory"
        previous = nodes.get(normalized_prefix)
        if previous is not None and previous != (
            original_prefix,
            node_kind,
        ):
            raise ArchiveError(
                "Tracked member has a Windows-normalized prefix collision: "
                f"{'/'.join(original_prefix)!r}"
            )
        nodes[normalized_prefix] = (original_prefix, node_kind)


def _reject_sensitive_tracked_member(
    name: str,
    parts: tuple[str, ...],
) -> None:
    if (
        any(part.casefold() == ".venv" for part in parts)
        or PurePosixPath(parts[-1]).suffix.casefold()
        in _SENSITIVE_TRACKED_SUFFIXES
    ):
        raise ArchiveError(
            f"Git HEAD contains a sensitive tracked member: {name}"
        )


def _head_files(
    package_root: Path,
) -> tuple[str, list[tuple[str, str, int]]]:
    git_root = Path(
        os.fsdecode(_git(package_root, "rev-parse", "--show-toplevel")).strip()
    )
    package_prefix = os.fsdecode(
        _git(package_root, "rev-parse", "--show-prefix")
    ).strip()
    git_root = Path(os.path.abspath(git_root))
    package_root = _validate_descendant(
        git_root,
        package_root,
        label="package root",
    )
    expected_root = git_root.joinpath(*PurePosixPath(package_prefix).parts)
    if package_root.resolve(strict=True) != expected_root.resolve(strict=True):
        raise ArchiveError("Package root does not match its Git prefix")

    unmerged_arguments = ["ls-files", "--unmerged", "--full-name", "-z"]
    if package_prefix:
        unmerged_arguments.extend(("--", f":(top){package_prefix}"))
    unmerged = _git(package_root, *unmerged_arguments)
    if unmerged:
        raise ArchiveError("Package index contains unmerged entries")

    head = os.fsdecode(
        _git(package_root, "rev-parse", "--verify", "HEAD^{commit}")
    ).strip()
    tree_arguments = ["ls-tree", "-r", "-z", "--full-tree", head]
    if package_prefix:
        tree_arguments.extend(("--", f":(top){package_prefix}"))
    records = _git(package_root, *tree_arguments)
    tracked: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    windows_nodes: dict[
        tuple[str, ...],
        tuple[tuple[str, ...], str],
    ] = {}
    prefix_parts = PurePosixPath(package_prefix.rstrip("/")).parts
    for raw_record in records.split(b"\0"):
        if not raw_record:
            continue
        try:
            raw_metadata, raw_name = raw_record.split(b"\t", 1)
            raw_mode, raw_type, raw_object_id = raw_metadata.split(b" ", 2)
            mode = raw_mode.decode("ascii")
            object_type = raw_type.decode("ascii")
            object_id = raw_object_id.decode("ascii")
            name = raw_name.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise ArchiveError("Git HEAD contains an unsupported tree record") from error
        if mode == "120000":
            raise ArchiveError(f"Tracked member must not be a symlink: {name}")
        if mode == "160000" or object_type == "commit":
            raise ArchiveError(f"Tracked member must not be a submodule: {name}")
        if object_type != "blob" or mode not in _ARCHIVE_MODES:
            raise ArchiveError(
                f"Tracked member must be a regular 100644/100755 blob: {name}"
            )
        parts = _validate_member_name(name, prefix_parts)
        _reject_sensitive_tracked_member(name, parts)
        relative_parts = parts[len(prefix_parts) :]
        archive_parts = (package_root.name, *relative_parts)
        archive_name = "/".join(archive_parts)
        if archive_name in seen:
            raise ArchiveError(
                f"Tracked member has a duplicate collision: {archive_name!r}"
            )
        _record_windows_nodes(archive_parts, windows_nodes)
        if parts[len(prefix_parts)] in _ARCHIVE_EXCLUDED_TOP_LEVEL_DIRECTORIES:
            continue
        seen.add(archive_name)
        tracked.append((archive_name, object_id, _ARCHIVE_MODES[mode]))
    if not tracked:
        raise ArchiveError("No committed package sources were found in Git HEAD")
    tracked.sort(key=lambda item: item[0])
    return head, tracked


def _blob(package_root: Path, object_id: str) -> bytes:
    try:
        return subprocess.check_output(
            [
                "git",
                "-C",
                os.fspath(package_root),
                "cat-file",
                "blob",
                object_id,
            ],
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ArchiveError(f"Unable to read committed blob {object_id}") from error


def create_archive(
    package_root: Path,
    output: Path,
) -> ArchiveManifest:
    package_root = Path(os.path.abspath(package_root))
    head, tracked = _head_files(package_root)
    archive_root = package_root / "dist-package"
    if os.path.lexists(archive_root):
        _validate_descendant(
            package_root,
            archive_root,
            label="archive directory",
        )
        if not archive_root.is_dir():
            raise ArchiveError("Archive directory must be a directory")
    else:
        archive_root.mkdir()
    output = _validate_descendant(
        archive_root,
        output,
        label="archive output",
    )
    if output.parent != archive_root:
        raise ArchiveError("Archive output must be directly under dist-package")
    if os.path.lexists(output):
        _validate_existing_path(output, label="archive output")
        if not output.is_file():
            raise ArchiveError("Archive output must be a regular file")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".source-archive-",
            suffix=".tmp",
            dir=archive_root,
            delete=False,
        ) as raw_output:
            temporary_name = raw_output.name
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                mtime=0,
                fileobj=raw_output,
            ) as compressed:
                with tarfile.open(
                    mode="w",
                    fileobj=compressed,
                    format=tarfile.PAX_FORMAT,
                ) as archive:
                    for name, object_id, mode in tracked:
                        data = _blob(package_root, object_id)
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        member.mode = mode
                        member.mtime = 0
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        archive.addfile(member, io.BytesIO(data))
        os.replace(temporary_name, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return ArchiveManifest(
        commit_sha=head,
        members=tuple(name for name, _object_id, _mode in tracked),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        create_archive(arguments.package_root, arguments.output)
    except (ArchiveError, OSError) as error:
        print(f"Archive creation failed: {error}", file=sys.stderr)
        return 1
    print(f"Created source archive: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
