# 食序管家 v0.4.4 保质期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Every behavior change follows red-green-refactor and receives a separate spec/quality review.

**Goal:** 所有新入库食品保存具体到期时间，旧库存可安全补全，并在紧凑查询中直接显示剩余天数、小时数或过期状态。

**Architecture:** 模型根据食品和储存上下文生成具体 `expires_at`；TypeScript 插件负责公开接口兼容和可恢复诊断；Python 服务负责严格时间校验、原子写入、旧库存元数据更新及确定性剩余时间计算。沿用现有 `pantry_batches.expires_at`，不增加迁移。

**Tech Stack:** TypeScript 5.9、TypeBox、Vitest、Python 3、pytest、SQLite、OpenClaw Plugin SDK。

## 全局约束

- 发布版本必须为 `0.4.4`。
- 不清空、不重建、不覆盖现有 SQLite 数据。
- 用户/标签日期优先；无明确日期时由模型结合上下文估算具体时间。
- 后端不使用一张固定天数表替代模型判断。
- 缺少到期时间最多自动补齐重试一次，不追问、不盲试。
- 查询不重新估算，不返回完整库存、内部编号或模型推理。
- 发布物封存到 `C:\path\to\personal-diet-pantry\0.4.4\`，不得覆盖 `0.4.3`。

---

### Task 1：新入库保质期契约

**Files**

- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/pantry.py`
- Test: `src-tests/plugin.test.ts`
- Test: `tests/test_cli.py`
- Test: `tests/test_pantry.py`

**接口**

- Consumes: pantry `add/preview_add` 的可选公开 `expires_at`。
- Produces: 缺失时的安全 `INVALID_INPUT(expires_at)`，或严格保存的具体到期时间。

- [ ] **Step 1：编写失败测试**

增加测试证明：

1. provider schema 接受未带 `expires_at` 的请求；
2. 插件执行边界返回：

```json
{
  "code": "INVALID_INPUT",
  "field": "expires_at",
  "reason": "required",
  "retryable": true
}
```

3. 失败时不调用 Python；
4. 直接绕过插件调用 Python 时，缺少日期或 `expires_at <= added_at` 均失败且五张正式表零写入；
5. 合法日期精确保存在 SQLite。

- [ ] **Step 2：运行专项测试并确认失败**

```text
npx vitest run src-tests/plugin.test.ts
<python> -m pytest tests/test_cli.py tests/test_pantry.py -q
```

- [ ] **Step 3：实现最小契约**

保留公开 schema 的可选字段，在 `normalizeToolPayload()` 中于 Python 调用前做业务必填检查。Python `_pantry_add_arguments()` 和 pantry 领域层严格要求非空、晚于 `added_at` 的 `expires_at`。

- [ ] **Step 4：运行专项测试并确认通过**

- [ ] **Step 5：提交**

```text
git commit -m "feat: require expiry for new pantry batches"
```

---

### Task 2：剩菜与熟食保质期

**Files**

- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/meals.py`
- Modify: `python/personal_diet_pantry/prepared_foods.py`
- Test: `src-tests/plugin.test.ts`
- Test: `tests/test_meals.py`
- Test: `tests/test_prepared_foods.py`

**接口**

- Consumes: 任意层级 meal item 的 `leftover`。
- Produces: 精确字段路径诊断，或带到期时间的原子剩菜入库。

- [ ] **Step 1：编写失败测试**

覆盖顶层和嵌套原料的 leftover 缺日期，断言插件返回类似：

`items[0].leftover.expires_at`

并且不调用 Python。直接调用 Python 的缺失/倒置日期也必须在原料扣减、饮食记录和剩菜入库前失败。

- [ ] **Step 2：运行专项测试并确认失败**

- [ ] **Step 3：实现递归检查和领域校验**

公开 leftover schema 继续允许请求进入插件。TypeScript 递归遍历最多已有的配料深度，返回第一个缺失路径；Python meal/prepared-food 层将到期时间视为剩菜必填并验证晚于剩菜入库时间。

- [ ] **Step 4：运行专项测试并确认通过**

- [ ] **Step 5：提交**

```text
git commit -m "feat: require expiry for prepared leftovers"
```

---

### Task 3：旧库存保质期补全

**Files**

- Modify: `src/schemas.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/pantry.py`
- Test: `src-tests/plugin.test.ts`
- Test: `tests/test_cli.py`
- Test: `tests/test_pantry.py`
- Test: `tests/test_compact_pantry_query.py`

**接口**

- Consumes: `query.missing_expiry_only` 与 `preview_update_metadata.expires_at`。
- Produces: 只更新目标批次到期时间的现有预览—提交工作流。

- [ ] **Step 1：编写失败测试**

覆盖：

1. `missing_expiry_only: true` 只返回 active/opened/frozen/thawed 且日期为空的批次；
2. 过滤结果继续分页且不公开内部编号；
3. 元数据更新可以只提交 `expires_at`；
4. 提交后数量、营养关联、重量和其他字段不变；
5. 陈旧 handle 继续返回 `STALE_PREVIEW`。

- [ ] **Step 2：运行专项测试并确认失败**

- [ ] **Step 3：扩展现有工作流**

在不增加新动作的情况下扩展 `preview_update_metadata`。目标解析、工作流 handle、版本检查和 commit 复用现有实现；只把 `expires_at` 加入允许更新字段。查询层增加 SQL 过滤，不先读取全部库存再在模型侧筛选。

- [ ] **Step 4：运行专项测试并确认通过**

- [ ] **Step 5：提交**

```text
git commit -m "feat: backfill pantry expiry metadata"
```

---

### Task 4：剩余时长与过期展示

**Files**

- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/reports.py`
- Test: `tests/test_compact_pantry_query.py`
- Test: `tests/test_reports_backup_health.py`

