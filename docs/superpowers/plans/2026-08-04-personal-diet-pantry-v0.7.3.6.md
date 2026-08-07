# 食序管家 v0.7.3.6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变受保护六项进度回执和既有数据库 Schema 的前提下，修复时区、通用时间查询、过期报告、模糊份量确认、工具预算和库存关系边界，并发布产品版本 `0.7.3.6`、技术版本 `0.7.9`。

**Architecture:** SQLite 继续保存 UTC 与正式业务事实；Python 新增声明式策略注册表和时间窗口解析器，服务层把结构化时间范围、当地时间投影、完整性和估算 resolution 暴露给现有七类工具。OpenClaw Skill 只保留稳定决策门禁，把时间、估算、调用预算和正式事实来源放入按需 reference；TypeScript Schema 允许经过校验的描述符，不维护自然语言关键词枚举。

**Tech Stack:** Python 3.11+、SQLite、pytest、TypeScript 5.9、TypeBox、Vitest、YAML、OpenClaw plugin SDK。

## Global Constraints

- 每个实施步骤前以 `docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md` 为最高产品兼容边界。
- v0.7.3.5 保持不可变；所有产品修改只进入 `0.7.3.6/personal-diet-pantry`。
- 产品版本固定为 `0.7.3.6`，技术 SemVer 固定为 `0.7.9`，前一版本分别为 `0.7.3.5` 和 `0.7.8`。
- migration 保持 001–021，不新增或修改迁移文件。
- 七个公共工具名称保持不变。
- 固定六项进度回执的顺序、两行结构、10 格进度条、真实百分比、本次增量和库存区块语义保持不变。
- 查询和预览不得改业务表；失败、未确认估算、计划、假设、否定和未发生事件保持零业务写入。
- 不调用 Exec、SQL、文件遍历或非正式记忆作为 Diet 工具失败后的降级路线。
- 当前版本快照在根仓库中未跟踪且根工作树包含其他用户版本；执行期间不自动 stage、commit、reset、clean 或移动其他版本文件，以逐任务测试记录代替 Git 提交检查点。

---

### Task 1: 声明式策略注册表与 fail-closed 校验

**Files:**
- Create: `python/personal_diet_pantry/policies.py`
- Create: `rules/temporal-scopes.yaml`
- Create: `rules/quantity-evidence.yaml`
- Create: `rules/intent-routes.yaml`
- Create: `rules/inventory-relations.yaml`
- Create: `rules/report-taxonomy.yaml`
- Create: `rules/fact-authority.yaml`
- Modify: `python/personal_diet_pantry/config.py`
- Modify: `python/personal_diet_pantry/service.py`
- Test: `tests/test_policy_registry.py`

**Interfaces:**
- Produces: `PolicyRegistry`, `PolicyEntry`, `load_policy_registry(source_root, data_paths)`, `PolicyRegistry.entries(registry_name)` and `PolicyRegistry.entry(registry_name, policy_key)`.
- Produces: six versioned registries whose entries reference allowlisted `operator` or `capability` values.
- Consumes later: temporal resolution, quantity confirmation, route-budget Skill contracts, report completeness and inventory relation projection.

- [x] **Step 1: Write failing registry contract tests**

```python
def test_policy_registry_loads_all_shipped_registries(project_root, data_paths):
    registry = load_policy_registry(project_root, data_paths)
    assert set(registry.names) == {
        "temporal-scopes", "quantity-evidence", "intent-routes",
        "inventory-relations", "report-taxonomy", "fact-authority",
    }
    assert registry.entry("temporal-scopes", "segment.night").operator == "local_segment"

def test_policy_registry_rejects_unknown_operator_without_business_writes(project_root, data_paths, service):
    before = snapshot_business_tables(service.connection)
    write_policy_override(data_paths, operator="execute_user_expression")
    with pytest.raises(ConfigurationError, match="unknown operator"):
        load_policy_registry(project_root, data_paths)
    assert snapshot_business_tables(service.connection) == before
```

- [x] **Step 2: Run RED**

Run: `python -m pytest tests/test_policy_registry.py -q`

