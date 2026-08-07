# Personal Diet Pantry v0.7.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one complete, reusable v0.7.2 Skill that gives models a preferred tool route with progress-based recovery, performs bounded keyword-first inventory resolution, combines targeted stock and nutrition reads, and substantially reduces the meal tool schema without removing behavior.

**Architecture:** Keep `SKILL.md` as the stable controller, domain references as progressive disclosure, and `contracts/tools.yaml` as the machine-checked action source. Add a bounded `diet_pantry search` action backed by deterministic SQLite filtering and short-lived product references; allow `diet_meal` to consume an explicitly selected product reference without a separate pantry preflight. Compact the eight-level meal schema with local JSON Schema definitions and references rather than reducing supported depth.

**Tech Stack:** Markdown Skill package, TypeScript 5.9, TypeBox 1.1, OpenClaw plugin API, Python 3.11+, SQLite migrations, pytest 8, Vitest 3, YAML-generated contracts.

## Global Constraints

- Product target is exactly `0.7.2`; baseline and direct rollback version is `0.7.1`.
- The deliverable is the reusable Skill and its bundled deterministic support, not an OpenClaw, Telegram, Docker, Lucky, Gateway, WireGuard, or soft-router deployment.
- Preserve all seven public tools: `diet_meal`, `diet_water`, `diet_weight`, `diet_pantry`, `diet_transaction`, `diet_report`, and `diet_system`.
- Do not cap recovery by a fixed number of calls; every follow-up attempt must add evidence, repair a named field, resolve ambiguity, verify outcome, or use a contract-verified equivalent capability.
- Never repeat the same capability, tool, action, normalized arguments, and error signature unchanged.
- A replacement tool is permitted only when the preferred capability is absent or renamed and the visible description/Schema proves equivalent input, output, and safety semantics.
- Business failures such as insufficient stock, low confidence, or non-undoable state cannot be bypassed by changing tools.
- Clear single-domain requests do not perform unrelated pantry, report, preview, or self-check calls.
- Inventory consumption resolves by targeted name/alias search; complete inventory is paginated only when the user explicitly asks to browse it.
- Keep `nutrition_profiles`, `pantry_nutrition_links`, snapshots, and cache normalized inside the same `diet.sqlite`; combine them at the tool response boundary instead of copying nutrition into every batch.
- `diet_pantry search` returns at most five compact product candidates and defaults to `nutrition_mode=none`.
- `nutrition_mode` is exactly `none | summary | full`; full nutrition is only returned for an explicit full-label request.
- Preserve eight supported meal ingredient levels; compact the Schema with `$defs/$ref`, not by lowering the depth.
- Preserve package floors: Node `>=22.22.3`, OpenClaw peer `>=2026.5.17`, pinned development OpenClaw `2026.7.1-2`, Python `>=3.11,<4`.
- Follow RED-GREEN-REFACTOR for every behavior change and commit after each independently passing task.
- Execute in an isolated `codex/v0.7.2-skill-routing` worktree so unrelated untracked files in the parent workspace are not modified or included.
- Use bundled Python `C:\Users\example-user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`; set `PDP_PYTHON` to that path before running `ci/verify.ps1`.

## File Responsibility Map

| Area | Files | Responsibility |
|---|---|---|
| Stable Skill controller | `skills/personal-diet-pantry/SKILL.md` | Activation, capability routing, write readiness, progress-based recovery, compact reply contract |
| Domain guidance | `skills/personal-diet-pantry/references/pantry-and-expiry.md`, `meal-and-nutrition.md`, `reply-style-and-error-boundaries.md` | Targeted pantry resolution, measurement basis, recoverable/terminal error behavior |
| Public schemas | `src/schemas.ts` | Compact meal `$defs`, pantry search input, product-reference input |
| Tool contract | `contracts/tools.yaml`, `scripts/generate_tool_contracts.py` and generated outputs | Single action inventory, Skill quick routes, mutation/retry metadata |
| Inventory domain | `python/personal_diet_pantry/inventory_matching.py`, `pantry.py` | Bounded exact/alias/keyword product search and deterministic batch behavior |
| Nutrition projection | `python/personal_diet_pantry/nutrition_profiles.py` | Uniform/partial/mixed linked nutrition projection for one targeted product |
| Service boundary | `python/personal_diet_pantry/service.py` | `pantry.search`, candidate handles, nutrition modes, structured errors, meal handle reuse |
| Persistence | `migrations/020_inventory_search.sql` | Indexed product lookup and `pantry_product_reference` workflow type |
| Reliability | `src/reliability.ts` | Generated formal-mutation inventory and outcome verification |
| Behavior tests | `tests/skill-evals/routing.yaml`, `tests/test_skill_progressive_disclosure.py`, Skill contract tests | Skill navigation and no-broad-query contract |
| Tool tests | New inventory search tests plus existing TypeScript/Python contract suites | Bounded search, nutrition modes, handles, Schema size, non-regression |
| Release | Version files, README/release/update docs, build scripts and tests | One coherent v0.7.2 package and rollback documentation |

---

### Task 1: Compact the eight-level meal Schema without changing accepted behavior

**Files:**
- Create: `src-tests/schema-size.test.ts`
- Modify: `src/schemas.ts:1-45,349-440,565-708`
- Modify: `src-tests/intake-schema.test.ts`

**Interfaces:**
- Consumes: existing `MealParametersSchema` action union and eight-level `MealItemSchema` semantics.
- Produces: `MealParametersSchema` with local `$defs.pdpMealItem1` through `$defs.pdpMealItem8`, unchanged public actions, and serialized size below 125,000 bytes.

- [ ] **Step 1: Write the failing Schema size and depth tests**

Create `src-tests/schema-size.test.ts` with these assertions:

```ts
import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { MealParametersSchema } from "../src/schemas.js";

const facts = {
  calories: 10, protein: 1, fat: 0, carbohydrate: 1,
  fiber: 0, sodium: 0, source: "fixture", source_grade: "A",
};

function nestedItem(depth: number): Record<string, unknown> {
  const item: Record<string, unknown> = {
    raw_name: `level-${depth}`,
    normalized_name: `level-${depth}`,
    consumed_weight_g: 100,
    nutrition_basis: "per_100g",
    nutrition_facts: facts,
  };
  if (depth > 1) item.ingredients = [nestedItem(depth - 1)];
  return item;
}

function request(depth: number) {
  return {
    action: "record",
    occurred_at: "2026-08-02T08:00:00+08:00",
    meal_type: "breakfast",
    source_text: "层级测试",
    location_type: "home",
    items: [nestedItem(depth)],
  };
}

describe("meal public schema budget", () => {
  it("stays below 125 KB while keeping every public action", () => {
    expect(JSON.stringify(MealParametersSchema).length).toBeLessThan(125_000);
  });

  it("accepts eight ingredient levels and rejects nine", () => {
    expect(Value.Check(MealParametersSchema, request(8))).toBe(true);
    expect(Value.Check(MealParametersSchema, request(9))).toBe(false);
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
npm test -- --run src-tests/schema-size.test.ts
```

Expected: the size assertion fails because the current serialized schema is about 1,000,414 bytes; the depth assertions still document the required boundary.

- [ ] **Step 3: Replace recursive structural expansion with local definitions**

In `src/schemas.ts`, import `type TSchemaOptions`, keep `MAX_INGREDIENT_LEVELS = 8`, and replace `mealItemSchema(level)` with one definition per depth and local references:

```ts
const mealItemDefinitionName = (level: number) => `pdpMealItem${level}`;
const mealItemRef = (level: number) =>
  Type.Ref(`#/$defs/${mealItemDefinitionName(level)}`);

function mealItemDefinition(level: number): TSchema {
  const base = strictObject({
    raw_name: Type.String({ minLength: 1 }),
    normalized_name: Type.String({ minLength: 1 }),
    amount: Type.Optional(NonNegativeQuantitySchema),
    unit: Type.Optional(Type.String({ minLength: 1 })),
    portion_expression: Type.Optional(Type.String({ minLength: 1 })),
    consumed_weight_g: Type.Optional(NonNegativeQuantitySchema),
    consumed_volume_ml: Type.Optional(NonNegativeQuantitySchema),
    consumed_servings: Type.Optional(NonNegativeQuantitySchema),
    raw_weight_g: Type.Optional(NonNegativeQuantitySchema),
    inventory_deduction_weight_g: Type.Optional(NonNegativeQuantitySchema),
    edible_ratio: Type.Optional(PositiveRatioSchema),
    cooking_yield: Type.Optional(PositiveQuantitySchema),
    nutrition_basis: Type.Optional(NutritionBasisSchema),
    nutrition_dataset_version: Type.Optional(Type.String({ minLength: 1 })),
    nutrition_facts: Type.Optional(NutritionFactsSchema),
    preparation_losses: Type.Optional(
      Type.Array(PreparationLossSchema, { maxItems: 8 }),
    ),
    brand: Type.Optional(Type.String({ minLength: 1 })),
    nutrition_estimate: Type.Optional(NutritionEstimateSchema),
    source_confidence: Type.Optional(ConfidenceSchema),
    name_match_confidence: Type.Optional(ConfidenceSchema),
    quantity_confidence: Type.Optional(ConfidenceSchema),
    batch_uniqueness: Type.Optional(ConfidenceSchema),
    context_consistency: Type.Optional(ConfidenceSchema),
    personal_rule_confidence: Type.Optional(ConfidenceSchema),
    confidence_signals: Type.Optional(ConfidenceSignalsSchema),
    leftover: Type.Optional(LeftoverSchema),
    ...(level < MAX_INGREDIENT_LEVELS
      ? {
          ingredients: Type.Optional(Type.Array(mealItemRef(level + 1), {
            maxItems: MAX_INGREDIENT_CHILDREN,
          })),
        }
      : {}),
  });
  return Type.Intersect([base, DirectNutritionEvidenceSchema]);
}

const MealItemDefinitions = Object.fromEntries(
  Array.from({ length: MAX_INGREDIENT_LEVELS }, (_, index) => {
    const level = index + 1;
    return [mealItemDefinitionName(level), mealItemDefinition(level)];
  }),
);
const MealItemSchema = mealItemRef(1);
```

Extend `boundedActionUnion` to accept root options and attach the definitions only once:

```ts
function boundedActionUnion<Branches extends TSchema[]>(
  branches: [...Branches],
  options: TSchemaOptions = {},
) {
  branches.forEach((branch) => applyPublicBounds(branch));
  const allowedProperties: Record<string, TSchema> = {};
  for (const branch of branches) {
    const properties = (branch as { properties?: Record<string, TSchema> }).properties;
    for (const key of Object.keys(properties ?? {})) allowedProperties[key] = Type.Unknown();
  }
  return Type.Union(branches, {
    ...options,
    properties: allowedProperties,
    additionalProperties: false,
  });
}

```

Leave all existing `MealParametersSchema` action branches in their current order. Change only the call terminator from `]);` to `], { $defs: MealItemDefinitions });` so the definitions are attached once at the union root.

- [ ] **Step 4: Strengthen value-equivalence coverage**

In `src-tests/intake-schema.test.ts`, keep every current valid/invalid case and add one assertion that an unknown nested property is rejected through a `$ref`:

```ts
it("keeps nested ingredient objects closed through local references", () => {
  const nested = {
    ...soyRequest.items[0],
    ingredients: [{
      raw_name: "糖",
      normalized_name: "sugar",
      unexpected: true,
    }],
  };
  expect(Value.Check(MealParametersSchema, {
    ...soyRequest,
    items: [nested],
  })).toBe(false);
});
```

- [ ] **Step 5: Run focused and full TypeScript tests**

Run:

```powershell
npm run build
npm test -- --run src-tests/schema-size.test.ts src-tests/intake-schema.test.ts src-tests/all-actions-schema.test.ts
```

Expected: build succeeds; all focused tests pass; serialized `MealParametersSchema` is below 125,000 bytes and depth eight remains valid.

- [ ] **Step 6: Commit**

```powershell
git add src/schemas.ts src-tests/schema-size.test.ts src-tests/intake-schema.test.ts
git commit -m "perf: compact the meal tool schema"
```

---

### Task 2: Add bounded inventory product search and indexed persistence support

**Files:**
- Create: `migrations/020_inventory_search.sql`
- Create: `tests/integration/test_inventory_search_migration.py`
- Create: `tests/contracts/test_inventory_search_contracts.py`
- Modify: `python/personal_diet_pantry/inventory_matching.py`

**Interfaces:**
- Consumes: `learning.learned_food_alias`, `_match_key`, pantry statuses, canonical unit aliases, and SQLite batch facts.
- Produces: `InventorySearchCandidate` and `search_inventory_candidates(connection, search_text, *, unit=None, statuses=None, storage_location=None, limit=5)`.

- [ ] **Step 1: Write migration RED tests**

Create `tests/integration/test_inventory_search_migration.py`:

```python
from pathlib import Path

from personal_diet_pantry.database import apply_migrations, connect_database


ROOT = Path(__file__).resolve().parents[2]


def test_inventory_search_migration_adds_index_and_product_reference(tmp_path):
    connection = connect_database(tmp_path / "diet.sqlite")
    apply_migrations(connection, ROOT / "migrations")

    indexes = {
        row["name"]
        for row in connection.execute("PRAGMA index_list('pantry_batches')")
    }
    assert "idx_pantry_batches_search" in indexes

    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='operation_previews'"
    ).fetchone()["sql"]
    assert "pantry_product_reference" in sql
```

- [ ] **Step 2: Run the migration test and verify RED**

Run:

```powershell
$PdpPython = 'C:\Users\example-user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $PdpPython -m pytest tests/integration/test_inventory_search_migration.py -q
```

Expected: FAIL because migration 020 and the index/workflow type do not exist.

- [ ] **Step 3: Create migration 020**

Create `migrations/020_inventory_search.sql` with these operations in order:

```sql
CREATE INDEX idx_pantry_batches_search
ON pantry_batches (
    normalized_name COLLATE NOCASE,
    unit COLLATE NOCASE,
    status,
    remaining_quantity
);

ALTER TABLE operation_previews
RENAME TO operation_previews_before_inventory_search;

CREATE TABLE operation_previews (
    token_hash TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'meal_preview', 'water_preview', 'pantry_add_preview',
        'pantry_deduct_preview', 'pantry_adjust_preview', 'water_reference',
        'weight_reference', 'pantry_batch_reference', 'pantry_product_reference',
        'meal_reference', 'transaction_undo_reference',
        'transaction_redo_reference', 'backup_reference',
        'shopping_list_preview', 'shopping_list_reference', 'import_preview',
        'delete_data_preview', 'export_reference', 'restore_preview'
    )),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resource_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at, 0
    )),
    expires_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at, 0
    )),
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', consumed_at, '+0 seconds') = consumed_at, 0
        )
    ),
    transaction_id TEXT REFERENCES transactions(id) ON DELETE SET NULL,
    CHECK (julianday(expires_at) > julianday(created_at))
);

