# 食序管家 v0.7.3.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付产品版本 v0.7.3.2，使 OpenClaw 能以“搜索库存 → 选择句柄 → 喝一盒”的最小参数完成包装换算、部分营养记录、FEFO 扣减与撤销闭环。

**Architecture:** 保留现有七个工具、公开 action 和 migration 001–021。搜索阶段把包装、营养和批次版本投影写入现有 workflow JSON；餐食入口使用句柄确定性换算展示单位，随后复用现有 meal preview/commit 事务。营养总计采用逐字段未知传播，不再要求完整 C/D 估算。

**Tech Stack:** TypeScript 5.9、TypeBox、Vitest 3.2、OpenClaw 2026.7.1-2、Python 3.11、pytest、SQLite、PowerShell。

## Global Constraints

- 源码工作目录固定为 `0.7.3.2/personal-diet-pantry`；只从已审计的 `0.7.3.1/personal-diet-pantry-0.7.3.1-source.tar.gz` 解包，不修改 v0.7.3.1 发布物。
- 产品版本使用 `0.7.3.2`；npm/OpenClaw/Python 技术包版本使用 `0.7.5`。
- 不增加、删除或改名七个公开工具；不改变已有公开 action 名称。
- 不增加 migration 022；数据库继续使用 migration 001–021。
- `unit` 仍为包装入库必填基础单位；仅允许根据包装三元组推导缺省 `quantity`。
- 未知营养保留 `null`；不得用低等级估算填补高等级标签缺失字段。
- 不安装到当前真实 OpenClaw，不修改真实用户数据库。
- 每项生产代码变更必须先有在 v0.7.3.1 上按预期失败的测试，再做最小实现。

---

## File Map

- `python/personal_diet_pantry/service.py`：搜索候选公开投影、选择句柄快照与校验、展示单位规范化、update 时间继承。
- `python/personal_diet_pantry/meals.py`：部分营养传播、营养状态与证据持久化、餐食总计未知语义。
- `python/personal_diet_pantry/nutrition.py`：允许对完整或部分营养结果进行确定性缩放。
- `src/index.ts`：包装入库参数推导、冲突检测和 query 参数透传。
- `src-tests/package-semantics-schema.test.ts` 与 `src-tests/pantry-search-schema.test.ts`：TypeScript 工具边界回归。
- `tests/contracts/test_inventory_search_contracts.py`：搜索包装投影、句柄快照与失效回归。
- `tests/contracts/test_live_intake_regressions.py`：真实“1盒 + handle”闭环回归。
- `tests/contracts/test_meal_water_contracts.py`：部分/未知营养和修正事务回归。
- `skills/personal-diet-pantry/SKILL.md` 与相关 references：最小调用模板和未知营养规则。
- `scripts/evaluate_skill.py`、`scripts/lint_skill.py`、`tests/test_skill_evaluation.py`：静态 lint 与行为轨迹校验分离。
- `package.json`、`package-lock.json`、`openclaw.plugin.json`、Python 版本文件和发布文档：版本与发布一致性。

---

### Task 1: 建立 v0.7.3.2 可写基线

**Files:**

- Create: `0.7.3.2/personal-diet-pantry/**`
- Source: `0.7.3.1/personal-diet-pantry-0.7.3.1-source.tar.gz`
- Verify: `0.7.3.1/SHA256SUMS`

**Interfaces:**

- Consumes: 已审计 v0.7.3.1 源码包和 SHA256 清单。
- Produces: 未修改的 v0.7.3.1 源码副本，作为后续任务唯一工作根目录。

- [ ] **Step 1: 校验源码包哈希**

Run:

```powershell
Get-FileHash -Algorithm SHA256 .\0.7.3.1\personal-diet-pantry-0.7.3.1-source.tar.gz
Get-Content .\0.7.3.1\SHA256SUMS
```

Expected: 源码包哈希为 `967BB0FF0E8ED9B0C2B5D88CEDE39C25A5BA3AD4834FACB5C52BC1F106B3C876`。

