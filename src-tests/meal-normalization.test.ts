import { describe, expect, it } from "vitest";

import { normalizeToolPayload } from "../src/index.js";

const facts = {
  calories: 33,
  protein: 3.5,
  fat: 1.8,
  carbohydrate: 2,
  fiber: 0,
  sodium: 50,
  hydration_ml: 95,
  source: "fixture label",
  source_grade: "A",
};

const estimate = {
  ...facts,
  source: "fixture estimate",
  source_grade: "C",
};

const liquidItem = {
  raw_name: "豆浆",
  normalized_name: "soy milk",
  consumed_volume_ml: 250,
  nutrition_basis: "per_100ml",
  nutrition_facts: facts,
};

function normalize(action: string, payload: Record<string, unknown>) {
  return normalizeToolPayload("meal", action, payload, {});
}

function expectInvalid(
  result: ReturnType<typeof normalize>,
  field: string,
  reason: string,
) {
  expect(result.error).toMatchObject({
    code: "INVALID_INPUT",
    field,
    reason,
    retryable: true,
  });
}

describe("meal nutrition normalization", () => {
  it("removes a stale granular count and old estimate after an exact weight correction", () => {
    const result = normalize("update", {
      draft: {
        source_text: "其实是5克。",
        items: [{
          raw_name: "花生",
          normalized_name: "花生",
          consumed_weight_g: 5,
          portion_expression: "10粒｜10克",
        }],
      },
    });

    expect(result.error).toBeUndefined();
    expect((result.payload.draft as { items: Array<Record<string, unknown>> })
      .items[0]?.portion_expression).toBe("5克");
  });

  it("treats a zero-width quantity interval matching the exact count as redundant", () => {
    const result = normalize("record", {
      source_text: "吃了个玉米。",
      items: [{
        raw_name: "玉米",
        normalized_name: "corn",
        amount: 1,
        unit: "个",
        portion_expression: "1个｜可食部（玉米粒）约90克（估算）",
        consumed_weight_g: 90,
        quantity_estimate: {
          suggested: 1,
          lower: 1,
          upper: 1,
          unit: "个",
        },
        nutrition_basis: "per_100g",
        nutrition_facts: facts,
      }],
    });

    expect(result.error).toBeUndefined();
    expect((result.payload.items as Array<Record<string, unknown>>)[0])
      .not.toHaveProperty("quantity_estimate");
  });

  it("keeps a genuine non-zero quantity range for vague intake", () => {
    const result = normalize("preview_record", {
      source_text: "吃了点花生。",
      items: [{
        raw_name: "花生",
        normalized_name: "peanut",
        amount: 25,
        unit: "g",
        portion_expression: "约25克（估算，范围15–35克）",
        consumed_weight_g: 25,
        quantity_estimate: {
          suggested: 25,
          lower: 15,
          upper: 35,
          unit: "g",
        },
        nutrition_basis: "per_100g",
        nutrition_facts: facts,
      }],
    });

    expect(result.error).toBeUndefined();
    expect((result.payload.items as Array<Record<string, unknown>>)[0])
      .toMatchObject({
        quantity_estimate: {
          suggested: 25,
          lower: 15,
          upper: 35,
          evidence_type: "household_range",
          policy_key: "portion.generic.small_amount",
        },
      });
  });

  it("rejects simultaneous facts and estimate", () => {
    const result = normalize("record", {
      items: [{ ...liquidItem, nutrition_estimate: estimate }],
    });

    expectInvalid(result, "items[0].nutrition_estimate", "incompatible");
  });

  it("requires a basis for direct nutrition", () => {
    const { nutrition_basis: _basis, ...withoutBasis } = liquidItem;
    const result = normalize("record", { items: [withoutBasis] });

    expectInvalid(result, "items[0].nutrition_basis", "required");
  });

  it("rejects an all-unknown label before dispatch but accepts omitted fields", () => {
    const result = normalize("record", {
      items: [{
        raw_name: "空标签饮料",
        normalized_name: "空标签饮料",
        amount: 180,
        unit: "ml",
        nutrition_basis: "per_100ml",
        nutrition_facts: {
          source: "包装标签",
          source_grade: "A",
        },
      }],
    });

    expectInvalid(result, "items[0].nutrition_facts", "required");
  });

  it("rejects a basis without direct nutrition", () => {
    const { nutrition_facts: _facts, ...withoutFacts } = liquidItem;
    const result = normalize("record", { items: [withoutFacts] });

    expectInvalid(result, "items[0].nutrition_basis", "incompatible");
  });

  it.each([
    ["per_100g", "consumed_weight_g"],
    ["per_100ml", "consumed_volume_ml"],
    ["per_serving", "consumed_servings"],
  ])("requires the matching positive measure for %s", (basis, measure) => {
    const result = normalize("record", {
      items: [{
        raw_name: "test",
        normalized_name: "test",
        nutrition_basis: basis,
        nutrition_facts: facts,
      }],
    });

    expectInvalid(result, `items[0].${measure}`, "required");
  });

  it("derives an exact consumed volume from a compatible amount for per-100ml facts", () => {
    const result = normalize("record", {
      source_text: "整盒是180毫升，就按包装标签直接记录。",
      items: [{
        raw_name: "一盒标签豆奶",
        normalized_name: "标签豆奶",
        amount: 180,
        unit: "ml",
        nutrition_basis: "per_100ml",
        nutrition_facts: {
          calories: 70,
          protein: 3,
          fat: 2,
          carbohydrate: 10,
          source: "包装标签",
          source_grade: "A",
        },
      }],
    });

    expect(result.error).toBeUndefined();
    expect(result.payload.items).toMatchObject([
      { amount: 180, unit: "ml", consumed_volume_ml: 180 },
    ]);
  });

  it("derives grams from an exact compatible kilogram amount", () => {
    const result = normalize("record", {
      items: [{
        raw_name: "整袋米",
        normalized_name: "米",
        amount: "0.08",
        unit: "kg",
        nutrition_basis: "per_100g",
        nutrition_facts: facts,
      }],
    });

    expect(result.error).toBeUndefined();
    expect(result.payload.items).toMatchObject([
      { consumed_weight_g: "80" },
    ]);
  });

  it("does not invent a consumed measure from an unbound package unit", () => {
    const result = normalize("record", {
      items: [{
        raw_name: "标签豆奶",
        normalized_name: "标签豆奶",
        nutrition_basis: "per_100ml",
        nutrition_facts: facts,
        amount: 1,
        unit: "盒",
      }],
    });

    expectInvalid(result, "items[0].consumed_volume_ml", "required");
  });

  it.each([
    ["一盒就是180毫升，按包装标签记。", 1, "盒", 180],
    ["每盒180毫升，喝了两盒。", 2, "盒", 360],
  ])(
    "derives an exact consumed volume from one unambiguous same-turn package conversion: %s",
    (sourceText, amount, unit, expectedVolume) => {
      const result = normalize("record", {
        source_text: sourceText,
        items: [{
          raw_name: "标签豆奶",
          normalized_name: "标签豆奶",
          amount,
          unit,
          nutrition_basis: "per_100ml",
          nutrition_facts: {
            calories: 70,
            protein: 3,
            fat: 2,
            carbohydrate: 10,
            source: "包装标签",
            source_grade: "A",
          },
        }],
      });

      expect(result.error).toBeUndefined();
      expect(result.payload.items).toMatchObject([
        { amount, unit, consumed_volume_ml: String(expectedVolume) },
      ]);
    },
  );

  it("derives exact grams from a same-turn bag conversion", () => {
    const result = normalize("record", {
      source_text: "每袋80克，我吃了一袋。",
      items: [{
        raw_name: "标签米饼",
        normalized_name: "标签米饼",
        amount: 1,
        unit: "袋",
        nutrition_basis: "per_100g",
        nutrition_facts: facts,
      }],
    });

    expect(result.error).toBeUndefined();
    expect(result.payload.items).toMatchObject([
      { amount: 1, unit: "袋", consumed_weight_g: "80" },
    ]);
  });

  it.each([
    ["每瓶180毫升。", "盒"],
    ["一盒180毫升，另一盒250毫升。", "盒"],
    ["这一盒就按包装记。", "盒"],
  ])(
    "does not infer a package measure from mismatched, conflicting, or incomplete evidence: %s",
    (sourceText, unit) => {
      const result = normalize("record", {
        source_text: sourceText,
        items: [{
          raw_name: "标签豆奶",
          normalized_name: "标签豆奶",
          amount: 1,
          unit,
          nutrition_basis: "per_100ml",
          nutrition_facts: facts,
        }],
      });

      expectInvalid(result, "items[0].consumed_volume_ml", "required");
    },
  );

  it("reports the exact cooking ingredient path", () => {
    const { consumed_volume_ml: _volume, ...withoutVolume } = liquidItem;
    const result = normalize("record_cooking", {
      dish: { ingredients: [withoutVolume] },
    });

    expectInvalid(
      result,
      "dish.ingredients[0].consumed_volume_ml",
      "required",
    );
  });

  it("reports the exact ordinary update path", () => {
    const { nutrition_basis: _basis, ...withoutBasis } = liquidItem;
    const result = normalize("update", {
      draft: { items: [withoutBasis] },
    });

    expectInvalid(result, "draft.items[0].nutrition_basis", "required");
  });

  it("reports the exact cooking update path", () => {
    const { nutrition_basis: _basis, ...withoutBasis } = liquidItem;
    const result = normalize("update", {
      draft: { dish: { ingredients: [withoutBasis] } },
    });

    expectInvalid(
      result,
      "draft.dish.ingredients[0].nutrition_basis",
      "required",
    );
  });

  it("accepts valid volume, consumed-total, and pantry-resolved items", () => {
    expect(normalize("record", { items: [liquidItem] }).error).toBeUndefined();
    expect(normalize("record", {
      items: [{
        ...liquidItem,
        consumed_volume_ml: undefined,
        nutrition_basis: "consumed_total",
      }],
    }).error).toBeUndefined();
    expect(normalize("record", {
      items: [{
        raw_name: "库存苹果",
        normalized_name: "apple",
        inventory_match_handle: "wfh_abcdefghijklmnopqrstuv",
      }],
    }).error).toBeUndefined();
  });
});

