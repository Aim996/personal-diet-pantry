# 食序管家 v0.7.3.1 液体饮食兼容修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付产品版本 v0.7.3.1，使 DeepSeek/OpenClaw 能稳定生成 `per_100ml + consumed_volume_ml` 餐食调用，按一次缩放写入营养与饮水，省略时间时使用插件可信系统时间，并保持 v0.7.3 的库存、包装、FEFO、撤销和安装升级能力。

**Architecture:** 公共工具名和七类 action 不变；模型侧餐食 Schema 改成扁平、非递归的提示接口，跨字段关系由 TypeScript 入口和 Python 业务层双重确定性校验，深层食材仍由现有输入上限与 Python 递归解析保护。数据库结构和 migration 001–021 不变。产品版本保持 `0.7.3.1`；为满足 npm/OpenClaw 三段 SemVer，内部技术包版本使用 `0.7.4`，发布文件名、更新说明和导出产品版本仍使用 `0.7.3.1`。

**Tech Stack:** TypeScript 5.9、TypeBox、Vitest、OpenClaw 2026.7.1-2 / `@openclaw/ai`、Python 3.11、pytest、SQLite、PowerShell 发布门禁。

## Global Constraints

- 基线固定为已提交的 v0.7.3；不得从生产实例反向覆盖源码。
- 不新增、删除或改名七个公开工具；不改变现有 action 名。
- 不增加数据库 migration，不修改已有用户 `dataDir`，不把测试数据写入生产库。
- `nutrition_facts` 与 `nutrition_estimate` 仍必须二选一；直接营养必须带 `nutrition_basis`。
- `per_100g`、`per_100ml`、`per_serving` 分别只由 `consumed_weight_g`、`consumed_volume_ml`、`consumed_servings` 缩放一次。
- 液体 `hydration_ml` 不得超过实际饮用体积；未知时保持未知，不编造饮水量。
- 省略 `occurred_at` 时只允许插件内部可信时钟补齐；模型不得生成 `context.now` 或猜时区。
- 正式写入、库存扣减、营养证据和撤销必须处于同一个既有事务闭环。
- 先写失败测试，再做最小实现，再运行回归；每个任务独立提交。
- 正式发布前必须通过完整门禁并保持 Git 工作树干净。
- 本计划只生成本地源码与可安装发布包；不自动安装到 `192.0.2.1`，生产安装另行执行备份和授权。

---

## Task 1: 用真实 OpenClaw 归一化建立 Schema 预算并扁平化餐食项

**Files:**

- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `src/schemas.ts`
- Modify: `src-tests/schema-size.test.ts`
- Modify: `src-tests/intake-schema.test.ts`

- [x] **Step 1: 锁定与宿主相同的 Schema 归一化实现**

运行：

```powershell
npm install --save-dev --save-exact @openclaw/ai@2026.7.1-2
```

预期：`package.json` 的 `devDependencies` 出现 `@openclaw/ai: 2026.7.1-2`，`package-lock.json` 同步更新；运行依赖不增加。

- [x] **Step 2: 先写模型侧失败测试**

在 `src-tests/schema-size.test.ts` 从 `@openclaw/ai/internal/openai` 导入 `normalizeToolParameterSchema`，以以下实参生成 DeepSeek/OpenAI-compatible 模型真正看到的 Schema：

```ts
const normalized = normalizeToolParameterSchema(MealParametersSchema, {
  modelProvider: "openai",
  modelId: "deepseek-v4-flash",
});
const serialized = JSON.stringify(normalized);

expect(Buffer.byteLength(serialized, "utf8")).toBeLessThan(160_000);
expect(serialized).toContain("per_100ml");
expect(serialized).toContain("consumed_volume_ml");
expect(serialized).not.toContain('"allOf"');
```

保留“所有 meal action 仍存在”的断言；删除把 TypeBox 递归展开深度当成模型侧预算的旧断言，递归深度改由 Python 输入上限测试负责。

- [x] **Step 3: 运行失败测试并记录基线**

运行：

```powershell
npm exec vitest -- run src-tests/schema-size.test.ts
```

预期：测试因归一化 Schema 大于 160000 字节和包含 `allOf` 失败；基线约为 813 KB，证明测试能捕获本次真实问题。

- [x] **Step 4: 把 `MealItemSchema` 改成单层公开结构**

在 `src/schemas.ts`：