- [ ] **Step 2: 解包到版本工作目录**

Run:

```powershell
New-Item -ItemType Directory -Force .\0.7.3.2
tar -xzf .\0.7.3.1\personal-diet-pantry-0.7.3.1-source.tar.gz -C .\0.7.3.2
```

Expected: `0.7.3.2/personal-diet-pantry/package.json` 存在，版本仍为基线 `0.7.4 / 0.7.3.1`。

- [ ] **Step 3: 运行基线 Python 与 TypeScript 测试**

Run from `0.7.3.2/personal-diet-pantry`:

```powershell
python -m pytest -q
npm test -- --run
npm run build
```

Expected: 使用已配置依赖环境时 Python 417+ 项核心测试和 TypeScript 66 项测试通过；仅环境依赖缺失不得误报为产品失败。

- [ ] **Step 4: 提交不可变基线**

```powershell
git add 0.7.3.2/personal-diet-pantry
git commit -m "chore: stage v0.7.3.2 source baseline"
```

---

### Task 2: 搜索返回包装投影并签发完整句柄

**Files:**

- Modify: `python/personal_diet_pantry/service.py:1428-1545`
- Modify: `python/personal_diet_pantry/service.py:2000-2100`
- Modify: `python/personal_diet_pantry/service.py:5608-5668`
- Test: `tests/contracts/test_inventory_search_contracts.py`

**Interfaces:**

- Consumes: `pantry._query_batch_targets(...)`、`nutrition_profiles.linked_product_nutrition(...)`、现有 `operation_previews`。
- Produces: `_PantryPackageProjection`、`_pantry_product_reference_snapshot(...)`、`_validate_pantry_product_reference(...)`；公开候选增加规范包装字段。

- [ ] **Step 1: 写包装投影和句柄快照失败测试**

Add tests equivalent to:

```python
def _stored_workflow(service, handle):
    token_hash = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    row = service.connection.execute(
        "SELECT result_json, resource_versions_json FROM operation_previews WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    assert row is not None
    return json.loads(row["result_json"]), json.loads(row["resource_versions_json"])


def _add_two_box_soy(service):
    result = _pantry(service, "add", {
        "food_name": "小象无糖豆浆",
        "normalized_name": "小象无糖豆浆",
        "quantity": "500",
        "unit": "ml",
        "display_quantity": "2",
        "display_unit": "盒",
        "base_quantity_per_display_unit": "250",
        "source_text": "买了两盒小象无糖豆浆，每盒250毫升",
        "nutrition_profile": {
            "normalized_name": "小象无糖豆浆",
            "serving_basis": "per_100ml",
            "nutrition": {
                "calories_kcal": "33", "protein_g": "3.5",
                "fat_g": "1.8", "carbohydrate_g": "2",
                "fiber_g": None, "sodium_mg": None,
            },
            "source_text": "包装营养标签", "source_grade": "A",
        },
    })
    assert result["ok"] is True


def test_search_projects_uniform_package_and_binds_private_snapshot(service):
    _add_two_box_soy(service)

    result = _pantry(service, "search", {
        "search_text": "豆浆",
        "unit": "ml",
        "nutrition_mode": "none",
    })

    candidate = result["data"]["candidates"][0]
    assert candidate["remaining_display_quantity"] == "2"
    assert candidate["display_unit"] == "盒"
    assert candidate["base_quantity_per_display_unit"] == "250"
    assert candidate["nutrition_status"] == "not_requested"
    assert "nutrition" not in candidate

    handle = candidate["workflow"]["inventory_match_handle"]
    assert re.fullmatch(r"wfh_[a-z0-9_-]+", handle)
    stored, versions = _stored_workflow(service, handle)
    assert stored["base_unit"] == "ml"
    assert stored["package"]["status"] == "uniform"
    assert stored["nutrition"]["snapshot"]["fiber_g"] is None
    assert len(versions) == 1
    assert versions[0]["batch_id"] > 0
    assert versions[0]["version"] == 1
```

