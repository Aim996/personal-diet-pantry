# 食序管家 GitHub 工作流入口文档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在项目根目录建立可供维护者和后续 AI 直接读取的 GitHub 工作流入口，并以 `0.7.4.2 / 0.8.2` 完成文档型版本迭代。

**Architecture:** `GITHUB-WORKFLOW.zh-CN.md` 只负责导航、决策、授权和交接；产品行为、正式发布、安装升级仍由现有专项文档作为单一事实来源。版本同步沿用现有双版本合同和发布构建器，不触碰业务实现、migrations 001–021 或生产 `dataDir`。

**Tech Stack:** Markdown、Git、GitHub Actions、Python 3.11+、Node.js 22.22.3+、pytest、Vitest、OpenClaw npm-pack。

## Global Constraints

- 完整遵守 `docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md`；未经用户明确授权，不改变任何受保护业务行为。
- 产品版本从 `0.7.4.1` 增至 `0.7.4.2`；技术包版本从 `0.8.1` 增至 `0.8.2`。
- migrations 保持 `001–021`；本轮不新增、不修改 migration。
- 不修改、读取或迁移生产 `dataDir`，不连接生产 OpenClaw。
- 不覆盖 `C:\path\to\personal-diet-pantry\0.7.4.1`；新候选只能写入不存在的 `C:\path\to\personal-diet-pantry\0.7.4.2`。
- 未经额外授权，不推送远端、不合并 PR、不创建 Tag/Release、不改变仓库可见性、不上线 OpenClaw。
- 根目录维护者手册进入源码归档，但不加入 OpenClaw 可安装包运行时白名单。

---

### Task 1: 为根目录 GitHub 入口建立失败测试

**Files:**
- Modify: `tests/test_public_repository_surface.py`
- Modify: `tests/test_version_contract.py`

**Interfaces:**
- Consumes: 设计文件 `docs/superpowers/specs/2026-08-05-github-workflow-guide-v0.7.4.2-design.md`。
- Produces: 根目录入口、文档关键语义和 `0.7.4.2 / 0.8.2` 双版本合同的可执行断言。

- [ ] **Step 1: 增加根目录入口文件和关键语义测试**

在 `tests/test_public_repository_surface.py` 增加测试，断言：

```python
def test_github_workflow_guide_is_a_safe_single_entrypoint() -> None:
    guide = text("GITHUB-WORKFLOW.zh-CN.md")
    for phrase in (
        "docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md",
        "先判断是否值得修改",
        "授权矩阵",
        "推送不等于发布",
        "发布不等于部署",
        "不得覆盖",
        "失败即停止",
        "交接记录模板",
    ):
        assert phrase in guide
    for forbidden in (
        "EXAMPLE_SECRET_MARKER",
        "192.0.2.1",
        "TO" + "DO",
        "TB" + "D",
    ):
        assert forbidden not in guide
```

同时把当前版本、更新文档、安装资产和发布命令断言更新为 `0.7.4.2 / 0.8.2`。

- [ ] **Step 2: 更新版本合同测试**

在 `tests/test_version_contract.py` 中设置：

```python
EXPECTED = "0.8.2"
PRODUCT_VERSION = "0.7.4.2"
PREVIOUS_EXPECTED = "0.8.1"
PREVIOUS_PRODUCT_VERSION = "0.7.4.1"
```

将当前发布文档、更新文档、回滚基线、兼容导入列表和核心门禁名称的断言同步到新版本，但保留对历史 `UPDATE-v0.7.4.1.zh-CN.md` 的审计检查。

