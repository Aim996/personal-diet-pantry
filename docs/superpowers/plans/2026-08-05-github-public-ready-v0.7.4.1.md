# GitHub Public-Ready v0.7.4.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the private 食序管家 repository into a public-ready, ordinary-OpenClaw-user-first project with reliable documentation, MIT licensing, deterministic release assets, guarded GitHub CI/Release automation, and no production-data exposure.

**Architecture:** Keep runtime behavior and migrations unchanged. Build a public repository surface around the existing release builder: concise user entry documents point to the existing detailed operational manual, repository contract tests prevent version/document/workflow drift, ordinary CI has read-only permissions, and only a validated `v0.7.4.1` tag on `main` may enter the final Release job. The implementation stays on a new version branch and produces a local immutable candidate archive, a pushed branch, and a Draft PR—but no merge, tag, or GitHub Release.

**Tech Stack:** Markdown, JSON/TOML metadata, Python 3.11+, pytest, TypeScript 5.9, Vitest, Node.js 22.22.3+, npm pack, PowerShell, Bash, Docker, GitHub Actions, OpenClaw.

## Global Constraints

- Read `docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md` completely before every implementation batch; it was read before this plan and remains authoritative.
- Product version is exactly `0.7.4.1`; npm/OpenClaw/Python technical package version is exactly `0.8.1`.
- Previous product version is `0.7.4.0`; previous technical package version is `0.8.0`; both must remain reproducible and unmodified.
- Work only on `codex/github-public-ready-v0.7.4.1`, based on commit `73496bf180eea44304b2b6d84b4021606780be3c` plus the approved design commits.
- Keep the GitHub repository Private, keep `main` unchanged, and do not create or push a Git tag or GitHub Release.
- Keep `package.json` `"private": true`; add MIT metadata without enabling npm publication.
- Do not change seven public tools, daily actions, protected receipt formatting, business logic, database schema, or migrations 001–021.
- Do not access, copy, alter, migrate, or delete a production `dataDir` or real diet, pantry, weight, preference, credential, backup, export, report, or log data.
- All tests and install checks use temporary isolated data directories.
- The final local candidate directory is exactly `C:\path\to\personal-diet-pantry\0.7.4.1`; it must not exist before publication and must never overwrite another directory.
- Final GitHub delivery is a new Draft PR based on `codex/personal-diet-pantry-v0.7.4.0`; it must explicitly say Private, not merged, not tagged, not released, no migration, and no user-data modification.

---

## File Map

### Public repository surface

- `README.md`: ordinary-user-first Chinese landing page and protected receipt example.
- `README.en.md`: accurate English entry, install/build boundary, and current-version facts.
- `LICENSE`: MIT text for `Aim996`.
- `CHANGELOG.md`: product-version history and migration/security statements.
- `SECURITY.md`: private vulnerability reporting and sensitive-data rules.
- `CONTRIBUTING.md`: contributor workflow, invariant/version gates, and safe test rules.
- `docs/INSTALL.md`: concise GitHub Release installation entry.
- `docs/UPGRADING.md`: concise backup/update/health/rollback entry.
- `docs/AI-PROMPTS.zh-CN.md`: three copy-ready prompts with no placeholders.
- `docs/RELEASING.md`: maintainer tag and Release checklist.
- `RELEASE.zh-CN.md`: validated v0.7.4.1 Release Notes source.
- `UPDATE-v0.7.4.1.zh-CN.md`: v0.7.4.1 user-visible change and compatibility record.

### Version and packaging

- `package.json`, `package-lock.json`, `openclaw.plugin.json`, `pyproject.toml`: 0.8.1 metadata and MIT declarations.
- `python/personal_diet_pantry/__init__.py`: technical and product versions.
- `python/personal_diet_pantry/data_import.py`: accept v0.7.4.1 portability bundles without removing older versions.
- `scripts/build_release.py`: v0.7.4.1 asset names and public document allowlist.
- `ci/verify.ps1`: current-version gate labels.
- `docker/first-user/Dockerfile`, `docker/first-user/verify.sh`, `.dockerignore`, `.github/workflows/docker-first-user.yml`: 0.8.1 package and 0.7.4.1 product expectations.

### GitHub automation and community files

