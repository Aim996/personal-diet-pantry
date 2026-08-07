# 食序管家 GitHub 更新与发布工作流

> 本文是维护食序管家 GitHub 仓库时的**第一读取入口**，供项目维护者和执行型 AI 使用。它负责说明“先读什么、是否应该改、能执行到哪一步、怎样验证和交接”。产品行为、正式发布和安装升级的具体事实仍以本文链接的专项文档为准。

## 1. 适用范围

本文适用于以下工作：

- 修复问题、优化体验或增加经过确认的能力；
- 更新文档、Skill、规则、源码、测试、配置或构建脚本；
- 创建版本分支、提交、推送和 Pull Request；
- 在明确授权后合并 PR、创建 Tag 和 GitHub Release；
- 为后续 OpenClaw 安装准备可验证制品和交接记录。

本文不授予任何执行者删除远端对象、改变仓库可见性、操作生产 OpenClaw 或修改真实业务数据的权限。

## 2. 60 秒快速入口

### 2.1 固定阅读顺序

每个变更批次开始前，按顺序完整阅读：

1. [`docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md`](docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md)：不可静默删除、弱化或改序的产品行为，以及强制版本迭代规则。
2. 本文：GitHub 操作、授权边界和交接方式。
3. [`docs/RELEASING.md`](docs/RELEASING.md)：Tag、Actions 和正式 Release 门禁。
4. 与任务直接相关的专项文档和测试；涉及安装、更新或回滚时，再读 [`docs/INSTALLATION.zh-CN.md`](docs/INSTALLATION.zh-CN.md) 与 [`docs/UPGRADING.md`](docs/UPGRADING.md)。

不得用聊天摘要、历史记忆或旧版本文档代替第一项约束文件。用户明确要求长期保留的新功能、格式、流程或行为，必须先登记到该约束文件并补齐运行规则、测试和示例，然后才能实施。

### 2.2 现场核对仓库

以下命令只读取本地仓库和远端元数据：

```powershell
git rev-parse --show-toplevel
git status --short --branch
git branch --show-current
git remote -v
git log -5 --oneline --decorate
git ls-remote --heads origin
git ls-remote --tags origin
gh auth status
gh repo view Aim996/personal-diet-pantry --json nameWithOwner,visibility,defaultBranchRef,url
```

任何写入前，至少确认：

- 当前目录确实属于 `Aim996/personal-diet-pantry`；
- 工作树是否包含用户尚未提交的改动；
- 当前分支、上游分支和默认分支分别是什么；
- 用户要求的是本地修改、推送、PR、合并、Release 还是部署；
- 当前产品版本、技术包版本和前一版本是什么；
- 同名版本目录、分支、Tag 或 Release 是否已经存在。

命令的现场结果高于本文的历史快照。发现不一致时，先查明原因，不得为了继续流程而自行覆盖现有状态。

### 2.3 最短决策路径

```text
收到请求
  → 先判断是否值得修改
  → 阅读产品行为约束并识别受保护能力
  → 明确授权止点
  → 建立新版本身份和功能分支
  → 最小实现与针对性测试
  → 全量验证和敏感扫描
  → 本地提交
  → 获得上传授权后推送并创建 Draft PR
  → 获得合并授权后合并
  → 获得正式发布授权后创建 Tag/Release
  → 获得上线授权并完成备份后部署 OpenClaw
```

**推送不等于发布，发布不等于部署。** 每个箭头都是独立阶段，后续阶段不能从前一阶段的授权中自动推断。

## 3. 当前项目状态与现场核对

以下是 `2026-08-06` 更新本文时的维护快照，只用于帮助接手者定位，不是永久事实：

| 项目 | 快照 |
|---|---|
| GitHub 仓库 | `Aim996/personal-diet-pantry` |
| 仓库可见性 | Private；仓库内容按未来公开可读的安全标准维护 |
| 默认分支 | `main`，必须用 `gh repo view` 复核 |
| 当前变更分支 | `codex/v0.7.4.16-preserve-correction-object` |
| 当前产品版本 | `0.7.4.16` |
| 当前技术包版本 | `0.8.16` |
| 前一产品/技术版本 | `0.7.4.14 / 0.8.14` |
| 数据库迁移 | migrations `001–021`，本轮不新增 |
| 正式 Release | 建立本文时尚未创建 `v0.7.4.16` |
| 自动部署 | GitHub 工作流不连接用户 OpenClaw，不自动部署 |