- [ ] **Step 3: 运行定向测试并确认先失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_public_repository_surface.py tests/test_version_contract.py -q
```

Expected: FAIL，原因至少包括 `GITHUB-WORKFLOW.zh-CN.md`、`UPDATE-v0.7.4.2.zh-CN.md` 尚不存在以及版本源仍为 `0.7.4.1 / 0.8.1`。

### Task 2: 编写根目录单一入口手册

**Files:**
- Create: `GITHUB-WORKFLOW.zh-CN.md`
- Modify: `README.md`
- Modify: `README.en.md`

**Interfaces:**
- Consumes: 产品行为约束、`docs/RELEASING.md`、`docs/INSTALLATION.zh-CN.md`、`docs/UPGRADING.md`、`docs/AI-PROMPTS.zh-CN.md`。
- Produces: 后续维护者和 AI 的第一读取入口；README 只提供导航链接。

- [ ] **Step 1: 编写入口手册的身份与快速入口**

写明仓库 `Aim996/personal-diet-pantry`、本地仓库识别命令、现场核对优先于快照、固定阅读顺序，以及“先判断是否值得修改”的六项价值检查。

- [ ] **Step 2: 编写授权矩阵和阶段边界**

逐项区分只读检查、本地编辑、分支、提交、推送、PR、合并、Tag、Release、仓库可见性和 OpenClaw 部署；明确一般“上传 GitHub”授权不包含合并、Release、改可见性或生产部署。

- [ ] **Step 3: 编写标准工作流与停止条件**

覆盖：预检、最小实现、版本同步、测试、敏感扫描、提交、推送、Draft PR、合并前检查、正式 Tag/Release、制品 SHA-256、失败关闭以及历史制品不可覆盖。

- [ ] **Step 4: 编写任务输入和交接记录模板**

任务模板必须要求：真实问题、当前行为、期望行为、保留功能、是否推送、是否合并、是否 Release、是否上线。交接模板必须记录：分支、提交、PR、产品/技术版本、测试结果、候选目录、SHA-256、远端状态、部署状态和未验证项。

- [ ] **Step 5: 增加 README 导航**

在中文 README 的“文档导航/开发者入口”和英文 README 的维护者导航中链接根目录入口，不改变普通 OpenClaw 用户优先的章节顺序。

- [ ] **Step 6: 运行入口定向测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_public_repository_surface.py -q
```

Expected: 入口语义测试通过；版本相关断言可在 Task 3 完成前继续失败。

### Task 3: 同步 `0.7.4.2 / 0.8.2` 版本和发布文档

**Files:**
- Create: `UPDATE-v0.7.4.2.zh-CN.md`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `pyproject.toml`
- Modify: `python/personal_diet_pantry/__init__.py`
- Modify: `python/personal_diet_pantry/data_import.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `CHANGELOG.md`
- Modify: `RELEASE.zh-CN.md`
- Modify: `docs/INSTALL.md`
- Modify: `docs/UPGRADING.md`
- Modify: `docs/INSTALLATION.zh-CN.md`
- Modify: `docs/TROUBLESHOOTING.zh-CN.md`
- Modify: `docs/AI-PROMPTS.zh-CN.md`
- Modify: `docs/RELEASING.md`

**Interfaces:**
- Consumes: 双版本合同和 `0.7.4.1` 回滚基线。
- Produces: 所有当前用户/维护者文档一致引用 `0.7.4.2` 安装资产，并保留 `0.7.4.1` 作为前一版本和回滚基线。

- [ ] **Step 1: 同步机器可读版本源**

将技术版本更新为 `0.8.2`，产品版本更新为 `0.7.4.2`；运行 `npm install --package-lock-only --ignore-scripts` 机械同步锁文件，禁止手工改依赖树。

- [ ] **Step 2: 扩展导入兼容列表**

在 `python/personal_diet_pantry/data_import.py` 的受支持产品版本中加入 `0.7.4.2`，保留所有既有版本。

- [ ] **Step 3: 编写更新说明与变更记录**

`UPDATE-v0.7.4.2.zh-CN.md` 必须说明：新增 GitHub 单一入口、无业务行为变化、无 migration、不会修改用户数据、不会自动推送/合并/Release/部署、升级和回滚仍使用冷备份。

- [ ] **Step 4: 同步当前发布文档**

把当前安装资产、Tag、Release 标题和回滚关系更新为 `0.7.4.2`，把 `0.7.4.1` 保留为前一版本；不重写任何历史更新文档。

- [ ] **Step 5: 运行版本合同测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_version_contract.py tests/test_public_repository_surface.py -q
```

Expected: PASS。

### Task 4: 同步 CI、发布构建器和制品白名单

