# 食序管家 v0.4.3 稳定性修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除可推导字段遗漏造成的红色工具错误，并精确保存明确包装规格。

**Architecture:** 公开 TypeBox 工具接口允许省略系统可推导字段，并让
`food_name`、`quantity`、`unit` 的缺失请求进入 TypeScript 执行边界取得
安全诊断。插件在调用 Python 服务前补齐系统字段、严格校验业务必填字段并
确定性计算包装规格；包装辅助字段随后删除。Python 服务继续接收严格、
完整的正式事务参数，并在写入 SQLite `REAL` 前验证十进制往返。

**Tech Stack:** TypeScript 5.9、TypeBox、Vitest、Python 3、pytest、SQLite、
OpenClaw Plugin SDK。

## Global Constraints

- 发布版本必须为 `0.4.3`。
- 不修改 SQLite 表结构，不清理或重建现有数据。
- 明确包装规格优先于模型估算。
- 系统生成 `added_at` 只能使用插件可信当前时间，不读取 `context.now`。
- 不能由 SQLite `REAL` 十进制往返的数量必须在任何写入前拒绝。
- 自动修正最多一次，不公开路径、SQL、堆栈和内部标识。
- 发布物封存到 `C:\path\to\personal-diet-pantry\0.4.3\`。

---

### Task 1: 系统字段自动补齐

**Files:**
- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Test: `src-tests/plugin.test.ts`

**Interfaces:**
- Consumes: OpenClaw `params`、插件可信当前时间。
- Produces: `normalizeToolPayload()` 返回包含 `added_at`、`source_text`
  的 Python 正式负载。

- [ ] **Step 1: 编写失败测试**

增加三个测试：

```ts
test("accepts pantry add without model-supplied system fields", () => {
  expect(Check(schemaFor("diet_pantry"), {
    action: "add",
    food_name: "鸡蛋",
    quantity: 30,
    unit: "piece",
  })).toBe(true);
});

test("defaults pantry add timestamps and source text before Python", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-27T09:15:30.123Z"));
  await pantryTool.execute("call", {
    action: "add",
    food_name: "鸡蛋",
    quantity: 30,
    unit: "piece",
    context: { now: "2099-01-01T00:00:00Z" },
  });
  expect(callPython).toHaveBeenCalledWith(expect.objectContaining({
    payload: expect.objectContaining({
      added_at: "2026-07-27T09:15:30.123Z",
      source_text: "OpenClaw pantry add: 鸡蛋 30 piece",
    }),
  }), expect.anything());
});

