# 食序管家（Personal Diet Pantry）v0.7.4.28

食序管家是一套面向单个 OpenClaw 用户、本地优先的饮食、营养、饮水、体重与家庭食材管理 Skill。本版产品版本为 `0.7.4.28`，npm/OpenClaw/Python 技术包版本为 `0.8.28`。

## 发布状态

这是公开正式版 Release Notes。标签为 `v0.7.4.28`，固定页面为 <https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.4.28>。发布只提供代码与制品，不会部署、安装、启用、配置或重启任何用户实例。

## 本版变化

- 把公开仓库、README、安装手册、升级/回滚说明、AI 操作提示词与正式 GitHub Release 对齐。
- 普通用户和其他智能体可以从固定 Release 下载可安装包和 SHA256SUMS，校验后通过 OpenClaw `npm-pack:` 安装。
- 安装流程补齐插件启用、外部 `dataDir`、运行时七类工具检查、用户明确授权初始化、`self_check`、零业务写入验收和失败回滚。
- Release 同时提供可安装包、源码快照、manifest、测试摘要和 SHA-256 清单。

本版没有运行时业务行为变化。七类业务工具、明确摄入直接写入、开放模糊量预览、纯饮水短回执、六项两行餐食进度、库存事务、时间范围、过期推荐硬过滤和所有跨版本保护项均保持 0.7.4.27 行为。

## 发布制品

正式 Release 只上传以下五个文件；`GitHub文档/` 仅保留在本地候选目录作为审阅树：

| 文件 | 用途 |
| --- | --- |
| `personal-diet-pantry-0.7.4.28-installable.tgz` | OpenClaw npm-pack 可安装包。 |
| `personal-diet-pantry-0.7.4.28-source.tar.gz` | 干净提交的可复核源码快照，不可交给插件安装器。 |
| `release-manifest.json` | 提交、环境、测试、可复现性和制品哈希证据。 |
| `TEST-SUMMARY-v0.7.4.28.zh-CN.md` | 当次构建生成的测试总计、通过、跳过和失败数。 |
| `SHA256SUMS` | 覆盖前四个文件的 SHA-256。 |

安装前必须校验 SHA-256，并且只安装可安装包：

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.4.28-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

初始化全新账本前必须获得用户明确授权；安装验收保持零业务写入。完整步骤见 [docs/INSTALL.md](docs/INSTALL.md)。

## 兼容、数据与回滚

- 本版没有新增 migration，继续使用 migrations 001–021，schema 与 v0.7.4.19 相同。
- 安装和验收必须保留外部 `dataDir`；源码构建、CI 和候选制品不会修改用户数据。
- 更新前停止目标实例，按[详细安装手册](docs/INSTALLATION.zh-CN.md#5-备份用途与降级冷备份)创建并校验包含已提交 WAL 数据的**升级前冷备份**。
- 更新后独立确认七类工具，再执行 `self_check` 和只读记录数量核对。
- 失败时停止实例并重新安装 `personal-diet-pantry-0.7.4.19-installable.tgz` 恢复技术运行状态；v0.7.4.19 已记录为真实 UAT 失败版本，不能作为产品验收通过的依据。在线 `diet_system backup` 只用于同版本恢复，不能替代升级前冷备份；数据或环境损坏时使用已校验的冷备份恢复。

完整用户变化见 [v0.7.4.28 更新说明](UPDATE-v0.7.4.28.zh-CN.md)，安装和升级入口分别见 [docs/INSTALL.md](docs/INSTALL.md) 与 [docs/UPGRADING.md](docs/UPGRADING.md)。
