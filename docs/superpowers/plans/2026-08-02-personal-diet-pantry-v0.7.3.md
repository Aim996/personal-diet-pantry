# 食序管家 v0.7.3 Skill 完整性增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents unless the user explicitly changes that constraint.

**Goal:** 交付可复制、可安装的食序管家 v0.7.3 Skill，使包装事实、跨批次 FEFO、日历到期日、熟食复用和错误恢复在新会话中稳定工作。

**Architecture:** 保留 v0.7.2 的 Skill、TypeScript 插件、Python 领域服务和 SQLite 事务分层。新增向后兼容迁移与小型公共动作，把换算、跨批次分配、时区和营养快照复用放在确定性工具中；Skill 只负责自然语言意图、最短路由和有限自救。

**Tech Stack:** OpenClaw plugin API、TypeScript 5.9、TypeBox、Python 3.11+、SQLite、pytest、Vitest、YAML Skill contracts。

## Global Constraints

- 目标版本固定为 `0.7.3`，基线固定为 `0.7.2`。
- 项目交付物是完整 Skill 包，不建设独立应用。
- 不修改软路由、OpenClaw 宿主、Telegram、Gateway、Docker、Lucky 或生产数据库。
- v0.7.2 数据库必须原地升级；旧批次不得根据历史原文猜测包装字段。
- 保留现有七个顶层工具名称和现有 action；新增能力必须向后兼容。
- `initial_quantity`、`remaining_quantity` 和 `unit` 继续作为基础量真值。
- 新行为必须先写失败测试并看见正确失败，再写实现。
- 当前工作树已有 `package.json`、`scripts/build_release.py`、`tests/integration/test_installable_e2e.py` 和 `tests/test_build_release.py` 修改；不得回退或覆盖这些内容。
- 不使用未经用户要求的子代理；Skill 行为 RED 基线使用已保存的两轮 UAT 和本地确定性评测。
- 每个任务只提交本任务文件；发现无关脏文件时保留不动。

## File Map

| 文件/模块 | 单一责任 |
| --- | --- |
| `migrations/021_package_semantics_and_product_operations.sql` | 包装持久化字段与查询索引 |
| `python/personal_diet_pantry/package_semantics.py` | Decimal 包装校验、显示量换算和多层包装验证 |
| `python/personal_diet_pantry/pantry.py` | 批次持久化与统一跨批次 reduction |
| `python/personal_diet_pantry/service.py` | 产品句柄、日历日期、熟食直达和公共 outcome |
| `python/personal_diet_pantry/timezones.py` | 当地日历日期与 UTC 时间戳互转 |
| `python/personal_diet_pantry/meals.py` | 熟食快照缩放和原子餐食提交 |
| `src/schemas.ts` | v0.7.3 公共 action Schema 与兼容字段 |
| `src/index.ts` | TypeScript 边界归一化，不再删除包装事实 |
| `src/reliability.ts` | 有界失败指纹缓存 |
| `contracts/tools.yaml` | action、处理函数与测试绑定事实源 |
| `skills/personal-diet-pantry` | 自然语言路由和按需行为说明 |
| `tests/contracts`、`src-tests` | P0/P1 回归和公共契约 |

---

### Task 1: 持久化包装事实并保持旧数据库兼容

**Files:**
- Create: `migrations/021_package_semantics_and_product_operations.sql`
- Create: `python/personal_diet_pantry/package_semantics.py`
- Modify: `python/personal_diet_pantry/pantry.py`
- Modify: `python/personal_diet_pantry/service.py`
- Create: `tests/integration/test_package_semantics_migration.py`
- Modify: `tests/contracts/test_pantry_transaction_contracts.py`

**Interfaces:**
- Produces: `PackageSpec`, `validate_package_spec(...)`, `to_base_quantity(...)`, `remaining_display_quantity(...)`
- Extends: `PantryBatch.initial_display_quantity`, `display_unit`, `base_quantity_per_display_unit`, `package_hierarchy`
- Preserves: legacy rows return all four package fields as `None`

- [ ] **Step 1: Write failing migration and domain tests**

```python
def test_package_fields_survive_add_and_new_service_session(service):
    result = _pantry(service, "add", {
        "food_name": "青禾无糖豆花",
        "normalized_name": "青禾无糖豆花",
        "quantity": "360",
        "unit": "g",
        "display_quantity": "2",
        "display_unit": "盒",
        "base_quantity_per_display_unit": "180",
        "added_at": "2026-08-02T08:00:00+08:00",
        "expires_at": "2026-08-03T23:59:59+08:00",
        "source_text": "两盒豆花，一盒180克",
    })
    assert result["ok"] is True
    row = service.connection.execute(
        "SELECT initial_display_quantity, display_unit, "
        "base_quantity_per_display_unit FROM pantry_batches"
    ).fetchone()
    assert tuple(row) == (2.0, "盒", 180.0)


def test_legacy_batch_keeps_unknown_package_fields_null(upgraded_connection):
    row = upgraded_connection.execute(
        "SELECT initial_display_quantity, display_unit, "
        "base_quantity_per_display_unit, package_hierarchy_json "
        "FROM pantry_batches WHERE normalized_name = 'legacy egg'"
    ).fetchone()
    assert tuple(row) == (None, None, None, None)
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_package_semantics_migration.py `
  tests/contracts/test_pantry_transaction_contracts.py::test_package_fields_survive_add_and_new_service_session `
  -q
```

