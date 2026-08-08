from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    assert path.is_file(), f"{path.name} must exist"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git_output(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _tree_snapshot(root: Path) -> dict[str, tuple[str, str]]:
    snapshot: dict[str, tuple[str, str]] = {}
    for path in root.rglob("*"):
        relative_name = path.relative_to(root).as_posix()
        if path.is_file():
            snapshot[relative_name] = (
                "file",
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        elif path.is_dir():
            snapshot[relative_name] = ("directory", "")
    return snapshot


@pytest.fixture
def release_project(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    project = repository / "personal-diet-pantry"
    project.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "personal-diet-pantry",
                "version": "0.9.4",
                "productVersion": "0.7.5.4",
            }
        ),
        encoding="utf-8",
    )
    for relative_path in (
        "README.md",
        "README.en.md",
        "GITHUB-WORKFLOW.zh-CN.md",
        "RELEASE.zh-CN.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
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
        "UPDATE-v0.7.4.28.zh-CN.md",
        "UPDATE-v0.7.5.0.zh-CN.md",
        "UPDATE-v0.7.5.2.zh-CN.md",
        "UPDATE-v0.7.5.3.zh-CN.md",
        "UPDATE-v0.7.5.4.zh-CN.md",
        "CONTEXT.md",
        "migrations/021_package_semantics_and_product_operations.sql",
        "migrations/022_pantry_default_provenance.sql",
        "migrations/023_goal_update_preview.sql",
        "scripts/cold_backup.py",
        "docs/ARCHITECTURE.zh-CN.md",
        "docs/DATA-MODEL.zh-CN.md",
        "docs/EXAMPLES.zh-CN.md",
        "docs/INSTALLATION.zh-CN.md",
        "docs/INSTALL.md",
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
        (
            "docs/development/"
            "0.6.1.5.1-NATURAL-LANGUAGE-TRIGGER-DESIGN.zh-CN.md"
        ),
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
        "docs/superpowers/specs/2026-08-07-v0.7.4.28-agent-installable-public-release-design.md",
        "docs/superpowers/plans/2026-08-07-v0.7.4.28-agent-installable-public-release.md",
        "docs/版本回望档案/0.7.4.28.md",
        "docs/superpowers/specs/2026-08-07-personal-diet-pantry-v0.7.5.0-skill-guidance-design.md",
        "docs/superpowers/plans/2026-08-07-personal-diet-pantry-v0.7.5.0-skill-guidance.md",
        "docs/版本回望档案/0.7.5.0.md",
        "docs/版本回望档案/0.7.5.2.md",
        "docs/版本回望档案/0.7.5.3.md",
        "docs/版本回望档案/0.7.5.4.md",
    ):
        path = project / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative_path}\n", encoding="utf-8")
    (project / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repository, "init")
    _git(repository, "config", "user.name", "Release Contract")
    _git(repository, "config", "user.email", "release@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return project, tmp_path / "release"


class FakeRunner:
    def __init__(self, build_release: ModuleType) -> None:
        self.summary = build_release.VerificationSummary(
            python_tests=84,
            python_passed=84,
            python_skipped=0,
            python_failed=0,
            typescript_tests=26,
            typescript_passed=26,
            typescript_skipped=0,
            typescript_failed=0,
            migrations=23,
            audit_status="warning",
            python_version="3.12.0",
            node_version="v24.15.0",
            npm_version="11.9.0",
        )

    def verify(self, _project_root: Path):
        return self.summary

    def create_source_archive(
        self,
        _project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"deterministic-source")
        return (
            "personal-diet-pantry/package.json",
            "personal-diet-pantry/scripts/cold_backup.py",
            "personal-diet-pantry/tests/test_contract.py",
            "personal-diet-pantry/src-tests/contract.test.ts",
        )

    def create_installable(
        self,
        _project_root: Path,
        output: Path,
    ) -> tuple[str, ...]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"deterministic-installable")
        return (
            "package/package.json",
            "package/dist/index.js",
            "package/dist/generated/tool-contracts.js",
            "package/python/personal_diet_pantry/__init__.py",
            "package/python/personal_diet_pantry/package_semantics.py",
            "package/migrations/021_package_semantics_and_product_operations.sql",
            "package/migrations/022_pantry_default_provenance.sql",
            "package/migrations/023_goal_update_preview.sql",
            "package/templates/en/daily-report.md",
            "package/templates/zh-CN/daily-report.md",
            "package/skills/personal-diet-pantry/SKILL.md",
            "package/LICENSE",
            "package/UPDATE-v0.7.5.4.zh-CN.md",
        )


def test_windows_powershell_release_verification_is_process_scoped() -> None:
    build_release = _load_script("build_release")
    script = PROJECT_ROOT / "ci" / "verify.ps1"

    windows_command = build_release._powershell_verify_command(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.EXE",
        script,
    )
    assert windows_command == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.EXE",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        os.fspath(script),
    ]

    pwsh_command = build_release._powershell_verify_command(
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        script,
    )
    assert "-ExecutionPolicy" not in pwsh_command