后续每次接手都必须运行第 2.2 节命令，并读取 `package.json`、`RELEASE.zh-CN.md`、最新 `UPDATE-*.zh-CN.md` 与 `CHANGELOG.md`。如果快照已经过期，更新本文中的快照属于实质文档变更，必须按新版本处理。

## 4. 单一事实来源

| 事实 | 权威入口 |
|---|---|
| 受保护产品行为和强制版本规则 | [`docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md`](docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md) |
| 当前用户可见变化 | `UPDATE-v<产品版本>.zh-CN.md` |
| 当前发布合同和 Release Notes | [`RELEASE.zh-CN.md`](RELEASE.zh-CN.md) |
| 历史版本摘要 | [`CHANGELOG.md`](CHANGELOG.md) |
| 正式 Tag/Release 顺序 | [`docs/RELEASING.md`](docs/RELEASING.md) |
| 安装、升级、冷备份和回滚 | [`docs/INSTALLATION.zh-CN.md`](docs/INSTALLATION.zh-CN.md) |
| 普通用户更新入口 | [`docs/UPGRADING.md`](docs/UPGRADING.md) |
| 可复制给 OpenClaw 的操作提示词 | [`docs/AI-PROMPTS.zh-CN.md`](docs/AI-PROMPTS.zh-CN.md) |
| 用户定位和快速开始 | [`README.md`](README.md) / [`README.en.md`](README.en.md) |
| CI 与发布实现 | [`.github/workflows/`](.github/workflows/) / [`ci/verify.ps1`](ci/verify.ps1) / [`scripts/build_release.py`](scripts/build_release.py) |

若文档之间出现冲突：

1. 受保护行为以产品行为约束为准；
2. 当前版本和资产名称以机器可读版本源、版本合同测试和当前发布文档共同核对；
3. 命令实现以当前脚本和工作流为准；
4. 不能安全消除冲突时，失败即停止并向用户报告，不得选择最方便执行的一份。

## 5. 先判断是否值得修改

后续执行者在编辑文件前必须完成以下价值检查：

1. **真实场景**：问题来自实际使用、稳定复现、测试证据，还是只有推测？
2. **当前行为**：现状到底是什么，证据来自哪条回复、测试、日志或代码路径？
3. **可观察结果**：修改后用户能感知到什么改善？不能只写“结构更优雅”。
4. **不改的影响**：不修改会造成错误数据、安全风险、明显卡顿还是仅维护者不喜欢？
5. **回归风险**：可能破坏哪些受保护能力、历史兼容、数据边界或已有成功场景？
6. **最小验证**：哪一条失败测试和哪一组回归测试能证明修改真实有效？

处理结论只有三类：

- **值得修改**：存在明确收益，按最小范围实施；
- **先验证**：证据不足，只做只读调查，不创建版本；
- **建议不改**：收益低于复杂度和回归风险，向用户说明原因。

不得以“重构”“统一风格”“模型觉得更合理”“减少几行代码”或“需要一个新版本”为唯一修改理由。

## 6. 授权矩阵

一般原则：只读调查可以主动完成；用户提出生成、修改、修复或构建后，可以执行该范围内的本地变化；影响远端状态、公开状态和生产实例的操作需要更具体授权。

| 操作 | 默认权限和要求 |
|---|---|
| 查看 Git 状态、差异、历史、远端分支、PR 和 CI | 可直接只读执行 |
| 阅读源码、文档、测试和构建脚本 | 可直接只读执行；不得读取生产用户数据来代替测试 |
| 在用户指定范围内编辑、测试、构建 | 用户明确要求修改、生成、修复或开发后可执行 |
| 创建 `codex/` 功能分支 | 属于正常实现步骤；不得覆盖同名远端分支 |
| 创建本地提交 | 用户要求完成项目修改时可执行；提交只包含本轮范围 |
| 推送分支 | 需要用户明确要求“上传、推送到 GitHub”或等价表达 |
| 创建或更新 Draft PR | 用户已授权上传 GitHub 时可以执行，并明确 base/head |
| 把 Draft PR 改为 Ready | 需要用户明确授权进入正式审阅 |
| 合并 PR | 必须得到针对该 PR 的明确合并授权 |
| 创建和推送 Tag | 必须得到针对该版本的正式发布授权，并通过全部门禁 |
| 创建 GitHub Release、上传资产 | 必须得到正式发布授权；不得覆盖同名 Release 或补传成“完整成功” |
| 改变 Public/Private 状态 | 必须得到针对仓库可见性的明确授权 |
| 删除分支、Tag、Release 或远端文件 | 必须得到对精确目标的明确删除授权；优先保留可恢复路径 |
| 安装或更新生产 OpenClaw、重启服务 | 必须得到上线授权，并先完成停机冷备份和恢复准备 |
| 删除、重建、修补或迁移真实业务数据 | 不属于一般 GitHub/上线授权；必须单独明确授权 |