Expected: FAIL because migration 021 and package persistence do not exist.

- [ ] **Step 3: Add migration 021**

```sql
ALTER TABLE pantry_batches ADD COLUMN initial_display_quantity REAL
    CHECK (initial_display_quantity IS NULL OR initial_display_quantity > 0);
ALTER TABLE pantry_batches ADD COLUMN display_unit TEXT
    CHECK (display_unit IS NULL OR length(trim(display_unit)) BETWEEN 1 AND 40);
ALTER TABLE pantry_batches ADD COLUMN base_quantity_per_display_unit REAL
    CHECK (
        base_quantity_per_display_unit IS NULL
        OR base_quantity_per_display_unit > 0
    );
ALTER TABLE pantry_batches ADD COLUMN package_hierarchy_json TEXT
    CHECK (
        package_hierarchy_json IS NULL
        OR (
            json_valid(package_hierarchy_json) = 1
            AND substr(ltrim(package_hierarchy_json), 1, 1) = '['
        )
    );
CREATE INDEX idx_pantry_batches_product_package
ON pantry_batches (
    normalized_name, unit, display_unit, status, expires_at, added_at
);
```

- [ ] **Step 4: Implement Decimal package validation**

```python
@dataclass(frozen=True)
class PackageSpec:
    initial_display_quantity: Decimal
    display_unit: str
    base_quantity_per_display_unit: Decimal
    package_hierarchy: tuple[Mapping[str, str], ...] = ()


def validate_package_spec(
    *,
    base_quantity: Decimal,
    spec: PackageSpec | None,
) -> PackageSpec | None:
    if spec is None:
        return None
    expected = (
        spec.initial_display_quantity
        * spec.base_quantity_per_display_unit
    )
    if expected != base_quantity:
        raise PackageSemanticError(
            "base quantity conflicts with package specification"
        )
    return spec


def to_base_quantity(
    quantity: Decimal,
    unit: str,
    *,
    base_unit: str,
    spec: PackageSpec | None,
) -> tuple[Decimal, str]:
    if unit.casefold() == base_unit.casefold():
        return quantity, base_unit
    if spec is not None and unit.casefold() == spec.display_unit.casefold():
        return quantity * spec.base_quantity_per_display_unit, base_unit
    raise PackageSemanticError("inventory unit cannot be converted")
```

- [ ] **Step 5: Thread fields through pantry and service**

Add optional package parameters to `add_batch`, `_add_batch_record_in_context`, `_pantry_add_arguments`, `_batch`, `_pantry_compact` and detailed query output. Serialize hierarchy with canonical JSON and derive:

```python
"remaining_display_quantity": (
    batch.remaining_quantity / batch.base_quantity_per_display_unit
    if batch.base_quantity_per_display_unit is not None
    else None
)
```

- [ ] **Step 6: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/integration/test_package_semantics_migration.py `
  tests/contracts/test_pantry_transaction_contracts.py `
  tests/integration/test_upgrade_e2e.py `
  -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- migrations/021_package_semantics_and_product_operations.sql `
  python/personal_diet_pantry/package_semantics.py `
  python/personal_diet_pantry/pantry.py `
  python/personal_diet_pantry/service.py `
  tests/integration/test_package_semantics_migration.py `
  tests/contracts/test_pantry_transaction_contracts.py