def test_installable_validator_requires_generated_tool_contracts() -> None:
    build_release = _load_script("build_release")
    members = (
        "package/package.json",
        "package/dist/index.js",
        "package/python/personal_diet_pantry/__init__.py",
        "package/python/personal_diet_pantry/package_semantics.py",
        "package/migrations/021_package_semantics_and_product_operations.sql",
        "package/migrations/022_pantry_default_provenance.sql",
        "package/migrations/023_goal_update_preview.sql",
        "package/templates/en/daily-report.md",
        "package/templates/zh-CN/daily-report.md",
        "package/skills/personal-diet-pantry/SKILL.md",
        "package/LICENSE",
        "package/UPDATE-v0.7.5.4.zh-CN.md",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="missing required runtime files",
    ):
        build_release._validate_installable_members(members)


def test_release_builder_refuses_dirty_source(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="clean Git worktree",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=FakeRunner(build_release),
        )


def test_release_builder_records_actual_counts_and_hashes(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    runner = FakeRunner(build_release)

    manifest = build_release.build_release(
        project,
        release_root,
        runner=runner,
    )

    assert manifest.python_tests == 84
    assert manifest.python_passed == 84
    assert manifest.python_skipped == 0
    assert manifest.python_failed == 0
    assert manifest.typescript_tests == 26
    assert manifest.typescript_passed == 26
    assert manifest.typescript_skipped == 0
    assert manifest.typescript_failed == 0
    assert manifest.source_sha256
    assert manifest.installable_sha256
    assert manifest.product_version == "0.7.5.4"
    assert manifest.version == "0.9.4"
    assert (
        release_root / "release-manifest.json"
    ).is_file()
    assert (
        release_root / "TEST-SUMMARY-v0.7.5.4.zh-CN.md"
    ).is_file()
    assert (release_root / "SHA256SUMS").is_file()
    assert not (release_root / "MANIFEST-SHA256.txt").exists()


def test_release_builder_accepts_skips_and_reports_all_test_outcomes(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    runner = FakeRunner(build_release)
    runner.summary = replace(
        runner.summary,
        python_tests=387,
        python_passed=385,
        python_skipped=2,
        python_failed=0,
        typescript_tests=41,
        typescript_passed=39,
        typescript_skipped=2,
        typescript_failed=0,
    )

    manifest = build_release.build_release(
        project,
        release_root,
        runner=runner,
    )

    assert manifest.python_tests == 387
    assert manifest.python_passed == 385
    assert manifest.python_skipped == 2
    assert manifest.python_failed == 0
    assert manifest.typescript_tests == 41
    assert manifest.typescript_passed == 39
    assert manifest.typescript_skipped == 2
    assert manifest.typescript_failed == 0
    persisted = json.loads(
        (release_root / "release-manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["python_skipped"] == 2
    assert persisted["python_failed"] == 0
    assert persisted["typescript_skipped"] == 2
    assert persisted["typescript_failed"] == 0
    summary = (
        release_root / "TEST-SUMMARY-v0.7.5.4.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "Python：总计 387；通过 385；跳过 2；失败 0" in summary
    assert "TypeScript：总计 41；通过 39；跳过 2；失败 0" in summary
    assert "385/387 通过" not in summary
    assert "39/41 通过" not in summary


@pytest.mark.parametrize(
    "summary_overrides",
    (
        {
            "python_tests": 84,
            "python_passed": 83,
            "python_skipped": 0,
            "python_failed": 1,
        },
        {
            "python_tests": 84,
            "python_passed": 83,
            "python_skipped": 0,
            "python_failed": 0,
        },
        {
            "typescript_tests": 26,
            "typescript_passed": 25,
            "typescript_skipped": 0,
            "typescript_failed": 1,
        },
        {
            "typescript_tests": 26,
            "typescript_passed": 25,
            "typescript_skipped": 0,
            "typescript_failed": 0,
        },
    ),
)
def test_release_builder_rejects_failed_or_inconsistent_test_counts(
    release_project: tuple[Path, Path],
    summary_overrides: dict[str, int],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    runner = FakeRunner(build_release)
    runner.summary = replace(runner.summary, **summary_overrides)

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="failed or inconsistent test counts",
    ):
        build_release.build_release(project, release_root, runner=runner)

    assert not release_root.exists()


def test_release_builder_reproduces_both_archives(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project

    manifest = build_release.build_release(
        project,
        release_root,
        runner=FakeRunner(build_release),
    )

    assert manifest.source_reproducible is True
    assert manifest.installable_reproducible is True


def test_release_builder_publishes_exact_top_level_contract(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project

    build_release.build_release(
        project,
        release_root,
        runner=FakeRunner(build_release),
    )

    assert {path.name for path in release_root.iterdir()} == {
        "personal-diet-pantry-0.7.5.4-source.tar.gz",
        "personal-diet-pantry-0.7.5.4-installable.tgz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.5.4.zh-CN.md",
        "SHA256SUMS",
        "GitHub文档",
    }


def test_sha256sums_parses_and_verifies_all_four_release_files(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project

    build_release.build_release(
        project,
        release_root,
        runner=FakeRunner(build_release),
    )

    parsed: dict[str, str] = {}
    for line in (release_root / "SHA256SUMS").read_text(
        encoding="utf-8"
    ).splitlines():
        digest, separator, filename = line.partition("  ")
        assert separator == "  "
        assert len(digest) == 64
        int(digest, 16)
        assert filename not in parsed
        parsed[filename] = digest

    assert set(parsed) == {
        "personal-diet-pantry-0.7.5.4-source.tar.gz",
        "personal-diet-pantry-0.7.5.4-installable.tgz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.5.4.zh-CN.md",
    }
    for filename, expected_digest in parsed.items():
        actual_digest = hashlib.sha256(
            (release_root / filename).read_bytes()
        ).hexdigest()
        assert actual_digest == expected_digest


@pytest.mark.parametrize(
    "destination_kind",
    (
        "project-root",
        "project-child",
        "worktree-root",
        "worktree-child",
    ),
)
def test_release_builder_rejects_destination_inside_source_tree_before_writes(
    release_project: tuple[Path, Path],
    destination_kind: str,
) -> None:
    build_release = _load_script("build_release")
    project, _external_release_root = release_project
    worktree = project.parent
    release_root = {
        "project-root": project,
        "project-child": project / "release-output",
        "worktree-root": worktree,
        "worktree-child": worktree / "release-output",
    }[destination_kind]
    project_before = _tree_snapshot(project)

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before destination validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="outside the Git worktree",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=VerifyMustNotRun(build_release),
        )

    if release_root not in (project, worktree):
        assert not release_root.exists()
    for artifact_name in (
        "personal-diet-pantry-0.7.5.4-source.tar.gz",
        "personal-diet-pantry-0.7.5.4-installable.tgz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.5.4.zh-CN.md",
        "SHA256SUMS",
        "MANIFEST-SHA256.txt",
        "GitHub文档",
    ):
        assert not (project / artifact_name).exists()
    assert _tree_snapshot(project) == project_before
    assert _git_output(worktree, "status", "--short") == ""


@pytest.mark.parametrize("existing_content", ("empty", "allowlisted"))
def test_release_builder_rejects_existing_external_destination_before_writes(
    release_project: tuple[Path, Path],
    existing_content: str,
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    release_root.mkdir()
    if existing_content == "allowlisted":
        manifest = release_root / "release-manifest.json"
        manifest.write_bytes(b"existing-manifest\x00bytes")
        github_docs = release_root / "GitHub文档"
        github_docs.mkdir()
        (github_docs / "existing.md").write_bytes(b"existing-doc\x00bytes")
    destination_before = _tree_snapshot(release_root)
    project_before = _tree_snapshot(project)

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before destination validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="must not already exist",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=VerifyMustNotRun(build_release),
        )

    assert _tree_snapshot(release_root) == destination_before
    assert _tree_snapshot(project) == project_before
    assert _git_output(project.parent, "status", "--short") == ""


def test_release_builder_rejects_destination_created_during_verification(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    sentinel = release_root / "sentinel-user-file.txt"
    manifest = release_root / "release-manifest.json"

    class DestinationRaceRunner(FakeRunner):
        def verify(self, project_root: Path):
            release_root.mkdir()
            sentinel.write_bytes(b"external-sentinel\x00bytes")
            manifest.write_bytes(b"external-manifest\x00bytes")
            return super().verify(project_root)

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="release root.*verification",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=DestinationRaceRunner(build_release),
        )

    assert sentinel.read_bytes() == b"external-sentinel\x00bytes"
    assert manifest.read_bytes() == b"external-manifest\x00bytes"
    assert {path.name for path in release_root.iterdir()} == {
        "sentinel-user-file.txt",
        "release-manifest.json",
    }


def test_release_builder_rejects_destination_link_created_during_verification(
    release_project: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    external = tmp_path / "external-race-target"
    external.mkdir()
    sentinel = external / "sentinel-user-file.txt"
    manifest = external / "release-manifest.json"
    sentinel.write_bytes(b"external-link-sentinel\x00bytes")
    manifest.write_bytes(b"external-link-manifest\x00bytes")

    class LinkRaceRunner(FakeRunner):
        def verify(self, project_root: Path):
            try:
                release_root.symlink_to(external, target_is_directory=True)
            except OSError as error:
                pytest.skip(f"directory symlinks are unavailable: {error}")
            return super().verify(project_root)

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="symbolic link or reparse point",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=LinkRaceRunner(build_release),
        )

    assert sentinel.read_bytes() == b"external-link-sentinel\x00bytes"
    assert manifest.read_bytes() == b"external-link-manifest\x00bytes"
    assert {path.name for path in external.iterdir()} == {
        "sentinel-user-file.txt",
        "release-manifest.json",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_release_builder_rejects_destination_junction_created_during_verification(
    release_project: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    external = tmp_path / "external-junction-race-target"
    external.mkdir()
    sentinel = external / "sentinel-user-file.txt"
    manifest = external / "release-manifest.json"
    sentinel.write_bytes(b"external-junction-sentinel\x00bytes")
    manifest.write_bytes(b"external-junction-manifest\x00bytes")

    class JunctionRaceRunner(FakeRunner):
        def verify(self, project_root: Path):
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", release_root, external],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if completed.returncode != 0:
                pytest.skip(
                    "Windows junction creation is unavailable: "
                    + completed.stderr.strip()
                )
            return super().verify(project_root)

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="symbolic link or reparse point",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=JunctionRaceRunner(build_release),
        )

    assert sentinel.read_bytes() == b"external-junction-sentinel\x00bytes"
    assert manifest.read_bytes() == b"external-junction-manifest\x00bytes"
    assert {path.name for path in external.iterdir()} == {
        "sentinel-user-file.txt",
        "release-manifest.json",
    }


def test_release_builder_builds_in_private_sibling_before_destination_claim(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    build_outputs: list[Path] = []

    class ObservedRunner(FakeRunner):
        def verify(self, project_root: Path):
            assert not release_root.exists()
            return super().verify(project_root)

        def create_source_archive(
            self,
            project_root: Path,
            output: Path,
        ) -> tuple[str, ...]:
            build_outputs.append(output)
            return super().create_source_archive(project_root, output)

        def create_installable(
            self,
            project_root: Path,
            output: Path,
        ) -> tuple[str, ...]:
            build_outputs.append(output)
            return super().create_installable(project_root, output)

    build_release.build_release(
        project,
        release_root,
        runner=ObservedRunner(build_release),
    )

    assert len(build_outputs) == 4
    assert all(release_root not in output.parents for output in build_outputs)
    assert all(release_root.parent in output.parents for output in build_outputs)


def test_release_builder_removes_staging_after_atomic_publish_failure(
    release_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    staging_pattern = f".{release_root.name}.release-build-*"
    staging_before = set(release_root.parent.glob(staging_pattern))

    def fail_atomic_publish(_source, _destination):
        raise OSError("injected atomic publish failure")

    monkeypatch.setattr(
        build_release,
        "_rename_directory_no_replace",
        fail_atomic_publish,
    )

    with pytest.raises(build_release.ReleaseBuildError, match="publish"):
        build_release.build_release(
            project,
            release_root,
            runner=FakeRunner(build_release),
        )

    assert not release_root.exists()
    assert set(release_root.parent.glob(staging_pattern)) == staging_before


def test_release_builder_preserves_foreign_file_created_at_publish(
    release_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    real_publish = build_release._rename_directory_no_replace
    foreign = release_root / "external-during-publish.txt"
    foreign_bytes = b"external-race-bytes\x00preserve"

    def create_foreign_destination_before_publish(source, destination):
        Path(destination).mkdir()
        foreign.write_bytes(foreign_bytes)
        return real_publish(source, destination)

    monkeypatch.setattr(
        build_release,
        "_rename_directory_no_replace",
        create_foreign_destination_before_publish,
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="release root.*verification",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=FakeRunner(build_release),
        )

    assert foreign.read_bytes() == foreign_bytes
    assert {path.name for path in release_root.iterdir()} == {foreign.name}


def test_release_builder_rejects_existing_destination_through_missing_parent(
    release_project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_release = _load_script("build_release")
    project, existing_release_root = release_project
    existing_release_root.mkdir()
    existing_manifest = existing_release_root / "release-manifest.json"
    existing_manifest.write_bytes(b"existing-manifest\x00bytes")
    aliased_release_root = (
        existing_release_root.parent
        / "missing-intermediate"
        / ".."
        / existing_release_root.name
    )
    destination_before = _tree_snapshot(existing_release_root)
    project_before = _tree_snapshot(project)
    original_exists = Path.exists

    def posix_style_exists(path: Path) -> bool:
        if path == aliased_release_root:
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", posix_style_exists)

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before destination validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="must not already exist",
    ):
        build_release.build_release(
            project,
            aliased_release_root,
            runner=VerifyMustNotRun(build_release),
        )

    assert _tree_snapshot(existing_release_root) == destination_before
    assert _tree_snapshot(project) == project_before
    assert _git_output(project.parent, "status", "--short") == ""


@pytest.mark.parametrize("link_kind", ("symlink", "junction"))
@pytest.mark.parametrize("link_position", ("self", "ancestor"))
def test_release_builder_rejects_link_or_reparse_point_before_writes(
    release_project: tuple[Path, Path],
    tmp_path: Path,
    link_kind: str,
    link_position: str,
) -> None:
    build_release = _load_script("build_release")
    project, _release_root = release_project
    actual_parent = tmp_path / f"actual-{link_kind}-{link_position}"
    actual_parent.mkdir()
    alias_parent = tmp_path / f"alias-{link_kind}-{link_position}"
    if link_kind == "symlink":
        try:
            alias_parent.symlink_to(actual_parent, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"directory symlinks are unavailable: {error}")
    else:
        if os.name != "nt":
            pytest.skip("Windows junction regression requires Windows")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", alias_parent, actual_parent],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            pytest.skip(
                "Windows junction creation is unavailable: "
                + completed.stderr.strip()
            )
    release_root = (
        alias_parent
        if link_position == "self"
        else alias_parent / "new-release"
    )
    destination_before = _tree_snapshot(actual_parent)
    project_before = _tree_snapshot(project)

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before reparse validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="symbolic link or reparse point",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=VerifyMustNotRun(build_release),
        )

    assert _tree_snapshot(actual_parent) == destination_before
    assert _tree_snapshot(project) == project_before
    assert _git_output(project.parent, "status", "--short") == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink regression")
def test_release_builder_rejects_posix_symlink_after_missing_parent_dotdot(
    release_project: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    project, _release_root = release_project
    actual_parent = tmp_path / "actual-posix-parent"
    actual_parent.mkdir()
    alias_parent = tmp_path / "alias-posix-parent"
    alias_parent.symlink_to(actual_parent, target_is_directory=True)
    release_root = (
        tmp_path
        / "missing-intermediate"
        / ".."
        / alias_parent.name
        / "new-release"
    )

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before symlink validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="symbolic link or reparse point",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=VerifyMustNotRun(build_release),
        )

    assert not (actual_parent / "new-release").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_release_builder_rejects_junction_after_missing_parent_dotdot(
    release_project: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_release = _load_script("build_release")
    project, _release_root = release_project
    actual_parent = tmp_path / "actual-junction-parent"
    actual_parent.mkdir()
    alias_parent = tmp_path / "alias-junction-parent"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", alias_parent, actual_parent],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        pytest.skip(
            "Windows junction creation is unavailable: "
            + completed.stderr.strip()
        )
    release_root = (
        tmp_path
        / "missing-intermediate"
        / ".."
        / alias_parent.name
        / "new-release"
    )
    original_lstat = Path.lstat

    def posix_style_lstat(path: Path):
        if "missing-intermediate" in path.parts:
            raise FileNotFoundError(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", posix_style_lstat)

    class VerifyMustNotRun(FakeRunner):
        def verify(self, _project_root: Path):
            raise AssertionError("verification ran before junction validation")

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="symbolic link or reparse point",
    ):
        build_release.build_release(
            project,
            release_root,
            runner=VerifyMustNotRun(build_release),
        )

    assert not (actual_parent / "new-release").exists()


def test_release_builder_publishes_exact_github_document_set(
    release_project: tuple[Path, Path],
) -> None:
    build_release = _load_script("build_release")
    project, release_root = release_project
    github_docs = release_root / "GitHub文档"

    build_release.build_release(
        project,
        release_root,
        runner=FakeRunner(build_release),
    )

    published = {
        path.relative_to(github_docs).as_posix()
        for path in github_docs.rglob("*")
        if path.is_file()
    }
    assert published == set(build_release.GITHUB_DOCUMENTS)
    for relative_path in published:
        assert (github_docs / relative_path).read_bytes() == (
            project / relative_path
        ).read_bytes()


def test_v075_public_release_inputs_are_complete() -> None:
    build_release = _load_script("build_release")
    assert {
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
        "UPDATE-v0.7.4.28.zh-CN.md",
        "UPDATE-v0.7.5.0.zh-CN.md",
        "UPDATE-v0.7.5.2.zh-CN.md",
        "UPDATE-v0.7.5.3.zh-CN.md",
        "UPDATE-v0.7.5.4.zh-CN.md",
        "GITHUB-WORKFLOW.zh-CN.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/INSTALL.md",
        "docs/UPGRADING.md",
        "docs/AI-PROMPTS.zh-CN.md",
        "docs/RELEASING.md",
        "docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md",
        (
            "docs/superpowers/specs/"
            "2026-08-02-personal-diet-pantry-v0.7.3-"
            "skill-completeness-design.md"
        ),
        (
            "docs/superpowers/plans/"
            "2026-08-02-personal-diet-pantry-v0.7.3.md"
        ),
        (
            "docs/superpowers/specs/"
            "2026-08-03-personal-diet-pantry-v0.7.3.1-"
            "liquid-schema-compat-design.md"
        ),
        (
            "docs/superpowers/plans/"
            "2026-08-03-personal-diet-pantry-v0.7.3.1-"
            "liquid-schema-compat.md"
        ),
        (
            "docs/superpowers/specs/"
            "2026-08-04-personal-diet-pantry-v0.7.3.2-"
            "trusted-pantry-loop-design.md"
        ),
        (
            "docs/superpowers/plans/"
            "2026-08-04-personal-diet-pantry-v0.7.3.2.md"
        ),
        (
            "docs/superpowers/specs/"
            "2026-08-07-v0.7.4.28-agent-installable-public-release-design.md"
        ),
        (
            "docs/superpowers/plans/"
            "2026-08-07-v0.7.4.28-agent-installable-public-release.md"
        ),
        "docs/版本回望档案/0.7.4.28.md",
        (
            "docs/superpowers/specs/"
            "2026-08-07-personal-diet-pantry-v0.7.5.0-skill-guidance-design.md"
        ),
        (
            "docs/superpowers/plans/"
            "2026-08-07-personal-diet-pantry-v0.7.5.0-skill-guidance.md"
        ),
        "docs/版本回望档案/0.7.5.0.md",
        "docs/版本回望档案/0.7.5.2.md",
        "docs/版本回望档案/0.7.5.3.md",
        "docs/版本回望档案/0.7.5.4.md",
        "CONTEXT.md",
        "migrations/021_package_semantics_and_product_operations.sql",
        "migrations/022_pantry_default_provenance.sql",
        "migrations/023_goal_update_preview.sql",
        "scripts/cold_backup.py",
    } <= set(build_release.GITHUB_DOCUMENTS)


def test_release_documentation_uses_the_complete_top_level_contract() -> None:
    required_names = {
        "personal-diet-pantry-0.7.5.4-source.tar.gz",
        "personal-diet-pantry-0.7.5.4-installable.tgz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.5.4.zh-CN.md",
        "SHA256SUMS",
        "GitHub文档",
    }
    for relative_path in (
        "README.md",
        "README.en.md",
        "RELEASE.zh-CN.md",
        "UPDATE-v0.7.5.4.zh-CN.md",
        "docs/INSTALLATION.zh-CN.md",
    ):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        for name in required_names:
            assert name in text, f"{relative_path} is missing {name}"
        assert "MANIFEST-SHA256.txt" not in text, relative_path


def test_github_document_sources_exclude_legacy_hash_filename() -> None:
    build_release = _load_script("build_release")
    offenders = [
        relative_path
        for relative_path in build_release.GITHUB_DOCUMENTS
        if "MANIFEST-SHA256.txt"
        in (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_task_8_builds_into_a_new_directory_outside_the_git_worktree() -> None:
    plan = (
        PROJECT_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-02-personal-diet-pantry-v0.7.3.md"
    ).read_text(encoding="utf-8")
    task_8 = plan.split("### Task 8:", maxsplit=1)[1]

    assert "git rev-parse --show-toplevel" in task_8
    assert "Split-Path -Parent $GitTopLevel" in task_8
    assert "Test-Path -LiteralPath $ReleaseRoot" in task_8
    assert "outside the Git worktree" in task_8
    assert "pdp-v0.7.3-release-task8-rerun" in task_8
    assert task_8.count("git status --short") >= 2
    assert task_8.count("if ($LASTEXITCODE -ne 0)") >= 3
    assert (
        "$ReleaseRoot = Join-Path (Split-Path -Parent (Get-Location))"
        not in task_8
    )
    for name in (
        "personal-diet-pantry-0.7.3-source.tar.gz",
        "personal-diet-pantry-0.7.3-installable.tgz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.3.zh-CN.md",
        "SHA256SUMS",
        "GitHub文档/",
    ):
        assert name in task_8


def test_task_8_stops_before_clean_status_when_release_build_fails(
    tmp_path: Path,
) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is required for the Task 8 command regression")
    plan = (
        PROJECT_ROOT
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-02-personal-diet-pantry-v0.7.3.md"
    ).read_text(encoding="utf-8")
    step_7 = plan.split(
        "- [ ] **Step 7: Build and inspect artifacts from the clean commit**",
        maxsplit=1,
    )[1].split("- [ ] **Step 8:", maxsplit=1)[0]
    command_block = step_7.split("```powershell", maxsplit=1)[1].split(
        "```",
        maxsplit=1,
    )[0]
    build_command = (
        "& $PdpPython scripts/build_release.py --project-root . "
        "--release-root $ReleaseRoot"
    )
    assert command_block.count(build_command) == 1
    status_command = "$PostBuildStatus = @(git status --short)"
    assert command_block.count(status_command) == 1
    marker = tmp_path / "clean-status-was-reached.txt"
    python_path = os.fspath(Path(sys.executable)).replace("'", "''")
    marker_path = os.fspath(marker).replace("'", "''")
    probe_script = command_block.replace(
        build_command,
        '& $PdpPython -c "import sys; sys.exit(7)"',
    ).replace(
        status_command,
        (
            f"Set-Content -LiteralPath '{marker_path}' -Value reached\n"
            + status_command
        ),
    )
    probe_repo = tmp_path / "probe-repository"
    probe_repo.mkdir()
    _git(probe_repo, "init")

    completed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            f"$PdpPython = '{python_path}'\n{probe_script}",
        ],
        cwd=probe_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode != 0
    assert not marker.exists()
    assert "release build failed with exit code 7" in (
        completed.stdout + completed.stderr
    )


def test_source_member_validation_requires_cold_backup_helper() -> None:
    build_release = _load_script("build_release")
    members = (
        "personal-diet-pantry/package.json",
        "personal-diet-pantry/tests/test_contract.py",
        "personal-diet-pantry/src-tests/contract.test.ts",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="missing required sources",
    ):
        build_release._validate_source_members(members)


@pytest.mark.parametrize(
    "member",
    (
        "personal-diet-pantry/.venv/private.pem",
        "personal-diet-pantry/python/.venv/secret.py",
        "personal-diet-pantry/config/signing.key",
    ),
)
def test_source_member_validation_rejects_venv_and_private_keys(
    member: str,
) -> None:
    build_release = _load_script("build_release")
    members = (
        "personal-diet-pantry/package.json",
        "personal-diet-pantry/scripts/cold_backup.py",
        "personal-diet-pantry/tests/test_contract.py",
        "personal-diet-pantry/src-tests/contract.test.ts",
        member,
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="runtime data or credentials",
    ):
        build_release._validate_source_members(members)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".venv/private.pem",
        "python/.venv/secret.py",
        "config/signing.key",
    ),
)
def test_source_archive_rejects_sensitive_tracked_members(
    release_project: tuple[Path, Path],
    relative_path: str,
) -> None:
    archive = _load_script("reproducible_archive")
    project, _release_root = release_project
    sensitive = project / relative_path
    sensitive.parent.mkdir(parents=True, exist_ok=True)
    sensitive.write_text("release-secret\n", encoding="utf-8")
    _git(project, "add", "--", relative_path)
    _git(project, "commit", "-m", "add unsafe tracked source")

    with pytest.raises(
        archive.ArchiveError,
        match="sensitive tracked member",
    ):
        archive.create_archive(
            project,
            project / "dist-package" / "source.tar.gz",
        )


def test_source_archive_accepts_normal_tracked_sources(
    release_project: tuple[Path, Path],
) -> None:
    archive = _load_script("reproducible_archive")
    project, _release_root = release_project

    manifest = archive.create_archive(
        project,
        project / "dist-package" / "source.tar.gz",
    )

    assert "personal-diet-pantry/package.json" in manifest.members


def test_source_archive_accepts_project_as_git_root(tmp_path: Path) -> None:
    archive = _load_script("reproducible_archive")
    project = tmp_path / "personal-diet-pantry"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"name": "personal-diet-pantry"}) + "\n",
        encoding="utf-8",
    )
    _git(project, "init")
    _git(project, "config", "user.name", "Release Contract")
    _git(project, "config", "user.email", "release@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "root fixture")

    manifest = archive.create_archive(
        project,
        project / "dist-package" / "source.tar.gz",
    )

    assert manifest.members == ("personal-diet-pantry/package.json",)


def test_source_archive_root_comes_from_committed_package_name(
    tmp_path: Path,
) -> None:
    archive = _load_script("reproducible_archive")
    project = tmp_path / "arbitrary-checkout-directory"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"name": "personal-diet-pantry"}) + "\n",
        encoding="utf-8",
    )
    _git(project, "init")
    _git(project, "config", "user.name", "Release Contract")
    _git(project, "config", "user.email", "release@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "arbitrary checkout fixture")

    manifest = archive.create_archive(
        project,
        project / "dist-package" / "source.tar.gz",
    )

    assert manifest.members == ("personal-diet-pantry/package.json",)


def test_source_archive_reroots_a_nested_project_to_its_package_name(
    tmp_path: Path,
) -> None:
    archive = _load_script("reproducible_archive")
    repository = tmp_path / "repository"
    project = repository / "0.7.3.5" / "personal-diet-pantry"
    project.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({"name": "personal-diet-pantry"}) + "\n",
        encoding="utf-8",
    )
    _git(repository, "init")
    _git(repository, "config", "user.name", "Release Contract")
    _git(repository, "config", "user.email", "release@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "nested fixture")

    manifest = archive.create_archive(
        project,
        project / "dist-package" / "source.tar.gz",
    )

    assert manifest.members == ("personal-diet-pantry/package.json",)


def _audit_payload() -> dict[str, object]:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "example-package": {
                "name": "example-package",
                "severity": "high",
                "via": [
                    {
                        "source": 123,
                        "name": "example-package",
                        "dependency": "example-package",
                        "title": "Example advisory",
                        "url": (
                            "https://github.com/advisories/"
                            "GHSA-1111-2222-3333"
                        ),
                        "severity": "high",
                    }
                ],
                "nodes": [
                    "node_modules/openclaw/node_modules/example-package"
                ],
            }
        },
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 1,
                "critical": 0,
                "total": 1,
            }
        },
    }