当授权表述可能覆盖多个阶段时，选择不会造成外部不可逆影响的最短止点，并在交接中说明哪些阶段尚未执行。

## 7. 变更批次与版本治理

### 7.1 双版本合同

食序管家同时维护：

- **产品版本**：面向用户，例如 `0.7.4.16`；
- **技术包版本**：npm、OpenClaw 插件和 Python 元数据使用的 SemVer，例如 `0.8.16`。

任何实质修改都必须同时递增两者。实质修改包括文档、Skill、reference、规则、配置、源码、测试和构建脚本的新增、删除或内容变化。只改文档、只改提示词或只改测试都不能沿用旧版本身份。

### 7.2 历史不可变

- 已创建的版本目录、候选制品、Tag 和 Release 不得覆盖、替换或重新解释；
- 同一连续实现批次可以多次编辑，但一旦完成验证或交付为候选，后续需求必须进入下一版本；
- 历史 `UPDATE-*.zh-CN.md` 保留原语义，不批量替换其中的旧版本号；
- 新候选必须写入新的外部目录；发现目标已存在时立即停止；
- 同名 Tag 或 Release 已存在时立即停止，不使用强制更新。

### 7.3 版本同步清单

每个新版本至少核对：

- `package.json`：`version`、`productVersion` 和当前更新文档白名单；
- `package-lock.json`；
- `openclaw.plugin.json`；
- `pyproject.toml`；
- `python/personal_diet_pantry/__init__.py`；
- `python/personal_diet_pantry/data_import.py` 的兼容版本；
- `RELEASE.zh-CN.md`、当前 `UPDATE-*.zh-CN.md`、`CHANGELOG.md`；
- README、安装、更新、排障、AI 提示词和维护者发布手册；
- `scripts/build_release.py`、CI、Release/Docker 工作流；
- Python/TypeScript 版本合同、制品内容和安装集成测试。

版本同步不能用对整个仓库的盲目全局替换完成，因为历史文档必须保留旧版本事实。

## 8. 标准开发工作流

### 8.1 接收和固化需求

开始前写清：

- 用户的真实输入或复现步骤；
- 当前结果与预期结果；
- 用户点名必须保留的能力；
- 明确非目标；
- 本轮远端操作止点；
- 是否可能涉及数据、migration、公开接口或生产部署。

如果用户点名某能力需要永久保留，先更新产品行为约束，再开始实现。一般性的“优化一下”不构成修改受保护行为的授权。

### 8.2 建立干净基线

```powershell
git status --short --branch
git diff --check
git log -3 --oneline --decorate
```

发现用户的未提交改动时，保留它们并避开重叠文件；无法安全隔离时停止并说明。禁止使用会丢失用户改动的重置或检出命令。

在可用环境中运行基线验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ci\verify.ps1
```

如果需要通过 `PDP_PYTHON` 或临时 `PATH` 指定运行时，只在当前进程设置，并在交接中记录。环境失败与项目测试失败必须分开报告。

### 8.3 创建功能分支

从经过核对的基线创建新分支，默认使用 `codex/` 前缀：

```powershell
git switch -c codex/<简短主题>-v<新产品版本>
```

创建前先确认本地和远端没有同名分支。若任务基于尚未合并的前序 PR，必须在 PR 正文说明依赖关系，不能假装它直接基于 `main`。

### 8.4 测试先行和最小实现

1. 增加能稳定复现问题的失败测试；
2. 运行定向测试，确认失败原因与问题一致；
3. 实现最小修改；
4. 运行定向测试；
5. 运行受保护核心门禁；
6. 运行完整验证；
7. 只修复本轮造成的失败，不顺带重构无关代码。

普通业务对话不能依靠扫描源码、数据库或生产文件来“验证”。测试使用临时 `dataDir`，不得读取真实饮食、库存、体重或偏好。

### 8.5 提交前审查

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-only
git diff
```