Expected: FAIL because `personal_diet_pantry.policies` and the six rule files do not exist.

- [x] **Step 3: Implement the minimal validated registry**

```python
@dataclass(frozen=True)
class PolicyEntry:
    policy_key: str
    operator: str
    values: Mapping[str, object]
    source: str
    version: int

@dataclass(frozen=True)
class PolicyRegistry:
    registries: Mapping[str, tuple[PolicyEntry, ...]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.registries)

    def entries(self, registry_name: str) -> tuple[PolicyEntry, ...]:
        return self.registries[registry_name]

    def entry(self, registry_name: str, policy_key: str) -> PolicyEntry:
        return next(
            entry
            for entry in self.entries(registry_name)
            if entry.policy_key == policy_key
        )

def load_policy_registry(source_root: Path, data_paths: DataPaths) -> PolicyRegistry:
    """Load shipped descriptors plus a validated optional policy-overrides.yaml."""
```

Validation must reject duplicate keys, unknown registry names, unknown operators/capabilities, non-overridable protected fields, same-priority conflicts, dangling references and cycles. `DietService` loads the registry before database mutation setup and enters `RULES_INVALID` degraded mode on failure.

- [x] **Step 4: Run GREEN and existing configuration tests**

Run: `python -m pytest tests/test_policy_registry.py tests/test_skill_package.py tests/test_release_audit.py -q`

Expected: PASS; malformed policies never write business rows.

- [x] **Step 5: Record audit checkpoint**

Checkpoint: `tests/test_policy_registry.py`, `tests/test_skill_package.py` and
`tests/test_release_audit.py` passed 19 tests. Six shipped registries load before
business dispatch; malformed user overrides fall back to shipped rules and enter
`RULES_INVALID` without changing business rows.

Record changed paths and exact passing commands in this plan's execution ledger; do not stage unrelated root files.

### Task 2: 通用自然时间窗口与三领域查询

**Files:**
- Create: `python/personal_diet_pantry/temporal.py`
- Modify: `python/personal_diet_pantry/timezones.py`
- Modify: `python/personal_diet_pantry/meals.py`
- Modify: `python/personal_diet_pantry/water.py`
- Modify: `python/personal_diet_pantry/body_weight.py`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `src/schemas.ts`
- Test: `tests/test_temporal_queries.py`
- Test: `src-tests/temporal-query-schema.test.ts`

**Interfaces:**
- Consumes: `PolicyRegistry.entry("temporal-scopes", ...)` from Task 1.
- Produces: `ResolvedWindow` and `resolve_query_window(payload, now, timezone_name, policies)`.
- Produces: mutually exclusive `occurred_on`, `calendar_window`, `rolling_window`, and `local_range` query inputs.
- Produces: query response `scope` with UTC/local half-open bounds, timezone, descriptor type, optional segment and `complete`.

- [x] **Step 1: Write failing time resolver and integration tests**

```python
@pytest.mark.parametrize(
    ("descriptor", "start", "end"),
    [
        ({"calendar_window": {"unit": "day", "offset": -1, "segment": "night"}},
         "2026-08-03T10:00:00Z", "2026-08-03T18:00:00Z"),
        ({"rolling_window": {"value": 3, "unit": "hour"}},
         "2026-08-03T14:00:00Z", "2026-08-03T17:00:00Z"),
    ],
)
def test_resolve_query_window_uses_profile_timezone_and_trusted_now(
    policy_registry, descriptor, start, end
):
    resolved = resolve_query_window(
        descriptor,
        now=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
        policies=policy_registry,
    )
    assert resolved is not None
    assert utc_text(resolved.start_utc) == start
    assert utc_text(resolved.end_utc) == end

def test_same_cross_day_scope_returns_meals_water_and_weights(service):
    # Insert dinner, snack, nutritious drink, water and weight inside one UTC range.
    # Query each domain with the same calendar_window descriptor.
    # Assert every response reports identical scope bounds and contains its in-range rows only.
```

Schema tests must accept a registered identifier pattern such as `post_workout` without adding a TypeScript literal and reject two simultaneous window modes.