def _acceptance() -> dict[str, object]:
    reviewed = date.today()
    return {
        "schema_version": 1,
        "accepted": [
            {
                "advisory_id": "GHSA-1111-2222-3333",
                "package": "example-package",
                "severity": "high",
                "dependency_path": (
                    "node_modules/openclaw/node_modules/example-package"
                ),
                "isolation_basis": (
                    "OpenClaw is development-only and excluded from npm pack"
                ),
                "reviewed_on": reviewed.isoformat(),
                "review_deadline": (
                    reviewed + timedelta(days=31)
                ).isoformat(),
            }
        ],
    }


def test_dependency_validator_requires_exact_advisory_path(
    tmp_path: Path,
) -> None:
    validator = _load_script("validate_dependency_audit")
    audit_path = tmp_path / "audit.json"
    acceptance_path = tmp_path / "acceptance.json"
    audit_path.write_text(
        json.dumps(_audit_payload()),
        encoding="utf-8",
    )
    acceptance_path.write_text(
        json.dumps(_acceptance()),
        encoding="utf-8",
    )

    assert validator.validate_dependency_audit(
        audit_path,
        acceptance_path,
        today=date.today(),
    ) == ()

    invalid = _acceptance()
    invalid["accepted"][0]["dependency_path"] = (
        "node_modules/another-package"
    )
    acceptance_path.write_text(
        json.dumps(invalid),
        encoding="utf-8",
    )
    findings = validator.validate_dependency_audit(
        audit_path,
        acceptance_path,
        today=date.today(),
    )
    assert "UNACCEPTED_DEPENDENCY_ADVISORY" in {
        item.code for item in findings
    }