git commit -m "feat: persist pantry package semantics"
```

---

### Task 2: 公开包装 Schema 并停止边界数据丢失

**Files:**
- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Create: `src-tests/package-semantics-schema.test.ts`
- Modify: `src-tests/intake-schema.test.ts`

**Interfaces:**
- Consumes: Task 1 canonical package fields
- Produces: `display_quantity`, `display_unit`, `base_quantity_per_display_unit`, `package_hierarchy`
- Compatibility: legacy package fields map to canonical fields

- [ ] **Step 1: Write failing TypeScript tests**

```typescript
it("keeps canonical package facts after normalization", async () => {
  const request = await captureRequest({
    action: "add",
    food_name: "青禾无糖豆花",
    quantity: "360",
    unit: "g",
    display_quantity: "2",
    display_unit: "盒",
    base_quantity_per_display_unit: "180",
    expiry_date: "2026-08-03",
  });
  expect(request.payload).toMatchObject({
    display_quantity: "2",
    display_unit: "盒",
    base_quantity_per_display_unit: "180",
  });
});
```

Add a second case proving the v0.7.2 legacy fields normalize to the same canonical payload.

- [ ] **Step 2: Verify RED**

```powershell
npm test -- --run src-tests/package-semantics-schema.test.ts
```

Expected: FAIL because canonical fields are rejected or removed.

- [ ] **Step 3: Add bounded Schema**

```typescript
const PackageHierarchyItemSchema = strictObject({
  quantity: Type.Optional(PositiveQuantitySchema),
  per_parent: Type.Optional(PositiveQuantitySchema),
  unit: Type.String({ minLength: 1, maxLength: 40 }),
});
const PackageSemanticFields = {
  display_quantity: Type.Optional(PositiveQuantitySchema),
  display_unit: Type.Optional(Type.String({ minLength: 1, maxLength: 40 })),
  base_quantity_per_display_unit: Type.Optional(PositiveQuantitySchema),
  package_hierarchy: Type.Optional(Type.Array(
    PackageHierarchyItemSchema,
    { minItems: 1, maxItems: 4 },
  )),
};
```

Require all three scalar package fields together using `dependentRequired`.

- [ ] **Step 4: Replace deletion with compatibility normalization**

```typescript
if (legacyPackageComplete && normalized.display_quantity === undefined) {
  normalized.display_quantity = normalized.package_count;
  normalized.base_quantity_per_display_unit =
    normalized.quantity_per_package;
}
delete normalized.package_count;
delete normalized.quantity_per_package;
delete normalized.package_unit;
```

Keep canonical fields in the Python payload and continue exact decimal correction of base quantity.

- [ ] **Step 5: Verify GREEN**

```powershell
npm test -- --run `
  src-tests/package-semantics-schema.test.ts `
  src-tests/intake-schema.test.ts `
  src-tests/all-actions-schema.test.ts
npm run build
```

Expected: PASS and TypeScript build exit 0.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- src/schemas.ts src/index.ts `
  src-tests/package-semantics-schema.test.ts `
  src-tests/intake-schema.test.ts
git commit -m "feat: preserve package facts at tool boundary"
```

---

### Task 3: 产品级句柄驱动跨批次 deduct 与 discard

**Files:**
- Modify: `python/personal_diet_pantry/pantry.py`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `src/schemas.ts`
- Modify: `contracts/tools.yaml`
- Modify: `tests/contracts/test_inventory_search_contracts.py`
- Modify: `tests/contracts/test_pantry_transaction_contracts.py`
- Modify: `src-tests/pantry-search-schema.test.ts`

**Interfaces:**
- Consumes: `inventory_match_handle` and `to_base_quantity`
- Produces: `diet_pantry deduct` and product-level `discard`
- Invariant: one transaction contains every batch update and movement

- [ ] **Step 1: Write the exact two-batch failing regression**

```python
def test_three_boxes_discard_uses_product_handle_and_fefo(service):
    _add_packaged_tofu(service, boxes="2", expiry_date="2026-08-03")
    _add_packaged_tofu(service, boxes="3", expiry_date="2026-08-07")
    search = _pantry(service, "search", {"search_text": "豆花"})
    handle = search["data"]["candidates"][0]["workflow"][
        "inventory_match_handle"
    ]
    result = _pantry(service, "discard", {
        "inventory_match_handle": handle,
        "quantity": "3",
        "unit": "盒",
        "source_text": "有三盒豆花鼓包了，刚扔掉",
        "waste_category": "spoilage",
    })
    assert result["ok"] is True
    rows = service.connection.execute(
        "SELECT remaining_quantity FROM pantry_batches "
        "WHERE normalized_name = '青禾无糖豆花' ORDER BY expires_at"
    ).fetchall()
    assert [row[0] for row in rows] == [0, 360]
    movements = service.connection.execute(
        "SELECT movement_type, quantity FROM pantry_movements "
        "WHERE movement_type = 'discard' ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in movements] == [
        ("discard", 360),
        ("discard", 180),
    ]
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_pantry_transaction_contracts.py::test_three_boxes_discard_uses_product_handle_and_fefo `
  -q
```

Expected: FAIL because `discard` requires one batch handle.

- [ ] **Step 3: Generalize pantry reduction**

```python
def _reduce_inventory_in_context(
    connection: sqlite3.Connection,
    context: MutationContext,
    *,
    normalized_name: str,
    quantity: Decimal,
    unit: str,
    movement_type: Literal["consume", "discard"],
    source_text: str,
    reason: str | None,
    waste_category: str | None = None,
    deduction_strategy: Sequence[str] | None = None,
) -> BatchSelection:
    name = _required_text(normalized_name, "normalized_name").lower()
    amount = _positive_quantity(quantity)
    wanted_unit = _unit(unit)
    strategy = _strategy(deduction_strategy)
    selection = _selection(
        connection,
        name,
        amount,
        None,
        expected_unit=wanted_unit,
        deduction_strategy=strategy,
    )
    for batch_id, line in _selected_rows(
        connection,
        name,
        amount,
        None,
        wanted_unit,
        strategy,
    ):
        row = connection.execute(
            "SELECT * FROM pantry_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise PantryValidationError(
                "selected batch no longer exists"
            )
        remaining = _decimal(row["remaining_quantity"]) - line.quantity
        terminal = DISCARDED if movement_type == "discard" else CONSUMED
        allocation = costs.allocation_for_reduction(row, line.quantity)
        changes: dict[str, object] = {
            "remaining_quantity": _sqlite_real(
                remaining, "remaining_quantity"
            ),
            "status": terminal if remaining == 0 else row["status"],
            "version": int(row["version"]) + 1,
        }
        if allocation is not None:
            changes["remaining_cost_minor"] = allocation.remaining_cost_minor
        context.update("pantry_batches", batch_id, changes)
        movement = _movement(
            context,
            batch_id,
            movement_type,
            line.quantity,
            wanted_unit,
            reason or source_text,
            None,
            waste_category=(
                waste_category if movement_type == "discard" else None
            ),
        )
        costs.record_allocation(
            context,
            batch_id=batch_id,
            movement_id=int(movement["id"]),
            allocation_kind=(
                "waste" if movement_type == "discard" else "consume"
            ),
            quantity=line.quantity,
            unit=wanted_unit,
            allocation=allocation,
            allocated_at=str(movement["created_at"]),
        )
    return selection
```