**接口**

- Consumes: 保存的 `expires_at` 和可信当前时间。
- Produces: `expiry_state` 与 `expiry_display`。

- [ ] **Step 1：编写失败测试**

在固定当前时间下覆盖：

- 超过 24 小时：`usable`、`剩余5天`；
- 18 小时：`expiring_soon`、`剩余18小时`；
- 过期 5 小时：`expired`、`已过期5小时`；
- 空日期：`missing`、`待补保质期`。

同时断言默认紧凑查询仍不含数据库编号、完整营养表、notes 和推理说明。

- [ ] **Step 2：运行专项测试并确认失败**

- [ ] **Step 3：实现纯读取计算**

新增一个无副作用的时间格式化函数。天数按完整天向下取整；不足一天或过期不足一天按小时向上取整，最少 1 小时。查询和临期报告复用该函数，不更新数据库状态。

- [ ] **Step 4：运行专项测试并确认通过**

- [ ] **Step 5：提交**

```text
git commit -m "feat: show pantry shelf life"
```

---

### Task 5：Skill、版本与更新文档

**Files**

- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `README.md`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `pyproject.toml`
- Modify: `python/personal_diet_pantry/__init__.py`
- Create: `UPDATE-v0.4.4.zh-CN.md`
- Test: `tests/test_skill_contract.py`

- [ ] **Step 1：编写失败的 Skill 契约测试**

验证 Skill 明确包含：

- 用户/标签/模型估算的固定优先级；
- 所有新入库和 leftover 都必须带具体日期；
- 不确定时自主给出偏保守估算，不询问；
- `INVALID_INPUT(expires_at)` 只补齐重试一次；
- 旧库存按需紧凑补全，不在每顿饭后扫描全库；
- 入库和库存查询的简短中文展示示例。

- [ ] **Step 2：运行测试并确认失败**

- [ ] **Step 3：更新 Skill 和版本**

所有版本来源统一改为 `0.4.4`。更新文档说明本版本沿用现有 SQLite 列，没有数据清理或重建。

- [ ] **Step 4：运行 Skill 契约、构建和插件校验**

```text
<python> -m pytest tests/test_skill_contract.py -q
npm run build
npm run plugin:validate
```

- [ ] **Step 5：提交**

```text
git commit -m "release: personal diet pantry v0.4.4"
```

---

### Task 6：完整验证与独立封存

**Files**

- Create/Replace only: `C:\path\to\personal-diet-pantry\0.4.4\`

- [ ] **Step 1：完整验证**

```text
npm test
<python> -m pytest -q
npm run build
npm run plugin:validate
```

- [ ] **Step 2：生成 runtime-only 发布物**

复用 `scripts/reproducible_archive.py`，排除测试、缓存、虚拟环境和 `node_modules`。目录体与 tar 成员必须一致，版本来源均为 `0.4.4`。

- [ ] **Step 3：写入封存目录**

仅创建 `C:\path\to\personal-diet-pantry\0.4.4\`，不得修改 `0.4.3`。包含：

- `personal-diet-pantry\`
- `personal-diet-pantry-0.4.4-source.tar.gz`
- `食序管家-v0.4.4-更新说明.md`
- `校验摘要.txt`

- [ ] **Step 4：验证归档**

验证 SHA256、UTF-8 文档、可安全解包、可复现、成员一致和版本一致。

- [ ] **Step 5：最终代码与规格复核**

审查从 v0.4.3 最终提交到 v0.4.4 最终提交的完整差异。阻断或重要问题必须修复后重新验证和封存。

