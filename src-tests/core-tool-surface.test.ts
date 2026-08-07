import { describe, expect, it } from "vitest";
import { Value } from "typebox/value";

import {
  CORE_TOOL_ACTIONS,
  MealParametersSchema,
  PantryParametersSchema,
  ReportParametersSchema,
  SystemParametersSchema,
  TransactionParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
} from "../src/core-schemas.js";
import { normalizeToolPayload } from "../src/index.js";
import { MealParametersSchema as InternalMealParametersSchema } from "../src/schemas.js";


const schemas = [
  MealParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
  PantryParametersSchema,
  TransactionParametersSchema,
  ReportParametersSchema,
  SystemParametersSchema,
];

function schemaActions(schema: unknown): string[] {
  return (schema as { anyOf: Array<{ properties: { action: { const: string } } }> })
    .anyOf.map((item) => item.properties.action.const);
}

describe("core tool surface", () => {
  it("exposes the frozen daily actions plus the pantry add workflow seam", () => {
    expect(schemaActions(MealParametersSchema)).toEqual(CORE_TOOL_ACTIONS.meal);
    expect(schemaActions(WaterParametersSchema)).toEqual(CORE_TOOL_ACTIONS.water);
    expect(schemaActions(WeightParametersSchema)).toEqual(CORE_TOOL_ACTIONS.weight);
    expect(schemaActions(PantryParametersSchema)).toEqual(CORE_TOOL_ACTIONS.pantry);
    expect(schemaActions(TransactionParametersSchema)).toEqual(CORE_TOOL_ACTIONS.transaction);
    expect(schemaActions(ReportParametersSchema)).toEqual(CORE_TOOL_ACTIONS.report);
    expect(schemaActions(SystemParametersSchema)).toEqual(CORE_TOOL_ACTIONS.system);
    expect(Object.values(CORE_TOOL_ACTIONS).flat()).toHaveLength(42);
    expect(CORE_TOOL_ACTIONS.pantry).toContain("preview_add");
    expect(CORE_TOOL_ACTIONS.pantry).toContain("commit_add");
  });

  it("stays below the agreed raw-byte ceiling and hides internal evidence fields", () => {
    const serialized = schemas.map((schema) => JSON.stringify(schema)).join("");

    expect(Buffer.byteLength(serialized)).toBeLessThanOrEqual(56_000);
    expect(serialized).not.toContain('"confirmed"');
    expect(serialized).not.toContain('"policy_key"');
    expect(serialized).not.toContain('"evidence_type"');
    expect(serialized).not.toContain('"confidence"');
    expect(serialized).not.toContain('"batch_code"');
  });

  it("turns a public confirmation handle into the internal commit flag", () => {
    const result = normalizeToolPayload(
      "meal",
      "commit_record",
      { commit_handle: "wfh_abcdefghijklmnopqrstuv" },
      {},
    );

    expect(result.error).toBeUndefined();
    expect(result.payload).toEqual({
      commit_handle: "wfh_abcdefghijklmnopqrstuv",
      confirmed: true,
    });
  });

  it("keeps a supplemented pantry add zero-write until a handle-bound commit", () => {
    const preview = {
      action: "preview_add",
      food_name: "原味燕麦奶",
      normalized_name: "原味燕麦奶",
      unit: "ml",
      display_quantity: 2,
      display_unit: "pack",
      base_quantity_per_display_unit: 250,
      expiry_date: "2026-08-20",
      storage_location: "冰箱",
      source_text: "刚买两盒原味燕麦奶，每盒250毫升，8月20日到期",
    };

    expect(Value.Check(PantryParametersSchema, preview)).toBe(true);
    expect(Value.Check(PantryParametersSchema, {
      action: "commit_add",
      commit_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toBe(true);
  });

  it("lets a just-completed water record use the trusted service clock", () => {
    expect(Value.Check(WaterParametersSchema, {
      action: "record",
      amount: 300,
      unit: "ml",
      source_text: "刚喝了300毫升水",
    })).toBe(true);
  });

  it("does not let model-visible meal parameters forge host authorization", () => {
    const request = {
      action: "record",
      meal_type: "dinner",
      source_text: "我已经吃了一个过期水煮蛋",
      location_type: "home",
      items: [{
        raw_name: "水煮蛋",
        normalized_name: "水煮蛋",
        amount: 1,
        unit: "piece",
      }],
    };

    expect(Value.Check(MealParametersSchema, request)).toBe(true);
    expect(Value.Check(MealParametersSchema, {
      ...request,
      _turn_completed_consumption: true,
    })).toBe(false);
  });

  it("attaches registered evidence to a public bounded quantity estimate", () => {
    const result = normalizeToolPayload(
      "meal",
      "preview_record",
      {
        items: [{
          raw_name: "玉米",
          normalized_name: "corn",
          amount: "90",
          unit: "g",
          portion_expression: "一个",
          quantity_estimate: {
            suggested: "90",
            lower: "80",
            upper: "110",
            unit: "g",
          },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    expect(
      (result.payload.items as Array<Record<string, unknown>>)[0]
        .quantity_estimate,
    ).toEqual({
      suggested: "90",
      lower: "80",
      upper: "110",
      unit: "g",
      evidence_type: "household_range",
      policy_key: "portion.generic.small_amount",
    });
  });

  it("accepts a count plus estimated gram weight as a direct standard portion", () => {
    const result = normalizeToolPayload(
      "meal",
      "record",
      {
        meal_type: "snack",
        source_text: "刚吃了根火腿肠",
        location_type: "home",
        items: [{
          raw_name: "火腿肠",
          normalized_name: "sausage",
          amount: "1",
          unit: "根",
          portion_expression: "一根",
          consumed_weight_g: "50",
          quantity_estimate: {
            suggested: "50",
            lower: "40",
            upper: "65",
            unit: "g",
          },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    expect(
      (result.payload.items as Array<Record<string, unknown>>)[0]
        .quantity_estimate,
    ).toEqual({
      suggested: "50",
      lower: "40",
      upper: "65",
      unit: "g",
      evidence_type: "standard_portion",
      policy_key: "portion.standard_count_weight",
    });
  });

  it("derives a standard consumed weight from a bounded count estimate", () => {
    const result = normalizeToolPayload(
      "meal",
      "record",
      {
        meal_type: "snack",
        source_text: "吃了个玉米",
        location_type: "unknown",
        items: [{
          raw_name: "玉米",
          normalized_name: "玉米（鲜）",
          amount: 1,
          unit: "个",
          portion_expression: "1个",
          quantity_estimate: {
            suggested: 90,
            lower: 80,
            upper: 100,
            unit: "克",
          },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    const item = (result.payload.items as Array<Record<string, unknown>>)[0];
    expect(item.consumed_weight_g).toBe(90);
    expect(item.quantity_estimate).toEqual({
      suggested: 90,
      lower: 80,
      upper: 100,
      unit: "克",
      evidence_type: "standard_portion",
      policy_key: "portion.standard_count_weight",
    });
  });

  it("keeps missing sodium unknown and routes grade B evidence as facts", () => {
    const result = normalizeToolPayload(
      "meal",
      "record",
      {
        meal_type: "snack",
        source_text: "吃了个玉米",
        location_type: "unknown",
        items: [{
          raw_name: "玉米",
          normalized_name: "玉米（鲜）",
          amount: 1,
          unit: "个",
          portion_expression: "1个",
          consumed_weight_g: 90,
          nutrition_basis: "per_100g",
          nutrition_estimate: {
            calories: 112,
            protein: 4,
            fat: 1.5,
            carbohydrate: 22.8,
            fiber: 2.9,
            source: "中国食物成分表常见估算",
            source_grade: "B",
          },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    expect(Value.Check(InternalMealParametersSchema, {
      action: "record",
      ...result.payload,
    })).toBe(true);
    const item = (result.payload.items as Array<Record<string, unknown>>)[0];
    expect(item.nutrition_estimate).toBeUndefined();
    expect((item.nutrition_facts as Record<string, unknown>).sodium)
      .toBeUndefined();
  });

  it("drops a stale estimate when an update supplies a different exact weight", () => {
    const result = normalizeToolPayload(
      "meal",
      "update",
      {
        meal_handle: "wfh_abcdefghijklmnopqrstuv",
        meal_type: "snack",
        source_text: "其实是80克",
        location_type: "unknown",
        items: [{
          raw_name: "玉米",
          normalized_name: "玉米（鲜）",
          amount: 1,
          unit: "个",
          portion_expression: "1个｜可食部（玉米粒）约90克（估算）",
          consumed_weight_g: 80,
          quantity_estimate: {
            suggested: 90,
            lower: 80,
            upper: 100,
            unit: "克",
          },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    const draft = result.payload.draft as Record<string, unknown>;
    const item = (draft.items as Array<Record<string, unknown>>)[0];
    expect(item.consumed_weight_g).toBe(80);
    expect(item.quantity_estimate).toBeUndefined();
    expect(item.portion_expression).toBe("1个｜可食部（玉米粒）80克");
  });

  it("removes a copied estimate label from an explicitly exact correction", () => {
    const result = normalizeToolPayload(
      "meal",
      "update",
      {
        meal_handle: "wfh_abcdefghijklmnopqrstuv",
        source_text: "其实是80克",
        items: [{
          raw_name: "玉米",
          normalized_name: "玉米",
          amount: 1,
          unit: "个",
          portion_expression: "1个｜可食部（玉米粒）约80克（估算）",
          consumed_weight_g: 80,
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    const draft = result.payload.draft as Record<string, unknown>;
    const item = (draft.items as Array<Record<string, unknown>>)[0];
    expect(item.portion_expression).toBe("1个｜可食部（玉米粒）80克");
  });

  it("does not drop a stale estimate for an approximate update", () => {
    const result = normalizeToolPayload(
      "meal",
      "update",
      {
        meal_handle: "wfh_abcdefghijklmnopqrstuv",
        meal_type: "snack",
        source_text: "大概80克吧",
        location_type: "unknown",
        items: [{
          raw_name: "玉米",
          normalized_name: "玉米（鲜）",
          amount: 1,
          unit: "个",
          portion_expression: "1个｜可食部（玉米粒）约90克（估算）",
          consumed_weight_g: 80,
          quantity_estimate: {
            suggested: 90,
            lower: 80,
            upper: 100,
            unit: "克",
          },
        }],
      },
      {},
    );

    expect(result.error).toMatchObject({
      field: "draft.items[0].quantity_estimate",
      reason: "incompatible",
    });
  });

  it("collapses identical duplicate nutrition evidence to the conservative estimate", () => {
    const estimate = {
      calories: "169.6",
      protein: "8",
      fat: "12.8",
      carbohydrate: "4.8",
      fiber: "0",
      sodium: "600",
      source: "常见火腿肠估算",
      source_grade: "C",
      uncertainty: "品牌和配方会有差异",
    };
    const result = normalizeToolPayload(
      "meal",
      "record",
      {
        meal_type: "snack",
        source_text: "刚吃了根火腿肠",
        location_type: "home",
        items: [{
          raw_name: "火腿肠",
          normalized_name: "sausage",
          amount: "1",
          unit: "根",
          consumed_weight_g: "50",
          nutrition_basis: "per_100g",
          nutrition_facts: { ...estimate },
          nutrition_estimate: { ...estimate },
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    const item = (result.payload.items as Array<Record<string, unknown>>)[0];
    expect(item.nutrition_facts).toBeUndefined();
    expect(item.nutrition_estimate).toEqual(estimate);
  });

  it("still rejects conflicting duplicate nutrition evidence", () => {
    const result = normalizeToolPayload(
      "meal",
      "record",
      {
        meal_type: "snack",
        source_text: "刚吃了根火腿肠",
        location_type: "home",
        items: [{
          raw_name: "火腿肠",
          normalized_name: "sausage",
          amount: "1",
          unit: "根",
          consumed_weight_g: "50",
          nutrition_basis: "per_100g",
          nutrition_facts: {
            calories: "169.6", source: "标签", source_grade: "A",
          },
          nutrition_estimate: {
            calories: "200", source: "估算", source_grade: "C",
          },
        }],
      },
      {},
    );

    expect(result.error).toMatchObject({
      field: "items[0].nutrition_estimate",
      reason: "incompatible",
    });
  });

  it("normalizes a flattened handle-bound correction and commits it directly", () => {
    const result = normalizeToolPayload(
      "meal",
      "update",
      {
        meal_handle: "wfh_abcdefghijklmnopqrstuv",
        meal_type: "snack",
        source_text: "刚才那根火腿肠其实是80克",
        location_type: "home",
        items: [{
          raw_name: "火腿肠",
          normalized_name: "sausage",
          amount: "1",
          unit: "根",
          consumed_weight_g: "80",
        }],
      },
      {},
    );

    expect(result.error).toBeUndefined();
    expect(result.payload).toEqual({
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
      draft: {
        meal_type: "snack",
        source_text: "刚才那根火腿肠其实是80克",
        location_type: "home",
        items: [{
          raw_name: "火腿肠",
          normalized_name: "sausage",
          amount: "1",
          unit: "根",
          consumed_weight_g: "80",
        }],
      },
    });
  });

  it("accepts a handle-bound correction without repeated meal-level history", () => {
    const item = {
      raw_name: "玉米",
      normalized_name: "玉米",
      amount: 1,
      unit: "个",
      portion_expression: "1个｜可食部（玉米粒）80克",
      consumed_weight_g: 80,
    };

    const request = {
      action: "update",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
      source_text: "其实是80克",
      items: [item],
    };

    expect(Value.Check(MealParametersSchema, request)).toBe(true);
    const { source_text: _sourceText, ...withoutCorrectionEvidence } = request;
    expect(Value.Check(MealParametersSchema, withoutCorrectionEvidence)).toBe(false);
    expect(normalizeToolPayload(
      "meal",
      "update",
      {
        meal_handle: request.meal_handle,
        source_text: request.source_text,
        items: request.items,
      },
      {},
    ).payload).toEqual({
      meal_handle: request.meal_handle,
      draft: { source_text: "其实是80克", items: [item] },
    });
  });

  it("lets a local-time selector reach the guard but rejects it at execution without a handle rewrite", () => {
    const item = {
      raw_name: "玉米",
      normalized_name: "玉米",
      amount: 1,
      unit: "个",
      consumed_weight_g: 80,
    };
    const localSelectorRequest = {
      action: "update",
      selector: {
        occurred_at: "2026-08-06T06:09:00",
        source_text: "吃了个玉米",
      },
      source_text: "其实是80克",
      items: [item],
    };

    expect(Value.Check(MealParametersSchema, localSelectorRequest)).toBe(true);
    expect(normalizeToolPayload(
      "meal",
      "update",
      {
        selector: localSelectorRequest.selector,
        source_text: localSelectorRequest.source_text,
        items: localSelectorRequest.items,
      },
      {},
    ).error).toMatchObject({
      code: "INVALID_INPUT",
      field: "selector.occurred_at",
      reason: "invalid_format",
    });

    expect(normalizeToolPayload(
      "meal",
      "update",
      {
        selector: {
          occurred_at: "2026-08-06T06:09:00+08:00",
          source_text: "吃了个玉米",
        },
        source_text: "其实是80克",
        items: [item],
      },
      {},
    ).error).toBeUndefined();
  });
});