def test_dependency_validator_rejects_expired_acceptance(
    tmp_path: Path,
) -> None:
    validator = _load_script("validate_dependency_audit")
    audit_path = tmp_path / "audit.json"
    acceptance_path = tmp_path / "acceptance.json"
    audit_path.write_text(
        json.dumps(_audit_payload()),
        encoding="utf-8",
    )
    acceptance = _acceptance()
    acceptance["accepted"][0]["reviewed_on"] = (
        date.today() - timedelta(days=10)
    ).isoformat()
    acceptance["accepted"][0]["review_deadline"] = (
        date.today() - timedelta(days=1)
    ).isoformat()
    acceptance_path.write_text(
        json.dumps(acceptance),
        encoding="utf-8",
    )

    findings = validator.validate_dependency_audit(
        audit_path,
        acceptance_path,
        today=date.today(),
    )

    assert "EXPIRED_DEPENDENCY_ACCEPTANCE" in {
        item.code for item in findings
    }


def test_pytest_count_parser_supports_testsuites_root(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites name="pytest tests">'
            '<testsuite tests="4" failures="1" errors="1" skipped="1" />'
            '<testsuite tests="2" failures="0" errors="0" skipped="0" />'
            "</testsuites>"
        ),
        encoding="utf-8",
    )

    assert build_release._pytest_counts(report) == (6, 3, 1, 2)