- `.github/workflows/ci.yml`: read-only PR/main validation.
- `.github/workflows/release.yml`: tag-validated build and conditional Release publication.
- `scripts/check_release_ref.py`: deterministic event/ref/product-version validation for the workflow.
- `.github/ISSUE_TEMPLATE/bug_report.yml`: bounded bug report without sensitive data.
- `.github/ISSUE_TEMPLATE/feature_request.yml`: user-value-first feature request.
- `.github/pull_request_template.md`: invariant, version, test, migration, and data-safety checklist.

### Tests

- `tests/test_version_contract.py`, `src-tests/version-contract.test.ts`: dual-version and previous-version progression.
- `tests/test_public_repository_surface.py`: docs, MIT, README order, prompts, links, and community contracts.
- `tests/test_release_ref.py`: tag/manual-dispatch fail-closed logic.
- `tests/test_github_workflows.py`: CI/Release triggers, permissions, gates, assets, and dry-run boundary.
- `tests/test_build_release.py`, `tests/integration/test_installable_e2e.py`, `src-tests/package-contents.test.ts`: release document set and installable contents.

---

### Task 1: Establish the v0.7.4.1 / 0.8.1 Version Contract

**Files:**
- Modify: `tests/test_version_contract.py:21-104`
- Modify: `src-tests/version-contract.test.ts:7-15`
- Modify: `package.json:1-24`
- Modify: `package-lock.json:1-15`
- Modify: `openclaw.plugin.json:1-8`
- Modify: `pyproject.toml:1-14`
- Modify: `python/personal_diet_pantry/__init__.py:1-5`
- Modify: `python/personal_diet_pantry/data_import.py:35-55`
- Modify: `scripts/build_release.py:1-31`
- Modify: `ci/verify.ps1:43-50`
- Modify: `.dockerignore:1-9`
- Modify: `.github/workflows/docker-first-user.yml:45-66`
- Modify: `docker/first-user/Dockerfile`
- Modify: `docker/first-user/verify.sh`

**Interfaces:**
- Consumes: current dual-version contract (`productVersion` plus SemVer package version).
- Produces: canonical constants `PRODUCT_VERSION = "0.7.4.1"` and `VERSION = "0.8.1"` for later documentation, build, and workflow tasks.

- [ ] **Step 1: Change the version tests first**

Use these exact expectations in `tests/test_version_contract.py`:

```python
EXPECTED = "0.8.1"
PRODUCT_VERSION = "0.7.4.1"
PREVIOUS_EXPECTED = "0.8.0"
PREVIOUS_PRODUCT_VERSION = "0.7.4.0"
```

Rename `test_all_version_sources_use_the_dual_0736_contract` to
`test_all_version_sources_use_the_dual_0741_contract`, expect the release heading
`# 食序管家（Personal Diet Pantry）v0.7.4.1`, and require
`UPDATE-v0.7.4.1.zh-CN.md`.

Use these exact TypeScript assertions:

```ts
expect(pkg.version).toBe("0.8.1");
expect(pkg.productVersion).toBe("0.7.4.1");
expect(plugin.version).toBe("0.8.1");
```

- [ ] **Step 2: Run the changed tests and observe the expected failure**

Run:

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_version_contract.py::test_every_material_change_requires_a_new_immutable_version -q
node node_modules/vitest/vitest.mjs run src-tests/version-contract.test.ts
```

Expected: both fail because repository metadata still says product `0.7.4.0` / package `0.8.0`.

- [ ] **Step 3: Update every machine-readable version source**

Set package/plugin/Python/TOML versions to `0.8.1`, product versions to `0.7.4.1`, and build constants to:

```python
PRODUCT_VERSION = "0.7.4.1"
VERSION = "0.8.1"
```

Append `"0.7.4.1"` to the accepted export product versions without deleting any existing entry. Change Docker/npm-pack expectations from `personal-diet-pantry-0.8.0.tgz` to `personal-diet-pantry-0.8.1.tgz`, and Docker verification from product `0.7.4.0` to `0.7.4.1`. Update the existing Docker onboarding checkout step from `actions/checkout@v6` to the current official major `actions/checkout@v7`; keep `setup-node@v6`, `setup-python@v6`, and `upload-artifact@v7`.

After editing `package.json`, refresh only lock metadata without scripts:

```powershell
npm install --package-lock-only --ignore-scripts
```

- [ ] **Step 4: Run the focused version checks**

Run:

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_version_contract.py::test_every_material_change_requires_a_new_immutable_version -q
node node_modules/vitest/vitest.mjs run src-tests/version-contract.test.ts
```

Expected: PASS. The broader version test may still fail until current release documents are created in Task 2.