- [x] **Step 2: Run RED**

Run: `python -m pytest tests/test_temporal_queries.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/temporal-query-schema.test.ts --configLoader runner`

Expected: FAIL because descriptor schemas, resolver and range-aware queries are absent.

- [x] **Step 3: Implement the pure resolver**

```python
@dataclass(frozen=True)
class ResolvedWindow:
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    timezone_name: str
    window_type: str
    unit: str | None
    segment: str | None
    complete: bool

def resolve_query_window(
    payload: Mapping[str, object], *, now: datetime,
    timezone_name: str, policies: PolicyRegistry,
) -> ResolvedWindow | None:
    selected = selected_window_descriptor(payload)
    if selected is None:
        return None
    return resolve_selected_descriptor(
        selected, now=now, timezone_name=timezone_name, policies=policies
    )
```

Implement day/week/month calendar operators, minute/hour/day/week rolling durations, registered local segments including cross-day windows, explicit local ranges and legacy `occurred_on`. Cap unfinished natural and local-range upper bounds at trusted `now`; retain the legacy explicit-date query's full-day bounds for compatibility and mark an unfinished day `complete:false`. Reject empty, inverted, ambiguous and unknown-policy ranges.

- [x] **Step 4: Add range-aware database queries**

```python
def query_meals(
    connection: sqlite3.Connection, *, occurred_on: date | None = None,
    start_utc: datetime | None = None, end_utc: datetime | None = None,
    meal_type: str | None = None, timezone_name: str = "UTC",
) -> tuple[MealRecord, ...]:
    return tuple(
        record for _, record in _query_meal_targets(
            connection, occurred_on=occurred_on, start_utc=start_utc,
            end_utc=end_utc, meal_type=meal_type, timezone_name=timezone_name,
        )
    )

def query_water(
    connection: sqlite3.Connection, *, start_utc: datetime,
    end_utc: datetime, timezone_name: str = "UTC",
) -> WaterSummary:
    return _query_water_range(
        connection, start_utc=start_utc, end_utc=end_utc,
        timezone_name=timezone_name,
    )

def query_body_weight(
    connection: sqlite3.Connection, *, now: datetime,
    start_utc: datetime | None = None, end_utc: datetime | None = None,
    limit: int = 20,
) -> BodyWeightSummary:
    return _query_body_weight_range(
        connection, now=now, start_utc=start_utc,
        end_utc=end_utc, limit=limit,
    )
```

Keep legacy single-day calls working. Do not map a time segment to `meal_type`; apply `meal_type` only when the caller explicitly supplies it.

- [x] **Step 5: Add open descriptor TypeBox schemas**

```typescript
const CalendarWindowSchema = strictObject({
  unit: PolicyKeySchema,
  offset: Type.Integer({ minimum: -10000, maximum: 10000 }),
  segment: Type.Optional(PolicyKeySchema),
});
const RollingWindowSchema = strictObject({
  value: Type.Number({ exclusiveMinimum: 0, maximum: 10000 }),
  unit: PolicyKeySchema,
});
const LocalRangeSchema = strictObject({ start: LocalDateTimeSchema, end: LocalDateTimeSchema });
```

Use schema mutual-exclusion constraints; identifiers remain open-but-bounded and Python registry validation decides whether a key exists.

- [x] **Step 6: Run GREEN and legacy query regressions**

Run: `python -m pytest tests/test_temporal_queries.py tests/contracts/test_meal_water_contracts.py tests/contracts/test_body_weight_contracts.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/temporal-query-schema.test.ts src-tests/all-actions-schema.test.ts --configLoader runner`

Expected: PASS.

- [x] **Step 7: Record audit checkpoint**

Checkpoint: Python temporal tests passed 13/13; temporal, body-weight,
meal/water and calendar-expiry regressions passed 48/48. TypeScript temporal,
seven-action inventory and body-weight schema tests passed 10/10. Coverage
includes cross-day scopes shared by meal/water/weight, day/week/month,
23-hour DST days, ambiguous/nonexistent wall times, mutually exclusive modes,
and a newly registered `post_workout` segment using the existing operator.