def test_pytest_count_parser_accepts_skipped_tests(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        '<testsuite tests="387" failures="0" errors="0" skipped="2" />',
        encoding="utf-8",
    )

    assert build_release._pytest_counts(report) == (387, 385, 2, 0)


def test_pytest_count_parser_rejects_inconsistent_counts(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        '<testsuite tests="2" failures="1" errors="1" skipped="1" />',
        encoding="utf-8",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="pytest machine report is invalid",
    ):
        build_release._pytest_counts(report)


def test_pytest_count_parser_rejects_negative_error_cancellation(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        '<testsuite tests="1" failures="1" errors="-1" skipped="0" />',
        encoding="utf-8",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="pytest machine report is invalid",
    ):
        build_release._pytest_counts(report)


def test_vitest_count_parser_uses_pending_and_todo_as_skipped(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "vitest-results.json"
    report.write_text(
        json.dumps(
            {
                "numTotalTests": 6,
                "numPassedTests": 3,
                "numFailedTests": 1,
                "numPendingTests": 1,
                "numTodoTests": 1,
            }
        ),
        encoding="utf-8",
    )

    assert build_release._vitest_counts(report) == (6, 3, 2, 1)


def test_vitest_count_parser_rejects_inconsistent_counts(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "vitest-results.json"
    report.write_text(
        json.dumps(
            {
                "numTotalTests": 5,
                "numPassedTests": 4,
                "numFailedTests": 0,
                "numPendingTests": 0,
                "numTodoTests": 0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="vitest machine report is invalid",
    ):
        build_release._vitest_counts(report)


def test_vitest_count_parser_rejects_negative_todo_cancellation(
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    report = tmp_path / "vitest-results.json"
    report.write_text(
        json.dumps(
            {
                "numTotalTests": 1,
                "numPassedTests": 1,
                "numFailedTests": 0,
                "numPendingTests": 1,
                "numTodoTests": -1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        build_release.ReleaseBuildError,
        match="vitest machine report is invalid",
    ):
        build_release._vitest_counts(report)


def test_version_resolves_windows_command_shims(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_release = _load_script("build_release")
    observed: list[list[str]] = []

    class Completed:
        stdout = "11.9.0\n"

    def fake_run(command, **_kwargs):
        observed.append(command)
        return Completed()

    monkeypatch.setattr(
        build_release.shutil,
        "which",
        lambda name: "C:/runtime/npm.CMD" if name == "npm" else None,
    )
    monkeypatch.setattr(build_release, "_run", fake_run)

    assert build_release._version(
        ["npm", "--version"],
        cwd=tmp_path,
    ) == "11.9.0"
    assert observed == [["C:/runtime/npm.CMD", "--version"]]