1. 删除 `DirectNutritionEvidenceSchema`、`MealItemBaseRef`、`mealItemDefinition*`、`MealItemDefinitions` 和 `$defs` 八层递归展开。
2. 将 `MealItemBaseSchema` 改为一个 `strictObject`；保留已有公开字段名。
3. `nutrition_basis`、`nutrition_facts`、`nutrition_estimate` 均为可选字段，关系校验移到 Task 2。
4. 三个 consumed measure 使用可选 `PositiveQuantitySchema`，阻止零值进入缩放。
5. `ingredients` 使用 `Type.Optional(Type.Array(Type.Unknown(), { maxItems: MAX_INGREDIENT_CHILDREN }))`，不在模型 Schema 内递归内联。
6. `CookingDishSchema.ingredients` 与普通餐食顶层 `items` 继续使用同一个扁平 `MealItemSchema`。
7. 删除只为 `$defs` 服务的赋值；保持 `MAX_MEAL_ITEMS=100`、`MAX_INGREDIENT_CHILDREN=50`。

- [x] **Step 5: 更新源 Schema 的职责测试**

在 `src-tests/intake-schema.test.ts` 调整断言：

- `per_100ml + consumed_volume_ml` 仍被 Schema 接受；
- 直接营养缺 basis、basis 缺 measure 在模型层允许通过，随后由 Task 2 的运行层测试拒绝；
- consumed measure 为零仍被 Schema 拒绝；
- 顶层字段仍闭合；嵌套食材的业务结构交由运行层/Python 测试；
- pantry handle 格式、preparation loss 和 ordinary/cooking update 分支继续通过原有断言。

- [x] **Step 6: 验证模型侧预算和现有餐食 Schema**

运行：

```powershell
npm exec vitest -- run src-tests/schema-size.test.ts src-tests/intake-schema.test.ts
```

预期：全部通过；归一化 UTF-8 大小 `<160000`，不存在 `allOf`，液体字段仍可见。

- [x] **Step 7: 提交 Schema 工作包**

```powershell
git add package.json package-lock.json src/schemas.ts src-tests/schema-size.test.ts src-tests/intake-schema.test.ts
git commit -m "fix: flatten model-facing meal schema"
```

---

## Task 2: 在 TypeScript 与 Python 运行层恢复严格营养关系校验

**Files:**