- [ ] **Step 5: Commit the version foundation**

```powershell
git add package.json package-lock.json openclaw.plugin.json pyproject.toml python/personal_diet_pantry/__init__.py python/personal_diet_pantry/data_import.py scripts/build_release.py ci/verify.ps1 .dockerignore .github/workflows/docker-first-user.yml docker/first-user/Dockerfile docker/first-user/verify.sh tests/test_version_contract.py src-tests/version-contract.test.ts
git commit -m "chore: start public-ready v0.7.4.1"
```

### Task 2: Build the User-First Repository Surface and Legal Baseline

**Files:**
- Create: `LICENSE`
- Create: `CHANGELOG.md`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `UPDATE-v0.7.4.1.zh-CN.md`
- Create: `tests/test_public_repository_surface.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `RELEASE.zh-CN.md`
- Modify: `package.json`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: product `0.7.4.1`, technical `0.8.1`, existing detailed manuals, and the protected receipt example.
- Produces: public-facing facts and legal metadata consumed by release packaging and later workflow tests.

- [ ] **Step 1: Write repository-surface tests**

Create `tests/test_public_repository_surface.py` with these contracts:

```python
from pathlib import Path
import json
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_repository_files_and_mit_metadata_exist() -> None:
    for name in ("LICENSE", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert (ROOT / name).is_file()
    assert "MIT License" in text("LICENSE")
    assert "Copyright (c) 2026 Aim996" in text("LICENSE")
    package = json.loads(text("package.json"))
    project = tomllib.loads(text("pyproject.toml"))
    assert package["private"] is True
    assert package["license"] == "MIT"
    assert project["project"]["license"] == "MIT"


def test_readme_is_user_first_and_keeps_the_protected_receipt() -> None:
    readme = text("README.md")
    headings = [
        "## 当前版本与状态",
        "## 它适合谁",
        "## 核心能力",
        "## 实际回执示例",
        "## 最快开始",
        "## 系统要求",
        "## 数据安全、更新与回滚",
        "## 常见问题",
        "## 文档导航",
        "## 开发者入口",
        "## 许可证",
    ]
    positions = [readme.index(item) for item in headings]
    assert positions == sorted(positions)
    assert "Personal Diet Pantry v0.7.4.1" in readme
    assert "技术包版本 `0.8.1`" in readme
    assert "发布准备中" in readme
    assert "GitHub Release 尚未创建" in readme
    assert "🔥 热量 █████░░░░░ 50%" in readme
    assert "🔥1000 / 2000 kcal +120kcal +6%" in readme
    assert "本仓库当前没有 `LICENSE`" not in readme


def test_current_release_documents_state_no_migration_or_remote_release() -> None:
    update = text("UPDATE-v0.7.4.1.zh-CN.md")
    release = text("RELEASE.zh-CN.md")
    for phrase in (
        "没有新增 migration",
        "0.7.4.0",
        "不会修改用户数据",
        "尚未创建 Git Tag 或 GitHub Release",
    ):
        assert phrase in update
    assert release.startswith("# 食序管家（Personal Diet Pantry）v0.7.4.1\n")
    assert "personal-diet-pantry-0.7.4.1-installable.tgz" in release


def test_changelog_uses_product_versions_and_migration_labels() -> None:
    changelog = text("CHANGELOG.md")
    assert "## [0.7.4.1]" in changelog
    assert "### Changed" in changelog
    assert "### Security" in changelog
    assert "Migration: none" in changelog
```

- [ ] **Step 2: Run the new test and observe missing-file failures**

Run:

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_public_repository_surface.py -q
```

Expected: FAIL because LICENSE and the public entry documents do not exist yet.

- [ ] **Step 3: Add MIT and repository governance documents**

Use the unmodified standard MIT body with:

```text
MIT License

Copyright (c) 2026 Aim996
```

`SECURITY.md` must say that sensitive vulnerabilities and personal data must never be posted in a public Issue, that the newest released version is the only supported security line, and that GitHub private vulnerability reporting is the preferred channel when available.

`CONTRIBUTING.md` must require: read the invariant file, use a new dual version for material changes, use isolated data directories, add tests before implementation, run both Python and TypeScript gates, declare migrations explicitly, and never commit real data or credentials.

Add `"license": "MIT"` to `package.json` while retaining `"private": true`, and add:

```toml
license = "MIT"
```

under `[project]` in `pyproject.toml`.

- [ ] **Step 4: Restructure README and current release documents**

Rewrite `README.md` to the exact heading order asserted above. Keep the existing protected six-metric two-line example byte-for-byte for its metric lines. Limit the capability list to these themes: meal/nutrition, hydration/weight, pantry/expiry, cooking/leftovers, natural time and estimates, correction/undo, reporting, and local-first safety.

The “fastest start” section must not offer a live Release link; it must say the repository is still preparing the v0.7.4.1 assets and point to `docs/INSTALL.md`. The current-state block must distinguish product `0.7.4.1` from technical package `0.8.1`.

Update `README.en.md` facts without claiming a published Release. Write `UPDATE-v0.7.4.1.zh-CN.md` and replace `RELEASE.zh-CN.md` with v0.7.4.1 content that names exact assets, no migration, rollback to v0.7.4.0, and the no-remote-release status.

Move concise version history into `CHANGELOG.md`; retain historical `UPDATE-*.zh-CN.md` files unchanged and link them instead of copying all detail onto the homepage.

- [ ] **Step 5: Run public-surface and full version-contract tests**

Run:

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_public_repository_surface.py tests/test_version_contract.py -q
```

Expected: PASS. If a legacy test still names 0.7.4.0 as the current release, update only that current-version assertion; do not edit old update documents.

- [ ] **Step 6: Commit the public repository surface**

```powershell
git add LICENSE CHANGELOG.md SECURITY.md CONTRIBUTING.md README.md README.en.md RELEASE.zh-CN.md UPDATE-v0.7.4.1.zh-CN.md package.json pyproject.toml tests/test_public_repository_surface.py tests/test_version_contract.py
git commit -m "docs: add public-ready user entry"
```

### Task 3: Add Install, Upgrade, AI Prompt, and Maintainer Release Entrypoints

**Files:**
- Create: `docs/INSTALL.md`
- Create: `docs/UPGRADING.md`
- Create: `docs/AI-PROMPTS.zh-CN.md`
- Create: `docs/RELEASING.md`
- Modify: `docs/INSTALLATION.zh-CN.md`
- Modify: `docs/TROUBLESHOOTING.zh-CN.md`
- Modify: `tests/test_public_repository_surface.py`

**Interfaces:**
- Consumes: current release asset names and the existing detailed cold-backup/rollback manual.
- Produces: short user routes and copy-ready AI operations that README and Release Notes can link to.

- [ ] **Step 1: Add failing tests for the four entry documents**

Append:

```python
def test_install_upgrade_and_release_entries_are_exact() -> None:
    install = text("docs/INSTALL.md")
    upgrade = text("docs/UPGRADING.md")
    releasing = text("docs/RELEASING.md")
    assert "personal-diet-pantry-0.7.4.1-installable.tgz" in install
    assert "SHA256SUMS" in install
    assert "openclaw plugins install npm-pack:" in install
    assert "dataDir" in install
    assert "七类工具" in install
    assert "升级前冷备份" in upgrade
    assert "0.7.4.0" in upgrade
    assert "记录数量" in upgrade
    assert "git ls-remote --tags origin refs/tags/v0.7.4.1" in releasing
    assert "不得覆盖" in releasing
    assert "GitHub Release" in releasing


def test_ai_prompts_are_complete_and_have_no_placeholders() -> None:
    prompts = text("docs/AI-PROMPTS.zh-CN.md")
    for heading in ("## A. 全新安装提示词", "## B. 安全更新提示词", "## C. 安装验收提示词"):
        assert heading in prompts
    assert prompts.count("```text") == 3
    assert "Aim996/personal-diet-pantry" in prompts
    assert "personal-diet-pantry-0.7.4.1-installable.tgz" in prompts
    for forbidden in ("<项目", "<版本", "TBD", "TODO", "EXAMPLE_SECRET_MARKER", "192.0.2.1"):
        assert forbidden not in prompts
```

- [ ] **Step 2: Run the focused test and observe the expected failure**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_public_repository_surface.py -q
```

Expected: FAIL because the four new documents do not exist.

- [ ] **Step 3: Write concise install and upgrade entry documents**

`docs/INSTALL.md` must contain: supported OpenClaw/Node/Python versions, fixed Release asset selection, SHA256 verification for PowerShell and Linux, external `dataDir`, npm-pack install command, enable/restart guidance, seven-tool registration check, initialize/self_check distinction, and a read-only acceptance checklist.

`docs/UPGRADING.md` must contain: current version and data directory discovery, stop instance, timestamped cold backup via the existing helper, backup verification, before counts, fixed asset/hash verification, package-only replacement, no-migration statement, restart, seven-tool and read-only checks, count comparison, and rollback to `0.7.4.0` using the same schema plus the cold backup for damage recovery.

Both documents link to `docs/INSTALLATION.zh-CN.md` for exact cold-backup commands. They must not duplicate or invent a second SQLite procedure.

- [ ] **Step 4: Write all three project-specific AI prompts**

Each fenced prompt must identify repository `Aim996/personal-diet-pantry`, product version `0.7.4.1`, asset name, SHA256SUMS, OpenClaw npm-pack installation, external `dataDir`, real application-layer validation, zero real-data mutations during acceptance, and an explicit final report. The safe-update prompt additionally requires before/after counts, migration result, backup path, and rollback. No prompt may contain a real host, username, password, token, or placeholder.

- [ ] **Step 5: Write the maintainer Release manual**

`docs/RELEASING.md` must specify this exact sequence:

```text
1. Confirm main is clean and CI is green.
2. Confirm productVersion 0.7.4.1 and package version 0.8.1.
3. Run `git ls-remote --tags origin refs/tags/v0.7.4.1`; stop if output is non-empty.
4. Build and verify the local immutable candidate.
5. Create the annotated tag only after approval.
6. Push only that tag.
7. Wait for the Release workflow.
8. Verify the Release page and all five assets can be downloaded and hashed.
9. Report any failed or unverified step instead of claiming success.
```

Also state that this implementation does not execute steps 5–8.

- [ ] **Step 6: Synchronize the detailed manuals and rerun tests**

Update only current-version and asset references in `docs/INSTALLATION.zh-CN.md` and `docs/TROUBLESHOOTING.zh-CN.md`. Preserve existing cold backup and rollback semantics.

Run:

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_public_repository_surface.py tests/test_version_contract.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit user operations documentation**

```powershell
git add docs/INSTALL.md docs/UPGRADING.md docs/AI-PROMPTS.zh-CN.md docs/RELEASING.md docs/INSTALLATION.zh-CN.md docs/TROUBLESHOOTING.zh-CN.md tests/test_public_repository_surface.py tests/test_version_contract.py
git commit -m "docs: add safe install and release paths"
```

### Task 4: Extend the Release Builder and Installable Contract

**Files:**
- Modify: `scripts/build_release.py:24-81`
- Modify: `package.json:8-25`
- Modify: `tests/test_build_release.py:68-125,946-1047`
- Modify: `tests/integration/test_installable_e2e.py:33-145`
- Modify: `src-tests/package-contents.test.ts:39-91`

**Interfaces:**
- Consumes: public document files from Tasks 2–3 and canonical versions from Task 1.
- Produces: a release directory with exactly five uploadable files plus the local `GitHub文档/` audit tree; the GitHub workflow consumes the five files.

- [ ] **Step 1: Update release-builder tests before the allowlist**

Change fixture metadata to package `0.8.1` / product `0.7.4.1`. Add the new files to the fixture and expected GitHub document set:

```python
"LICENSE",
"CHANGELOG.md",
"SECURITY.md",
"CONTRIBUTING.md",
"UPDATE-v0.7.4.1.zh-CN.md",
"docs/INSTALL.md",
"docs/UPGRADING.md",
"docs/AI-PROMPTS.zh-CN.md",
"docs/RELEASING.md",
```

Change required asset names to:

```python
{
    "personal-diet-pantry-0.7.4.1-source.tar.gz",
    "personal-diet-pantry-0.7.4.1-installable.tgz",
    "release-manifest.json",
    "TEST-SUMMARY-v0.7.4.1.zh-CN.md",
    "SHA256SUMS",
    "GitHub文档",
}
```

Update installable tests to require `LICENSE` and `UPDATE-v0.7.4.1.zh-CN.md` while retaining all runtime and exclusion assertions.

- [ ] **Step 2: Run focused package tests and observe expected failures**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_build_release.py::test_release_builder_publishes_exact_github_document_set tests/test_build_release.py::test_release_documentation_uses_the_complete_top_level_contract tests/integration/test_installable_e2e.py -q
node node_modules/vitest/vitest.mjs run src-tests/package-contents.test.ts
```

Expected: FAIL because the release allowlist and installable file list still use v0.7.4.0.

- [ ] **Step 3: Update build and npm package allowlists**

Add the exact public documents above to `GITHUB_DOCUMENTS`. Keep historical update documents and the invariant file. Update `package.json.files` to ship `LICENSE` and `UPDATE-v0.7.4.1.zh-CN.md`; remove only the old current-version `UPDATE-v0.7.4.0.zh-CN.md` from the installable allowlist, not from Git or GitHub documentation.

Do not add tests, source, `.github`, databases, reports, or development documents to the installable npm package.

- [ ] **Step 4: Run release-builder and installable tests**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_build_release.py tests/integration/test_installable_e2e.py -q
node node_modules/vitest/vitest.mjs run src-tests/package-contents.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit packaging changes**

```powershell
git add scripts/build_release.py package.json tests/test_build_release.py tests/integration/test_installable_e2e.py src-tests/package-contents.test.ts
git commit -m "build: package public-ready release assets"
```

### Task 5: Add Fail-Closed GitHub CI, Release Automation, and Community Templates

**Files:**
- Create: `scripts/check_release_ref.py`
- Create: `tests/test_release_ref.py`
- Create: `tests/test_github_workflows.py`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/pull_request_template.md`

**Interfaces:**
- Consumes: `package.json.productVersion`, `scripts/build_release.py`, `RELEASE.zh-CN.md`, and five release asset names.
- Produces: `validate_release_context(project_root: Path, event_name: str, ref_name: str) -> str`, returning the validated product version or raising `ReleaseRefError`; CI and Release workflow contracts.

- [ ] **Step 1: Write release-ref unit tests**

Create `tests/test_release_ref.py`:

```python
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "check_release_ref.py"
    spec = importlib.util.spec_from_file_location("check_release_ref", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps({"productVersion": "0.7.4.1"}), encoding="utf-8"
    )
    return tmp_path


def test_tag_push_must_exactly_match_product_version(tmp_path: Path) -> None:
    module = load_module()
    assert module.validate_release_context(project(tmp_path), "push", "v0.7.4.1") == "0.7.4.1"
    with pytest.raises(module.ReleaseRefError, match="v0.7.4.1"):
        module.validate_release_context(project(tmp_path), "push", "v0.7.4.0")


def test_manual_dispatch_is_dry_run_without_tag_requirement(tmp_path: Path) -> None:
    module = load_module()
    assert module.validate_release_context(project(tmp_path), "workflow_dispatch", "main") == "0.7.4.1"


def test_unknown_event_fails_closed(tmp_path: Path) -> None:
    module = load_module()
    with pytest.raises(module.ReleaseRefError, match="unsupported"):
        module.validate_release_context(project(tmp_path), "pull_request", "v0.7.4.1")
```

- [ ] **Step 2: Run the ref tests and observe the missing-module failure**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_release_ref.py -q
```

Expected: FAIL because `scripts/check_release_ref.py` does not exist.

- [ ] **Step 3: Implement deterministic ref validation**

Implement:

```python
class ReleaseRefError(RuntimeError):
    pass


def validate_release_context(
    project_root: Path,
    event_name: str,
    ref_name: str,
) -> str:
    package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    product_version = package["productVersion"]
    if event_name == "workflow_dispatch":
        return product_version
    if event_name != "push":
        raise ReleaseRefError(f"unsupported release event: {event_name}")
    expected = f"v{product_version}"
    if ref_name != expected:
        raise ReleaseRefError(f"release ref must be {expected}, got {ref_name}")
    return product_version
```

The CLI accepts `--project-root`, `--event-name`, and `--ref-name`, prints only the validated product version on success, writes a concise error to stderr, and exits 1 on failure.

- [ ] **Step 4: Write workflow-contract tests**

Create `tests/test_github_workflows.py` that reads the two YAML files as UTF-8 text and asserts:

```python
def test_ci_is_read_only_and_runs_all_source_gates() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for phrase in ("pull_request:", "branches: [main]", "workflow_dispatch:", "contents: read", "scan_sensitive_content.py", "npm run build", "vitest.mjs", "python -m pytest", "validate_skill.py", "release_audit.py", "npm pack --dry-run"):
        assert phrase in text
    assert "contents: write" not in text
    assert "gh release create" not in text


def test_release_workflow_separates_build_from_publish() -> None:
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    for phrase in ("tags: [\"v*\"]", "workflow_dispatch:", "check_release_ref.py", "scripts/build_release.py", "actions/upload-artifact@v7", "actions/download-artifact@v8", "contents: write", "gh release view", "gh release create", "--verify-tag"):
        assert phrase in text
    for asset in ("personal-diet-pantry-0.7.4.1-installable.tgz", "personal-diet-pantry-0.7.4.1-source.tar.gz", "release-manifest.json", "TEST-SUMMARY-v0.7.4.1.zh-CN.md", "SHA256SUMS"):
        assert asset in text
    assert "github.event_name == 'push'" in text
    assert "refs/tags/" in text
```

- [ ] **Step 5: Implement CI and Release workflows**

`ci.yml` triggers on PRs to main, pushes to main, and manual dispatch. It uses top-level `permissions: contents: read`, concurrency cancellation, `actions/checkout@v7`, `actions/setup-node@v6` with Node 24, `actions/setup-python@v6` with Python 3.12, `npm ci`, test requirements, sensitive scan, build, Vitest, pytest, Skill validation, release audit, and npm pack dry-run.

`release.yml` has two jobs:

1. `verify-build` with `contents: read`, `actions/checkout@v7` and `fetch-depth: 0`, deterministic tag/manual validation, dependency install, and `scripts/build_release.py` writing to a new `$RUNNER_TEMP` directory. It uploads the entire verified directory with `actions/upload-artifact@v7` as a short-lived artifact.
2. `publish` with `contents: write`, conditional on a pushed tag, and dependent on `verify-build`. It checks out the tag with `actions/checkout@v7` and `fetch-depth: 0`, downloads the verified artifact with `actions/download-artifact@v8`, confirms the tagged commit is an ancestor of `origin/main`, fails if `gh release view "$GITHUB_REF_NAME"` succeeds, and only then runs one `gh release create ... --verify-tag --notes-file <download-root>/GitHub文档/RELEASE.zh-CN.md` command with the five exact assets from the downloaded artifact.

Manual dispatch must run `verify-build` but skip `publish` entirely.

- [ ] **Step 6: Add minimal community templates**

The bug form requests product version, package version, OpenClaw/Node/Python/OS, reproduction, expected/actual behavior, and confirms no database, token, address, or personal diet data is attached. The feature form asks for user scenario, value, current workaround, and interaction with protected behavior. The PR template checks invariants read, dual version iteration, tests, migration declaration, data safety, docs, and rollback.

- [ ] **Step 7: Run automation tests and sensitive scan**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest tests/test_release_ref.py tests/test_github_workflows.py tests/test_sensitive_content_scan.py -q
.\.venv-ci\Scripts\python.exe scripts/scan_sensitive_content.py .
```

Expected: all tests PASS and the scan reports zero findings.

- [ ] **Step 8: Commit GitHub automation**

```powershell
git add scripts/check_release_ref.py tests/test_release_ref.py tests/test_github_workflows.py .github/workflows/ci.yml .github/workflows/release.yml .github/ISSUE_TEMPLATE/bug_report.yml .github/ISSUE_TEMPLATE/feature_request.yml .github/pull_request_template.md
git commit -m "ci: add guarded GitHub release workflow"
```

### Task 6: Run Full Verification and Build the Immutable Local Candidate

**Files:**
- Verify only: all tracked project files
- Create outside Git: `C:\path\to\personal-diet-pantry\0.7.4.1\`

**Interfaces:**
- Consumes: clean committed v0.7.4.1 source and all release gates.
- Produces: verified local assets and SHA-256 evidence for the Draft PR; no remote Release object.

- [ ] **Step 1: Confirm the branch and worktree are clean**

```powershell
git status --short --branch
git branch --show-current
```

Expected: branch is `codex/github-public-ready-v0.7.4.1` and no uncommitted files remain.

- [ ] **Step 2: Run TypeScript build and all Vitest tests**

```powershell
npm run build
node node_modules/vitest/vitest.mjs run
```

Expected: build exits 0 and all TypeScript tests pass.

- [ ] **Step 3: Run all Python tests**

```powershell
.\.venv-ci\Scripts\python.exe -m pytest -q
```

Expected: all tests pass; recorded skips are allowed only if the failure count is zero.

- [ ] **Step 4: Run release and security gates**

```powershell
.\.venv-ci\Scripts\python.exe scripts/scan_sensitive_content.py .
.\.venv-ci\Scripts\python.exe scripts/validate_skill.py
.\.venv-ci\Scripts\python.exe scripts/release_audit.py .
npm pack --dry-run --json
```

Expected: zero sensitive findings, Skill validation passes, release audit has no errors, and npm pack contains only the runtime allowlist.

- [ ] **Step 5: Prove the destination is new and build once into E:**

```powershell
$ReleaseRoot = 'C:\path\to\personal-diet-pantry\0.7.4.1'
if (Test-Path -LiteralPath $ReleaseRoot) { throw "Refusing to overwrite $ReleaseRoot" }
.\.venv-ci\Scripts\python.exe scripts/build_release.py --project-root . --release-root $ReleaseRoot
```

Expected: the builder creates the directory atomically, runs its full verification, and emits the manifest JSON. It must not alter the Git worktree.

- [ ] **Step 6: Independently verify the candidate**

```powershell
$ReleaseRoot = 'C:\path\to\personal-diet-pantry\0.7.4.1'
Get-Content -LiteralPath (Join-Path $ReleaseRoot 'SHA256SUMS') -Encoding UTF8
Get-ChildItem -LiteralPath $ReleaseRoot -Force | Select-Object Name,Length
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ReleaseRoot 'personal-diet-pantry-0.7.4.1-installable.tgz')
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ReleaseRoot 'personal-diet-pantry-0.7.4.1-source.tar.gz')
git status --short
```

Expected: the directory has exactly the two archives, manifest, test summary, SHA256SUMS, and GitHub文档; independent hashes match SHA256SUMS; Git is clean.

- [ ] **Step 7: Record exact verification evidence in the Draft PR body**

Capture actual—not predicted—Python/Vitest totals, build result, audit result, scan result, installable SHA-256, source SHA-256, candidate directory, and the explicit statements: no migration, no production data touched, repository remains Private, no tag or Release created.

### Task 7: Push the Branch and Open a Stacked Draft PR

**Files:**
- No new source files.
- Remote objects: branch `codex/github-public-ready-v0.7.4.1` and one Draft PR.

**Interfaces:**
- Consumes: clean verified branch and exact evidence from Task 6.
- Produces: reviewable GitHub state without modifying `main`, visibility, tags, or releases.

- [ ] **Step 1: Push the exact current branch**

```powershell
git push -u origin codex/github-public-ready-v0.7.4.1
```

Expected: remote branch is created and upstream tracking is configured.

- [ ] **Step 2: Verify local and remote commit identity**

```powershell
git rev-parse HEAD
git rev-parse @{upstream}
git ls-remote origin refs/heads/codex/github-public-ready-v0.7.4.1
```

Expected: all three SHAs are identical.

- [ ] **Step 3: Create a Draft PR through the GitHub connector**

Use:

```text
Repository: Aim996/personal-diet-pantry
Base: codex/personal-diet-pantry-v0.7.4.0
Head: codex/github-public-ready-v0.7.4.1
Title: docs: prepare GitHub public-ready v0.7.4.1
Draft: true
```

The body contains Summary, Ordinary-user experience, Release safety, Version/compatibility, Verification, Data safety, and Current boundaries. It explicitly explains that the PR is stacked because `main` does not yet contain v0.7.4.0; after PR #1 is merged, this PR must be rebased or retargeted before merge.

- [ ] **Step 4: Verify remote state through GitHub**

Confirm the PR is open, Draft, unmerged, head/base are exact, repository visibility is Private, `main` still points to its pre-task commit, and no v0.7.4.1 Git tag or GitHub Release exists.

- [ ] **Step 5: Write the local GitHub upload trace**

Create a new record under `C:\path\to\personal-diet-pantry` named
`GitHub上传留痕-食序管家-v0.7.4.1-2026-08-05.md`. Record repository, branch, commit SHA, Draft PR URL, versions, actual tests, asset hashes, exclusions, current Private/unmerged/untagged/unreleased state, and next review steps. Do not edit or overwrite the v0.7.4.0 trace.

---

## Final Reporting Contract

The final response must report:

1. GitHub repository URL, visibility, branch, commit SHA, and Draft PR URL.
2. Product `0.7.4.1`, technical package `0.8.1`, and absence of a Tag/Release.
3. Local candidate directory and exact installable/source SHA-256 values.
4. Actual Python, TypeScript, build, release audit, sensitive scan, package and isolation results.
5. New and modified file groups.
6. Database migration result: none; schema remains migrations 001–021.
7. User data result: no production data read, modified, deleted, migrated, or uploaded.
8. Remaining manual action: review and eventually merge PR #1, then retarget/rebase and review the v0.7.4.1 Draft PR; only after approval may a tag and Release be created.