test("defaults water delete source text", async () => {
  await waterTool.execute("call", {
    action: "delete",
    record_handle: workflowHandle,
  });
  expect(callPython).toHaveBeenCalledWith(expect.objectContaining({
    payload: expect.objectContaining({
      source_text: "OpenClaw water record deletion",
    }),
  }), expect.anything());
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```text
npx vitest run src-tests/plugin.test.ts
```

Expected: 新增断言因公开 schema 仍要求字段或负载没有默认值而失败。

- [ ] **Step 3: 实现最小修复**

在 `src/schemas.ts` 中把公开的 `added_at`、库存 `source_text` 和饮水删除
`source_text` 改为可选。在 `src/index.ts` 的边界归一化中补齐默认值；
`added_at` 只使用插件可信当前 ISO 时间，绕过 schema 传入的 `context.now`
不得影响它。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```text
npx vitest run src-tests/plugin.test.ts
```

Expected: PASS。

- [ ] **Step 5: 提交**

```text
git add src/schemas.ts src/index.ts src-tests/plugin.test.ts
git commit -m "fix: default model-omitted system fields"
```

### Task 2: 精确包装规格

**Files:**
- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Test: `src-tests/plugin.test.ts`

**Interfaces:**
- Consumes: `package_count: number`、`quantity_per_package: number`、
  `package_unit: "g" | "ml"`。
- Produces: 标准 `quantity`、`unit` 和可选修正警告。

- [ ] **Step 1: 编写失败测试**

```ts
test("derives exact liquid total from package specification", async () => {
  await pantryTool.execute("call", {
    action: "add",
    food_name: "5.0蛋白浓醇豆浆",
    normalized_name: "豆浆",
    quantity: 2000,
    unit: "ml",
    package_count: 6,
    quantity_per_package: 330,
    package_unit: "ml",
  });
  expect(callPython).toHaveBeenCalledWith(expect.objectContaining({
    payload: expect.objectContaining({
      food_name: "5.0蛋白浓醇豆浆",
      normalized_name: "豆浆",
      quantity: 1980,
      unit: "ml",
    }),
  }), expect.anything());
});
```

同时断言结果警告说明模型提交的 `2000ml` 已按明确规格修正为 `1980ml`；
字段只提供一部分时，`add/preview_add` 返回
`INVALID_INPUT(package_specification)` 且不调用 Python；完整包装字段计算
后不出现在传给 Python 的负载中。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```text
npx vitest run src-tests/plugin.test.ts
```

Expected: 新包装字段被 schema 拒绝或数量仍为 2000。

- [ ] **Step 3: 实现确定性计算**

在 `PantryAddSchema` 加入三个可选包装字段。`normalizeToolPayload()` 在
三项完整时计算乘积并覆盖标准数量与单位；部分提供时返回结构化可恢复错误。
如果原数量不同，向成功响应追加一条简短警告。计算后删除三个包装字段，
避免 Python 预览与工作流保存它们。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```text
npx vitest run src-tests/plugin.test.ts
```

Expected: PASS。

- [ ] **Step 5: 提交**

```text
git add src/schemas.ts src/index.ts src-tests/plugin.test.ts
git commit -m "fix: preserve exact package quantities"
```

### Task 3: 安全的字段级错误

**Files:**
- Modify: `python/personal_diet_pantry/service.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 已映射的 `_ServiceError`。
- Produces: `error.field`、`error.reason`、`error.expected`、
  `error.retryable`，或现有通用安全错误。

- [ ] **Step 1: 编写失败测试**

添加测试，验证缺少 `food_name` 时公开：

```python
assert response["error"] == {
    "code": "INVALID_INPUT",
    "message": "The request is invalid",
    "field": "food_name",
    "reason": "required",
    "expected": "non-empty text",
    "retryable": True,
}
```

同时验证数据库异常、路径、SQL 和未知异常仍只返回通用消息。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```text
python -m pytest tests/test_cli.py -q
```

Expected: 当前 `error_response()` 删除原消息，新增字段断言失败。

- [ ] **Step 3: 实现安全诊断映射**

为已知输入校验异常建立白名单映射，不直接公开原始异常文本。给
`_ServiceError` 增加可选安全诊断字段；其他错误保持现有脱敏逻辑。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```text
python -m pytest tests/test_cli.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```text
git add python/personal_diet_pantry/service.py tests/test_cli.py
git commit -m "fix: return safe input diagnostics"
```

### Task 4: Skill 规则、乱码与版本文档

**Files:**
- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `pyproject.toml`
- Modify: `python/personal_diet_pantry/__init__.py`
- Create: `UPDATE-v0.4.3.zh-CN.md`
- Test: `tests/test_skill_contract.py`

**Interfaces:**
- Consumes: v0.4.3 已实现接口行为。
- Produces: 无乱码、可由 OpenClaw 稳定执行的 Skill 指令和一致版本号。

- [ ] **Step 1: 编写失败测试**

测试 Skill 不含常见乱码字符，包含“明确包装规格必须精确计算”和
“字段级错误最多修正一次”，并验证所有版本文件均为 `0.4.3`。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```text
python -m pytest tests/test_skill_contract.py -q
```

Expected: 乱码或版本断言失败。

- [ ] **Step 3: 修复文档和版本**

以 UTF-8 重写损坏段落，加入 v0.4.3 调用规则。更新全部版本文件并编写
中文更新说明，说明兼容性、修复内容和升级方式。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```text
python -m pytest tests/test_skill_contract.py -q
npm run build
```

Expected: PASS。

- [ ] **Step 5: 提交**

```text
git add skills/personal-diet-pantry/SKILL.md package.json package-lock.json openclaw.plugin.json pyproject.toml python/personal_diet_pantry/__init__.py UPDATE-v0.4.3.zh-CN.md tests/test_skill_contract.py
git commit -m "release: personal diet pantry v0.4.3"
```

### Task 5: 全量验证与封存

**Files:**
- Modify: `scripts/reproducible_archive.py`
- Test: `tests/test_skill_contract.py`
- Create externally: `C:\path\to\personal-diet-pantry\0.4.3\食序管家-v0.4.3-更新说明.md`
- Create externally: `C:\path\to\personal-diet-pantry\0.4.3\校验摘要.txt`
- Create externally: 完整源码目录和安装包

**Interfaces:**
- Consumes: Git 中已提交的 v0.4.3 源码。
- Produces: 可复查、可安装、版本隔离的只读留存副本。

- [ ] **Step 1: 运行完整验证**

Run:

```text
npm test
python -m pytest -q
npm run plugin:validate
```

Expected: 全部 PASS。

- [ ] **Step 2: 构建发布包**

运行项目现有 `scripts/build-package.sh` 或等价 Windows 入口，确认发布包
只包含运行所需文件，不包含 `tests/`、`src-tests/`、`node_modules/` 或
`.pytest_cache/`。

- [ ] **Step 3: 建立隔离封存目录**

确认绝对目标为 `C:\path\to\personal-diet-pantry\0.4.3\` 后，复制已提交源码和发布包，
写入中文更新说明与包含提交号、测试结果、文件校验值的校验摘要。

- [ ] **Step 4: 核对封存**

从封存目录读取版本文件，确认均为 `0.4.3`；比较发布包校验值，确认复制
过程没有改变内容。

### Task 6: 最终整分支审查修复

**Files:**
- Modify: `src/index.ts`
- Modify: `src/schemas.ts`
- Modify: `python/personal_diet_pantry/pantry.py`
- Modify: `python/personal_diet_pantry/service.py`
- Test: `src-tests/plugin.test.ts`
- Test: `tests/test_pantry.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 可选公开库存必填字段、精确标准数量、包装辅助字段。
- Produces: 可执行的安全 `INVALID_INPUT`，或不含包装辅助字段且可由 SQLite
  `REAL` 十进制往返的严格 Python 负载。

- [x] **Step 1: RED — 观察审查发现**

新增工具/provider 测试，证明缺失 `food_name`、`quantity`、`unit` 被公开
schema 提前拦截；新增 Python/SQLite 测试，证明超大整数和高精度小数被静默
舍入，预览会保存包装字段。最终审查又通过真实 bridge 测试证明，不安全的
JavaScript `number` 会在进入 Python 前丢失原始精度并被错误写入。以上均
分别观察到预期断言失败。

- [x] **Step 2: GREEN — 建立执行边界诊断**

公开 schema 仅为诊断可达性把三个字段设为可选；`execute` 立即按固定顺序
本地校验并返回与 Python 相同的安全错误形状。部分包装字段统一使用
`field: package_specification`，使 Skill 能在同一动作中一次补全。

- [x] **Step 3: GREEN — 可信时间与临时字段剥离**

系统生成 `added_at` 使用可信当前时间并忽略 `context.now`。包装计算完成后
删除 `package_count`、`quantity_per_package`、`package_unit`；Python
预览边界再次防御性删除，公开预览与工作流 JSON 都不保存这些字段。

- [x] **Step 4: GREEN — Provider 与 SQLite 双层精确性边界**

插件对 `quantity`、`package_count`、`quantity_per_package` 中不安全的
JavaScript `number` 在 bridge 前返回可恢复诊断，并要求精确原始值使用
十进制字符串。Python 在任何批次、移动、事务或工作流写入前继续执行
`Decimal → float → Decimal(str(float))` 校验。不可往返时返回可恢复
`INVALID_INPUT(quantity)`。真实 plugin/provider → bridge/Python → SQLite
测试证明拒绝路径零写入，`1980` 和 `0.3` 保持成功且准确。

- [x] **Step 5: 完整验证与原子提交**

```text
npx vitest run src-tests/plugin.test.ts
..\.venv-task5\Scripts\python.exe -m pytest tests/test_pantry.py tests/test_cli.py -q
npm run build
```

Expected: 全部 PASS；版本保持 `0.4.3`，不新增数据库迁移。
