# 食序管家 v0.6.0 更新说明

## 升级结论

0.6.0 不是对 0.5.0 的版本号重贴，而是一次“可证明行为”的正式发布：目标确认来源进入数据模型，洞察输出受可信证据和硬边界约束，六个业务域的行为由 51-action 机器契约和重建测试覆盖，源码包与可安装包可从同一干净提交重复生成。

## 从 0.5.0 继承并解决的问题

### 目标真相

0.5.0 的目标数值无法可靠区分“随配置提供的默认值”和“用户明确确认的目标”。0.6.0 的迁移 `012_goal_provenance.sql` 增加：

- `goal_source`：仅允许 `configuration_default` 或 `user_confirmed`；
- `confirmed_at`：用户确认目标时写入的 UTC 时间；默认目标为 `null`。

餐食回执、饮水回执、`progress`、`insights`、`query_goals` 和 `update_goals` 都公开同一组来源字段。未确认时目标、百分比、进度条和目标差值为 `null`，不能把配置默认值包装成个人承诺。目标更新的撤销/重做同时恢复数值、来源和确认时间。

### 洞察证据

`diet_report insights` 现在是只读、限界、可解释的接口：

- 指标固定为热量、蛋白、脂肪、碳水、纤维、钠和饮水；
- 营养证据使用显式 `nutrition_data_state`，区分完整、部分、缺失等状态；
- 日、周、月边界按档案时区计算，并覆盖夏令时边界；
- 临期窗口为 1–30 天，返回数量为 1–10，最终行动优先级最多三条；
- 没有用户确认目标时不产生 `goal_gap`；
- 只读 SQLite 正式事实，不读取已经生成的 Markdown 报告来重新拼结论。

### 报告语言

报告模板支持 `zh-CN` 和英文。档案语言为 `zh-CN` 时使用简体中文标题和术语；未知或缺失语言确定性回退到英文。语言只改变表现层，不改变 SQLite 事实或结构化接口字段。

### 六域回归基线

0.6.0 为餐食、饮水、库存、事务、报告和系统六个域重建了可定位的 Python 合同测试，并让 `contracts/public-behavior.yaml` 的每个 action 指向一个真实存在且可独立执行的测试。TypeScript 侧验证 51 个 action schema、递归隐私过滤、包文件白名单和桥接协议。

### 真实升级与安装

发布门禁不再只检查静态文件：

- 从不可变 0.5.0 源生成旧数据库，再由 0.6.0 应用迁移 `012`；
- 验证迁移失败时数据库整体回滚；
- 通过 JSONL 桥接执行完整业务场景；
- 对 `npm pack` 生成的实际 `.tgz` 做隔离安装和工具注册验证；
- 重新解包源码制品并执行其自带测试。

## 升级步骤

1. 停止目标实例，保存当前软件包，并完整备份 `dataDir`。
2. 核对备份可读且包含 `diet.sqlite`。
3. 仅通过 npm-pack 路径安装 `personal-diet-pantry-0.6.0-installable.tgz`。
4. 保留原 `dataDir` 并启动插件，让迁移按编号顺序执行。
5. 运行 `diet_system self_check`。
6. 运行 `diet_system query_goals`：从旧版本迁移来的目标应显示 `goal_source=configuration_default`、`confirmed_at=null`。
7. 运行 `diet_report progress` 和 `diet_pantry query` 做只读连续性核对。
8. 若用户愿意确认目标，再使用 `diet_system update_goals` 一次性提交七项完整目标。

## 回滚步骤

若迁移、启动或自检失败：

1. 立即停止业务写入，不重复迁移，不修改迁移 SQL 或 `schema_migrations`。
2. 停止实例。
3. 同时恢复升级前软件包与升级前完整 `dataDir` 备份。
4. 启动旧版本并执行其自检与只读查询。

只回退软件包、不回退数据库不是受支持的回滚方式。

## 兼容性与依赖

- Python：`>=3.11,<4`。
- Node.js：`>=22.22.3`；CI 矩阵固定为 22.22.3 与 24.15.0。
- OpenClaw peer：`>=2026.5.17`。
- OpenClaw 开发依赖：精确锁定 `2026.7.1-2`。

生产安装包不携带开发依赖或 `node_modules`，生产依赖审计必须为零漏洞。尚未由上游修复的开发依赖公告逐项接受至 2026-08-29；详见[依赖风险接受记录](docs/development/DEPENDENCY-RISK-ACCEPTANCE.md)。

## 发布制品与验证

`scripts/build_release.py` 只在整个 Git 工作树干净时运行。它先执行完整 CI，再分别生成两次源码包和两次可安装包；只有同类制品的 SHA-256 与成员清单完全一致时，才原子发布：

- `personal-diet-pantry-0.6.0-source.tar.gz`
- `personal-diet-pantry-0.6.0-installable.tgz`
- `release-manifest.json`
- `MANIFEST-SHA256.txt`
- `TEST-SUMMARY-v0.6.0.zh-CN.md`

最终通过数以发布目录中的机器生成摘要为准，不在说明文档中手填。历史文档中的“216 个 TypeScript 测试”没有对应现存测试文件，已明确退役，不能作为 0.6.0 的质量声明。

## 本版本明确不做

以下内容保留给 0.6.x 后续子版本，不伪装成本次已经完成：

- Service/Meals 等高复杂度模块的深层拆分；
- 自动维护日志和长期运行趋势；
- 将超长 `SKILL.md` 拆成核心指令与按需参考；
- 更完整的 schema/服务层债务治理；
- 多人、云同步、医疗诊断和自动替用户确认目标。

后续顺序和验收标准见项目根目录的《总的后续迭代路线图.md》。