Implement the body by reusing `_selected_rows`. For each selected line update quantity/version/status and write the requested movement. `discard` uses waste cost allocation; `consume` keeps current allocation.

- [ ] **Step 4: Resolve product handles without fuzzy requery**

```python
def _pantry_product_reference(
    service: DietService,
    handle: str,
    *,
    now: datetime,
) -> tuple[str, str]:
    row = _workflow_row(
        service.connection,
        handle,
        "pantry_product_reference",
        now=now,
    )
    value = _stored_object(row["result_json"], "stored pantry product")
    return (
        _required_text(value, "normalized_name"),
        _required_text(value, "unit"),
    )
```

Convert display unit to base unit from persisted package facts, then call the reduction once.

- [ ] **Step 5: Extend Schema and machine contract**

```typescript
actionBranch("deduct", {
  inventory_match_handle: HandleSchema,
  quantity: PositiveQuantitySchema,
  unit: Type.String({ minLength: 1, maxLength: 40 }),
  source_text: Type.String({ minLength: 1 }),
  reason: Type.Optional(Type.String({ minLength: 1 })),
})
```

Make `discard` accept either its legacy batch-target branch or the product-handle quantity branch.

- [ ] **Step 6: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_pantry_transaction_contracts.py `
  tests/contracts/test_inventory_search_contracts.py `
  -q
npm test -- --run src-tests/pantry-search-schema.test.ts
.\.venv\Scripts\python.exe scripts/generate_tool_contracts.py --root .
.\.venv\Scripts\python.exe scripts/generate_tool_contracts.py --root . --check
```

Expected: PASS; insufficient stock leaves every batch unchanged.

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- python/personal_diet_pantry/pantry.py `
  python/personal_diet_pantry/service.py src/schemas.ts `
  contracts/tools.yaml src/generated/tool-contracts.ts `
  python/personal_diet_pantry/generated_tool_contracts.py `
  tests/contracts/test_inventory_search_contracts.py `
  tests/contracts/test_pantry_transaction_contracts.py `
  src-tests/pantry-search-schema.test.ts
git commit -m "feat: add product-level FEFO inventory reductions"
```

---

### Task 4: 用日历日期替代模型时区偏移

**Files:**
- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Modify: `python/personal_diet_pantry/timezones.py`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/meals.py`
- Create: `src-tests/calendar-expiry-schema.test.ts`
- Create: `tests/contracts/test_calendar_expiry_contracts.py`

**Interfaces:**
- Produces: `local_expiry_end(date, timezone_name) -> datetime`
- Rule: `expiry_date` and `expires_at` are mutually exclusive
- Default timezone: configured profile timezone

- [ ] **Step 1: Write failing calendar tests**

```python
def test_expiry_date_preserves_shanghai_calendar_day(service):
    result = _pantry(service, "add", {
        "food_name": "豆花",
        "quantity": "180",
        "unit": "g",
        "added_at": "2026-08-02T08:00:00+08:00",
        "expiry_date": "2026-08-05",
        "source_text": "豆花8月5日到期",
    })
    assert result["ok"] is True
    queried = _pantry(service, "query", {
        "normalized_name": "豆花",
        "include_details": True,
    })
    assert queried["data"]["batches"][0]["expiry_date"] == "2026-08-05"
```

Add a TypeScript test that rejects simultaneous `expiry_date` and `expires_at`.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_calendar_expiry_contracts.py -q
npm test -- --run src-tests/calendar-expiry-schema.test.ts
```

Expected: both suites FAIL because `expiry_date` is absent.

- [ ] **Step 3: Implement timezone conversion**

```python
def local_expiry_end(value: date, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    next_day = datetime.combine(
        value + timedelta(days=1),
        time.min,
        tzinfo=zone,
    )
    return next_day - timedelta(seconds=1)


def local_calendar_date(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).date().isoformat()
```

Resolve dates in service before pantry/meals and return the same local date in public batch and leftover results.