Record changed paths and test counts in the execution ledger.

### Task 3: 当地时间公共投影

**Files:**
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/timezones.py`
- Test: `tests/test_local_time_projection.py`
- Modify: `tests/contracts/test_body_weight_contracts.py`
- Modify: `tests/contracts/test_meal_water_contracts.py`

**Interfaces:**
- Consumes: profile IANA timezone and `local_datetime`.
- Produces: meal/water `occurred_at_local`, weight `measured_at_local`, and `timezone_name`, while preserving existing UTC fields.

- [x] **Step 1: Write failing projection tests**

```python
def test_public_meal_water_and_weight_project_shanghai_local_time(service):
    # 2026-08-03T14:37:00Z -> 2026-08-03T22:37:00+08:00
    # 2026-08-03T17:25:00Z -> 2026-08-04T01:25:00+08:00
    assert meal["occurred_at"] == "2026-08-03T14:37:00Z"
    assert meal["occurred_at_local"] == "2026-08-03T22:37:00+08:00"
    assert weight["measured_at_local"] == "2026-08-04T01:25:00+08:00"
    assert meal["timezone_name"] == weight["timezone_name"] == "Asia/Shanghai"
```

Add a DST-zone case proving offsets come from IANA rules instead of hardcoded `+08:00`.

- [x] **Step 2: Run RED**

Run: `python -m pytest tests/test_local_time_projection.py -q`

Expected: FAIL because the new local projection fields are absent.

- [x] **Step 3: Implement one shared projection helper**

```python
def _public_local_timestamp(value: datetime, timezone_name: str) -> str:
    return local_datetime(value, timezone_name).isoformat()
```

Use it only in public meal, water and weight mappings; do not rewrite stored timestamps or selector values.

- [x] **Step 4: Run GREEN and persistence regressions**

Run: `python -m pytest tests/test_local_time_projection.py tests/contracts/test_meal_water_contracts.py tests/contracts/test_body_weight_contracts.py tests/integration/test_data_correctness_migration.py -q`

Expected: PASS; stored timestamps remain UTC and migration count stays unchanged.

- [x] **Step 5: Record audit checkpoint**

Checkpoint: the local projection RED tests failed on absent fields, then passed
2/2 after adding one IANA-backed projection helper. Meal/water/body-weight and
data-correctness migration regressions passed 33/33. Public responses preserve
UTC fields and add local event fields plus `timezone_name`; direct SQLite checks
prove stored values remain UTC. New York winter/summer fixtures prove `-05:00`
and `-04:00` come from timezone rules rather than a fixed offset.

Record test evidence in the execution ledger.

### Task 4: 完整库存状态报告与可证明库存关系

**Files:**
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/reports.py`
- Modify: `tests/contracts/test_report_system_contracts.py`
- Test: `tests/test_expiring_report_completeness.py`
- Test: `tests/test_inventory_lineage_projection.py`

**Interfaces:**
- Consumes: `report-taxonomy` policies and existing `reports.describe_expiry`.
- Produces: `complete`, `state_counts`, `range`, stable sorted `batches`, and all remaining expired batches plus future batches inside `within_days`.
- Produces: pantry search candidates with registered `inventory_kind` and evidence-backed `relations`; raw and prepared rows without a formal relation remain separate facts.

- [x] **Step 1: Write failing report tests**

```python
def test_expiring_report_includes_past_expired_remaining_batches(service):
    # Add one expired batch with remaining quantity, one consumed batch,
    # one future-in-window batch and one future-outside-window batch.
    result = report_expiring(service, report_date="2026-08-04", within_days=7)
    assert [item["normalized_name"] for item in result["data"]["batches"]] == [
        "boiled-egg", "tofu-pudding"
    ]
    assert result["data"]["complete"] is True
    assert result["data"]["state_counts"]["expired"] == 1
```

- [x] **Step 2: Run RED**

Run: `python -m pytest tests/test_expiring_report_completeness.py -q`

Expected: FAIL because `_report_expiring` excludes dates before `today` and omits completeness metadata.

- [x] **Step 3: Implement the complete read-only collection**