必须确认：

- 差异仅包含本轮授权范围；
- 受保护行为没有删除、弱化、改序或缩减；
- 版本源和当前文档一致；
- migration 数量和编号符合设计；
- 没有数据库、日志、备份、导出、缓存、环境文件或凭据；
- 测试结果来自当前差异，不复用旧版本结果。

### 8.6 本地提交

只暂存明确文件，不使用容易把无关文件带入的粗放命令：

```powershell
git add -- <本轮明确文件列表>
git diff --cached --check
git diff --cached --stat
git commit -m "<类型>: <可审计摘要>"
```

提交后再次运行：

```powershell
git status --short --branch
git show --stat --oneline HEAD
```

## 9. 推送与 Pull Request

只有获得上传 GitHub 授权后才执行本节。

### 9.1 推送功能分支

```powershell
git push -u origin HEAD
```

不得使用强制推送覆盖远端历史。若远端同名分支与本地不一致，停止并比较提交关系。

### 9.2 创建 Draft PR

先确定 base/head：

```powershell
git branch --show-current
gh pr list --head <当前分支> --state all
```

没有现有 PR 时再创建：

```powershell
gh pr create --draft --base <目标基线分支> --head <当前分支> --title "<标题>" --body-file <已校对的PR正文文件>
```

PR 正文至少包含：

- 用户可见变化和修改价值；
- 非目标与受保护行为结论；
- 产品/技术包版本；
- migration 结论；
- 测试与构建证据；
- 数据和敏感信息边界；
- 本地候选及 SHA-256 状态；
- 是否基于另一个未合并分支；
- “未合并、未 Tag、未 Release、未部署”的真实状态。

创建 PR 后核对 URL、Draft 状态、base/head、提交数、文件数和 CI。不能只凭 `gh pr create` 退出码宣称 PR 内容正确。

### 9.3 合并边界

合并前必须获得明确授权，并确认：

- 审阅意见已处理；
- 必需 CI 全绿；
- base 没有偏离预期；
- 同名版本、Tag 和 Release 仍不存在；
- Release Notes 与制品名称一致。

未获合并授权时，保持 Draft PR 或已推送分支，不自行合并。

## 10. 正式 Tag 与 GitHub Release

只有用户明确批准正式发布后，完整读取 [`docs/RELEASING.md`](docs/RELEASING.md) 并执行其当前版本命令。基本门禁如下：

1. 发布提交已进入预期默认分支，工作树干净，CI 全绿；
2. 产品/技术版本、Tag、Release 文档和构建器完全一致；
3. 远端不存在同名 Tag，GitHub 不存在同名 Release；
4. 敏感扫描、Python、TypeScript、Skill、release audit 和隔离安装全部通过；
5. 源码包和安装包各自两次构建得到相同字节哈希；
6. SHA-256 独立复核通过；
7. 只有完成所有前置检查后才创建 annotated Tag；
8. 只推送该 Tag，由 Release 工作流生成和上传正式资产；
9. 下载远端资产并再次核对哈希和成员；
10. 任一步失败都不能报告“发布完成”。

正式 Release 资产固定包含：

```text
personal-diet-pantry-<产品版本>-installable.tgz
personal-diet-pantry-<产品版本>-source.tar.gz
release-manifest.json
TEST-SUMMARY-v<产品版本>.zh-CN.md
SHA256SUMS
```

本地候选还包含仅供审阅的 `GitHub文档/` 目录；正式 Release 不上传该目录。OpenClaw 插件安装器只能使用 `installable.tgz`，不能使用源码包。

## 11. GitHub 与 OpenClaw 部署边界

三个结果必须分开验收：

| 结果 | 证明方式 |
|---|---|
| GitHub 已上传 | 远端分支/PR 存在，提交 SHA、base/head 和文件差异正确 |
| GitHub 已正式发布 | Tag 和 Release 存在，五项资产可下载且 SHA-256 正确 |
| OpenClaw 已上线 | 目标实例完成冷备份、安装、重启、版本/七类工具/self_check/只读数据核对 |