- Create: `src-tests/meal-normalization.test.ts`
- Modify: `src/index.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `tests/contracts/test_live_intake_regressions.py`

- [x] **Step 1: 先写 TypeScript 运行层失败测试**

直接测试已导出的 `normalizeToolPayload`。覆盖普通餐食、`record_cooking`、ordinary update 和 cooking update，并要求精确字段路径：

| 输入 | 期望字段 | reason |
| --- | --- | --- |
| facts/estimate 均存在 | `items[0].nutrition_estimate` | `incompatible` |
| 有直接营养、无 basis | `items[0].nutrition_basis` | `required` |
| 有 basis、无直接营养 | `items[0].nutrition_basis` | `incompatible` |
| `per_100g` 无正数重量 | `items[0].consumed_weight_g` | `required` |
| `per_100ml` 无正数体积 | `items[0].consumed_volume_ml` | `required` |
| `per_serving` 无正数份数 | `items[0].consumed_servings` | `required` |
| cooking 子食材错误 | `dish.ingredients[0].consumed_volume_ml` | `required` |
| update cooking 子食材错误 | `draft.dish.ingredients[0].nutrition_basis` | `required` |

再添加成功案例：`per_100ml + consumed_volume_ml`、`consumed_total`、无调用方营养但由库存解析的 item 均不返回 error。

- [x] **Step 2: 运行并确认失败**

```powershell
npm exec vitest -- run src-tests/meal-normalization.test.ts
```

预期：当前 `normalizeToolPayload` 没有营养关系校验，因此失败。

- [x] **Step 3: 实现一次遍历的确定性校验**

在 `src/index.ts`：

1. 增加统一 `invalidMealInputError(field, reason, expected)`，返回 `INVALID_INPUT`、`retryable: true`。
2. 增加 `firstMealNutritionError(items, prefix)`，按数组顺序深度优先遍历 `ingredients`，首次错误立即返回。
3. 使用现有十进制解析能力判断 measure 是否为严格正数，不使用 JavaScript 浮点重新计算营养。
4. 将 action 路由精确映射为：
   - record/preview → `items`；
   - record_cooking → `dish.ingredients`；
   - ordinary update → `draft.items`；
   - cooking update → `draft.dish.ingredients`。
5. 营养关系校验在 leftover expiry 校验之前执行；验证只读，不修改 payload。

- [x] **Step 4: 在 Python 业务边界写失败契约**

扩展 `tests/contracts/test_live_intake_regressions.py`，直接通过 `DietService.dispatch` 验证缺 basis、basis 与 evidence 不匹配、三类 basis 缺匹配 measure 都返回：

```python
assert result["ok"] is False
assert result["error"]["code"] == "INVALID_INPUT"
assert result["error"]["field"] == expected_field
assert result["error"]["reason"] in {"required", "incompatible"}
assert connection.execute("SELECT count(*) FROM meals").fetchone()[0] == 0
```

- [x] **Step 5: 让 Python 返回相同字段语义**

在 `_meal_item(..., field=...)` 内把现有字符串型 `MealValidationError` 前置成 `_ServiceError`：

- facts 与 estimate 不能同时出现；
- evidence 与 basis 必须同时存在；
- basis 枚举必须有效；
- scaling basis 必须有对应严格正数 measure；
- `consumed_total` 不要求额外 measure。

保留 `MealItemDraft` 和 nutrition normalization 的最终业务校验，不删除防御层。

- [x] **Step 6: 验证双层校验**

```powershell
npm exec vitest -- run src-tests/meal-normalization.test.ts
python -m pytest tests/contracts/test_live_intake_regressions.py -k "nutrition_basis or direct_nutrition" -q
```

预期：TypeScript 与 Python 测试全部通过，错误字段稳定且没有任何写库。

- [x] **Step 7: 提交校验工作包**

```powershell
git add src/index.ts src-tests/meal-normalization.test.ts python/personal_diet_pantry/service.py tests/contracts/test_live_intake_regressions.py
git commit -m "fix: validate meal nutrition evidence deterministically"
```

---

## Task 3: 让餐食和做饭省略时间时使用可信系统时钟

**Files:**

- Modify: `src/schemas.ts`
- Modify: `src-tests/intake-schema.test.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `tests/contracts/test_meal_water_contracts.py`
- Modify: `tests/contracts/test_natural_language_trigger_skill_contract.py`
- Modify: `skills/personal-diet-pantry/SKILL.md`

- [x] **Step 1: 先写时间默认失败测试**

新增断言：

- `diet_meal record` 和 `record_cooking` 的 TypeBox 请求省略 `occurred_at` 仍有效；
- 固定 `service._clock = lambda: datetime(2026, 8, 3, 1, 2, 3, tzinfo=timezone.utc)` 后，record 与 record_cooking 省略时间，数据库都保存 `2026-08-03T01:02:03Z`；
- 显式传入 `2026-08-03T08:30:00+08:00` 时仍尊重用户时间；
- Skill 的 system-time defaults 明确包含 `diet_meal record`、`preview_record`、`record_cooking`，并禁止模型填写 `context.now`。

- [x] **Step 2: 确认测试先失败**

```powershell
npm exec vitest -- run src-tests/intake-schema.test.ts
python -m pytest tests/contracts/test_meal_water_contracts.py tests/contracts/test_natural_language_trigger_skill_contract.py -k "system_time or trusted_clock" -q
```

预期：Schema、`_meal_draft`、`_cooking_draft` 和 Skill 规则尚未支持该默认，因此失败。

- [x] **Step 3: 修改 Schema 与 Python draft 构造**

在 `src/schemas.ts` 将 `MealDraftSchema.occurred_at`、`CookingMealDraftSchema.occurred_at` 和 `record_cooking` action 的 `occurred_at` 改为 `Type.Optional(DateTimeSchema)`。

在 `service.py` 的 `_meal_draft`、`_cooking_draft` 使用与 `_meal_record_prepared` 相同的规则：字段存在则 `_datetime_value`，否则使用调用方已传入的 `now`。不得读取 `payload.context.now`。

- [x] **Step 4: 按 writing-skills 规则最小修改 Skill**

只在 `SKILL.md` 的 `System-time defaults` 列表加入餐食记录和做饭；不重复营养算法、不扩大主 Skill 超过 500 行。文字明确：用户未说时间时省略字段，由插件可信时钟补齐；用户明确说时间时传递明确时间。

- [x] **Step 5: 验证时间与 Skill 契约**