INSERT INTO operation_previews (
    token_hash, operation_type, request_json, result_json,
    resource_versions_json, created_at, expires_at, consumed_at, transaction_id
)
SELECT token_hash, operation_type, request_json, result_json,
       resource_versions_json, created_at, expires_at, consumed_at, transaction_id
FROM operation_previews_before_inventory_search;

DROP TABLE operation_previews_before_inventory_search;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews(expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews(operation_type, expires_at, consumed_at);
```

- [ ] **Step 4: Write the domain search RED tests**

In `tests/contracts/test_inventory_search_contracts.py`, seed 100 unrelated products, two egg batches, two milk SKUs, one learned alias, and assert:

```python
def test_search_is_bounded_and_aggregates_same_product(service):
    candidates = inventory_matching.search_inventory_candidates(
        service.connection, "鸡蛋", unit="piece", limit=5
    )
    assert len(candidates) == 1
    assert candidates[0].normalized_name == "鸡蛋"
    assert candidates[0].batch_count == 2
    assert candidates[0].available_quantity == Decimal("32")


def test_search_returns_distinct_milk_products_without_auto_choice(service):
    candidates = inventory_matching.search_inventory_candidates(
        service.connection, "牛奶", unit="ml", limit=5
    )
    assert {item.normalized_name for item in candidates} == {
        "小象巴氏乳", "川象鲜牛奶"
    }


def test_search_uses_learned_alias_before_keyword_expansion(service):
    # Seed an active food_alias rule mapping “早餐奶” to “川象鲜牛奶”.
    candidates = inventory_matching.search_inventory_candidates(
        service.connection, "早餐奶", unit="ml", limit=5
    )
    assert len(candidates) == 1
    assert candidates[0].match_kind == "learned_alias"


def test_search_never_materializes_every_inventory_name(service):
    statements = []
    service.connection.set_trace_callback(statements.append)
    inventory_matching.search_inventory_candidates(
        service.connection, "鸡蛋", unit="piece", limit=5
    )
    assert not any(
        "SELECT DISTINCT normalized_name FROM pantry_batches" in sql
        for sql in statements
    )
```

- [ ] **Step 5: Run the domain tests and verify RED**

Run:

```powershell
& $PdpPython -m pytest tests/contracts/test_inventory_search_contracts.py -q
```

Expected: collection or assertion failure because `InventorySearchCandidate` and `search_inventory_candidates` do not exist.

- [ ] **Step 6: Implement the bounded search domain**

In `inventory_matching.py`, add:

```python
@dataclass(frozen=True)
class InventorySearchCandidate:
    food_name: str
    normalized_name: str
    unit: str
    available_quantity: Decimal
    batch_count: int
    match_kind: str


def search_inventory_candidates(
    connection: sqlite3.Connection,
    search_text: str,
    *,
    unit: str | None = None,
    statuses: tuple[str, ...] | None = None,
    storage_location: str | None = None,
    limit: int = 5,
) -> tuple[InventorySearchCandidate, ...]:
    requested = _required_text(search_text, "search_text")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit must be between 1 and 5")
    normalized_unit = canonical_inventory_unit(unit) if unit is not None else None
    eligible = statuses or _ELIGIBLE_STATUSES

    learned = learning.learned_food_alias(connection, requested)
    if learned is not None:
        alias_rows = _candidate_rows(
            connection,
            exact_text=learned,
            contains_text=None,
            unit=normalized_unit,
            statuses=eligible,
            storage_location=storage_location,
            limit=limit,
        )
        if alias_rows:
            return _search_candidates(alias_rows, "learned_alias")

    exact_rows = _candidate_rows(
        connection,
        exact_text=requested,
        contains_text=None,
        unit=normalized_unit,
        statuses=eligible,
        storage_location=storage_location,
        limit=limit,
    )
    if exact_rows:
        return _search_candidates(exact_rows, "exact")

    keyword_rows = _candidate_rows(
        connection,
        exact_text=None,
        contains_text=requested,
        unit=normalized_unit,
        statuses=eligible,
        storage_location=storage_location,
        limit=limit,
    )
    return _search_candidates(keyword_rows, "keyword")
```

Implement `_candidate_rows` as one grouped SQL query with status/unit/location predicates, `remaining_quantity > 0`, exact equality before keyword `instr`, `GROUP BY normalized_name, unit`, deterministic ordering by exactness then normalized name, and `LIMIT ?`. Convert numeric output through `Decimal(str(value))`; do not query every name into Python.

- [ ] **Step 7: Run focused migration and search tests**

Run:

```powershell
& $PdpPython -m pytest tests/integration/test_inventory_search_migration.py tests/contracts/test_inventory_search_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add migrations/020_inventory_search.sql python/personal_diet_pantry/inventory_matching.py tests/integration/test_inventory_search_migration.py tests/contracts/test_inventory_search_contracts.py
git commit -m "feat: add bounded inventory product search"
```

---

### Task 3: Expose `diet_pantry search` with compact nutrition modes and product handles

**Files:**
- Modify: `contracts/tools.yaml`
- Modify: `scripts/generate_tool_contracts.py`
- Modify generated: `contracts/public-behavior.yaml`, `src/generated/tool-contracts.ts`, `python/personal_diet_pantry/generated_tool_contracts.py`, `docs/GENERATED-ACTIONS.zh-CN.md`
- Modify: `src/schemas.ts:766-885`
- Modify: `src-tests/all-actions-schema.test.ts`
- Create: `src-tests/pantry-search-schema.test.ts`
- Modify: `python/personal_diet_pantry/nutrition_profiles.py`
- Modify: `python/personal_diet_pantry/service.py`
- Extend test: `tests/contracts/test_inventory_search_contracts.py`

**Interfaces:**
- Consumes: `search_inventory_candidates`, `nutrition_profiles` snapshots, `_issue_workflow`.
- Produces: public `pantry.search`; candidate objects with `inventory_match_handle`; `nutrition_mode=none|summary|full`.

- [ ] **Step 1: Write the public Schema RED tests**

Create `src-tests/pantry-search-schema.test.ts`:

```ts
import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";
import { PantryParametersSchema } from "../src/schemas.js";

describe("pantry targeted search schema", () => {
  it("accepts bounded keyword search and nutrition mode", () => {
    expect(Value.Check(PantryParametersSchema, {
      action: "search",
      search_text: "牛奶",
      unit: "ml",
      nutrition_mode: "summary",
      limit: 5,
    })).toBe(true);
  });

  it("rejects empty text, more than five candidates, and unknown modes", () => {
    expect(Value.Check(PantryParametersSchema, {
      action: "search", search_text: "", limit: 5,
    })).toBe(false);
    expect(Value.Check(PantryParametersSchema, {
      action: "search", search_text: "牛奶", limit: 6,
    })).toBe(false);
    expect(Value.Check(PantryParametersSchema, {
      action: "search", search_text: "牛奶", nutrition_mode: "auto",
    })).toBe(false);
  });
});
```

Update `all-actions-schema.test.ts` to include `search` in the pantry action list.

- [ ] **Step 2: Run TypeScript tests and verify RED**

Run:

```powershell
npm test -- --run src-tests/pantry-search-schema.test.ts src-tests/all-actions-schema.test.ts
```

Expected: FAIL because `search` is not in `PantryParametersSchema`.

- [ ] **Step 3: Add the action to Schema and tool contract**

Add this branch to `PantryParametersSchema`:

```ts
actionBranch("search", {
  search_text: Type.String({ minLength: 1, maxLength: 120 }),
  unit: Type.Optional(PantryUnitSchema),
  storage_location: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
  statuses: Type.Optional(Type.Array(PantryStatusSchema, {
    minItems: 1,
    uniqueItems: true,
  })),
  nutrition_mode: Type.Optional(Type.Union([
    Type.Literal("none"),
    Type.Literal("summary"),
    Type.Literal("full"),
  ])),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 5 })),
}),
```

In `contracts/tools.yaml`, add handler `search: _pantry_search` and action metadata:

```yaml
search:
  mode: read
  confirmation: none
  retry: safe_read
  python_test: tests/contracts/test_inventory_search_contracts.py::test_public_search_returns_bounded_candidates_and_handles
  typescript_test: src-tests/pantry-search-schema.test.ts