- [ ] **Step 4: Add Schema compatibility**

Add optional `expiry_date` to add, metadata and leftover Schema. Require exactly one date form wherever expiry is required. In `src/index.ts`, validate the calendar format without constructing a UTC offset.

- [ ] **Step 5: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_calendar_expiry_contracts.py `
  tests/contracts/test_live_intake_regressions.py `
  -q
npm test -- --run `
  src-tests/calendar-expiry-schema.test.ts `
  src-tests/intake-schema.test.ts
```

Expected: PASS, including leftover date consistency.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- src/schemas.ts src/index.ts `
  python/personal_diet_pantry/timezones.py `
  python/personal_diet_pantry/service.py `
  python/personal_diet_pantry/meals.py `
  src-tests/calendar-expiry-schema.test.ts `
  tests/contracts/test_calendar_expiry_contracts.py
git commit -m "feat: preserve local expiry calendar dates"
```

---

### Task 5: 增加熟食快照单步食用接口

**Files:**
- Modify: `src/schemas.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/meals.py`
- Modify: `python/personal_diet_pantry/prepared_foods.py`
- Modify: `contracts/tools.yaml`
- Create: `src-tests/prepared-food-schema.test.ts`
- Create: `tests/contracts/test_prepared_food_direct_contracts.py`

**Interfaces:**
- Produces: `prepared_food_handle` on an exact prepared batch
- Produces: `diet_meal record_prepared`
- Invariant: prepared consumption never deducts original ingredients

- [ ] **Step 1: Write failing direct-consumption regression**

```python
def test_record_prepared_reuses_snapshot_and_only_deducts_leftover(service):
    _record_cat_ears_cooking(service)
    search = _pantry(service, "search", {"search_text": "煮猫耳朵面"})
    handle = search["data"]["candidates"][0]["workflow"][
        "prepared_food_handle"
    ]
    result = _meal(service, "record_prepared", {
        "prepared_food_handle": handle,
        "source_text": "刚把冰箱那盒猫耳朵吃了",
    })
    assert result["ok"] is True
    assert result["outcome"] == "write_committed"
    assert result["data"]["meal"]["total_calories"] == "266.25"
    assert _remaining(service, "煮猫耳朵面") == Decimal("0")
    assert _remaining(service, "猫耳朵面") == Decimal("450")
```

Add an undo assertion restoring prepared stock to 180g while raw stock stays 450g.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_prepared_food_direct_contracts.py -q
npm test -- --run src-tests/prepared-food-schema.test.ts
```

Expected: FAIL because the prepared handle and action do not exist.

- [ ] **Step 3: Issue typed prepared handles**

When a search candidate maps to one active batch with one prepared profile, issue:

```python
"prepared_food_handle": _issue_workflow(
    service,
    "prepared_food_reference",
    request={"action": "select_prepared_food"},
    result={
        "batch_id": batch_id,
        "prepared_food_profile_id": profile_id,
    },
    resource_versions={"version": batch.version},
    now=now,
)
```

Keep both IDs inside the opaque handle and never return them publicly.

- [ ] **Step 4: Implement `record_prepared`**

Resolve the handle, load the `portion_total` snapshot, select the provided quantity or the whole exact batch, scale nutrients by `quantity / initial_quantity`, and build one exact inventory-bound `MealDraft`. Use the existing meal transaction path so undo/redo remains unchanged.

```python
def _meal_record_prepared(
    service: DietService,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Mapping[str, Any]:
    now = _operation_now(payload, context)
    reference = _prepared_food_reference(service, payload, now=now)
    result = meals.record_prepared(
        service.connection,
        TransactionManager(service.connection),
        reference=reference,
        quantity=_optional_decimal(payload.get("quantity"), "quantity"),
        unit=_optional_text(payload.get("unit"), "unit"),
        source_text=_required_text(payload, "source_text"),
        occurred_at=(
            _datetime_value(payload["occurred_at"], "occurred_at")
            if "occurred_at" in payload
            else now
        ),
        meal_type=_optional_text(payload.get("meal_type"), "meal_type"),
        timezone_name=service.settings.profile.timezone,
        deduction_strategy=(
            service.settings.behavior.inventory.deduction_strategy
        ),
    )
    return _meal_commit_payload(service, result)
```

- [ ] **Step 5: Add compact Schema and contract binding**

```typescript
actionBranch("record_prepared", {
  prepared_food_handle: HandleSchema,
  quantity: Type.Optional(PositiveQuantitySchema),
  unit: Type.Optional(Type.String({ minLength: 1, maxLength: 40 })),
  source_text: Type.String({ minLength: 1 }),
  occurred_at: Type.Optional(DateTimeSchema),
  meal_type: Type.Optional(MealTypeSchema),
}, {
  dependentRequired: { quantity: ["unit"], unit: ["quantity"] },
})
```

Bind `meal.record_prepared` in `contracts/tools.yaml` to its Python and TypeScript tests.

- [ ] **Step 6: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_prepared_food_direct_contracts.py `
  tests/contracts/test_live_intake_regressions.py `
  -q