Add separate tests for `partial`, `mixed`, and `none`; these states must not expose a guessed `remaining_display_quantity`.

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
python -m pytest tests/contracts/test_inventory_search_contracts.py -k "package_and_binds or package_projection" -vv
```

Expected: FAIL because候选缺少包装字段，句柄只含 `normalized_name/unit`，且 token 可能含大写字符。

- [ ] **Step 3: 实现最小包装投影**

Add an immutable internal projection:

```python
@dataclass(frozen=True)
class _PantryPackageProjection:
    status: str
    remaining_display_quantity: Decimal | None
    display_unit: str | None
    base_quantity_per_display_unit: Decimal | None
    package_hierarchy: tuple[Mapping[str, str], ...] | None
    resource_versions: tuple[tuple[int, int], ...]
```

Build it from eligible `_query_batch_targets` for the candidate name/base unit. Return `uniform` only when every eligible batch has the same display unit, conversion factor, and hierarchy. Sum base remaining quantity and divide once by the shared factor.

- [ ] **Step 4: 把营养与包装写入句柄但按 mode 控制公开响应**

Issue the workflow with this shape:

```python
result={
    "normalized_name": candidate.normalized_name,
    "base_unit": candidate.unit,
    "package": _package_projection_payload(package_projection),
    "nutrition": _nutrition_projection_payload(
        nutrition_profiles.linked_product_nutrition(...)
    ),
},
resource_versions=[
    {"batch_id": batch_id, "version": version}
    for batch_id, version in package_projection.resource_versions
],
```

Use `secrets.token_hex(24)` for `_issue_workflow`, producing lowercase handles without weakening entropy.

- [ ] **Step 5: 校验句柄绑定资源版本**

Change `_pantry_product_reference(...)` to return the stored snapshot and compare all stored batch versions with current rows. Missing, version-changed, ineligible, or zero-stock rows raise `_ServiceError("STALE_PREVIEW", ...)`.

- [ ] **Step 6: 运行目标测试和搜索契约集**

```powershell
python -m pytest tests/contracts/test_inventory_search_contracts.py -vv
```

Expected: 全部通过。

- [ ] **Step 7: 提交搜索与句柄工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/python/personal_diet_pantry/service.py 0.7.3.2/personal-diet-pantry/tests/contracts/test_inventory_search_contracts.py
git commit -m "fix: preserve package facts in pantry handles"
```

---

### Task 3: 以句柄把“1盒”规范化为基础量

**Files:**

- Modify: `python/personal_diet_pantry/service.py:4520-4705`
- Test: `tests/contracts/test_live_intake_regressions.py:543-705`
- Test: `tests/contracts/test_inventory_search_contracts.py`

**Interfaces:**

- Consumes: Task 2 的句柄快照结构。
- Produces: `_meal_item_from_pantry_reference(...)`，返回基础 `amount/unit`、消费 measure 和保留的 `portion_expression`。

- [ ] **Step 1: 把现有盒装豆浆测试改成真实最小参数**

Replace the over-fitted meal item with:

```python
{
    "raw_name": "一盒小象无糖豆浆",
    "normalized_name": product_name,
    "amount": "1",
    "unit": "盒",
    "portion_expression": "1盒",
    "inventory_match_handle": candidate["workflow"]["inventory_match_handle"],
}
```