```

Run the generator after its contract test has been updated to expect `search`:

```powershell
& $PdpPython scripts/generate_tool_contracts.py --root .
```

- [ ] **Step 4: Write nutrition projection RED tests**

Extend `test_inventory_search_contracts.py` with four cases:

```python
def test_public_search_returns_bounded_candidates_and_handles(service):
    result = _pantry(service, "search", {
        "search_text": "牛奶", "unit": "ml", "limit": 5
    })
    assert result["ok"] is True
    assert len(result["data"]["candidates"]) <= 5
    assert all("inventory_match_handle" in item["workflow"] for item in result["data"]["candidates"])
    assert all("nutrition" not in item for item in result["data"]["candidates"])


def test_summary_returns_one_uniform_linked_snapshot(service):
    result = _pantry(service, "search", {
        "search_text": "牛奶", "unit": "ml", "nutrition_mode": "summary"
    })
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "uniform"
    assert set(candidate["nutrition"]) == {
        "serving_basis", "source_grade", "calories_kcal", "protein_g",
        "fat_g", "carbohydrate_g", "fiber_g", "sodium_mg"
    }


def test_mixed_batch_labels_are_not_silently_merged(service):
    result = _pantry(service, "search", {
        "search_text": "牛奶", "unit": "ml", "nutrition_mode": "full"
    })
    candidate = result["data"]["candidates"][0]
    assert candidate["nutrition_status"] == "mixed"
    assert "nutrition" not in candidate
```

Add `test_partially_linked_batches_report_partial_without_nutrition`: seed two eligible batches of one product, link nutrition to only one, search with `nutrition_mode="full"`, and assert `nutrition_status == "partial"` with no `nutrition` object.

- [ ] **Step 5: Run the service tests and verify RED**

Run:

```powershell
& $PdpPython -m pytest tests/contracts/test_inventory_search_contracts.py -q
```

Expected: FAIL with unknown pantry action or missing nutrition projection.

- [ ] **Step 6: Implement nutrition projection**

In `nutrition_profiles.py`, add the projection type and function signature:

```python
@dataclass(frozen=True)
class LinkedNutritionProjection:
    status: str  # none | partial | uniform | mixed
    serving_basis: str | None
    source_grade: str | None
    nutrition: Mapping[str, str | None] | None


def linked_product_nutrition(
    connection: sqlite3.Connection,
    *,
    normalized_name: str,
    unit: str,
    statuses: tuple[str, ...],
) -> LinkedNutritionProjection:
```

Implement it with one parameterized query from eligible `pantry_batches`, left joining `pantry_nutrition_links` and `nutrition_profiles`. Filter `normalized_name` and `unit` case-insensitively, filter the supplied non-empty status tuple with generated `?` placeholders, require `remaining_quantity > 0`, and order by batch ID. Classify the result deterministically:

1. No eligible batches, or no eligible batch has a link: `none`.
2. At least one but not every eligible batch has a link: `partial`.
3. Every batch is linked but canonical `(nutrition_snapshot_json, serving_basis, source_grade)` tuples differ: `mixed`.
4. Every batch has the same canonical tuple: `uniform` and decode that one snapshot defensively.

Canonicalize each decoded snapshot again with `_canonical_json` before comparing; reject a non-object stored snapshot with `NutritionProfileValidationError`. For `summary`, select exactly `calories_kcal`, `protein_g`, `fat_g`, `carbohydrate_g`, `fiber_g`, and `sodium_mg` plus basis/grade. For `full`, return the stored snapshot map plus basis/grade. Never average or merge different labels.

- [ ] **Step 7: Implement `_pantry_search` and candidate handles**

In `service.py`, add `_pantry_search` and register it in `PANTRY_ACTIONS`:

```python
def _pantry_search(service, payload, context):
    mode = _optional_text(payload.get("nutrition_mode"), "nutrition_mode") or "none"
    if mode not in {"none", "summary", "full"}:
        raise _ServiceError(
            "INVALID_INPUT", "The request is invalid",
            field="nutrition_mode", reason="unsupported_value",
            expected="none, summary, or full", retryable=True,
        )
    limit = _positive_integer(payload.get("limit", 5), "limit")
    if limit > 5:
        raise _ServiceError(
            "INVALID_INPUT", "The request is invalid",
            field="limit", reason="out_of_range",
            expected="an integer from 1 to 5", retryable=True,
        )
    now = _operation_now(payload, context)
    candidates = inventory_matching.search_inventory_candidates(
        service.connection,
        _required_text(payload, "search_text"),
        unit=_optional_text(payload.get("unit"), "unit"),
        statuses=(
            tuple(_text_sequence(payload["statuses"], "statuses"))
            if "statuses" in payload else None
        ),
        storage_location=_optional_text(payload.get("storage_location"), "storage_location"),
        limit=limit,
    )
    return {
        "candidates": tuple(
            _pantry_search_candidate_public(service, item, mode=mode, now=now)
            for item in candidates
        ),
        "returned_count": len(candidates),
    }