describe("meal enum normalization", () => {
  it("fills honest defaults when clear intake omits analytical labels", () => {
    const result = normalize("record", {
      source_text: "吃了个玉米。",
      items: [liquidItem],
    });

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      meal_type: "other",
      location_type: "unknown",
    });
  });

  it("normalizes common Chinese meal and location aliases before dispatch", () => {
    const result = normalize("record", {
      meal_type: "午餐",
      location_type: "家里",
      items: [liquidItem],
    });

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      meal_type: "lunch",
      location_type: "home",
    });
  });

  it("normalizes aliases inside an update draft", () => {
    const result = normalize("update", {
      draft: {
        meal_type: "加餐",
        location_type: "外卖",
        items: [liquidItem],
      },
    });

    expect(result.error).toBeUndefined();
    expect(result.payload.draft).toMatchObject({
      meal_type: "snack",
      location_type: "takeout",
    });
  });

  it.each([
    ["meal_type", "下午茶", "breakfast, lunch, dinner, snack, or other"],
    ["location_type", "办公室", "home, restaurant, takeout, or unknown"],
  ])("returns a field-specific error for unsupported %s", (field, value, expected) => {
    const result = normalize("record", {
      meal_type: "午餐",
      location_type: "家里",
      [field]: value,
      items: [liquidItem],
    });

    expectInvalid(result, field, "unsupported_value");
    expect(result.error).toMatchObject({ expected });
  });
});