Filter to active/opened/frozen/thawed rows with `remaining_quantity > 0` and a known expiry. Include every `expiry_state=expired`; include non-expired rows only through local `cutoff`. Sort by UTC expiry then stable public batch attributes. Return exact state counts, `complete:true`, and the local/UTC report range. Do not discard, consume or adjust any batch.

- [x] **Step 4: Run GREEN and read-only regressions**

Run: `python -m pytest tests/test_expiring_report_completeness.py tests/contracts/test_report_system_contracts.py tests/test_report_goal_truth.py -q`

Expected: PASS and business table snapshots remain identical.

- [x] **Step 5: Write RED tests for inventory relation projection**

```python
def test_prepared_food_search_projects_only_formal_cooking_relation(service):
    prepared = cook_and_store_leftover(service, raw_food="egg", prepared_food="boiled-egg")
    candidate = pantry_search(service, "boiled egg")["data"]["candidates"][0]
    assert candidate["inventory_kind"] == "prepared_food"
    assert candidate["relations"] == [{
        "relation_type": "prepared_from_cooking",
        "evidence_type": "committed_transaction",
        "summary": "由已提交的烹饪事务生成",
    }]

def test_unrelated_raw_and_prepared_counts_are_not_reported_as_containing_each_other(service):
    add_raw_and_unlinked_prepared_eggs(service, raw_count="22", prepared_count="3")
    candidates = pantry_search(service, "egg")["data"]["candidates"]
    assert all(candidate["relations"] == [] for candidate in candidates)
```

Run: `python -m pytest tests/test_inventory_lineage_projection.py -q`

Expected: FAIL because current pantry search candidates expose handles but no public inventory kind or relation projection.

- [x] **Step 6: Implement relation projection from existing committed facts**

Use `prepared_food_profiles.source_meal_id` joined through the existing prepared batch as evidence. Return only registered public relation names and summaries; never expose meal IDs, batch IDs or transaction IDs. A normal raw batch returns `inventory_kind:"raw_food"` and `relations:[]`. Do not infer containment, change stock or manufacture history for unlinked rows.

- [x] **Step 7: Run lineage GREEN and existing prepared-food regressions**

Run: `python -m pytest tests/test_inventory_lineage_projection.py tests/contracts/test_prepared_food_direct_contracts.py tests/contracts/test_live_intake_regressions.py -q`

Expected: PASS with unchanged inventory deductions and atomic cooking behavior.

- [x] **Step 8: Record audit checkpoint**

Checkpoint: the report RED fixture reproduced the missing expired batch. The
complete collection then passed 1/1 and report/read-only regressions passed
22/22, including a business-table snapshot with no report writes. Lineage RED
tests failed on absent projections, then passed 2/2; prepared-food and live
intake regressions passed 37/37. Search results expose only registered kinds and
a cooking relation backed by a still-committed transaction. Ordinary raw or
merely processed-looking names retain independent quantities and empty
relations; persistence identifiers are not exposed.

Record changed paths and test evidence.

### Task 5: 模糊份量 resolution、一次预览与确认后提交

**Files:**
- Modify: `python/personal_diet_pantry/meals.py`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `src/schemas.ts`
- Modify: `src/index.ts`
- Test: `tests/contracts/test_meal_water_contracts.py`
- Test: `tests/test_quantity_resolution.py`
- Test: `src-tests/quantity-estimate-schema.test.ts`

**Interfaces:**
- Consumes: `quantity-evidence` policy and existing `portion_expression`, meal preview handle and `commit_record`.
- Produces: optional `quantity_estimate {suggested, lower, upper, unit, evidence_type, policy_key}` on meal items.
- Produces: `resolution {subject, state, normalized_value, interval, evidence, policy_key, requires_confirmation, confirmation_options, warnings}` in unconfirmed preview responses.
- Produces: new internal `ConfirmationReason.PORTION_ESTIMATE_UNCONFIRMED` that `diet_meal record` never auto-confirms.

- [ ] **Step 1: Write failing zero-write and single-commit tests**