```

`_pantry_search_candidate_public` issues `pantry_product_reference` with result `{normalized_name, unit}` and returns only display name, normalized name, unit, available quantity, batch count, match kind, `nutrition_available`, `nutrition_status`, optional bounded nutrition, and `workflow.inventory_match_handle`. Do not return row IDs, all batches, source text, prices, or full metadata.

- [ ] **Step 8: Run generator, Schema, and service tests**

Run:

```powershell
& $PdpPython scripts/generate_tool_contracts.py --root . --check
npm run build
npm test -- --run src-tests/pantry-search-schema.test.ts src-tests/all-actions-schema.test.ts src-tests/generated-contracts.test.ts
& $PdpPython -m pytest tests/contracts/test_inventory_search_contracts.py tests/test_tool_contract_generation.py -q
```

Expected: all pass; generated files are current.

- [ ] **Step 9: Commit**

```powershell
git add contracts/tools.yaml contracts/public-behavior.yaml scripts/generate_tool_contracts.py src/generated/tool-contracts.ts python/personal_diet_pantry/generated_tool_contracts.py docs/GENERATED-ACTIONS.zh-CN.md src/schemas.ts src-tests/all-actions-schema.test.ts src-tests/pantry-search-schema.test.ts python/personal_diet_pantry/nutrition_profiles.py python/personal_diet_pantry/service.py tests/contracts/test_inventory_search_contracts.py
git commit -m "feat: expose targeted pantry search with nutrition modes"
```

---

### Task 4: Reuse a selected inventory product directly inside one meal action

**Files:**
- Modify: `src/schemas.ts` meal item definitions
- Modify: `src-tests/intake-schema.test.ts`
- Modify: `python/personal_diet_pantry/service.py`
- Modify: `python/personal_diet_pantry/meals.py`
- Modify: `tests/contracts/test_live_intake_regressions.py`
- Extend: `tests/contracts/test_inventory_search_contracts.py`

**Interfaces:**
- Consumes: `workflow.inventory_match_handle` from `pantry.search`.
- Produces: optional meal-item `inventory_match_handle`; exact product identity verified by `pantry_product_reference` while preserving `raw_name`.

- [ ] **Step 1: Write the handle-reuse RED regression**

Add a scenario that seeds both 200ml and 250ml milk products, searches the ambiguous phrase, chooses the 250ml handle, then records one meal item with the original raw phrase:

```python
def test_selected_product_handle_avoids_requery_and_deducts_only_chosen_sku(service):
    search = _dispatch(service, "pantry", "search", {
        "search_text": "牛奶", "unit": "ml"
    })
    chosen = next(
        item for item in search["data"]["candidates"]
        if item["normalized_name"] == "小象巴氏乳"
    )
    meal = _dispatch(service, "meal", "record", {
        "occurred_at": "2026-08-02T08:00:00+08:00",
        "meal_type": "breakfast",
        "source_text": "喝了一瓶小瓶牛奶",
        "location_type": "home",
        "items": [{
            "raw_name": "小瓶牛奶",
            "normalized_name": "小象巴氏乳",
            "amount": 250,
            "unit": "ml",
            "inventory_match_handle": chosen["workflow"]["inventory_match_handle"],
        }],
    })
    assert meal["ok"] is True
    remaining = dict(service.connection.execute(
        "SELECT normalized_name, remaining_quantity FROM pantry_batches"
    ).fetchall())
    assert remaining["小象巴氏乳"] == 0
    assert remaining["川象鲜牛奶"] == 200
```

Also add a mismatch test that supplies the 250ml handle with the other SKU name and expects structured `INVALID_INPUT` with `field=items[0].inventory_match_handle`, `reason=identity_mismatch`, and `retryable=true`.

- [ ] **Step 2: Run the regression and verify RED**

Run:

```powershell
& $PdpPython -m pytest tests/contracts/test_inventory_search_contracts.py::test_selected_product_handle_avoids_requery_and_deducts_only_chosen_sku -q
```

Expected: FAIL because the meal Schema/service does not accept or resolve the product handle.

- [ ] **Step 3: Add the optional public field**

Add to the shared meal-item definition:

```ts
inventory_match_handle: Type.Optional(HandleSchema),
```

Add a TypeScript test proving the field accepts a real `wfh_...` handle and rejects an invented short string.

- [ ] **Step 4: Resolve the product reference before constructing `MealItemDraft`**

Refactor service helpers to pass one operation timestamp:

```python
def _meal_draft(service: DietService, value: Mapping[str, Any], *, now: datetime) -> meals.MealDraft: ...
def _cooking_draft(service: DietService, value: Mapping[str, Any], *, now: datetime) -> meals.CookingDraft: ...
def _meal_item(service: DietService, value: Mapping[str, Any], *, now: datetime, field: str) -> meals.MealItemDraft: ...
```

Inside `_meal_item`, initialize `inventory_match_name = None`, then resolve the optional handle:

```python
normalized_name = _required_text(value, "normalized_name")
inventory_match_name = None
handle = _optional_text(value.get("inventory_match_handle"), "inventory_match_handle")
if handle is not None:
    row = _workflow_row(
        service.connection, handle, "pantry_product_reference", now=now
    )
    selected = _stored_object(row["result_json"], "stored pantry product reference")
    selected_name = _required_text(selected, "normalized_name")
    selected_unit = _required_text(selected, "unit")
    supplied_unit = _optional_text(value.get("unit"), "unit")
    if normalized_name.casefold() != selected_name.casefold() or (
        supplied_unit is not None
        and inventory_matching.canonical_inventory_unit(supplied_unit)
        != inventory_matching.canonical_inventory_unit(selected_unit)
    ):
        raise _ServiceError(
            "INVALID_INPUT", "The request is invalid",
            field=f"{field}.inventory_match_handle",
            reason="identity_mismatch",
            expected="the normalized_name and unit returned with this handle",
            retryable=True,
        )
    normalized_name = selected_name
    inventory_match_name = selected_name
```

Pass `now` and an indexed field label recursively (`items[0]`, `items[0].ingredients[0]`, `dish.ingredients[0]`). Preserve `raw_name`; do not replace it with the selected SKU. Pass `inventory_match_name=inventory_match_name` into the returned `MealItemDraft`.

Add this internal-only field immediately after `normalized_name` in `meals.MealItemDraft`; it is not persisted or exposed in public output:

```python
inventory_match_name: str | None = field(default=None, repr=False, compare=False)
```

In `_prepare_item`, use a verified product identity directly and call the existing fuzzy resolver only when no verified handle was supplied:

```python
if item.inventory_match_name is not None:
    resolved_name = _required_text(
        item.inventory_match_name, "inventory_match_name"
    ).lower()
else:
    resolved_name = inventory_matching.resolve_meal_inventory_name(
        connection,
        raw_name,
        normalized_name,
        inventory_unit,
    )
if resolved_name is not None:
    normalized_name = resolved_name
```

This bypass is limited to identities recovered from a valid, unexpired `pantry_product_reference`; a caller-provided `normalized_name` without a handle still follows the existing resolver.

- [ ] **Step 5: Run focused meal, inventory, and Schema regressions**

Run:

```powershell
npm run build
npm test -- --run src-tests/intake-schema.test.ts src-tests/schema-size.test.ts
& $PdpPython -m pytest tests/contracts/test_inventory_search_contracts.py tests/contracts/test_live_intake_regressions.py -q
```

Expected: selected SKU is the only product deducted; meal nutrition uses its linked batch snapshot; existing cooking and quantity-conservation tests remain green.

- [ ] **Step 6: Commit**

```powershell
git add src/schemas.ts src-tests/intake-schema.test.ts python/personal_diet_pantry/service.py python/personal_diet_pantry/meals.py tests/contracts/test_inventory_search_contracts.py tests/contracts/test_live_intake_regressions.py
git commit -m "feat: reuse pantry product matches in meal records"
```

---

### Task 5: Generate capability routes and remove hard-coded mutation drift

**Files:**
- Modify: `contracts/tools.yaml`
- Modify: `scripts/generate_tool_contracts.py`
- Modify: `tests/test_tool_contract_generation.py`
- Modify generated outputs listed in Task 3
- Modify: `src/reliability.ts`
- Modify: `src-tests/reliability.test.ts`

**Interfaces:**
- Consumes: seven-domain action contract.
- Produces: validated `skill_routes`, generated `SKILL_ROUTES`, generated formal mutation use in reliability.

- [ ] **Step 1: Write route validation RED tests**

Extend `test_tool_contract_generation.py` to assert these exact route targets exist:

```python
EXPECTED_ROUTES = {
    "meal_record": ("meal", "record"),
    "cooking_record": ("meal", "record_cooking"),
    "water_record": ("water", "record"),
    "weight_record": ("weight", "record"),
    "pantry_search": ("pantry", "search"),
    "pantry_add": ("pantry", "add"),
    "pantry_discard": ("pantry", "discard"),
    "recent_operations": ("transaction", "get_recent"),
    "undo": ("transaction", "undo"),
    "redo": ("transaction", "redo"),
    "daily_progress": ("report", "progress"),
    "self_check": ("system", "self_check"),
}
```

Add a malformed fixture test proving the loader rejects a route whose target action is absent.

- [ ] **Step 2: Run generator tests and verify RED**

Run:

```powershell
& $PdpPython -m pytest tests/test_tool_contract_generation.py -q
```

Expected: FAIL because `skill_routes` is not loaded or generated.

- [ ] **Step 3: Add and validate `skill_routes`**

Add this top-level mapping to `contracts/tools.yaml`:

```yaml
skill_routes:
  meal_record: {domain: meal, action: record}
  cooking_record: {domain: meal, action: record_cooking}
  water_record: {domain: water, action: record}
  weight_record: {domain: weight, action: record}
  pantry_search: {domain: pantry, action: search}
  pantry_add: {domain: pantry, action: add}
  pantry_discard: {domain: pantry, action: discard}
  recent_operations: {domain: transaction, action: get_recent}
  undo: {domain: transaction, action: undo}
  redo: {domain: transaction, action: redo}
  daily_progress: {domain: report, action: progress}
  self_check: {domain: system, action: self_check}