npm test -- --run `
  src-tests/prepared-food-schema.test.ts `
  src-tests/all-actions-schema.test.ts
```

Expected: PASS; direct consumption is one meal action and remains undoable.

- [ ] **Step 7: Commit Task 5**

```powershell
git add -- src/schemas.ts contracts/tools.yaml `
  src/generated/tool-contracts.ts `
  python/personal_diet_pantry/generated_tool_contracts.py `
  python/personal_diet_pantry/service.py `
  python/personal_diet_pantry/meals.py `
  python/personal_diet_pantry/prepared_foods.py `
  src-tests/prepared-food-schema.test.ts `
  tests/contracts/test_prepared_food_direct_contracts.py
git commit -m "feat: add direct prepared-food recording"
```

---

### Task 6: 统一 outcome、字段错误和失败指纹缓存

**Files:**
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `src/reliability.ts`
- Modify: `src/runtime-tool.ts`
- Modify: `src-tests/reliability.test.ts`
- Modify: `src-tests/public-response-filter.test.ts`
- Create: `tests/contracts/test_public_outcome_contracts.py`

**Interfaces:**
- Produces: `write_committed | preview_ready | read_completed | no_op | failed`
- Produces: complete recoverable `INVALID_INPUT`
- Produces: bounded per-session exact-failure cache

- [ ] **Step 1: Write failing public outcome tests**

```python
def test_public_outcomes_distinguish_read_preview_write_and_failure(service):
    assert _pantry(service, "query", {})["outcome"] == "read_completed"
    assert _pantry(
        service, "preview_add", valid_add()
    )["outcome"] == "preview_ready"
    assert _pantry(
        service, "add", valid_add()
    )["outcome"] == "write_committed"
    failed = _pantry(service, "add", {"food_name": "x"})
    assert failed["outcome"] == "failed"
    assert {"field", "reason", "expected", "retryable"} <= set(
        failed["error"]
    )
```

Add one explicit zero-effect service fixture asserting `no_op` and zero new transaction rows. Empty meal items must stay an invalid request, not a successful no-op.

- [ ] **Step 2: Write failing failure-cache test**

```typescript
it("does not call Python twice for one identical session failure", async () => {
  const runner = vi.fn().mockResolvedValue(invalidInputResult);
  const request = pantryRequest("discard", invalidPayload);
  await callPythonReliably(request, {}, { runner, runtimeIdentity });
  await callPythonReliably(request, {}, { runner, runtimeIdentity });
  expect(runner).toHaveBeenCalledTimes(1);
});
```

Add a second case changing the rejected field and expecting two backend calls.

- [ ] **Step 3: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_public_outcome_contracts.py -q
npm test -- --run src-tests/reliability.test.ts
```

Expected: FAIL because outcome and failure caching are absent.

- [ ] **Step 4: Add outcome to service responses**

```python
@dataclass(frozen=True)
class _HandlerResult:
    data: Mapping[str, Any]
    outcome: str
    warnings: tuple[str, ...] = ()
    requires_confirmation: bool = False
    confirmation_options: tuple[Mapping[str, Any], ...] = ()
```

Formal mutation defaults to `write_committed`, preview to `preview_ready`, read to `read_completed`, explicit zero-effect to `no_op`, and every error to `failed`. Keep `ok` for compatibility.

- [ ] **Step 5: Complete recoverable diagnostics**

Map v0.7.3 package, handle, date and unit errors to:

```python
_ServiceError(
    "INVALID_INPUT",
    "The request is invalid",
    field="unit",
    reason="unsupported_conversion",
    expected="the stored base unit or display unit",
    retryable=True,
)
```

Never return traceback, SQL, raw identifiers or full source text.

- [ ] **Step 6: Add bounded failure cache**

Cache safe `ok=false` results by session identity, domain, action, canonical public payload and error code. Maximum 256 entries, TTL 5 minutes. Do not cache `DATABASE_BUSY`, `STALE_PREVIEW`, timeout or unknown-outcome responses. Return a deep copy.

- [ ] **Step 7: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_public_outcome_contracts.py `
  tests/contracts/test_pantry_transaction_contracts.py `
  -q
npm test -- --run `
  src-tests/reliability.test.ts `
  src-tests/public-response-filter.test.ts
```

Expected: PASS; an exact repeated invalid request uses one backend call, while a corrected field reaches the backend.

- [ ] **Step 8: Commit Task 6**

```powershell
git add -- python/personal_diet_pantry/service.py `
  src/reliability.ts src/runtime-tool.ts `
  src-tests/reliability.test.ts `
  src-tests/public-response-filter.test.ts `
  tests/contracts/test_public_outcome_contracts.py
git commit -m "feat: make tool outcomes and recovery deterministic"
```

---

### Task 7: 更新 Skill 路由、按需 references 和行为评测