Delete manually supplied `consumed_volume_ml`、`nutrition_basis`、`nutrition_dataset_version` and `nutrition_facts` from this test.

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py::test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo -vv
```

Expected: FAIL with `identity_mismatch` because `盒` differs from stored base unit `ml`。

- [ ] **Step 3: 实现句柄单位换算**

In `_meal_item`, resolve the stored reference before constructing `MealItemDraft`:

```python
base_amount, base_unit = _pantry_reference_base_amount(
    reference,
    amount=_optional_decimal(value.get("amount"), "amount"),
    unit=_optional_text(value.get("unit"), "unit"),
    field=field,
)
```

The helper must:

- accept an already matching base unit;
- accept the stored uniform display unit;
- multiply with `Decimal` only;
- reject missing/mixed package facts with field-level `INVALID_INPUT`;
- set exactly one of `consumed_weight_g`、`consumed_volume_ml`、`consumed_servings` when the caller omitted it;
- reject a caller-supplied consumed measure that contradicts the derived base amount.

Persist `amount=base_amount` and `unit=base_unit`; keep `raw_name` and `portion_expression="1盒"` for presentation.

- [ ] **Step 4: 验证同单位、错误单位和矛盾 measure**

Add tests asserting:

```python
assert record_with(handle, amount="250", unit="ml")["ok"] is True
assert record_with(handle, amount="1", unit="瓶")["error"]["field"] == "items[0].unit"
assert record_with(handle, amount="1", unit="盒", consumed_volume_ml="200")["error"]["reason"] == "incompatible"
```

- [ ] **Step 5: 运行主链路测试**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py::test_packaged_soy_meal_uses_volume_hydration_inventory_and_public_undo tests/contracts/test_inventory_search_contracts.py -vv
```

Expected: `1盒` 被规范化为 `250ml`，库存扣减和撤销通过。

- [ ] **Step 6: 提交单位换算工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/python/personal_diet_pantry/service.py 0.7.3.2/personal-diet-pantry/tests/contracts
git commit -m "fix: resolve pantry display units in meal records"
```

---

### Task 4: 允许部分与未知营养原子落库

**Files:**

- Modify: `python/personal_diet_pantry/nutrition.py:160-190`
- Modify: `python/personal_diet_pantry/meals.py:960-1065`
- Modify: `python/personal_diet_pantry/meals.py:1680-1830`
- Modify: `python/personal_diet_pantry/meals.py:2040-2140`
- Modify: `python/personal_diet_pantry/meals.py:2565-2800`
- Modify: `python/personal_diet_pantry/meals.py:3350-3610`
- Test: `tests/contracts/test_live_intake_regressions.py`
- Test: `tests/contracts/test_meal_water_contracts.py`

**Interfaces:**

- Consumes: `nutrition_resolution.merge_sources(...)` 的 `complete/partial/incomplete` 结果。
- Produces: 可包含空营养字段的 meal/item/evidence，准确的 `nutrition_status` 和 `nutrition_missing_fields_json`。

- [ ] **Step 1: 写部分营养失败测试**

Use an A-grade label missing fiber and sodium, then assert:

```python
assert stored_item["calories"] == "82.5"
assert stored_item["protein"] == "8.75"
assert stored_item["fiber"] is None
assert stored_item["sodium"] is None
assert stored_meal["nutrition_status"] == "partial"
assert json.loads(stored_meal["nutrition_missing_fields_json"]) == ["fiber", "sodium"]
assert stored_meal["total_fiber"] is None
assert stored_meal["total_sodium"] is None
assert service.connection.execute(
    "SELECT count(*) FROM meal_items WHERE nutrition_source LIKE '%estimate%'"
).fetchone()[0] == 0
```

Add a fully unknown restaurant item test; it records with all nutrient totals `NULL`, `nutrition_status=incomplete`, and no fake evidence row.

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py -k "packaged_soy or partial_nutrition or unknown_nutrition" -vv
```

Expected: FAIL with `NUTRITION_ESTIMATE_REQUIRED`。

- [ ] **Step 3: 移除完整营养硬门槛**

In `_prepare_item`, use:

```python
resolution = nutrition_resolution.merge_sources(...)
nutrition = (
    None
    if resolution.status == "incomplete"
    and all(getattr(resolution.result, field) is None
            for field in (*nutrition_resolution.CORE_FIELDS, "hydration_ml"))
    else resolution.result
)
```

Remove both `raise NutritionEstimateRequired(...)` branches and the dish-only completeness guard. Keep low-confidence confirmation only for genuinely uncertain identity/quantity, not missing nutrition.

- [ ] **Step 4: 让缩放和证据接受部分结果**