```

Keep `load_tool_contract(path) -> dict[str, DomainContract]` backward compatible. Add a separate route model and loader:

```python
@dataclass(frozen=True)
class SkillRouteContract:
    domain: str
    action: str


def load_skill_routes(
    path: Path,
    domains: Mapping[str, DomainContract] | None = None,
) -> dict[str, SkillRouteContract]:
    validated_domains = load_tool_contract(path) if domains is None else domains
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    route_values = _mapping(raw.get("skill_routes"), "skill_routes")
    routes: dict[str, SkillRouteContract] = {}
    for route_name, route_value in route_values.items():
        route = _mapping(route_value, f"skill_routes.{route_name}")
        if set(route) != {"domain", "action"}:
            raise ValueError(f"skill_routes.{route_name} has unsupported fields")
        domain = _text(route["domain"], f"skill_routes.{route_name}.domain")
        action = _text(route["action"], f"skill_routes.{route_name}.action")
        if domain not in validated_domains or action not in validated_domains[domain].actions:
            raise ValueError(f"skill_routes.{route_name} targets an unknown action")
        routes[str(route_name)] = SkillRouteContract(domain=domain, action=action)
    return routes
```

In `generated_outputs`, load the domain contract once, pass it into `load_skill_routes`, and pass both mappings to `_public_behavior`, `_typescript`, `_python`, and `_documentation`. Generate this exact TypeScript route map from YAML:

```ts
export const SKILL_ROUTES = {
  meal_record: ['meal', 'record'],
  cooking_record: ['meal', 'record_cooking'],
  water_record: ['water', 'record'],
  weight_record: ['weight', 'record'],
  pantry_search: ['pantry', 'search'],
  pantry_add: ['pantry', 'add'],
  pantry_discard: ['pantry', 'discard'],
  recent_operations: ['transaction', 'get_recent'],
  undo: ['transaction', 'undo'],
  redo: ['transaction', 'redo'],
  daily_progress: ['report', 'progress'],
  self_check: ['system', 'self_check'],
} as const;
```

Generate the same mapping in Python and documentation. Add top-level `skill_routes` to `public-behavior.yaml`, preserving its existing `schema_version` and `domains` keys.

- [ ] **Step 4: Replace reliability's mutation list with generated data**

In `src/reliability.ts`:

```ts
import { FORMAL_MUTATION_ACTIONS } from "./generated/tool-contracts.js";

const FORMAL_MUTATIONS = new Set(
  FORMAL_MUTATION_ACTIONS.map((value) => value.replace(".", ":")),
);
```

Delete the hand-maintained string list. Add a Vitest assertion that every generated formal mutation enters the operation-receipt path, while `pantry.search` and `report.progress` do not.

- [ ] **Step 5: Generate and verify**

Run:

```powershell
& $PdpPython scripts/generate_tool_contracts.py --root .
& $PdpPython scripts/generate_tool_contracts.py --root . --check
& $PdpPython -m pytest tests/test_tool_contract_generation.py -q
npm run build
npm test -- --run src-tests/generated-contracts.test.ts src-tests/reliability.test.ts
```

Expected: all pass; no generated drift.

- [ ] **Step 6: Commit**

```powershell
git add contracts/tools.yaml contracts/public-behavior.yaml scripts/generate_tool_contracts.py tests/test_tool_contract_generation.py src/generated/tool-contracts.ts python/personal_diet_pantry/generated_tool_contracts.py docs/GENERATED-ACTIONS.zh-CN.md src/reliability.ts src-tests/reliability.test.ts
git commit -m "refactor: generate Skill capability routes"
```

---

### Task 6: Rewrite the Skill controller around guided routes and progress-based recovery

**Files:**
- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `skills/personal-diet-pantry/references/pantry-and-expiry.md`
- Modify: `skills/personal-diet-pantry/references/meal-and-nutrition.md`
- Modify: `skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md`
- Modify: `tests/skill-evals/routing.yaml`
- Modify: `tests/test_skill_progressive_disclosure.py`
- Modify: `tests/contracts/test_natural_language_trigger_skill_contract.py`
- Modify: `src-tests/skill-triggering.test.ts`

**Interfaces:**
- Consumes: generated quick routes, `pantry.search`, `inventory_match_handle`, structured error fields, existing write-readiness contract.
- Produces: cache-stable Skill guidance that points to the preferred route, permits evidence-based self-recovery, and forbids unchanged loops and broad pantry preflights.

- [ ] **Step 1: Write failing Skill behavior contracts**

Change `test_main_skill_is_a_bounded_one_level_navigation_core` to remove the artificial 350-line minimum:

```python
assert len(main.splitlines()) <= 400
assert len(main.encode("utf-8")) <= 20_000
```

Add assertions that the Skill bundle contains:

```python
for phrase in (
    "preferred capability route",
    "verified equivalent capability",
    "next action must add progress",
    "normalized arguments",
    "same error signature",
    "diet_pantry search",
    "inventory_match_handle",
    "search before browse",
    "nutrition_mode",
    "full inventory only when the user explicitly asks",
):
    assert phrase in bundle

for obsolete in (
    "retry the same action once",
    "allow at most one correction",
    "genuinely cross-domain intent loads at most two",
):
    assert obsolete not in main
```

Add routing fixtures:

```yaml
- id: meal-inventory-exact-targeted
  prompt: 吃了库存里的一个鸡蛋
  expected_domain: meal
  expected_reference: meal-and-nutrition.md
  allowed_tools: [diet_meal]
  forbidden_tools: [diet_pantry]
  write_expectation: write
  reference_contains: [internal targeted inventory resolution, no separate pantry preflight]
  reply_contains: [已记录, 库存]