**Files:**
- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `skills/personal-diet-pantry/references/pantry-and-expiry.md`
- Modify: `skills/personal-diet-pantry/references/meal-and-nutrition.md`
- Modify: `skills/personal-diet-pantry/references/cooking-and-leftovers.md`
- Modify: `skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md`
- Modify: `tests/skill-evals/routing.yaml`
- Modify: `tests/contracts/test_natural_language_trigger_skill_contract.py`
- Modify: `tests/test_skill_progressive_disclosure.py`

**Interfaces:**
- Consumes: Tasks 1–6 public actions and outcomes
- Produces: compact route cards for packaging, product reductions, calendar expiry and prepared food
- RED evidence: two v0.7.2 live UAT documents

- [ ] **Step 1: Add failing Skill behavior cases**

```yaml
- input: "有三盒豆花鼓包了，刚扔掉"
  expected_domain: pantry
  expected_action: discard
  forbidden_actions: [query]
  required_terms: [inventory_match_handle, quantity, unit]

- input: "刚把冰箱那盒猫耳朵吃了"
  expected_domain: meal
  expected_action: record_prepared
  forbidden_actions: [nutrition_estimate, preview_record]
```

Contract tests must require these rules:

```text
同商品多批次不是商品歧义
包装单位交给工具换算
日期优先提交 expiry_date
相同失败指纹不得原样重复
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_natural_language_trigger_skill_contract.py `
  tests/test_skill_progressive_disclosure.py `
  -q
.\.venv\Scripts\python.exe scripts/evaluate_skill.py `
  --skill skills/personal-diet-pantry `
  --cases tests/skill-evals/routing.yaml
```

Expected: FAIL because v0.7.2 Skill does not route to the new actions.

- [ ] **Step 3: Update the smallest necessary instructions**

Add one compact main route table:

```text
入库带包装 → diet_pantry add，保留显示数量/单位/单件规格
已丢弃或已使用库存 → search 唯一商品后 discard/deduct
已吃熟食剩菜 → search 取得 prepared_food_handle 后 record_prepared
库存或剩菜到期日 → expiry_date，不构造时区偏移
```

Keep parameter details in the four references. Remove conflicting instructions that ask the model to select a physical batch for one product or manually calculate FEFO.

- [ ] **Step 4: Verify GREEN and progressive disclosure**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/contracts/test_natural_language_trigger_skill_contract.py `
  tests/test_skill_progressive_disclosure.py `
  tests/test_skill_package.py `
  -q
.\.venv\Scripts\python.exe scripts/evaluate_skill.py `
  --skill skills/personal-diet-pantry `
  --cases tests/skill-evals/routing.yaml
.\.venv\Scripts\python.exe scripts/validate_skill.py
```

Expected: PASS; a single-domain case names only its needed reference and the main Skill remains within the project size budget.

- [ ] **Step 5: Commit Task 7**

```powershell
git add -- skills/personal-diet-pantry/SKILL.md `
  skills/personal-diet-pantry/references/pantry-and-expiry.md `
  skills/personal-diet-pantry/references/meal-and-nutrition.md `
  skills/personal-diet-pantry/references/cooking-and-leftovers.md `
  skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md `
  tests/skill-evals/routing.yaml `
  tests/contracts/test_natural_language_trigger_skill_contract.py `
  tests/test_skill_progressive_disclosure.py
git commit -m "feat: teach the skill deterministic inventory routes"
```

---

### Task 8: 版本、文档、构建包和离线交付验证

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `pyproject.toml`
- Modify: `openclaw.plugin.json`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `RELEASE.zh-CN.md`
- Create: `UPDATE-v0.7.3.zh-CN.md`
- Modify: `docs/DATA-MODEL.zh-CN.md`
- Modify: `docs/TOOLS-REFERENCE.zh-CN.md`
- Modify: `docs/USER-GUIDE.zh-CN.md`
- Modify: `contracts/v070-core-tests.txt`
- Modify only if required: existing dirty release/build files listed in Global Constraints

**Interfaces:**
- Produces: installable v0.7.3 archive and E-drive project mirror
- Verifies: migration, Skill, generated contracts, bridge, installable package and rollback artifact

- [ ] **Step 1: Write failing version and package tests**

Make all public version assertions require `0.7.3`. Require:

```python
assert (
    "package/migrations/"
    "021_package_semantics_and_product_operations.sql"
) in members
assert "package/dist/generated/tool-contracts.js" in members
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_version_contract.py `
  tests/test_build_release.py `
  tests/integration/test_installable_e2e.py `
  -q
npm test -- --run `
  src-tests/version-contract.test.ts `
  src-tests/package-contents.test.ts
```

Expected: FAIL because public versions remain v0.7.2 and the release contract lacks migration 021.

- [ ] **Step 3: Bump version and update durable documentation**

Set package, lockfile, Python project and plugin manifest to `0.7.3`. Document packaging persistence, product-level FEFO, calendar dates, prepared-food direct recording, outcome semantics, v0.7.2 upgrade and rollback. State that release creation does not deploy production.

- [ ] **Step 4: Preserve existing release fixes**

```powershell
git diff -- package.json scripts/build_release.py `
  tests/integration/test_installable_e2e.py `
  tests/test_build_release.py
```

