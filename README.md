# 食序管家

**Personal Diet Pantry v0.7.5.2**

**简体中文** | [English](README.en.md)

面向普通 OpenClaw 用户的本地饮食与家庭食材管家。你可以用日常说话的方式记录吃饭、饮水、体重和库存，查询营养进度与临期食材，并安全地修改或撤销刚才的操作。SQLite 是唯一正式事实来源，报告可以随时重新生成。

## 当前版本与状态

- 产品版本 `0.7.5.2`；技术包版本 `0.9.2`。
- 当前是**本地开发候选**，尚未创建 Git Tag、GitHub Release，也未安装到任何 OpenClaw 实例。
- 本版不新增 migration，继续使用 v0.7.5.0 的 migration 022；库存位置和到期日来源留痕及旧值保留规则不变。
- 候选通过完整门禁后才可生成可安装包、源码快照、manifest、测试摘要和 SHA-256 清单；构建不会自动部署。
- 上一正式版 `v0.7.4.28` 的发布与回下载验证证据继续保留在 [v0.7.4.28 公开发布实录](docs/releases/v0.7.4.28.zh-CN.md)；v0.7.5.2 必须重新生成自己的门禁与资产证据，不得沿用旧结论。

## 它适合谁

食序管家适合希望把个人饮食与家中食材放在同一本可信账本里管理的 OpenClaw 用户，尤其适合需要“说一句就记下、事后能查清、写错能撤销”的单人本地场景。

它不是医疗诊断工具，也不是多人家庭协作、云端营养平台或图片识别器。订单和营养标签图片应先由独立图片识别 Skill 提取为结构化信息，再交给食序管家确认和记录。

## 核心能力

- **饮食与营养**：记录正餐、加餐、饮料和补剂，保存标签值或有来源的估算。
- **饮水与体重**：独立记录纯饮水、食物隐性水分和体重趋势。
- **库存与临期**：管理包装、批次、启封/冷冻状态、到期日和家庭库存扣减。
- **自然入库默认值**：生产日期和保质期可省略；位置按品类推断，到期日按入库日期估算，并明确区分用户事实与系统估算。
- **做饭与剩菜**：一次事务中扣原料、记录已吃部分并把剩菜放回库存。
- **自然时间与份量**：理解跨日时间范围；“一个玉米”等明确常见计数会带可食部估算直接记录，并区分玉米芯等不可食部分；“一点、几口”等开放份量先展示范围再确认。
- **纠错、删除、撤销与重做**：对最近的可识别操作做净变化修正；“把刚才那条删了”使用同会话安全凭证直接执行，不要求用户记内部 ID。
- **进度与报告**：按热量、蛋白质、脂肪、碳水、纤维和饮水展示进度，查询成本、浪费与趋势。
- **写入安全闸门**：纯查询不会补写；“刚才记上了吗”只核对一次最近操作，多域复合写入在首次提交前整体阻断，体重删除先预览再确认。
- **本地优先安全**：七类类型化工具把正式事实写入外部 `dataDir` 中的 SQLite；失败不会冒充成功，过期食品不进入计划或推荐候选，已经发生的摄入仍如实记账。

## 实际回执示例

成功记录后的六项进度固定采用两行式展示；下列数字只用于说明格式，实际结果来自 SQLite：

```text
已记录！火腿肠 1根 50克（估算）｜84.8 kcal

🔥 热量 ░░░░░░░░░░ 4%
🔥84.8 / 1900 kcal +84.8kcal +4%
🥩 蛋白 ░░░░░░░░░░ 2%
🥩4 / 170 g +4g +2%
🧈 脂肪 █░░░░░░░░░ 12%
🧈6.4 / 55 g +6.4g +12%
🌾 碳水 ░░░░░░░░░░ 2%
🌾2.4 / 150 g +2.4g +2%
🥬 纤维 ░░░░░░░░░░ 0%
🥬0 / 30 g
💧 饮水 █░░░░░░░░░ 12%
💧374ml / 3L
```

只有本次成功提交确实改变库存时，才显示 `📦 库存变动`。数量明确的“一个玉米、一根火腿肠”按显式估算直接记录；玉米回执会说明 `1个｜可食部（玉米粒）约90克（估算）`，不会把玉米芯计入营养。“一点、一些、几口”等开放份量会先展示具体重量范围、营养估算和依据，用户确认前保持零写入。纯水只显示饮水确认与饮水进度。

## 最快开始

v0.7.5.2 正式发布后，从对应 GitHub Release 下载固定版本资产，再按[简明安装入口](docs/INSTALL.md)完成 SHA-256 校验、外部 `dataDir`、npm-pack 安装和只读验收；当前本地候选不得冒充已发布资产。