```python
def test_vague_portion_returns_bounded_preview_without_writing(service):
    before = snapshot_business_tables(service.connection)
    preview = record_meal(service, portion_expression="一点", amount="25", unit="g",
                          quantity_estimate={"suggested": "25", "lower": "10", "upper": "40",
                                             "unit": "g", "evidence_type": "household_range",
                                             "policy_key": "portion.generic.small_amount"})
    assert preview["ok"] is True
    assert preview["requires_confirmation"] is True
    assert preview["data"]["resolution"]["state"] == "bounded_estimate"
    assert snapshot_business_tables(service.connection) == before
    committed = commit_meal(service, preview["data"]["preview"]["workflow"]["commit_handle"])
    assert committed["ok"] is True
    assert count_active_meals(service.connection) == 1
```

Add four named tests with literal outcomes:

```python
def test_confirmed_learned_portion_records_without_an_estimate_preview(service):
    learn_portion(service, food="peanut", expression="我的一小把", amount="18", unit="g")
    result = record_meal(service, portion_expression="我的一小把")
    assert result["outcome"] == "write_committed"

def test_invalid_quantity_estimate_bounds_fail_without_writes(service):
    before = snapshot_business_tables(service.connection)
    result = record_meal(service, quantity_estimate={
        "suggested": "25", "lower": "40", "upper": "10", "unit": "g",
        "evidence_type": "household_range", "policy_key": "portion.generic.small_amount",
    })
    assert result["ok"] is False
    assert snapshot_business_tables(service.connection) == before

def test_changed_quantity_requires_a_new_preview_instead_of_old_handle(service):
    original = vague_portion_preview(service, suggested="25")
    changed = vague_portion_preview(service, suggested="5")
    assert original["data"]["preview"]["workflow"] != changed["data"]["preview"]["workflow"]

def test_denied_event_trace_has_zero_tool_calls():
    trace = load_stabilization_trace("denied-vague-intake")
    assert trace["tool_calls"] == []
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_quantity_resolution.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/quantity-estimate-schema.test.ts --configLoader runner`

Expected: FAIL because quantity estimate metadata, resolution output and hard confirmation reason do not exist.

- [ ] **Step 3: Implement validated estimate metadata and hard confirmation**

```python
class ConfirmationReason(StrEnum):
    PORTION_ESTIMATE_UNCONFIRMED = "portion_estimate_unconfirmed"

def _quantity_resolution(payload: Mapping[str, Any], preview: MealPreview) -> Mapping[str, Any] | None:
    """Build a public bounded-estimate or unresolved resolution from declared evidence."""
```

Require `0 < lower <= suggested <= upper`, matching units, registered evidence and policy keys, and a non-empty original `portion_expression`. When the expression has a confirmed learned portion, treat that rule as exact and do not require another confirmation. `record` may auto-commit nutrition-only soft confidence but must return the preview for `PORTION_ESTIMATE_UNCONFIRMED`.

- [ ] **Step 4: Implement the TypeBox input contract**

Add bounded decimal strings/numbers for `suggested/lower/upper`, open-but-bounded policy identifiers and `dependentRequired` so estimate metadata cannot appear without `portion_expression`, amount and unit.

- [ ] **Step 5: Run GREEN and mutation regressions**

Run: `python -m pytest tests/test_quantity_resolution.py tests/contracts/test_meal_water_contracts.py tests/contracts/test_live_intake_regressions.py tests/test_intake_identity.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/quantity-estimate-schema.test.ts src-tests/meal-normalization.test.ts src-tests/all-actions-schema.test.ts --configLoader runner`

Expected: PASS; confirmation-before-write and one final commit are demonstrated by business-table assertions.

- [ ] **Step 6: Record audit checkpoint**

Record red/green evidence and changed paths.

### Task 6: Skill 路由、调用预算、事件状态、库存关系与正式事实降级

