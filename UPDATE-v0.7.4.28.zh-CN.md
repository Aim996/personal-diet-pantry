# 食序管家 v0.7.4.28 更新说明

## 本版目标

把已经公开的 GitHub 源码项目补齐为普通 OpenClaw 用户和其他智能体都能安全安装、核验、更新与回滚的正式 Release。产品版本为 `0.7.4.28`，技术包版本为 `0.8.28`。

## 用户可见变化

- GitHub README 不再显示 Private、发布准备中或“Release 尚未创建”的过期状态。
- 固定 Release 页面提供可安装包、源码快照、manifest、测试摘要和 SHA-256 清单。
- 安装说明明确只把 `personal-diet-pantry-0.7.4.28-installable.tgz` 交给 OpenClaw `npm-pack:`；`source.tar.gz` 只用于审阅。
- 安装流程补齐 Python 锁定依赖、外部专用 `dataDir`、插件启用、重启/重新加载、`--runtime --json` 检查、七类工具、初始化授权、`self_check` 和零业务写入验收。
- 三套 AI 提示词可分别执行全新安装、安全更新和只读验收，并在缺少证据时停止而不是冒充成功。
- Node.js 兼容范围与锁定 OpenClaw 依赖对齐为 `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`，不再把 Node 23 或 24.0–24.14 误标为支持。
- 源码归档根目录从已提交 `package.json.name` 取得，因此仓库克隆成任意本地目录名都能产生相同成员路径并通过发布验证。

正式资产名为 `personal-diet-pantry-0.7.4.28-installable.tgz`、`personal-diet-pantry-0.7.4.28-source.tar.gz`、`release-manifest.json`、`TEST-SUMMARY-v0.7.4.28.zh-CN.md` 和 `SHA256SUMS`；构建目录额外保留 `GitHub文档/` 审阅树，但它不作为第六个远端资产上传。

## 运行时与数据边界

- 运行时业务行为没有变化；本版不重构饮食、饮水、库存、体重、时间、回执、推荐或事务逻辑。
- 没有新增 migration，继续使用 migrations 001–021；schema 与 v0.7.4.27/v0.7.4.19 相同。
- `dataDir` 必须独立于插件和源码目录；更新只替换程序包，不覆盖个人数据。
- 全新账本初始化需要用户明确授权；默认安装验收是零业务写入。
- CORE-01～10、SAFE-01～07、GOV-01～02、UI-01 和 GF-001～005 全部保持原样。

## 安装与回滚

正式入口是 <https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.4.28>。下载后先核对 `SHA256SUMS`，再执行 npm-pack 安装。更新现有实例前必须停止实例并创建、验证**升级前冷备份**。失败时可重新安装已校验的 v0.7.4.19 包恢复技术运行状态；如果数据库或记录数量异常，应先从冷备份恢复。v0.7.4.19 不是产品 UAT 通过基线，在线 `diet_system backup` 也不能替代升级前冷备份。

## 发布边界

本版创建正式 Git Tag 和 GitHub Release，但不会自动部署、安装、启用、配置或重启任何 OpenClaw 实例。GitHub Actions 当前因账户计费锁定不能启动，因此本次 Release 使用同一套本地完整门禁、可复现构建和 SHA-256 审计后人工创建；该限制不得被描述为 Actions 已通过。