```powershell
npm exec vitest -- run src-tests/intake-schema.test.ts
python -m pytest tests/contracts/test_meal_water_contracts.py tests/contracts/test_natural_language_trigger_skill_contract.py -q
python scripts/validate_skill.py
```

预期：全部通过；未出现模型猜测的时区或伪造 `context.now`。

- [x] **Step 6: 提交可信时间工作包**

```powershell
git add src/schemas.ts src-tests/intake-schema.test.ts python/personal_diet_pantry/service.py tests/contracts/test_meal_water_contracts.py tests/contracts/test_natural_language_trigger_skill_contract.py skills/personal-diet-pantry/SKILL.md
git commit -m "fix: default meal timestamps from trusted clock"
```

---

## Task 4: 增加“盒装豆浆 → 定向搜索 → 喝一盒 → 撤销”整链路回归

**Files:**

- Modify: `tests/contracts/test_live_intake_regressions.py`
- Modify: `contracts/v070-core-tests.txt`

- [x] **Step 1: 写完整公开行为测试**

新增 `test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo`：

1. 固定可信时钟；
2. 通过 `diet_pantry add` 入库 2 盒 × 250ml 豆浆，保留 `display_quantity`、`display_unit`、`base_quantity_per_display_unit`，营养档案 basis 为 `per_100ml`；
3. 通过 `diet_pantry search(search_text="豆浆", unit="ml")` 取得真实 `inventory_match_handle`；
4. 通过 `diet_meal record` 记录喝一盒，省略 `occurred_at`，传 `consumed_volume_ml=250`、`nutrition_basis=per_100ml`，标签每 100ml 为 33 kcal、3.5g 蛋白、95ml hydration；
5. 断言餐食为 82.5 kcal、8.75g 蛋白、237.5ml hydration，证据 scale factor 为 2.5，`consumed_weight_g IS NULL`；
6. 断言库存只从 500ml 减到 250ml，包装展示为剩 1 盒；
7. 用 `diet_transaction get_recent(operation="undo", operation_type="meal_record")` 取得公开 `operation_handle`，再调用 `diet_transaction undo`；
8. 断言餐食、营养证据和本次 hydration 聚合消失，库存恢复 500ml/2 盒。

- [x] **Step 2: 先运行并观察真实缺口**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py::test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo -q
```

预期：在 Task 1–3 前失败；完成前述任务后无需特例代码即可通过。若失败，只修复触发失败的通用路径，不为测试硬编码豆浆。

- [x] **Step 3: 把整链路纳入不超过 30 项的核心门禁**

在 `contracts/v070-core-tests.txt` 用该新测试替换较窄的 `test_selected_product_handle_avoids_requery_and_deducts_only_chosen_sku` 门禁项；旧测试仍保留在完整测试套件。把文件首行更新为 v0.7.3.1，但继续保持最多 30 项。

- [x] **Step 4: 运行液体、库存、撤销回归组**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py tests/contracts/test_inventory_search_contracts.py tests/contracts/test_pantry_transaction_contracts.py -q
```

预期：豆浆、牛奶、多液体、商品句柄、包装单位、FEFO、撤销/重做全部通过。

- [x] **Step 5: 提交整链路工作包**

```powershell
git add tests/contracts/test_live_intake_regressions.py contracts/v070-core-tests.txt
git commit -m "test: cover packaged liquid meal lifecycle"
```

---

## Task 5: 发布 v0.7.3.1 文档并建立四段产品版本兼容层

**Files:**

- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `pyproject.toml`
- Modify: `openclaw.plugin.json`
- Modify: `python/personal_diet_pantry/__init__.py`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/data_import.py`
- Modify: `src-tests/version-contract.test.ts`
- Modify: `src-tests/package-contents.test.ts`
- Modify: `tests/test_version_contract.py`
- Modify: `tests/test_build_release.py`
- Modify: `tests/integration/test_installable_e2e.py`
- Modify: `scripts/build_release.py`
- Modify: `ci/verify.ps1`
- Create: `UPDATE-v0.7.3.1.zh-CN.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `RELEASE.zh-CN.md`
- Modify: `CONTEXT.md`
- Modify: `docs/ARCHITECTURE.zh-CN.md`
- Modify: `docs/DATA-MODEL.zh-CN.md`
- Modify: `docs/INSTALLATION.zh-CN.md`
- Modify: `docs/TOOLS-REFERENCE.zh-CN.md`
- Modify: `docs/TROUBLESHOOTING.zh-CN.md`