Change `scale_nutrition` to accept `NutritionFacts | NutritionResult`, multiplying known fields and preserving `None`。Change `_PreparedNutritionEvidence.input_facts` to the same union and make `_facts_payload` encode nullable fields with `_optional_decimal_payload`。

When nutrition is partial and not direct model evidence, persist consumed-total evidence with `calculation_status="valid"` and `provenance_status="partial"`; when no nutrient or hydration field is known, do not create an evidence row.

- [ ] **Step 5: 按字段传播总计未知并写入状态**

Replace `_nutrient_total` with:

```python
def _nutrient_total(items, field_name):
    values = [getattr(item.public, field_name) for item in items]
    if not values or any(value is None for value in values):
        return None
    return sum(values, Decimal("0"))
```

In `_insert_prepared_meal`, derive:

```python
missing = [field for field in CORE_FIELDS if payload.get(f"total_{field}") is None]
status = "complete" if not missing else "incomplete" if len(missing) == len(CORE_FIELDS) else "partial"
```

Persist `status` and canonical JSON `missing`; remove the final complete-total exception.

- [ ] **Step 6: 运行营养、餐食和豆浆闭环测试**

```powershell
python -m pytest tests/contracts/test_live_intake_regressions.py tests/contracts/test_meal_water_contracts.py tests/test_nutrition_normalization.py -vv
```

Expected: 完整、部分和未知营养场景全部通过，豆浆撤销恢复全部状态。

- [ ] **Step 7: 提交部分营养工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/python/personal_diet_pantry/nutrition.py 0.7.3.2/personal-diet-pantry/python/personal_diet_pantry/meals.py 0.7.3.2/personal-diet-pantry/tests
git commit -m "fix: preserve unknown nutrition fields"
```

---

### Task 5: 统一包装入库和查询边界

**Files:**

- Modify: `src/index.ts:530-685`
- Test: `src-tests/package-semantics-schema.test.ts`
- Test: `src-tests/pantry-search-schema.test.ts`

**Interfaces:**

- Consumes: 规范包装三元组和必填基础 `unit`。
- Produces: 缺省 `quantity` 的精确推导、矛盾字段错误、`storage_location` 查询透传。

- [ ] **Step 1: 写 TypeScript 失败测试**

```ts
it("derives pantry quantity from canonical package facts", () => {
  const result = normalizeToolPayload("pantry", "add", {
    food_name: "豆浆",
    unit: "ml",
    display_quantity: "2",
    display_unit: "盒",
    base_quantity_per_display_unit: "250",
  }, {});
  expect(result.error).toBeUndefined();
  expect(result.payload.quantity).toBe("500");
});

it("rejects a conflicting explicit base quantity", () => {
  const result = normalizeToolPayload("pantry", "add", {
    food_name: "豆浆", quantity: "400", unit: "ml",
    display_quantity: "2", display_unit: "盒",
    base_quantity_per_display_unit: "250",
  }, {});
  expect(result.error?.field).toBe("quantity");
});

it("preserves query storage_location", () => {
  const result = normalizeToolPayload("pantry", "query", {
    food_name: "豆浆", storage_location: "fridge",
  }, {});
  expect(result.payload.storage_location).toBe("fridge");
});
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
npm exec vitest -- run src-tests/package-semantics-schema.test.ts src-tests/pantry-search-schema.test.ts
```

Expected: 缺 `quantity` 被拒绝、冲突被静默修正、`storage_location` 被删除。

- [ ] **Step 3: 重新排序 add 规范化**

Validate `food_name` and `unit` first. If package facts are complete, calculate exact quantity before the required-field scan. When explicit `quantity` is present and differs, return `invalidMealInputError("quantity", "incompatible", "display_quantity × base_quantity_per_display_unit")` instead of correction warning.

- [ ] **Step 4: 保留查询位置筛选**

Delete only `food_name` after copying it to `normalized_name`; do not delete `storage_location`。

- [ ] **Step 5: 运行 TypeScript 测试和构建**

```powershell
npm test -- --run
npm run build
```

Expected: 全部通过且无 TypeScript 错误。

- [ ] **Step 6: 提交适配器工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/src/index.ts 0.7.3.2/personal-diet-pantry/src-tests
git commit -m "fix: align pantry package input contracts"
```

