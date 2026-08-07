#!/usr/bin/env python3
"""Verify and atomically publish reproducible v0.7.4.27 artifacts."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Protocol
import xml.etree.ElementTree as ET


PRODUCT_VERSION = "0.7.4.27"
VERSION = "0.8.27"
SOURCE_NAME = f"personal-diet-pantry-{PRODUCT_VERSION}-source.tar.gz"
INSTALLABLE_NAME = f"personal-diet-pantry-{PRODUCT_VERSION}-installable.tgz"
MANIFEST_NAME = "release-manifest.json"
SUMMARY_NAME = f"TEST-SUMMARY-v{PRODUCT_VERSION}.zh-CN.md"
HASHES_NAME = "SHA256SUMS"
GITHUB_DOCS_NAME = "GitHub文档"
RELEASE_ENTRY_NAMES = (
    SOURCE_NAME,
    INSTALLABLE_NAME,
    MANIFEST_NAME,
    SUMMARY_NAME,
    HASHES_NAME,
    GITHUB_DOCS_NAME,
)
GITHUB_DOCUMENTS = (
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "README.md",
    "README.en.md",
    "GITHUB-WORKFLOW.zh-CN.md",
    "RELEASE.zh-CN.md",
    "UPDATE-v0.7.3.zh-CN.md",
    "UPDATE-v0.7.3.1.zh-CN.md",
    "UPDATE-v0.7.3.2.zh-CN.md",
    "UPDATE-v0.7.3.3.zh-CN.md",
    "UPDATE-v0.7.3.4.zh-CN.md",
    "UPDATE-v0.7.3.5.zh-CN.md",
    "UPDATE-v0.7.4.0.zh-CN.md",
    "UPDATE-v0.7.4.2.zh-CN.md",
    "UPDATE-v0.7.4.3.zh-CN.md",
    "UPDATE-v0.7.4.4.zh-CN.md",
    "UPDATE-v0.7.4.5.zh-CN.md",
    "UPDATE-v0.7.4.27.zh-CN.md",
    "CONTEXT.md",
    "migrations/021_package_semantics_and_product_operations.sql",
    "scripts/cold_backup.py",
    "docs/ARCHITECTURE.zh-CN.md",
    "docs/DATA-MODEL.zh-CN.md",
    "docs/EXAMPLES.zh-CN.md",
    "docs/INSTALL.md",
    "docs/INSTALLATION.zh-CN.md",
    "docs/UPGRADING.md",
    "docs/AI-PROMPTS.zh-CN.md",
    "docs/RELEASING.md",
    "docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md",
    "docs/TOOLS-REFERENCE.zh-CN.md",
    "docs/TROUBLESHOOTING.zh-CN.md",
    "docs/USER-GUIDE.zh-CN.md",
    "docs/development/DEPENDENCY-RISK-ACCEPTANCE.md",
    "docs/development/6.1.5-WEIGHT-DESIGN.zh-CN.md",
    "docs/development/6.1.5-WEIGHT-IMPLEMENTATION-PLAN.zh-CN.md",
    "docs/development/0.6.1.5.1-NATURAL-LANGUAGE-TRIGGER-DESIGN.zh-CN.md",
    (
        "docs/development/"
        "0.6.1.5.1-NATURAL-LANGUAGE-TRIGGER-IMPLEMENTATION-PLAN.zh-CN.md"
    ),
    "docs/development/0.6.1-DESIGN.zh-CN.md",
    "docs/development/0.6.1-IMPLEMENTATION-PLAN.zh-CN.md",
    "docs/development/0.6.7-CONTINUOUS-FILE-MATRIX.zh-CN.md",
    "docs/superpowers/specs/2026-08-01-personal-diet-pantry-v0.7.0-pragmatic-upgrade-design.md",
    "docs/superpowers/plans/2026-08-01-v0.7.0-pragmatic-upgrade.md",
    "docs/superpowers/specs/2026-08-02-personal-diet-pantry-v0.7.3-skill-completeness-design.md",
    "docs/superpowers/plans/2026-08-02-personal-diet-pantry-v0.7.3.md",
    "docs/superpowers/specs/2026-08-03-personal-diet-pantry-v0.7.3.1-liquid-schema-compat-design.md",
    "docs/superpowers/plans/2026-08-03-personal-diet-pantry-v0.7.3.1-liquid-schema-compat.md",
    "docs/superpowers/specs/2026-08-04-personal-diet-pantry-v0.7.3.2-trusted-pantry-loop-design.md",
    "docs/superpowers/plans/2026-08-04-personal-diet-pantry-v0.7.3.2.md",
)


class ReleaseBuildError(RuntimeError):
    """Raised when verified release evidence cannot be produced."""


@dataclass(frozen=True)
class VerificationSummary:
    python_tests: int
    python_passed: int
    python_skipped: int
    python_failed: int
    typescript_tests: int
    typescript_passed: int
    typescript_skipped: int
    typescript_failed: int
    migrations: int
    audit_status: str
    python_version: str
    node_version: str
    npm_version: str


@dataclass(frozen=True)
class ReleaseManifest:
    product_version: str
    version: str
    commit_sha: str
    built_at: str
    python_tests: int
    python_passed: int
    python_skipped: int
    python_failed: int
    typescript_tests: int
    typescript_passed: int
    typescript_skipped: int
    typescript_failed: int
    migrations: int
    audit_status: str
    python_version: str
    node_version: str
    npm_version: str
    source_archive: str
    installable_archive: str
    source_sha256: str
    installable_sha256: str
    source_reproducible: bool
    installable_reproducible: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ReleaseRunner(Protocol):
    def verify(self, project_root: Path) -> VerificationSummary:
        ...

    def create_source_archive(
        self,
        project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        ...

    def create_installable(
        self,
        project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        ...


def _powershell_verify_command(shell: str, script: Path) -> list[str]:
    command = [shell, "-NoProfile"]
    shell_name = shell.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if shell_name in {"powershell", "powershell.exe"}:
        command.extend(("-ExecutionPolicy", "Bypass"))
    command.extend(("-File", os.fspath(script)))
    return command


class LocalReleaseRunner:
    """Execute the real local CI and archive builders."""

    def verify(self, project_root: Path) -> VerificationSummary:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise ReleaseBuildError("PowerShell is required for release verification")
        environment = os.environ.copy()
        environment["PDP_PYTHON"] = sys.executable
        _run(
            _powershell_verify_command(
                shell,
                project_root / "ci" / "verify.ps1",
            ),
            cwd=project_root,
            env=environment,
        )
        artifact_root = project_root / "dist-package"
        python_tests, python_passed, python_skipped, python_failed = (
            _pytest_counts(
                artifact_root / "pytest-results.xml"
            )
        )
        (
            typescript_tests,
            typescript_passed,
            typescript_skipped,
            typescript_failed,
        ) = _vitest_counts(
            artifact_root / "vitest-results.json",
        )
        audit = _read_json(artifact_root / "release-audit.json")
        return VerificationSummary(
            python_tests=python_tests,
            python_passed=python_passed,
            python_skipped=python_skipped,
            python_failed=python_failed,
            typescript_tests=typescript_tests,
            typescript_passed=typescript_passed,
            typescript_skipped=typescript_skipped,
            typescript_failed=typescript_failed,
            migrations=len(tuple((project_root / "migrations").glob("*.sql"))),
            audit_status=str(audit.get("status", "unknown")),
            python_version=_version(
                [sys.executable, "--version"],
                cwd=project_root,
            ),
            node_version=_version(["node", "--version"], cwd=project_root),
            npm_version=_version(["npm", "--version"], cwd=project_root),
        )

    def create_source_archive(
        self,
        project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        archive_root = project_root / "dist-package"
        archive_root.mkdir(exist_ok=True)
        local_output = archive_root / (
            f".release-{os.getpid()}-{output.parent.name}-source.tar.gz"
        )
        try:
            _run(
                [
                    sys.executable,
                    os.fspath(
                        project_root / "scripts" / "reproducible_archive.py"
                    ),
                    "--package-root",
                    os.fspath(project_root),
                    "--output",
                    os.fspath(local_output),
                ],
                cwd=project_root,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_output, output)
        finally:
            local_output.unlink(missing_ok=True)
        return _tar_members(output)

    def create_installable(
        self,
        project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        output.parent.mkdir(parents=True, exist_ok=True)
        npm = shutil.which("npm")
        if npm is None:
            raise ReleaseBuildError("npm is required to build the installable")
        completed = _run(
            [
                npm,
                "pack",
                "--json",
                "--pack-destination",
                os.fspath(output.parent),
            ],
            cwd=project_root,
            capture=True,
        )
        try:
            payload = json.loads(completed.stdout)
            packed = output.parent / payload[0]["filename"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ReleaseBuildError("npm pack did not report its archive") from error
        if packed != output:
            os.replace(packed, output)
        return _tar_members(output)


def verify_release(
    project_root: Path,
    *,
    runner: ReleaseRunner | None = None,
) -> VerificationSummary:
    return (runner or LocalReleaseRunner()).verify(Path(project_root).resolve())


def _successful_test_counts(
    total: int,
    passed: int,
    skipped: int,
    failed: int,
) -> bool:
    return (
        min(total, passed, skipped, failed) >= 0
        and failed == 0
        and passed + skipped == total
    )


def build_release(
    project_root: Path,
    release_root: Path,
    *,
    runner: ReleaseRunner | None = None,
) -> ReleaseManifest:
    """Build both archives twice and publish evidence only after all gates."""

    raw_destination = Path(release_root)
    if not raw_destination.is_absolute():
        raw_destination = Path.cwd() / raw_destination
    project = Path(project_root).resolve()
    destination = _validate_release_destination(project, raw_destination)
    _assert_version(project)
    commit_sha = _assert_clean_git(project)
    release_runner = runner or LocalReleaseRunner()
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.release-build-",
        dir=destination.parent,
    ) as temporary:
        staging = Path(temporary)
        verification = release_runner.verify(project)
        if not _successful_test_counts(
            verification.python_tests,
            verification.python_passed,
            verification.python_skipped,
            verification.python_failed,
        ) or not _successful_test_counts(
            verification.typescript_tests,
            verification.typescript_passed,
            verification.typescript_skipped,
            verification.typescript_failed,
        ):
            raise ReleaseBuildError(
                "verification summary contains failed or inconsistent test counts"
            )
        source_a = staging / "source-a" / SOURCE_NAME
        source_b = staging / "source-b" / SOURCE_NAME
        install_a = staging / "install-a" / INSTALLABLE_NAME
        install_b = staging / "install-b" / INSTALLABLE_NAME
        source_members_a = release_runner.create_source_archive(
            project,
            source_a,
        )
        source_members_b = release_runner.create_source_archive(
            project,
            source_b,
        )
        install_members_a = release_runner.create_installable(
            project,
            install_a,
        )
        install_members_b = release_runner.create_installable(
            project,
            install_b,
        )
        source_hash_a = _sha256(source_a)
        source_hash_b = _sha256(source_b)
        install_hash_a = _sha256(install_a)
        install_hash_b = _sha256(install_b)
        source_reproducible = (
            source_hash_a == source_hash_b
            and source_members_a == source_members_b
        )
        installable_reproducible = (
            install_hash_a == install_hash_b
            and install_members_a == install_members_b
        )
        if not source_reproducible:
            raise ReleaseBuildError("source archive is not reproducible")
        if not installable_reproducible:
            raise ReleaseBuildError("installable archive is not reproducible")
        _validate_source_members(source_members_a)
        _validate_installable_members(install_members_a)

        manifest = ReleaseManifest(
            product_version=PRODUCT_VERSION,
            version=VERSION,
            commit_sha=commit_sha,
            built_at=datetime.now(timezone.utc).isoformat().replace(
                "+00:00",
                "Z",
            ),
            **asdict(verification),
            source_archive=SOURCE_NAME,
            installable_archive=INSTALLABLE_NAME,
            source_sha256=source_hash_a,
            installable_sha256=install_hash_a,
            source_reproducible=True,
            installable_reproducible=True,
        )
        publish_root = staging / "publish"
        publish_root.mkdir()
        published_source = publish_root / SOURCE_NAME
        published_installable = publish_root / INSTALLABLE_NAME
        shutil.copyfile(source_a, published_source)
        shutil.copyfile(install_a, published_installable)
        manifest_path = publish_root / MANIFEST_NAME
        summary_path = publish_root / SUMMARY_NAME
        hashes_path = publish_root / HASHES_NAME
        github_docs_path = publish_root / GITHUB_DOCS_NAME
        _stage_github_documents(project, github_docs_path)
        manifest_path.write_text(
            json.dumps(
                manifest.as_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        summary_path.write_text(
            _summary_markdown(manifest),
            encoding="utf-8",
        )
        hashes_path.write_text(
            "\n".join(
                (
                    f"{source_hash_a}  {SOURCE_NAME}",
                    f"{install_hash_a}  {INSTALLABLE_NAME}",
                    f"{_sha256(manifest_path)}  {MANIFEST_NAME}",
                    f"{_sha256(summary_path)}  {SUMMARY_NAME}",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        _publish_release_directory(publish_root, destination)
    return manifest


def _validate_release_destination(
    project_root: Path,
    raw_destination: Path,
) -> Path:
    _assert_no_link_or_reparse_point(raw_destination)
    normalized_destination = Path(
        os.path.normpath(os.fspath(raw_destination))
    )
    if normalized_destination != raw_destination:
        _assert_no_link_or_reparse_point(normalized_destination)
    destination = normalized_destination.resolve()
    git_root_output = _run(
        [
            "git",
            "-C",
            os.fspath(project_root),
            "rev-parse",
            "--show-toplevel",
        ],
        cwd=project_root,
        capture=True,
    ).stdout.strip()
    git_root = Path(git_root_output).resolve()
    protected_roots = {git_root, project_root.resolve()}
    if any(
        destination == protected_root
        or protected_root in destination.parents
        for protected_root in protected_roots
    ):
        raise ReleaseBuildError(
            "release root must be outside the Git worktree and project root"
        )
    if destination.exists():
        raise ReleaseBuildError("release root must not already exist")
    return destination


def _assert_no_link_or_reparse_point(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ReleaseBuildError(
                "cannot inspect release root path for links or reparse points"
            ) from error
        if _is_link_or_reparse(metadata):
            raise ReleaseBuildError(
                "release root path must not contain a symbolic link or reparse point"
            )


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        reparse_flag
        and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _assert_version(project_root: Path) -> None:
    package = _read_json(project_root / "package.json")
    if package.get("version") != VERSION:
        raise ReleaseBuildError(
            f"release builder requires package version {VERSION}"
        )
    if package.get("productVersion") != PRODUCT_VERSION:
        raise ReleaseBuildError(
            "release builder requires product version "
            f"{PRODUCT_VERSION}"
        )


def _assert_clean_git(project_root: Path) -> str:
    status = _run(
        [
            "git",
            "-C",
            os.fspath(project_root),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        cwd=project_root,
        capture=True,
    ).stdout
    if status.strip():
        raise ReleaseBuildError("release requires a clean Git worktree")
    return _run(
        [
            "git",
            "-C",
            os.fspath(project_root),
            "rev-parse",
            "HEAD",
        ],
        cwd=project_root,
        capture=True,
    ).stdout.strip()


def _pytest_counts(path: Path) -> tuple[int, int, int, int]:
    try:
        root = ET.parse(path).getroot()
        root_name = root.tag.rsplit("}", 1)[-1]
        if root_name == "testsuite":
            suites = (root,)
        elif root_name == "testsuites":
            suites = tuple(
                child
                for child in root
                if child.tag.rsplit("}", 1)[-1] == "testsuite"
            )
            if not suites:
                raise ValueError("testsuites report has no testsuite")
        else:
            raise ValueError(f"unexpected JUnit root: {root_name}")
        suite_counts = tuple(
            (
                int(suite.attrib["tests"]),
                int(suite.attrib.get("failures", 0)),
                int(suite.attrib.get("errors", 0)),
                int(suite.attrib.get("skipped", 0)),
            )
            for suite in suites
        )
        if any(
            min(total, failures, errors, suite_skipped) < 0
            or failures + errors + suite_skipped > total
            for total, failures, errors, suite_skipped in suite_counts
        ):
            raise ValueError("negative or inconsistent pytest counts")
        tests = sum(item[0] for item in suite_counts)
        failed = sum(item[1] + item[2] for item in suite_counts)
        skipped = sum(item[3] for item in suite_counts)
        passed = tests - failed - skipped
    except (OSError, KeyError, TypeError, ValueError, ET.ParseError) as error:
        raise ReleaseBuildError("pytest machine report is invalid") from error
    return tests, passed, skipped, failed


def _vitest_counts(path: Path) -> tuple[int, int, int, int]:
    report = _read_json(path)
    try:
        tests = int(report["numTotalTests"])
        passed = int(report["numPassedTests"])
        failed = int(report["numFailedTests"])
        pending = int(report["numPendingTests"])
        todo = int(report["numTodoTests"])
        if (
            min(tests, passed, failed, pending, todo) < 0
            or passed + failed + pending + todo != tests
        ):
            raise ValueError("inconsistent Vitest counts")
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseBuildError("vitest machine report is invalid") from error
    return tests, passed, pending + todo, failed


def _validate_source_members(members: tuple[str, ...]) -> None:
    prefix = "personal-diet-pantry/"
    required = (
        f"{prefix}package.json",
        f"{prefix}scripts/cold_backup.py",
        f"{prefix}tests/",
        f"{prefix}src-tests/",
    )
    if not all(
        any(
            member == item.rstrip("/") or member.startswith(item)
            for member in members
        )
        for item in required
    ):
        raise ReleaseBuildError("source archive is missing required sources")
    _reject_sensitive_members(members)


def _validate_installable_members(members: tuple[str, ...]) -> None:
    required = (
        "package/package.json",
        "package/LICENSE",
        "package/dist/index.js",
        "package/dist/generated/tool-contracts.js",
        "package/python/personal_diet_pantry/package_semantics.py",
        "package/migrations/021_package_semantics_and_product_operations.sql",
        "package/templates/en/",
        "package/templates/zh-CN/",
        "package/skills/personal-diet-pantry/SKILL.md",
        "package/UPDATE-v0.7.4.27.zh-CN.md",
    )
    if not all(
        any(
            member == item.rstrip("/") or member.startswith(item)
            for member in members
        )
        for item in required
    ):
        raise ReleaseBuildError(
            "installable archive is missing required runtime files"
        )
    forbidden = (
        "package/src/",
        "package/tests/",
        "package/src-tests/",
        "package/contracts/",
        "package/node_modules/",
    )
    if any(member.startswith(forbidden) for member in members):
        raise ReleaseBuildError("installable archive contains source-only files")
    _reject_sensitive_members(members)


def _reject_sensitive_members(members: tuple[str, ...]) -> None:
    unsafe = re.compile(
        r"(?:^|/)(?:reports?|backups?)(?:/|$)"
        r"|(?:^|/)\.venv(?:/|$)"
        r"|\.(?:db|key|pem|sqlite|sqlite3)$"
        r"|(?:^|/)(?:\.env(?:\.|$)|[^/]*(?:credential|secret|api-key)[^/]*)",
        flags=re.IGNORECASE,
    )
    if any(unsafe.search(member) for member in members):
        raise ReleaseBuildError("archive contains runtime data or credentials")


def _summary_markdown(manifest: ReleaseManifest) -> str:
    return (
        f"# 食序管家 v{manifest.product_version} 测试摘要\n\n"
        f"- 内部包版本：`{manifest.version}`\n"
        f"- 源码提交：`{manifest.commit_sha}`\n"
        f"- Python：总计 {manifest.python_tests}；"
        f"通过 {manifest.python_passed}；"
        f"跳过 {manifest.python_skipped}；失败 {manifest.python_failed}\n"
        f"- TypeScript：总计 {manifest.typescript_tests}；"
        f"通过 {manifest.typescript_passed}；"
        f"跳过 {manifest.typescript_skipped}；"
        f"失败 {manifest.typescript_failed}\n"
        f"- 数据库迁移：{manifest.migrations}\n"
        f"- 发布审计：`{manifest.audit_status}`\n"
        f"- Python：`{manifest.python_version}`\n"
        f"- Node.js：`{manifest.node_version}`\n"
        f"- npm：`{manifest.npm_version}`\n"
        f"- 源码包 SHA-256：`{manifest.source_sha256}`\n"
        f"- 安装包 SHA-256：`{manifest.installable_sha256}`\n"
        "- 源码包与安装包均通过两次独立生成的字节级复现检查。\n"
    )


def _tar_members(path: Path) -> tuple[str, ...]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            return tuple(sorted(member.name for member in archive.getmembers()))
    except (OSError, tarfile.TarError) as error:
        raise ReleaseBuildError(f"archive is unreadable: {path.name}") from error


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseBuildError(f"unable to read {path.name}") from error
    if not isinstance(value, dict):
        raise ReleaseBuildError(f"{path.name} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_release_directory(source: Path, destination: Path) -> None:
    source_names = {path.name for path in source.iterdir()}
    if source_names != set(RELEASE_ENTRY_NAMES):
        raise ReleaseBuildError("staged release does not contain exactly six entries")
    for entry in source.rglob("*"):
        try:
            metadata = entry.lstat()
        except OSError as error:
            raise ReleaseBuildError(
                "unable to inspect staged release entry"
            ) from error
        if _is_link_or_reparse(metadata) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise ReleaseBuildError(
                "staged release entries must be regular files or directories"
            )
    _assert_no_link_or_reparse_point(destination)
    try:
        _rename_directory_no_replace(source, destination)
    except FileExistsError as error:
        raise ReleaseBuildError(
            "release root was created during verification"
        ) from error
    except OSError as error:
        raise ReleaseBuildError("unable to publish release root") from error


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically move one directory while refusing an existing destination."""

    if os.name == "nt":
        _move_directory_no_replace_windows(source, destination)
        return
    if sys.platform == "darwin":
        _move_directory_no_replace_macos(source, destination)
        return
    _move_directory_no_replace_linux(source, destination)


