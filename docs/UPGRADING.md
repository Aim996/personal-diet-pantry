# 安全更新与回滚

本文描述从产品 v0.7.5.3 更新到 v0.7.5.4 的最小安全顺序。本版没有新 migration，继续使用 migrations 001–023；它只改变插件代码、Skill 指引和安装验证，不改写既有业务事实。

## 更新前

1. 只读记录当前产品/技术版本、插件位置、实际 `dataDir` 和关键表记录数量，不输出个人明细。
2. 按目标实例既有流程停止写入，确认没有插件进程继续写入。
3. 使用受信源码中的 `scripts/cold_backup.py`，在 `dataDir` 外的新路径创建并验证冷备份；不得覆盖旧备份。
4. 从正式 v0.7.5.4 Release 取得 `personal-diet-pantry-0.7.5.4-installable.tgz` 和 `SHA256SUMS`，核对同名文件 SHA-256。Release 尚不存在时只能作为本地候选测试，不得冒充正式版。

## 安装更新

1. 保留原外部 `dataDir`，不要手改 `diet.sqlite`、WAL 或 `schema_migrations`。
2. 通过 `openclaw plugins install npm-pack:` 安装已校验的 v0.7.5.4 可安装包。
3. 使用目标实例真实的服务管理方式重启。普通本机服务可以使用 `openclaw gateway restart`；Docker 前台 Gateway 必须通过容器管理方式重建进程。
4. 重启前后核对 Gateway PID 或启动时间。若没有变化，就仍是旧运行时，本次更新未完成；不得只凭安装命令 `exit 0` 宣称成功。

## 更新后验收

1. 用 `openclaw plugins inspect personal-diet-pantry --runtime --json` 确认七类工具全部注册。
2. 运行 `diet_system(self_check)`；失败时停止日常写入。
3. 只读确认产品版本 `0.7.5.4`、技术包版本 `0.9.4`、migration 最大版本仍为 023，并比较更新前后的关键记录数量。
4. 定向查询目标、进度和一条旧库存，确认事实未被升级改变。
5. 真实写入 UAT 只能使用用户明确授权的数据和口语化输入，逐项记录结果；不得把自动化通过冒充实机通过。

## 回滚

v0.7.5.3 与 v0.7.5.4 使用同一 migrations 001–023。普通代码回退可以：

1. 停止实例。
2. 安装已校验的 `personal-diet-pantry-0.7.5.3-installable.tgz`。
3. 真实重启 Gateway，并确认进程启动时间变化。
4. 核对产品/技术版本、七类工具、自检、migration 023 和关键记录数量。

若更新后出现 SQLite 完整性、外键、migration 校验或记录数量异常，不要直接复用当前数据库进行普通回退；先停止实例、隔离异常状态并恢复升级前冷备份，再安装 v0.7.5.3。没有可验证备份时应保留现状并报告未完成，不能编辑 SQLite 或 `schema_migrations` 伪造恢复。