- id: pantry-nutrition-summary
  prompt: 看看库存里这瓶牛奶的主要营养
  expected_domain: pantry
  expected_reference: pantry-and-expiry.md
  allowed_tools: [diet_pantry]
  forbidden_tools: [diet_meal, diet_report]
  write_expectation: read
  reference_contains: [diet_pantry search, nutrition_mode summary]
  reply_contains: [营养]
```

- [ ] **Step 2: Run Skill tests and verify RED**

Run:

```powershell
& $PdpPython scripts/evaluate_skill.py --skill skills/personal-diet-pantry --cases tests/skill-evals/routing.yaml
& $PdpPython -m pytest tests/test_skill_progressive_disclosure.py tests/contracts/test_natural_language_trigger_skill_contract.py -q
npm test -- --run src-tests/skill-triggering.test.ts
```

Expected: failures for missing guided recovery/search wording and obsolete one-retry/two-reference limits.

- [ ] **Step 3: Rewrite the stable controller**

Keep activation, write readiness, measurement invariants, success rendering, and all seven tool names. Replace the rigid readiness/error sections with this compact contract:

```markdown
## Preferred capability routes

Use the current preferred tool/action shown below when it is visible. If it is absent or
renamed, use another visible tool only when its description and Schema explicitly prove the
same capability, input/output meaning, and write safety. A business validation error never
authorizes changing tools.

| Intent | Preferred route |
| --- | --- |
| completed food/nutritious drink | `diet_meal record` |
| cooking with consumed and stored portions | `diet_meal record_cooking` |
| plain water | `diet_water record` |
| body weight | `diet_weight record` |
| targeted pantry identity | `diet_pantry search` |
| pantry intake | `diet_pantry add` |
| recent/undo/redo | `diet_transaction get_recent/undo/redo` |
| daily progress | `diet_report progress` |
| explicit health check | `diet_system self_check` |

## Progress-based recovery

