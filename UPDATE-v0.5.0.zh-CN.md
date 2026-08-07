# 食序管家 v0.5.0 更新说明

`0.5.0` 将饮食、饮水、库存、个人规则和事务统一到 SQLite 数据库。SQLite 是唯一事实来源；Markdown 日报、周报和月报均为可重新生成的派生文件。运行数据应放在独立数据目录，不存入本源码包。

## 数据与迁移

本版本新增 `011_v050_data_truth.sql`（migration 11），用于补齐营养完整性、会话来源、目标档案、语义回执和隐性水分相关的数据基础。服务会在自动 migration 前备份；升级前仍应由操作者备份当前数据目录和 SQLite 文件。

迁移 001 至 010 保持不变。既有记录会保留，历史记录中的未知营养不会被当作零值。

## 营养、进度与报告

营养解析优先级固定为：已保存剩菜的营养快照、库存批次标签快照、`nutrition_cache`、内置 `rules/nutrition-foods.yaml`、本次请求提供的 `nutrition_estimate`。高优先级来源只会被低优先级来源补齐缺失字段，不能被覆盖。

新的 `diet_report(action="progress")` 直接返回结构化六项营养进度，不再生成或返回周报。饮水进度同时统计显式饮水和食物/饮料的隐性水分；日报、周报和月报使用同一聚合结果。历史营养未完整时会显示已知下限和缺失数量。

新餐食的六项核心营养不能全部为空。需要估算时，系统只允许一次补充估算重试；仍失败则不写入饮食，也不扣减库存。

## 做饭、剩菜与历史补录

`diet_meal(action="record_cooking")` 以单个事务处理整道菜：扣除全部实际原料、只记录已吃部分的营养，并把剩菜及其营养快照写入库存。再次吃剩菜时优先使用该快照，不会重复扣减生原料。任何原料不足、营养缺失、有效期非法或数据库错误都会回滚整笔事务。

历史营养补录是用户主动动作：先查询待补录记录，再提交完整估算。安装或升级不会自动补录历史数据、不会调用模型，也不会擅自改写历史估算。

## 个人规则与回复

份量规则和食物别名持久化在 SQLite 中，跨会话生效。例如“一杯豆浆”可按已保存规则换算为 330ml；“番茄”会优先安全匹配鲜食西红柿，不会误扣番茄罐头。

工具错误采用错误熔断：数据库完整性错误会停止本轮写操作；参数修复和营养估算最多各重试一次；其他同一动作错误不连续重试。回复只陈述真实落库结果和必要进度，不输出完整库存、数据库编号或内部指纹，保持简洁回复。

## 安装与升级

构建会同时生成两个用途不同的归档：

- `personal-diet-pantry-0.5.0-source.tar.gz` 是提交态源码快照，用于审阅、留档和源码复现；它不含编译后的 `dist/`，不能作为插件安装包。
- `personal-diet-pantry-0.5.0-installable.tgz` 是 npm-compatible OpenClaw installable package，包含编译后的插件、OpenClaw/package metadata、Python 运行包、`requirements.lock`、配置、规则、迁移、模板和 Skill。它不包含测试、TypeScript 源码、`node_modules`、虚拟环境、缓存、SQLite/数据库、`.env`、密钥或用户报告。

使用 OpenClaw 官方 npm-pack 安装路径安装 `.tgz`：

```bash
openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.5.0-installable.tgz
```

1. 在隔离环境中准备 installable 包和受控数据目录；用于运行插件的 Python（`PYTHON`，未设置时为 `python`）需具备包内 `requirements.lock` 锁定的依赖。
2. 停止目标测试实例，完整备份升级前数据目录和 SQLite 文件。
3. 用上述命令安装 `0.5.0`，保留原 `dataDir` 映射；命令会在所选 OpenClaw state 中注册并启用插件。
4. 启动后让自动 migration 完成，再运行 `diet_system self_check` 和下方验证。

仅做安装验证时，应先把 `OPENCLAW_HOME`、`OPENCLAW_STATE_DIR` 和 `OPENCLAW_CONFIG_PATH` 指向同一个新建临时根目录下的隔离路径，不得使用现有 OpenClaw state 或个人饮食数据。

## 验证

```bash
python -m pytest -q
npm test
npm run build
npm run plugin:validate
```

另请确认 `diet_report progress` 能读取同日既有记录、隐性水分出现在进度与报告中、做饭剩菜不会重复扣原料，以及个人份量和别名可跨会话使用。

## 回滚

1. 停止实例。
2. 恢复升级前的源码或发布包，**同时恢复升级前备份的数据目录和 SQLite 文件**。
3. 启动后执行只读自检。

旧版本无法理解 migration 11 新字段，必须连同升级前备份一起回滚，不得直接删除 migration 记录，也不要手工修改 `schema_migrations`。