正式安装只使用 `personal-diet-pantry-0.7.5.2-installable.tgz`，不要把 `source.tar.gz` 交给插件安装器：

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.5.2-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

安装后先确认 `diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system` 七类工具均已注册。只有全新账本且用户明确授权时才运行 `initialize`；随后运行 `self_check`。`self_check` 只证明数据库和配置健康，不能代替七类工具注册检查。验收保持零业务写入。

## 系统要求

- OpenClaw `>=2026.5.17`
- Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`（与锁定 OpenClaw 运行范围一致）
- Python `>=3.11,<4`
- 位于源码目录之外、可持久化且权限受控的专用 `dataDir`

## 数据安全、更新与回滚

仓库、安装包和测试不应包含真实数据库、备份、导出、报告、凭据、地址或个人饮食数据。测试只能使用新建的一次性目录；不要把测试指向现有 OpenClaw 状态或个人 `dataDir`。

从 v0.7.5.0 更新到 v0.7.5.2 不新增 migration，保留原外部 `dataDir` 即可；仍建议更新前创建并校验冷备份。从 v0.7.4.28 直接更新时会应用既有 migration 022，回退前必须恢复升级前冷备份，再安装 v0.7.4.28。在线 `diet_system backup` 不能替代升级前冷备份。详见[更新与回滚入口](docs/UPGRADING.md)。

## 常见问题

### 安装后为什么还看不到全部工具？

安装包注册成功不等于当前会话已经加载全部工具。请先在 OpenClaw 工具列表中确认上述七类工具，再检查插件启用状态并重启对应实例；最后运行 `self_check`。

### 可以直接安装源码包吗？

不可以。`personal-diet-pantry-0.7.5.2-source.tar.gz` 只用于审阅和复现；OpenClaw 插件安装器只接收 `personal-diet-pantry-0.7.5.2-installable.tgz`。

### 报告和数据库不一致时以哪个为准？

以外部 `dataDir` 中的 SQLite 为准。Markdown 报告是可重建输出，不能通过手改报告改变账本。

### 会自动吃掉、丢弃或推荐过期食品吗？

不会主动推荐。已过期库存会被排除在计划、菜谱和推荐候选之外；丢弃库存也需要明确操作。若你明确说自己已经吃了，系统会如实记录事实并把库存原因记为食用，不会伪装成丢弃。

## 文档导航

- [GitHub 更新与发布工作流](GITHUB-WORKFLOW.zh-CN.md)（维护者和后续 AI 第一读取入口）
- [简明安装](docs/INSTALL.md) / [安全更新与回滚](docs/UPGRADING.md)
- [用户指南](docs/USER-GUIDE.zh-CN.md) / [完整安装手册](docs/INSTALLATION.zh-CN.md) / [故障排除](docs/TROUBLESHOOTING.zh-CN.md)
- [工具参考](docs/TOOLS-REFERENCE.zh-CN.md) / [数据模型](docs/DATA-MODEL.zh-CN.md) / [架构](docs/ARCHITECTURE.zh-CN.md)
- [AI 安装、更新与验收提示词](docs/AI-PROMPTS.zh-CN.md)
- [v0.7.5.2 更新说明](UPDATE-v0.7.5.2.zh-CN.md) / [变更记录](CHANGELOG.md)
- [v0.7.4.28 公开发布实录](docs/releases/v0.7.4.28.zh-CN.md)
- [跨版本产品行为约束](docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md)

## 开发者入口

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和跨版本产品行为约束。源码构建不会安装、启用、重启或修改 OpenClaw；本地测试必须使用隔离数据目录。

```text
python -m pytest -q
npm run build
node node_modules/vitest/vitest.mjs run
python scripts/scan_sensitive_content.py .
```

先阅读根目录 [GitHub 更新与发布工作流](GITHUB-WORKFLOW.zh-CN.md)，再按 [docs/RELEASING.md](docs/RELEASING.md) 执行正式发布门禁。历史版本细节留在各 `UPDATE-*.zh-CN.md`，首页不再重复堆叠。

本地候选目录顶层恰好是 `personal-diet-pantry-0.7.5.2-source.tar.gz`、`personal-diet-pantry-0.7.5.2-installable.tgz`、`release-manifest.json`、`TEST-SUMMARY-v0.7.5.2.zh-CN.md`、`SHA256SUMS` 和审阅用 `GitHub文档/`；正式 Release 只上传前五个文件。

## 许可证

本项目采用 [MIT License](LICENSE)。个人饮食记录、数据库、导出和备份不是项目示例资产，不应提交或公开。