---

### Task 6: 修正餐食时继承原发生时间

**Files:**

- Modify: `python/personal_diet_pantry/service.py:877-920`
- Test: `tests/contracts/test_meal_water_contracts.py`
- Test: `tests/contracts/test_live_intake_regressions.py`

**Interfaces:**

- Consumes: `_meal_target(...)` 选中的原餐食和 update draft。
- Produces: `_meal_update_draft_values(...)`，只在缺省时注入原 `occurred_at`。

- [ ] **Step 1: 写发生时间和回滚失败测试**

Record at `2026-08-03T03:46:50Z`, update 2 eggs to 3 without `occurred_at`, then assert the active record keeps `03:46:50Z` and inventory net deduction is 3。Add a forced insufficient-stock update and assert old meal/inventory remain unchanged.

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
python -m pytest tests/contracts/test_meal_water_contracts.py -k "update and occurred_at" -vv
```

Expected: 当前实现把时间改为 update 的系统时间。

- [ ] **Step 3: 在服务层继承原时间**

After `_meal_target`, read the selected active meal. Copy `draft_values` and set:

```python
if "occurred_at" not in draft:
    draft["occurred_at"] = original_meal.occurred_at.isoformat()
```

Do not change `meals.update_meal/update_cooking` transaction structure.

- [ ] **Step 4: 运行修正与事务回归**

```powershell
python -m pytest tests/contracts/test_meal_water_contracts.py tests/contracts/test_live_intake_regressions.py -k "update or correction or packaged_soy" -vv
```

Expected: 时间继承、库存 reconciliation 和失败回滚均通过。

- [ ] **Step 5: 提交修正工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/python/personal_diet_pantry/service.py 0.7.3.2/personal-diet-pantry/tests/contracts
git commit -m "fix: preserve meal occurrence time on correction"
```

---

### Task 7: 精简 Skill 调用模板并建立真实轨迹门禁

**Files:**

- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `skills/personal-diet-pantry/references/pantry.md`
- Modify: `skills/personal-diet-pantry/references/meal-and-nutrition.md`
- Create: `scripts/lint_skill.py`
- Create: `scripts/validate_behavior_trace.py`
- Create: `tests/fixtures/traces/packaged-soy-one-box.json`
- Create: `tests/test_skill_evaluation.py`
- Modify: `tests/test_skill_progressive_disclosure.py`

**Interfaces:**

- Consumes: 修复后的公开契约与脱敏 OpenClaw JSON 轨迹。
- Produces: 静态 lint 和行为 trace validator 两种明确不同的门禁。

- [ ] **Step 1: 写 Skill 文本和轨迹验证失败测试**

Assert that the active guidance contains the exact minimum templates:

```text
pantry add: food_name + unit + display_quantity + display_unit + base_quantity_per_display_unit
meal from pantry: raw_name + normalized_name + amount + unit + inventory_match_handle
```

Assert it says missing label nutrients remain unknown and forbids model-filled estimates merely to satisfy a write.