GitHub Actions 不连接用户的软路由器或 OpenClaw 实例，也不读取生产 `dataDir`。上线操作必须单独获得授权，使用 [`docs/AI-PROMPTS.zh-CN.md`](docs/AI-PROMPTS.zh-CN.md) 中的更新/验收提示词，并遵守：

- 先停止实例并创建、校验升级前一致冷备份；
- 只替换程序包，保留外部 `dataDir`；
- 只执行版本声明的 migration；
- 重启后验证真实运行版本、七类工具、`self_check` 和只读记录数量；
- 不用测试餐、测试饮水或临时库存污染生产数据；
- 失败时停止重复尝试并按成套回滚文档恢复。

## 12. 安全、隐私和制品边界

以下内容不得进入 Git、PR、Actions 日志或 Release：

- `.env`、API key、令牌、密码、SSH 凭据或会话 Cookie；
- SQLite 数据库、WAL、journal、备份、导出或恢复包；
- 真实饮食、饮水、体重、库存、偏好和健康记录；
- 生产主机地址、生产绝对路径和含身份信息的截图；
- 日志、缓存、临时数据目录、测试生成报告和本地虚拟环境；
- 未经审计的二进制或来源不明的依赖包。

固定三层检查：

1. `.gitignore` 和源码归档排除规则；
2. `scripts/scan_sensitive_content.py`；
3. 发布构建器的成员白名单和敏感成员拒绝规则。

安全扫描发现疑似内容时，不把值复制到对话或报告；只报告文件和问题类别，先阻止提交/发布。

## 13. 验证命令与完成证据

### 13.1 定向验证

根据修改范围运行精确测试。版本或 GitHub 文档变化至少覆盖：

```powershell
python -m pytest -p no:cacheprovider tests/test_public_repository_surface.py tests/test_version_contract.py -q
python -m pytest -p no:cacheprovider tests/test_build_release.py tests/test_github_workflows.py tests/test_release_ref.py -q
node node_modules/vitest/vitest.mjs run src-tests/version-contract.test.ts src-tests/package-contents.test.ts
```

### 13.2 完整门禁

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ci\verify.ps1
```

完成报告应给出真实统计，包括 Python 通过/跳过/失败、TypeScript 通过/跳过/失败、migration 数量、敏感扫描、release audit 和集成测试状态。若环境需要额外运行时路径，记录运行时来源和版本。

### 13.3 候选构建

只有工作树干净、所有门禁通过且外部目标目录不存在时构建：

```powershell
python scripts/build_release.py --project-root . --release-root '<新的外部候选目录>'
```

构建完成后核对 `release-manifest.json`、`SHA256SUMS`、顶层六项、安装包成员和两次构建的可复现结果。候选目录存在即停止，不删除后重建同名版本。

## 14. 失败和停止条件

以下任一情况触发**失败即停止**：

- 未完整读取产品行为约束；
- 用户要求与受保护行为冲突，但没有精确修改授权；
- 工作区包含无法安全隔离的用户改动；
- 真实基线、分支、版本或 PR 关系不清楚；
- 需要新增 migration 或修改真实数据，但本轮未授权；
- 测试、敏感扫描、release audit、可复现构建或隔离安装失败；
- 同名候选目录、Tag 或 Release 已存在；
- 版本源、文档、制品名称或哈希不一致；
- 远端操作或生产操作超出用户明确授权；
- 无法证明成功，只能依据猜测或文件存在作结论。

停止时报告：已完成的只读证据、失败点、没有执行的写操作、对用户数据的影响，以及一个安全的下一步。不得连续原样重试，也不得用强制推送、覆盖制品、编辑 migration 历史或直接修改数据库来绕过失败。

## 15. 后续任务输入模板

把以下内容交给后续维护者或 AI；未知项应写“不确定，需要只读核对”，不要省略：

```text
项目：Aim996/personal-diet-pantry

真实问题或目标：
当前可观察行为：
期望可观察行为：
复现输入或证据：
必须永久保留的功能/格式/流程：
明确不做的内容：

本轮授权止点：
- 允许本地修改和测试：是/否
- 允许创建本地提交：是/否
- 允许推送 GitHub 分支：是/否
- 允许创建或更新 Draft PR：是/否
- 允许合并 PR：是/否
- 允许创建 Tag/GitHub Release：是/否
- 允许改变仓库可见性：是/否
- 允许安装到生产 OpenClaw：是/否

