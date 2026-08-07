# 安全更新与回滚

本文描述从产品 v0.7.5.0 更新到 v0.7.5.2 的最小安全顺序。本补丁不新增 migration，继续使用 migration 022；从 v0.7.4.28 直接更新时仍必须按 migration 022 的要求成套升级、成套回退。

## 更新前

1. 只读记录当前产品/技术版本、插件位置、实际 `dataDir` 和关键表记录数量；不要复制个人明细到工单。
2. 按目标实例既有流程停止实例，确认没有插件进程继续写入。
3. 使用受信源码中的 `scripts/cold_backup.py`，在 `dataDir` 外创建新的升级前冷备份并执行验证；不得覆盖旧备份。
4. 取得 `personal-diet-pantry-0.7.5.2-installable.tgz` 和 `SHA256SUMS`，核对同名文件 SHA-256。若 v0.7.5.2 Release 尚未创建，只能把本地制品当作候选测试包，不能冒充公开正式版。

## 安装更新

1. 保持实例停止，保留原外部 `dataDir`，不要手改 `diet.sqlite`、WAL 或 `schema_migrations`。
2. 通过 `openclaw plugins install npm-pack:` 安装已校验的 v0.7.5.2 可安装包。
3. 按实例既有方式启动或重新加载。从 v0.7.5.0 更新时 migration 最大版本应保持 022；从 v0.7.4.28 直接更新时首次连接只新增 migration 022。任何更早 migration 校验失败都必须停止。

## 更新后验收

1. 用 `openclaw plugins inspect personal-diet-pantry --runtime --json` 独立确认七类工具全部注册。
2. 运行 `diet_system(self_check)`；失败时停止日常写入。
3. 用只读方式确认产品版本 `0.7.5.2`、技术包版本 `0.9.2`、migration 最大版本 022，并比较关键记录数量。
4. 定向查询一条旧库存，确认数量、位置和到期时间未改变，新增来源为 `legacy_unknown`。
5. 真实写入验收另行使用明确授权的测试数据，结束后完整回滚；只读验收不应写业务数据。

## 回滚

v0.7.4.28 不认识 migration 022，因此禁止只替换旧程序包并继续使用已升级数据库。

1. 停止实例。
2. 隔离当前已迁移的 `dataDir`，不要覆盖或删除它。
3. 用 `scripts/cold_backup.py` 验证并恢复升级前冷备份。
4. 安装已校验的 `personal-diet-pantry-0.7.4.28-installable.tgz`。
5. 启动后重新检查七类工具、自检、migration 最大版本 021 和关键记录数量。

如果没有可验证的升级前冷备份，不得宣称已安全回退；应保持实例停止并保留现状等待人工处理。在线同版本备份不能替代升级前冷备份。