Create a trace fixture with user input, one search, one meal record, final reply, timing, and before/after/undo DB assertions. The validator must fail if `meal record` contains manual `nutrition_facts` or if more than one normal record attempt occurs.

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
python -m pytest tests/test_skill_evaluation.py tests/test_skill_progressive_disclosure.py -vv
```

Expected: 当前静态 evaluator 不读取工具轨迹，旧模板仍鼓励手工补字段。

- [ ] **Step 3: 分离静态 lint 和轨迹验证**

Move existing static checks to `lint_skill.py` without changing their semantics. `validate_behavior_trace.py` must parse the fixture and assert tool order, arguments, result status, final reply count, elapsed time presence, database changes, and undo cleanup.

- [ ] **Step 4: 更新 Skill 的渐进披露内容**

Keep `SKILL.md` as routing and invariant guidance; put exact pantry and meal templates in their existing references. Do not add duplicate field vocabularies. Keep total `SKILL.md` under 20,000 UTF-8 bytes and remove the bare-number rule that writes `105` directly as weight.

- [ ] **Step 5: 运行 Skill 校验与行为轨迹门禁**

```powershell
python scripts/lint_skill.py
python scripts/validate_behavior_trace.py tests/fixtures/traces/packaged-soy-one-box.json
python -m pytest tests/test_skill_evaluation.py tests/test_skill_progressive_disclosure.py -vv
```

Expected: 静态 lint 与行为轨迹分别通过，输出名称不再混淆。

- [ ] **Step 6: 提交 Skill 与轨迹工作区**

```powershell
git add 0.7.3.2/personal-diet-pantry/skills 0.7.3.2/personal-diet-pantry/scripts 0.7.3.2/personal-diet-pantry/tests
git commit -m "test: validate packaged pantry behavior traces"
```

---

### Task 8: 版本、全量验证和发布包

**Files:**

- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `python/personal_diet_pantry/__init__.py`
- Modify: `tests/test_version_contract.py`
- Create: `UPDATE-v0.7.3.2.zh-CN.md`
- Modify: `RELEASE.zh-CN.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: release scripts and release-manifest expectations as required by existing audit tests.

**Interfaces:**

- Consumes: Tasks 2–7 的稳定行为与测试。
- Produces: 产品 `0.7.3.2`、技术包 `0.7.5` 的源码包、可安装包、哈希和测试摘要。

- [ ] **Step 1: 先更新版本契约测试并确认 RED**

Set expected technical version to `0.7.5` and product version to `0.7.3.2` in `tests/test_version_contract.py`。Run:

```powershell
python -m pytest tests/test_version_contract.py -vv
```

Expected: FAIL because package and plugin manifests still advertise v0.7.3.1/v0.7.4。

- [ ] **Step 2: 更新所有版本源和发布说明**

Change machine-readable technical versions to `0.7.5`, product-facing versions to `0.7.3.2`, and document the exact core fixes and deferred items from the design spec.

- [ ] **Step 3: 运行完整验证矩阵**

```powershell
python -m pytest -q
npm test -- --run
npm run build
python scripts/validate_skill.py
python scripts/lint_skill.py
```

Expected: 0 failures; skips only match documented environment/platform conditions。

- [ ] **Step 4: 运行安装、升级和发布审计**

```powershell
python -m pytest tests/integration/test_installable_e2e.py tests/integration/test_upgrade_e2e.py tests/test_release_audit.py -vv
```

Expected: 新安装和从 v0.7.3.1 升级均保持 migration 001–021，发布审计通过。

- [ ] **Step 5: 构建发布物并校验哈希**

Use the repository's existing release script to generate source archive、installable `.tgz`、`SHA256SUMS`、manifest and test summary。Recompute hashes with `Get-FileHash` and compare every manifest entry.

- [ ] **Step 6: 最终提交**

```powershell
git add 0.7.3.2/personal-diet-pantry
git commit -m "release: prepare personal diet pantry v0.7.3.2"
```

---

## Plan Self-Review

- 规格覆盖：C01–C10 和 L01–L04 均映射到 Task 2–8；D01–D12 明确不在本计划实施。
- 数据类型：句柄统一使用 `base_unit`；公开候选继续使用现有 `unit`；包装持久化继续使用 `display_quantity/display_unit/base_quantity_per_display_unit/package_hierarchy`。
- 营养语义：`NutritionFacts` 保持完整事实类型，`NutritionResult` 表示可空结果；证据容器允许二者，数据库无需迁移。
- 原子性：餐食记录、库存扣减、营养写入和修正继续使用现有 `TransactionManager`，计划没有引入第二个写事务。
- 无占位内容：所有任务均给出目标文件、失败测试、失败原因、最小实现、验证命令和提交边界。
