# Personal Diet Pantry v0.4.5 更新说明

## 迁移与完整性

迁移 2～7 的失败根因是 CRLF/LF 的字节差异；仅换行符不同不会被视为
语义 SQL 改动。任何语义 SQL 改动仍拒绝，以避免接受被篡改的迁移内容。

遇到 `DATABASE_INTEGRITY_ERROR` 时，返回 `retryable: false`。Agent 仅会紧随
一次 `self_check`；若仍不可修复，即熔断写入，本次会话不再执行写入操作。

在首次调用前，系统会补齐包装字段、`expires_at` 和营养结构所需的默认结构。

## 数据与升级

升级到 v0.4.5 前请备份数据目录，然后按现有本地构建与安装流程替换源码包。
升级会保留现有 SQLite 数据，不修改 `schema_migrations`。本说明不表示已部署，
也不表示 UAT 已通过。

## 回滚

如需回滚，请先保留当前数据目录与备份，再替换回先前的源码包；不要手动编辑
SQLite 数据库或 `schema_migrations`。v0.4.4 更新说明保留在独立归档文件
[`UPDATE-v0.4.4.zh-CN.md`](UPDATE-v0.4.4.zh-CN.md)。
