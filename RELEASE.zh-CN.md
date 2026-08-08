# 食序管家（Personal Diet Pantry）v0.7.5.4

食序管家是一套面向单个 OpenClaw 用户、本地优先的饮食、饮水、体重与家庭食材管理 Skill。产品版本为 `0.7.5.4`，npm/OpenClaw/Python 技术包版本为 `0.9.4`。

## 当前状态

这是本地开发候选说明，不代表 Git Tag、GitHub Release、路由器安装或真实数据验收已经完成。只有完整测试、敏感信息扫描、可复现构建和人工批准全部通过后，才可创建 `v0.7.5.4` Release；发布也不会自动部署到任何 OpenClaw 实例。

## 核心变化

- 工具钩子的临时 `sessionId` 不再覆盖提示阶段捕获的稳定会话身份；同会话唯一餐食纠正直接更新，不再查询后二次确认。
- C/D 级营养估算允许纤维、钠和食物含水等未知字段保持缺失；未知按未知保存，不为通过校验伪造零。
- 用户陈述家中实际存在的食品直接形成库存事实；已过期项照实标记，同时继续从建议候选中硬过滤。
- Docker 前台 Gateway 更新后必须验证进程或容器启动时间确实改变，避免安装成功但旧运行时继续服务。

玉米直接记录与可食部说明、纯水短回执、普通餐食六项双行回执、SQLite 事实源、库存联动事务、跨时间范围查询、查询零写入、否定/未来/他人行为零写入和跨域部分提交阻断保持不变。

## 发布制品

正式 Release 只上传以下五个文件；`GitHub文档/` 仅保留在本地候选目录：

| 文件 | 用途 |
| --- | --- |
| `personal-diet-pantry-0.7.5.4-installable.tgz` | OpenClaw npm-pack 可安装包。 |
| `personal-diet-pantry-0.7.5.4-source.tar.gz` | 干净提交的源码快照，不可交给插件安装器。 |
| `release-manifest.json` | 提交、环境、测试、可复现性和制品哈希证据。 |
| `TEST-SUMMARY-v0.7.5.4.zh-CN.md` | 当次构建生成的测试总计、通过、跳过和失败数。 |
| `SHA256SUMS` | 覆盖前四个文件的 SHA-256。 |

## 数据迁移与回退

本版不新增 migration，继续使用 migrations 001–023，不改写既有餐食、饮水、体重、库存或目标。

从 v0.7.5.3 更新前应停止实例并创建、校验位于 `dataDir` 外的 SQLite 冷备份；在线 `diet_system backup` 不能替代升级前冷备份。普通回退可重新安装 v0.7.5.3 并复用相同的 023 schema；若数据完整性异常，应停止实例并先恢复升级前冷备份。

完整变化见 [v0.7.5.4 更新说明](UPDATE-v0.7.5.4.zh-CN.md)，安装和升级入口分别见 [docs/INSTALL.md](docs/INSTALL.md) 与 [docs/UPGRADING.md](docs/UPGRADING.md)。