**Files:**
- Modify: `scripts/build_release.py`
- Modify: `ci/verify.ps1`
- Modify: `.github/workflows/release.yml`
- Modify: `.github/workflows/docker-first-user.yml`
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Modify: `docker/first-user/Dockerfile`
- Modify: `docker/first-user/verify.sh`
- Modify: `.dockerignore`
- Modify: `src-tests/version-contract.test.ts`
- Modify: `src-tests/package-contents.test.ts`
- Modify: `tests/test_build_release.py`
- Modify: `tests/test_github_workflows.py`
- Modify: `tests/test_release_ref.py`
- Modify: `tests/integration/test_installable_e2e.py`

**Interfaces:**
- Consumes: `0.7.4.2 / 0.8.2` 版本源和 `UPDATE-v0.7.4.2.zh-CN.md`。
- Produces: 新文件名、Release ref、容器验证、安装包成员和构建器常量完全一致。

- [ ] **Step 1: 更新构建器常量和安装包必需成员**

构建器使用 `0.7.4.2 / 0.8.2`、新源码包/安装包/测试摘要文件名，并要求安装包包含 `UPDATE-v0.7.4.2.zh-CN.md`。把 `GITHUB-WORKFLOW.zh-CN.md` 加入 `GitHub文档/` 审阅树和源码包，不加入 `package.json.files`。

- [ ] **Step 2: 更新 CI 和 Docker 验证引用**

所有固定安装资产和核心门禁标签更新到新版本，运行时版本断言更新到 `0.8.2 / 0.7.4.2`。

- [ ] **Step 3: 更新构建、工作流和包内容测试**

测试必须同时证明：新入口存在于源码/审阅材料；安装包包含新更新说明；安装包不包含根目录维护者入口；Tag ref 只接受 `v0.7.4.2`。

- [ ] **Step 4: 运行 TypeScript 和发布定向测试**

Run:

```powershell
npm run build
npm test -- --run
.\.venv\Scripts\python.exe -m pytest tests/test_build_release.py tests/test_github_workflows.py tests/test_release_ref.py tests/integration/test_installable_e2e.py -q
```

Expected: PASS。

### Task 5: 全量校验、提交与本地候选归档

**Files:**
- Modify only if validation reveals an in-scope defect.
- Create outside worktree: `C:\path\to\personal-diet-pantry\0.7.4.2`

**Interfaces:**
- Consumes: Tasks 1–4 的完整变更。
- Produces: 干净提交、全量验证证据和不可覆盖的本地候选目录；不产生远端写入。

- [ ] **Step 1: 校对文档和差异范围**

Run:

```powershell
git diff --check
rg -n "TO[D]O|TB[D]|EXAMPLE_SECRET_MARKER|192\.168\.100\.1" GITHUB-WORKFLOW.zh-CN.md docs README.md README.en.md UPDATE-v0.7.4.2.zh-CN.md RELEASE.zh-CN.md
git diff --stat
```

Expected: 无空白错误、无凭据/内部主机信息、无占位符；差异只属于本轮入口和版本同步。

- [ ] **Step 2: 运行项目完整验证**

Run:

```powershell
& .\ci\verify.ps1
```

Expected: Python、TypeScript、Skill、敏感扫描和 release audit 全部通过，migrations 为 21。

- [ ] **Step 3: 提交经过验证的实现**

```powershell
git add -- GITHUB-WORKFLOW.zh-CN.md README.md README.en.md CHANGELOG.md RELEASE.zh-CN.md UPDATE-v0.7.4.2.zh-CN.md package.json package-lock.json openclaw.plugin.json pyproject.toml python docs scripts ci .github docker src-tests tests .dockerignore
git commit -m "docs: add GitHub workflow guide v0.7.4.2"
```

- [ ] **Step 4: 从干净提交构建不可覆盖候选**

Precondition: `C:\path\to\personal-diet-pantry\0.7.4.2` 不存在；若存在则停止。

Run:

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py --project-root . --release-root 'C:\path\to\personal-diet-pantry\0.7.4.2'
```

Expected: 顶层恰好六项，源码包和安装包均可复现，`SHA256SUMS` 复核通过。

- [ ] **Step 5: 最终只读核对**

Run:

```powershell
git status --short --branch
git log -2 --oneline
Get-Content -Raw -Encoding utf8 'C:\path\to\personal-diet-pantry\0.7.4.2\release-manifest.json'
```

Expected: 工作树干净；分支为 `codex/github-workflow-guide-v0.7.4.2`；本地候选版本和测试摘要与提交一致；没有远端推送、Tag、Release 或部署。