def _move_directory_no_replace_windows(source: Path, destination: Path) -> None:
    import ctypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileW
    move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    move_file.restype = ctypes.c_int
    if move_file(os.fspath(source), os.fspath(destination)):
        return
    error = ctypes.get_last_error()
    if error in {80, 183}:
        raise FileExistsError(error, ctypes.FormatError(error), destination)
    raise OSError(error, ctypes.FormatError(error), destination)


def _move_directory_no_replace_macos(source: Path, destination: Path) -> None:
    import ctypes
    import errno

    rename_exclusive = 0x00000004
    libc = ctypes.CDLL(None, use_errno=True)
    renamex = getattr(libc, "renamex_np", None)
    if renamex is None:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    renamex.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    renamex.restype = ctypes.c_int
    if renamex(
        os.fsencode(source),
        os.fsencode(destination),
        rename_exclusive,
    ) == 0:
        return
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), destination)


def _move_directory_no_replace_linux(source: Path, destination: Path) -> None:
    import ctypes
    import errno

    at_fdcwd = -100
    rename_noreplace = 1
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        rename_noreplace,
    ) == 0:
        return
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), destination)


def _stage_github_documents(project_root: Path, destination: Path) -> None:
    for relative_name in GITHUB_DOCUMENTS:
        source = project_root / relative_name
        if not source.is_file():
            raise ReleaseBuildError(
                f"required GitHub document is missing: {relative_name}"
            )
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _version(command: list[str], *, cwd: Path) -> str:
    executable = shutil.which(command[0]) or command[0]
    resolved = [executable, *command[1:]]
    return _run(resolved, cwd=cwd, capture=True).stdout.strip()


def _run(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError(
            f"release command failed: {Path(command[0]).name}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        manifest = build_release(
            arguments.project_root,
            arguments.release_root,
        )
    except ReleaseBuildError as error:
        print(f"Release build failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            manifest.as_dict(),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