Safe field repair, status verification, and verified equivalent-capability substitution happen
in the background. A next action must add progress: new evidence, one named field repair,
target disambiguation, uncertain-outcome verification, or a verified equivalent capability.
Track capability + tool + action + normalized arguments + error signature for this turn. Do
not repeat an unchanged fingerprint. Success ends the workflow immediately.
```

Readiness is per required capability, not “all seven tools must exist.” Replace the fixed two-reference ceiling with “load only references whose rules materially determine the current action; do not reread one already loaded.”

- [ ] **Step 4: Update domain references**

In `pantry-and-expiry.md`, specify search before browse, five-candidate maximum, exact/alias/keyword expansion, same-product batch aggregation, different-product confirmation, handle reuse, `nutrition_mode`, and explicit full-inventory pagination.

In `meal-and-nutrition.md`, require internal targeted resolution for an exact inventory meal, no separate `diet_pantry query`, and use `per_serving` consistently; remove any invalid `per_unit` wording.

In `reply-style-and-error-boundaries.md`, define the error matrix from the approved design: field repair, ambiguity, outcome status, stale handle refresh, transient busy retry, terminal business result, integrity breaker, and unchanged fingerprint stop. State the positive recipe for the final user reply; do not narrate repair attempts.

- [ ] **Step 5: Run Skill evaluation and static contracts**

Run:

```powershell
& $PdpPython scripts/evaluate_skill.py --skill skills/personal-diet-pantry --cases tests/skill-evals/routing.yaml
& $PdpPython -m pytest tests/test_skill_progressive_disclosure.py tests/contracts/test_natural_language_trigger_skill_contract.py -q
npm test -- --run src-tests/skill-triggering.test.ts
& $PdpPython scripts/validate_skill.py
```

Expected: evaluation score and safety score both `1.000`; all tests pass; validation status is `pass`.

- [ ] **Step 6: Commit**

```powershell
git add skills/personal-diet-pantry/SKILL.md skills/personal-diet-pantry/references/pantry-and-expiry.md skills/personal-diet-pantry/references/meal-and-nutrition.md skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md tests/skill-evals/routing.yaml tests/test_skill_progressive_disclosure.py tests/contracts/test_natural_language_trigger_skill_contract.py src-tests/skill-triggering.test.ts
git commit -m "feat: guide Skill tool routing and recovery"
```

---

### Task 7: Publish one coherent v0.7.2 source and documentation contract

**Files:**
- Create: `UPDATE-v0.7.2.zh-CN.md`
- Modify: `package.json`, `package-lock.json`, `pyproject.toml`, `openclaw.plugin.json`
- Modify: `python/personal_diet_pantry/__init__.py`, `python/personal_diet_pantry/data_import.py`
- Modify: `tests/test_version_contract.py`, `src-tests/version-contract.test.ts`, `tests/test_build_release.py`
- Modify: `scripts/build_release.py`, `ci/verify.ps1`, `contracts/v070-core-tests.txt`
- Modify: `README.md`, `README.en.md`, `RELEASE.zh-CN.md`, `docs/INSTALLATION.zh-CN.md`, `docs/TOOLS-REFERENCE.zh-CN.md`, `docs/DATA-MODEL.zh-CN.md`, `docs/ARCHITECTURE.zh-CN.md`

**Interfaces:**
- Consumes: completed Tasks 1-6 and the approved design/plan documents.
- Produces: consistent version `0.7.2`, migration/route/search documentation, build manifest inputs, and `0.7.1` rollback instructions.

- [ ] **Step 1: Update version tests first and verify RED**

Change expected values in Python and TypeScript version tests to `0.7.2`; require `UPDATE-v0.7.2.zh-CN.md`, this design, and this plan in release inputs. Add an import test that accepts exports produced by `0.7.1` and `0.7.2`.

Run:

```powershell
& $PdpPython -m pytest tests/test_version_contract.py tests/test_build_release.py -q
npm test -- --run src-tests/version-contract.test.ts
```

Expected: FAIL while production version sources remain `0.7.1`.

- [ ] **Step 2: Update every version source atomically**

Set exactly `0.7.2` in:

```text
package.json version and productVersion
package-lock.json root version fields
pyproject.toml project.version
openclaw.plugin.json version
python/personal_diet_pantry/__init__.py __version__
scripts/build_release.py VERSION and PRODUCT_VERSION
```

Add `0.7.1` and `0.7.2` to `SUPPORTED_PRODUCT_VERSIONS` without removing existing compatible versions.

- [ ] **Step 3: Update release inputs and core gate**

In `scripts/build_release.py` and `tests/test_build_release.py`, replace v0.7.1 release filenames with v0.7.2, include:

```text
UPDATE-v0.7.2.zh-CN.md
docs/superpowers/specs/2026-08-01-personal-diet-pantry-v0.7.2-guided-routing-and-inventory-search-design.md
docs/superpowers/plans/2026-08-02-personal-diet-pantry-v0.7.2.md
CONTEXT.md
migrations/020_inventory_search.sql
```

Append the new inventory search, handle reuse, Schema size, generated route, and migration tests to `contracts/v070-core-tests.txt`. Change the `ci/verify.ps1` gate label to `v0.7.2 core behavior gate`; the filename may remain for continuity.

Parse JUnit and Vitest machine reports into explicit total, passed, skipped, and failed counts. JUnit errors count as failures; Vitest pending and todo tests count as skipped. A release may contain legitimate skips only when failures are zero and `passed + skipped == total`; failures, errors, negative values, and inconsistent totals fail closed before artifact publication. Persist all four outcomes for both runtimes in `release-manifest.json` and `TEST-SUMMARY-v0.7.2.zh-CN.md`.

- [ ] **Step 4: Write exact update and release documentation**

`UPDATE-v0.7.2.zh-CN.md` must contain these headings and facts:

```markdown
# 食序管家 v0.7.2 更新说明
## 本版目标
## Skill 导航与后台自救
## 库存定向搜索
## 库存与营养一次组合返回
## 餐食 Schema 压缩
## 数据迁移与兼容
## 验证结果
## 升级与回退
## 非目标
```

State clearly that nutrition remains normalized in one SQLite database, `search` returns at most five candidates, v0.7.2 does not auto-deploy, and rollback requires both the v0.7.1 package and the pre-upgrade database backup.

Update README, architecture, data model, tool reference, installation, and release pages with the new action, `nutrition_mode`, `inventory_match_handle`, migration 020, Schema budget, package names, and rollback boundary. Do not claim measured test totals until the final gate produces them.

- [ ] **Step 5: Run version, build, and documentation tests**

Run:

```powershell
& $PdpPython -m pytest tests/test_version_contract.py tests/test_build_release.py tests/test_tool_contract_generation.py -q
npm run build
npm test -- --run src-tests/version-contract.test.ts src-tests/package-contents.test.ts
& $PdpPython scripts/scan_sensitive_content.py .
```

Expected: all pass; sensitive-content scan reports no findings.

- [ ] **Step 6: Commit**

```powershell
git add package.json package-lock.json pyproject.toml openclaw.plugin.json python/personal_diet_pantry/__init__.py python/personal_diet_pantry/data_import.py tests/test_version_contract.py src-tests/version-contract.test.ts tests/test_build_release.py scripts/build_release.py ci/verify.ps1 contracts/v070-core-tests.txt UPDATE-v0.7.2.zh-CN.md README.md README.en.md RELEASE.zh-CN.md docs/INSTALLATION.zh-CN.md docs/TOOLS-REFERENCE.zh-CN.md docs/DATA-MODEL.zh-CN.md docs/ARCHITECTURE.zh-CN.md docs/superpowers/plans/2026-08-02-personal-diet-pantry-v0.7.2.md
git commit -m "release: prepare personal diet pantry v0.7.2"
```

---

### Task 8: Run the complete gate and build reproducible release artifacts

**Files:**
- Generated in ignored test evidence: `dist-package/`
- Generated outside the current Git worktree: a new, previously nonexistent release artifact directory
- No tracked changes are expected in this task. A verified failure reopens the owning source/test task from Tasks 1-7 before the complete gate is rerun.

**Interfaces:**
- Consumes: clean committed v0.7.2 worktree.
- Produces: passing full gate and the exact six-entry release-directory contract: installable `.tgz`, source archive, release manifest, test summary, standard hashes, and GitHub documentation.

- [ ] **Step 1: Confirm the worktree is clean and at v0.7.2**

Run:

```powershell
$InitialStatus = @(git status --short)
if ($LASTEXITCODE -ne 0) {
    throw "initial git status failed with exit code $LASTEXITCODE"
}
if ($InitialStatus.Count -ne 0) {
    $InitialStatus
    throw 'Task 8 requires a clean Git worktree'
}
git log -1 --oneline
if ($LASTEXITCODE -ne 0) {
    throw "git log failed with exit code $LASTEXITCODE"
}
& $PdpPython -c "import sys; sys.path.insert(0, 'python'); import personal_diet_pantry as p; assert p.__version__ == '0.7.2'"
if ($LASTEXITCODE -ne 0) {
    throw "v0.7.2 import assertion failed with exit code $LASTEXITCODE"
}
```

Expected: empty status output; version assertion exits zero.

- [ ] **Step 2: Run the complete repository gate**

Run:

```powershell
$env:PDP_PYTHON = $PdpPython
powershell -ExecutionPolicy Bypass -File .\ci\verify.ps1
if ($LASTEXITCODE -ne 0) {
    throw "complete repository gate failed with exit code $LASTEXITCODE"
}
```

Expected: generated contracts, Skill evaluation, sensitive scan, core tests, full pytest, build, Vitest, compileall, Skill validation, release audit, npm pack dry run, integration tests, and dependency audits all pass.

- [ ] **Step 3: Build artifacts from the clean commit**

Run:

```powershell
$GitTopLevel = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "git top-level resolution failed with exit code $LASTEXITCODE"
}
if (-not $GitTopLevel) { throw 'cannot resolve Git top-level' }
$ReleaseRoot = Join-Path (Split-Path -Parent $GitTopLevel) 'pdp-v0.7.2-release-task8-rerun'
if (Test-Path -LiteralPath $ReleaseRoot) {
    throw 'release root must be a new, previously nonexistent directory'
}
$GitTopLevelPath = [IO.Path]::GetFullPath($GitTopLevel).TrimEnd('\', '/')
$ReleaseRootPath = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\', '/')
$GitPrefix = $GitTopLevelPath + [IO.Path]::DirectorySeparatorChar
if (
    $ReleaseRootPath -eq $GitTopLevelPath -or
    $ReleaseRootPath.StartsWith(
        $GitPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    throw 'release root must remain outside the Git worktree'
}
$ReleaseRoot = $ReleaseRootPath
& $PdpPython scripts/build_release.py --project-root . --release-root $ReleaseRoot
if ($LASTEXITCODE -ne 0) {
    throw "release build failed with exit code $LASTEXITCODE"
}
$PostBuildStatus = @(git status --short)
if ($LASTEXITCODE -ne 0) {
    throw "post-build git status failed with exit code $LASTEXITCODE"
}
if ($PostBuildStatus.Count -ne 0) {
    $PostBuildStatus
    throw 'release build changed or dirtied the Git worktree'
}
```

Expected exact top-level entries:

```text
personal-diet-pantry-0.7.2-source.tar.gz
personal-diet-pantry-0.7.2-installable.tgz
release-manifest.json
TEST-SUMMARY-v0.7.2.zh-CN.md
SHA256SUMS
GitHub文档/
```

Expected: the pre-build nonexistence assertion and outside-the-Git-worktree assertion pass, the builder creates exactly these six entries, and the post-build `git status --short` output remains empty. `release-manifest.json` and the test summary explicitly report total, passed, skipped, and failed counts for Python and TypeScript; legitimate skips are accepted only with zero failures and consistent totals. `SHA256SUMS` parses as four standard `<sha256><two spaces><filename>` lines and independently verifies the source archive, installable archive, release manifest, and test summary.

- [ ] **Step 4: Verify artifact contents and installable E2E**

Run:

```powershell
& $PdpPython -m pytest tests/integration/test_installable_e2e.py tests/integration/test_upgrade_e2e.py -q
if ($LASTEXITCODE -ne 0) {
    throw "installable and upgrade E2E failed with exit code $LASTEXITCODE"
}
npm pack --dry-run --json
if ($LASTEXITCODE -ne 0) {
    throw "npm pack dry run failed with exit code $LASTEXITCODE"
}
```

Expected: installable package contains Skill, compiled JS, Python package, configuration, migration 020, rules and templates; it excludes credentials, databases, backups, temporary files, and source-only plans.

- [ ] **Step 5: Record final evidence without rewriting history**

Use the generated test summary and hashes as the final evidence. If any gate fails, return to the owning task, add a failing regression test, implement the minimum correction, rerun its focused tests, commit, then rerun Task 8 from Step 1. Do not edit generated test totals by hand.
