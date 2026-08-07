# Contributing

感谢你改进食序管家。这个项目把“账本是否真实、能否回滚”放在功能数量之前；提交应解决清楚的用户场景，并保持现有行为可验证。

## 开始之前

1. 完整阅读 [`docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md`](docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md)。用户明确要求保留的功能、格式、流程或行为属于受保护项，未经点名授权不得修改。
2. 实质修改必须同时递增产品版本和技术包版本；已经创建的版本目录和同名制品不可覆盖。
3. 先写能失败的测试，再实施最小修复。不要为了“顺便优化”扩大业务范围。
4. 明确声明数据库迁移。没有 schema 变化时写 `Migration: none`；不得改写 migrations 001–021。

## 安全开发边界

- 测试只使用新建的一次性 `dataDir`，不得指向个人 OpenClaw 状态、现有 `diet.sqlite` 或生产备份。
- 不提交数据库、备份、导出、报告、缓存、日志、凭据、地址、主机信息或真实饮食与体重数据。
- 不在普通业务逻辑中加入源码扫描、Shell/Exec 兜底或静默零值写入。
- 改动安装、升级或发布路径时，必须保留升级前冷备份、校验、只读验收和可执行回滚。

## 本地检查

在隔离环境中至少运行：

```text
python -m pytest -q
npm run build
node node_modules/vitest/vitest.mjs run
python scripts/scan_sensitive_content.py .
python scripts/validate_skill.py
python scripts/release_audit.py .
npm pack --dry-run --json
```

PR 必须说明用户价值、产品/技术版本、测试结果、migration 状态、数据安全影响和回滚方式。失败或未验证的步骤应如实报告，不能写成已完成。