**Files:**
- Modify: `skills/personal-diet-pantry/SKILL.md`
- Create: `skills/personal-diet-pantry/references/time-and-query-scopes.md`
- Create: `skills/personal-diet-pantry/references/estimation-and-confirmation.md`
- Create: `skills/personal-diet-pantry/references/tool-budget-and-recovery.md`
- Modify: `skills/personal-diet-pantry/references/meal-and-nutrition.md`
- Modify: `skills/personal-diet-pantry/references/pantry-and-expiry.md`
- Modify: `skills/personal-diet-pantry/references/goals-preferences-learning.md`
- Modify: `skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md`
- Modify: `tests/test_skill_progressive_disclosure.py`
- Create: `tests/test_skill_stabilization.py`
- Modify: `tests/skill-evals/routing.yaml`

**Interfaces:**
- Consumes: Tasks 1–5 public descriptors, scope metadata, estimate resolution and existing public handles.
- Produces: compact main Skill routing plus on-demand time, estimation and budget references.
- Produces: route budgets: simple read/write 1, targeted search+operation 2, correction/undo 2, estimate preview up to 2, confirmation commit 1; only declared cursor progress may extend a route.

- [ ] **Step 1: Add failing behavioral/pressure tests before editing Skill prose**

```yaml
cases:
  - id: cross-day-all-intake
    prompt: 昨天晚上到今天凌晨都吃喝了什么
    expected_reference: time-and-query-scopes.md
    allowed_tools: [diet_meal]
    forbidden_tools: [diet_pantry, diet_system]
    write_expectation: read
  - id: vague-home-intake
    prompt: 刚吃了一点库存里的花生
    expected_reference: estimation-and-confirmation.md
    allowed_tools: [diet_meal]
    forbidden_tools: [diet_report, diet_system]
    write_expectation: preview
  - id: negated-vague-intake
    prompt: 昨天本来想吃一些花生但最后没吃
    expected_reference: SKILL.md
    allowed_tools: []
    forbidden_tools: [diet_meal, diet_pantry, diet_report, diet_system]
    write_expectation: zero
  - id: preference-read
    prompt: 我有什么忌口
    expected_reference: goals-preferences-learning.md
    allowed_tools: [diet_system]
    forbidden_tools: [exec]
    write_expectation: read
  - id: processed-stock-relationship
    prompt: 这3个水煮蛋算不算在22个生鸡蛋里面
    expected_reference: pantry-and-expiry.md
    allowed_tools: [diet_pantry]
    forbidden_tools: [diet_meal, diet_transaction]
    write_expectation: read
```

Add sanitized JSON traces for repeated failure and combined preview. Assert the former contains one failed Diet call and no Exec/file call; assert the latter contains one preview call, zero business-row differences before confirmation and exactly one commit call after confirmation.

Each test must exercise the repository's behavior-trace/evaluation consumer or a concrete routing fixture; do not merely assert one exact prose line.

- [ ] **Step 2: Run RED against the unmodified v0.7.3.5 Skill baseline**

Run: `python -m pytest tests/test_skill_stabilization.py tests/test_skill_evaluation.py -q`

Expected: FAIL for missing generalized routes, estimate confirmation and bounded recovery.

- [ ] **Step 3: Write minimal progressive-disclosure Skill changes**

Keep `SKILL.md` below 500 lines. Add only stable rules:

```text
event status -> independent fact resolutions -> bounded route plan ->
read/preview or confirmed write -> one reply from terminal result
```

Move descriptor details and examples to one-level references. State that natural-language examples are tests, not a closed runtime keyword list. Require one combined preview for multiple bounded estimates; a negative/planned/hypothetical event short-circuits before time or quantity work. Preference failure stops without Exec/file search. Inventory inclusion/deduction claims require returned relation or committed `inventory_effects`.

- [ ] **Step 4: Preserve the protected reply contract**

Run: `python -m pytest tests/test_skill_progressive_disclosure.py::test_post_commit_progress_receipt_preserves_the_legacy_renderer -q`

Expected: PASS with the exact six metrics, two-line layout and no `📊 今日进度：` title after successful writes.

- [ ] **Step 5: Run GREEN skill evaluation**

Run: `python -m pytest tests/test_skill_stabilization.py tests/test_skill_evaluation.py tests/test_skill_progressive_disclosure.py tests/test_sensitive_content_scan.py -q`

