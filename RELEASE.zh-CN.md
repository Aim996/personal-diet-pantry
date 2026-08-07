# 食序管家（Personal Diet Pantry）v0.7.4.27

食序管家是一套面向单个 OpenClaw 用户、本地优先的饮食、营养、饮水、体重与家庭食材管理 Skill。本版产品版本为 `0.7.4.27`，npm/OpenClaw/Python 技术包版本为 `0.8.27`。

## 发布状态

当前内容是正式发布前的候选说明：仓库保持 Private，尚未创建 Git Tag 或 GitHub Release，也没有部署、安装、启用或重启任何用户实例。只有完整验证通过、人工批准且 `v0.7.4.27` 标签与同名 Release 均不存在时，维护者才可执行发布。

## 本版变化

- 显式日期端点中的具体时刻优先于早上、傍晚、晚上等时段默认边界；中文和阿拉伯数字时刻统一按 `Asia/Shanghai` 处理。
- 同一用户消息中的唯一精确包装换算可补齐 per-100ml/per-100g 消费量，使部分标签、餐食写入和库存扣减一次原子完成；冲突或不匹配时不猜测。
- 完整只读餐食历史清单同时接管 WebUI transcript 和出站回复，严格逐条保留工具返回的独立记录，不再被最终自由文本合并或标记为疑似重复。

本版只修改时间端点解析、同轮精确包装规范化和官方 transcript 回复钩子，没有改变七类业务工具、数据库 schema、migrations 001–021、明确摄入直接写入、模糊开放份量零写入、纯饮水短回执、六项两行餐食进度、过期推荐硬过滤或独立的 WebUI “新会话”按钮问题。

## 发布制品

正式 Release 只上传以下五个文件；`GitHub文档/` 仅保留在本地候选目录作为审阅树：

| 文件 | 用途 |
| --- | --- |
| `personal-diet-pantry-0.7.4.27-installable.tgz` | OpenClaw npm-pack 可安装包。 |
| `personal-diet-pantry-0.7.4.27-source.tar.gz` | 干净提交的可复核源码快照，不可交给插件安装器。 |
| `release-manifest.json` | 提交、环境、测试、可复现性和制品哈希证据。 |
| `TEST-SUMMARY-v0.7.4.27.zh-CN.md` | 当次构建生成的测试总计、通过、跳过和失败数。 |
| `SHA256SUMS` | 覆盖前四个文件的 SHA-256。 |

安装前必须校验 SHA-256，并且只安装可安装包：

```text
openclaw plugins install npm-pack:/path/to/personal-diet-pantry-0.7.4.27-installable.tgz
```

## 兼容、数据与回滚

- 本版没有新增 migration，继续使用 migrations 001–021，schema 与 v0.7.4.19 相同。
- 安装和验收必须保留外部 `dataDir`；源码构建、CI 和候选制品不会修改用户数据。
- 更新前停止目标实例，按[详细安装手册](docs/INSTALLATION.zh-CN.md#5-备份用途与降级冷备份)创建并校验包含已提交 WAL 数据的**升级前冷备份**。
- 更新后独立确认七类工具，再执行 `self_check` 和只读记录数量核对。
- 失败时停止实例并重新安装 `personal-diet-pantry-0.7.4.19-installable.tgz` 恢复技术运行状态；v0.7.4.19 已记录为真实 UAT 失败版本，不能作为产品验收通过的依据。在线 `diet_system backup` 只用于同版本恢复，不能替代升级前冷备份；数据或环境损坏时使用已校验的冷备份恢复。

完整用户变化见 [v0.7.4.27 更新说明](UPDATE-v0.7.4.27.zh-CN.md)，安装和升级入口分别见 [docs/INSTALL.md](docs/INSTALL.md) 与 [docs/UPGRADING.md](docs/UPGRADING.md)。