- [x] **Step 1: 先把版本契约改成失败状态**

测试中固定以下双版本合同：

```text
package/openclaw/pyproject technical version = 0.7.4
package productVersion / release / export version = 0.7.3.1
```

要求安装包名为 `personal-diet-pantry-0.7.3.1-installable.tgz`，测试摘要为 `TEST-SUMMARY-v0.7.3.1.zh-CN.md`，包内包含 `UPDATE-v0.7.3.1.zh-CN.md`。运行版本测试并确认当前 v0.7.3 失败。

- [x] **Step 2: 实现技术版本与产品版本分离**

1. `package.json`、lockfile、`pyproject.toml`、`openclaw.plugin.json`、Python `__version__` 设为合法且高于 0.7.3 的 `0.7.4`。
2. `package.json.productVersion` 设为 `0.7.3.1`。
3. Python 增加 `__product_version__ = "0.7.3.1"`，服务导出 manifest 使用它，不再把技术包版本误当产品版本。
4. `SUPPORTED_PRODUCT_VERSIONS` 加入 `0.7.3.1`，保留所有既有兼容版本。
5. `scripts/build_release.py` 使用 `VERSION="0.7.4"` 校验内部包，使用 `PRODUCT_VERSION="0.7.3.1"` 生成发布目录文件名、摘要名与更新说明路径。

- [x] **Step 3: 编写 v0.7.3.1 更新说明**

`UPDATE-v0.7.3.1.zh-CN.md` 必须说明：

- 根因是 OpenClaw 模型侧归一化把递归 `allOf/anyOf` 扩大到约 813 KB；
- 修复后模型侧预算 `<160000` bytes 且保留 `per_100ml`；
- 跨字段校验移动到确定性运行层，没有降低验证强度；
- 可信系统时间、液体单次缩放、hydration 上限、库存与公开撤销的行为；
- 无新 migration，继续复用同一个 `diet.sqlite` 和原 `dataDir`；
- 可直接回退到 v0.7.3 代码，但仍建议安装前冷备份；
- 构建发布不等于部署，不修改 OpenClaw、软路由或生产数据。

- [x] **Step 4: 更新当前文档而不篡改历史文档**

将当前 README、RELEASE、安装、工具、排障、架构和数据模型页的“当前版本”更新为 v0.7.3.1；保留 `UPDATE-v0.7.3.zh-CN.md` 和旧设计/计划作为历史记录。迁移 021 必须表述为“沿用 v0.7.3 已有迁移”，不能写成 v0.7.3.1 新增迁移。

- [x] **Step 5: 更新发布器和安装包测试**

同步 `scripts/build_release.py` 的 GitHub 文档集，加入本设计、本文计划和新更新说明。更新发布器、包内容、安装后 Python import、版本一致性与冷回退边界测试；不要削弱“发布目录位于工作树外”“必须干净提交”“恰好六项顶层内容”“可重复构建”和敏感内容扫描。

- [x] **Step 6: 运行版本和发布器测试**

```powershell
npm exec vitest -- run src-tests/version-contract.test.ts src-tests/package-contents.test.ts
python -m pytest tests/test_version_contract.py tests/test_build_release.py tests/integration/test_installable_e2e.py -q
```

预期：双版本合同、产品文件名、包内容和独立安装启动全部通过。

- [x] **Step 7: 提交发布文档工作包**

```powershell
git add package.json package-lock.json pyproject.toml openclaw.plugin.json python/personal_diet_pantry/__init__.py python/personal_diet_pantry/service.py python/personal_diet_pantry/data_import.py src-tests/version-contract.test.ts src-tests/package-contents.test.ts tests/test_version_contract.py tests/test_build_release.py tests/integration/test_installable_e2e.py scripts/build_release.py ci/verify.ps1 UPDATE-v0.7.3.1.zh-CN.md README.md README.en.md RELEASE.zh-CN.md CONTEXT.md docs/ARCHITECTURE.zh-CN.md docs/DATA-MODEL.zh-CN.md docs/INSTALLATION.zh-CN.md docs/TOOLS-REFERENCE.zh-CN.md docs/TROUBLESHOOTING.zh-CN.md docs/superpowers/specs/2026-08-03-personal-diet-pantry-v0.7.3.1-liquid-schema-compat-design.md docs/superpowers/plans/2026-08-03-personal-diet-pantry-v0.7.3.1-liquid-schema-compat.md
git commit -m "release: prepare personal diet pantry v0.7.3.1"
```