Keep existing nested generated JavaScript packaging and generated-contract checks. Add v0.7.3 changes around them without removing their hunks.

- [ ] **Step 5: Run focused and full verification**

```powershell
.\.venv\Scripts\python.exe scripts/generate_tool_contracts.py --root . --check
.\.venv\Scripts\python.exe -m pytest -q
npm run build
npm test
.\.venv\Scripts\python.exe -m compileall -q python scripts
.\.venv\Scripts\python.exe scripts/validate_skill.py
.\.venv\Scripts\python.exe scripts/release_audit.py .
npm pack --dry-run --json
$env:PDP_PYTHON=(Resolve-Path '.\.venv\Scripts\python.exe')
.\ci\verify.ps1
```

Expected: every command exits 0; pytest and Vitest report zero failures.

- [ ] **Step 6: Commit release sources before artifact creation**

```powershell
git add -- package.json package-lock.json pyproject.toml `
  openclaw.plugin.json README.md README.en.md RELEASE.zh-CN.md `
  UPDATE-v0.7.3.zh-CN.md docs/DATA-MODEL.zh-CN.md `
  docs/TOOLS-REFERENCE.zh-CN.md docs/USER-GUIDE.zh-CN.md `
  docs/INSTALLATION.zh-CN.md docs/TROUBLESHOOTING.zh-CN.md `
  docs/ARCHITECTURE.zh-CN.md `
  contracts/v070-core-tests.txt scripts/build_release.py `
  tests/integration/test_installable_e2e.py tests/test_build_release.py `
  tests/test_version_contract.py src-tests/version-contract.test.ts `
  src-tests/package-contents.test.ts `
  python/personal_diet_pantry/__init__.py `
  python/personal_diet_pantry/data_import.py ci/verify.ps1 `
  docs/superpowers/plans/2026-08-02-personal-diet-pantry-v0.7.3.md
git commit -m "release: prepare personal diet pantry v0.7.3"
```

The release builder rejects a dirty source tree, so artifact creation begins only after this commit.

- [ ] **Step 7: Build and inspect artifacts from the clean commit**

```powershell
$InitialStatus = @(git status --short)
if ($LASTEXITCODE -ne 0) { throw "initial git status failed" }
if ($InitialStatus.Count -ne 0) { throw "release build requires a clean worktree" }
$GitTopLevel = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GitTopLevel) { throw "cannot resolve Git top level" }
$ReleaseRoot = Join-Path (Split-Path -Parent $GitTopLevel) 'pdp-v0.7.3-release-task8-rerun'
if (Test-Path -LiteralPath $ReleaseRoot) { throw "release root must not already exist" }
$GitTopLevelPath = [IO.Path]::GetFullPath($GitTopLevel).TrimEnd('\', '/')
$ReleaseRootPath = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\', '/')
$GitPrefix = $GitTopLevelPath + [IO.Path]::DirectorySeparatorChar
if ($ReleaseRootPath -eq $GitTopLevelPath -or $ReleaseRootPath.StartsWith($GitPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "release root must remain outside the Git worktree"
}
$ReleaseRoot = $ReleaseRootPath
& $PdpPython scripts/build_release.py --project-root . --release-root $ReleaseRoot
if ($LASTEXITCODE -ne 0) { throw "release build failed with exit code $LASTEXITCODE" }
$PostBuildStatus = @(git status --short)
if ($LASTEXITCODE -ne 0) { throw "post-build git status failed" }
if ($PostBuildStatus.Count -ne 0) { throw "release build dirtied the worktree" }
```

The exact top-level entries are:

```text
personal-diet-pantry-0.7.3-source.tar.gz
personal-diet-pantry-0.7.3-installable.tgz
release-manifest.json
TEST-SUMMARY-v0.7.3.zh-CN.md
SHA256SUMS
GitHub文档/
```

The installable archive must contain `package/skills/personal-diet-pantry/SKILL.md`,
`package/migrations/021_package_semantics_and_product_operations.sql`,
`package/dist/index.js`, `package/dist/generated/tool-contracts.js`,
`package/python/personal_diet_pantry/package_semantics.py`, and
`package/UPDATE-v0.7.3.zh-CN.md`. Run installable E2E from the unpacked package.

- [ ] **Step 8: Mirror verified version**

Create or update `C:\path\to\personal-diet-pantry\0.7.3\`. Copy only verified release artifacts, source snapshot, design, plan and UAT records. Verify the archive and E-drive mirror SHA-256 hashes match. Do not deploy to `192.0.2.1`.

- [ ] **Step 9: Final evidence gate**

Invoke `superpowers:verification-before-completion` and record:

```text
HEAD commit
git status for project-owned files
pytest pass/fail count
Vitest pass/fail count
TypeScript build exit
Skill validation exit
release audit exit
archive SHA-256
E-drive mirror SHA-256
v0.7.2 upgrade fixture result
```

Do not claim the live OpenClaw installation is upgraded; deployment is a separate authorized task.