Expected: PASS.

- [ ] **Step 6: Forward-test unfamiliar equivalent scenarios**

Use fresh-context evaluators with the v0.7.3.6 Skill artifact and prompts not listed verbatim in the design: one cross-day time query, one negated vague intake, one multi-estimate home-stock record, one repeated tool failure, and one ambiguous processed-stock relationship. Record raw outputs and judge tool count, zero-write boundary, confirmation shape and privacy.

- [ ] **Step 7: Record audit checkpoint**

Record evaluator prompts, outcomes, test commands and any tightened wording.

### Task 7: 版本合同、文档、构建与可安装制品

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `pyproject.toml`
- Modify: `python/personal_diet_pantry/__init__.py`
- Modify: `tests/test_version_contract.py`
- Modify: `src-tests/version-contract.test.ts`
- Create: `UPDATE-v0.7.3.6.zh-CN.md`
- Modify: `RELEASE.zh-CN.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `docs/INSTALLATION.zh-CN.md`
- Modify: `docs/TROUBLESHOOTING.zh-CN.md`
- Modify: `docs/TOOLS-REFERENCE.zh-CN.md`
- Modify: `contracts/v070-core-tests.txt`
- Modify generated build output under `dist/` by running the build.

**Interfaces:**
- Consumes: every previous task's final public behavior and tests.
- Produces: product `0.7.3.6`, SemVer `0.7.9`, prior-version declarations, update notes, generated JS/declarations and a uniquely named installable archive.

- [ ] **Step 1: Change version tests first and run RED**

```python
EXPECTED = "0.7.9"
PRODUCT_VERSION = "0.7.3.6"
PREVIOUS_EXPECTED = "0.7.8"
PREVIOUS_PRODUCT_VERSION = "0.7.3.5"
```

Add import acceptance through `0.7.3.6`, require `UPDATE-v0.7.3.6.zh-CN.md`, require no migrations beyond 021, and require the new behavior tests in the core gate.

Run: `python -m pytest tests/test_version_contract.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/version-contract.test.ts --configLoader runner`

Expected: FAIL against copied v0.7.3.5 metadata.

- [ ] **Step 2: Update every version source and release document**

Set package/plugin/Python versions to `0.7.9` and product version to `0.7.3.6`. Replace current-release references only; historical update documents stay unchanged. The new update document must state: no migration, v0.7.3.5 rollback package, protected progress renderer unchanged, UTC storage plus IANA local projection, generic time scopes, estimate confirmation, report completeness, route budgets, no automatic deployment and deferred host-streaming diagnosis.

- [ ] **Step 3: Regenerate contracts and build outputs**

Run: `python scripts/generate_tool_contracts.py`

Run: `npm run build`

Run: `python scripts/build_release.py`

Expected: build exits 0 and produces `personal-diet-pantry-0.7.3.6-installable.tgz` without overwriting an earlier artifact.

- [ ] **Step 4: Run the complete Python suite**

Run: `python -m pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 5: Run the complete TypeScript suite and package validators**

Run: `node node_modules/vitest/vitest.mjs run --configLoader runner`

Run: `python scripts/validate_skill.py skills/personal-diet-pantry`

Run: `python scripts/release_audit.py`

Expected: all commands exit 0; skill frontmatter, package contents, sensitive scan and generated contracts pass.

- [ ] **Step 6: Verify installable archive in isolation**

Run: `python -m pytest tests/integration/test_installable_e2e.py tests/test_build_release.py tests/test_release_audit.py -q`

Expected: isolated archive installs and exposes the same seven tools; migration list remains 001–021.

- [ ] **Step 7: Compare protected behavior and previous version immutability**

Hash the v0.7.3.5 tree before/after execution excluding test caches and compare byte-for-byte. Re-run the protected progress test and inspect the archive manifest for product `0.7.3.6`/SemVer `0.7.9`.

- [ ] **Step 8: Record final audit checkpoint**

Record full suite counts, build artifact name/hash, migration count, v0.7.3.5 comparison result, and explicitly note that upload/install/restart were not performed in this implementation task.