---

## Task 6: 完整复检、可重复构建并交付 E 盘发布目录

**Files:**

- Verify: all tracked source and test files
- Produce outside Git worktree: `C:\path\to\personal-diet-pantry\0.7.3.1\`

- [ ] **Step 1: 确认提交态干净且版本正确**

```powershell
git status --short
git log -5 --oneline
```

预期：`git status --short` 无输出，最近提交包含本计划的四个实现工作包和发布提交。

- [ ] **Step 2: 运行完整门禁**

```powershell
$env:PDP_PYTHON = 'C:\Users\example-user\Documents\Skill\.venv-task5\Scripts\python.exe'
& .\ci\verify.ps1
```

预期：生成契约检查、Skill 路由评估、敏感内容扫描、30 项核心门禁、完整 Python/Vitest、TypeScript 构建、Skill 校验、发布审计、npm dry-run、bridge/upgrade/installable E2E、依赖审计全部通过。

- [ ] **Step 3: 若门禁修复产生改动，重新提交并从头验证**

只修复已定位的根因；不得跳过、放宽或删除失败测试。修复后提交原子 commit，再重新运行完整 `ci/verify.ps1`，直至干净提交全绿。

- [ ] **Step 4: 从干净 HEAD 构建正式发布目录**

先确认目标目录不存在，再运行：

```powershell
$ReleaseRoot = 'C:\path\to\personal-diet-pantry\0.7.3.1'
if (Test-Path -LiteralPath $ReleaseRoot) { throw "Release destination already exists" }
& $env:PDP_PYTHON scripts\build_release.py --project-root . --release-root $ReleaseRoot
```

预期：发布器原子创建目录，顶层恰好为：

```text
personal-diet-pantry-0.7.3.1-source.tar.gz
personal-diet-pantry-0.7.3.1-installable.tgz
release-manifest.json
TEST-SUMMARY-v0.7.3.1.zh-CN.md
SHA256SUMS
GitHub文档/
```

- [ ] **Step 5: 校验最终证据**

```powershell
Get-Content -LiteralPath 'C:\path\to\personal-diet-pantry\0.7.3.1\release-manifest.json' -Encoding UTF8
Get-Content -LiteralPath 'C:\path\to\personal-diet-pantry\0.7.3.1\TEST-SUMMARY-v0.7.3.1.zh-CN.md' -Encoding UTF8
Get-Content -LiteralPath 'C:\path\to\personal-diet-pantry\0.7.3.1\SHA256SUMS' -Encoding UTF8
git status --short
```

预期：manifest 标明 technical `0.7.4` / product `0.7.3.1`、构建提交号和两个归档哈希；摘要失败数为 0；SHA256 覆盖规定文件；源码工作树仍干净。

- [ ] **Step 6: 交付但不自动部署生产**

交付说明必须列出：修复内容、实际测试数、发布目录、安装包 SHA-256、未做数据库迁移、未触碰生产 `dataDir`。若用户随后要求安装到软路由，先对现有实例做一致冷备份，再安装该 installable 包并运行 `diet_system self_check` 与豆浆 UAT。

---

## Definition of Done

- [ ] DeepSeek/OpenClaw 归一化后的 meal Schema `<160000` UTF-8 bytes、无 `allOf`，仍包含 `per_100ml` 与 `consumed_volume_ml`。
- [ ] 模型侧能够一次生成液体参数，运行层对缺 basis/measure 给出精确字段错误且零写入。
- [ ] 250ml 液体只按 2.5 倍缩放，hydration 不超过 250ml且只进入汇总一次。
- [ ] 省略餐食时间使用可信系统时钟，显式用户时间仍被保留。
- [ ] 盒装豆浆完整通过入库、搜索、记录、营养、饮水、扣库存和公开撤销。
- [ ] v0.7.3 的包装单位、FEFO、多批次、剩菜、幂等、纠正和撤销回归无退化。
- [ ] 数据库仍停留在 migration 021，无 schema 或用户数据迁移。
- [ ] 产品版本为 v0.7.3.1，内部技术版本为合法 SemVer 0.7.4，安装包可被 OpenClaw 接受。
- [ ] 完整门禁全绿，正式发布目录可重复构建、哈希完整、源码工作树干净。