验收重点：
已知风险或历史回归：
```

如果用户只说“按食序管家标准流程修复并上传 GitHub”，默认授权本地修改、验证、提交、推送功能分支和创建 Draft PR；不默认授权合并、Tag、Release、改变可见性或生产部署。

## 16. 可复制给执行型 AI 的简版指令

```text
维护 Aim996/personal-diet-pantry。先完整读取 docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md 和根目录 GITHUB-WORKFLOW.zh-CN.md，再核对真实 Git 状态、远端、当前双版本和发布状态。

先判断本次修改是否有真实用户价值；若证据不足，先做只读调查。任何用户明确要求保留的行为必须在实现前登记进产品行为约束，并补齐运行规则、测试和示例。只做能证明改善的最小修改，不做无关重构，不读取或写入生产 dataDir。

所有实质修改同时递增产品版本和技术包版本，旧候选、Tag 和 Release 不得覆盖。先写失败测试，完成定向与全量验证、敏感扫描、release audit、可复现构建和隔离安装后再提交。

严格停在用户授权阶段：上传 GitHub 不等于允许合并，合并不等于允许 Tag/Release，Release 不等于允许部署。结束时按本文交接记录模板报告真实完成项、未执行项、测试统计、提交/PR、制品哈希和数据影响。
```

## 17. 交接记录模板

每轮结束必须生成可复核的交接，不使用“应该成功”“看起来正常”等推测表达：

```text
食序管家 GitHub 交接记录

一、范围与价值
- 用户目标：
- 实际改善：
- 非目标：
- 受保护行为变化：无 / 已获授权并登记（列出条目）

二、版本与 Git
- 仓库：Aim996/personal-diet-pantry
- 产品版本：
- 技术包版本：
- 前一产品/技术版本：
- 分支：
- 提交 SHA：
- 上游分支：
- 工作树：干净 / 有保留改动（说明）

三、GitHub
- 推送：未执行 / 已执行（远端分支）
- PR：未创建 / Draft / Ready / 已合并（URL、base、head）
- CI：通过 / 失败 / 未验证
- Tag：未创建 / 已创建（名称和提交）
- Release：未创建 / 已创建 / 不完整（URL）
- 仓库可见性：未改变 / 已按授权改变

四、验证
- Python：总计、通过、跳过、失败
- TypeScript：总计、通过、跳过、失败
- 核心行为门禁：
- migrations：
- 敏感信息扫描：
- release audit：
- 隔离安装/只读冒烟：
- 未验证项：

五、候选与哈希
- 候选目录：未生成 / 路径
- 源码包：文件名和 SHA-256
- 安装包：文件名和 SHA-256
- 可复现构建：通过 / 失败 / 未验证
- 同名历史制品：未覆盖

六、OpenClaw
- 生产数据读写：无 / 说明已获授权的操作
- 冷备份：未执行 / 已校验（路径不进入公开记录）
- 安装：未执行 / 已执行
- 重启与运行版本：未执行 / 已验证
- 七类工具、自检和只读数量核对：
- 回滚状态：未需要 / 已执行 / 已准备未执行

七、下一步
- 仍需用户授权的动作：
- 已知风险：
- 推荐下一步：
```

## 18. 最终核对清单

完成前逐项回答“是”或明确写“未执行”：

- [ ] 已完整读取产品行为约束和本文；
- [ ] 已说明修改价值、证据、风险和非目标；
- [ ] 用户点名保留的行为已登记并有测试；
- [ ] 新双版本完整递增，历史版本未改写；
- [ ] migrations 与授权一致；
- [ ] 定向测试、核心门禁、完整验证和敏感扫描通过；
- [ ] 差异只包含本轮范围，没有用户数据或凭据；
- [ ] 提交、推送、PR、合并、Tag、Release、可见性和部署分别核对授权；
- [ ] 候选目录和远端对象没有覆盖同名历史内容；
- [ ] GitHub、Release 和 OpenClaw 三个状态分别验收；
- [ ] 交接记录包含真实统计、SHA、未执行项和下一步。

这份清单的目的不是增加流程，而是让每一次修改都能回答：它是否真正改善项目、是否保住用户已经确认的能力、是否可以安全复现和回退。
