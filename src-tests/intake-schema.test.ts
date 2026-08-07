import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { MealParametersSchema } from "../src/schemas.js";


const nutritionFacts = {
  calories: 33,
  protein: 3.5,
  fat: 1.8,
  carbohydrate: 2,
  fiber: 0,
  sodium: 50,
  hydration_ml: 95,
  source: "fixture",
  source_grade: "A",
};

const soyRequest = {
  action: "record",
  occurred_at: "2026-07-30T08:00:00+08:00",
  meal_type: "breakfast",
  source_text: "喝了500ml豆浆",
  location_type: "home",
  items: [{
    raw_name: "豆浆",
    normalized_name: "soy milk",
    amount: 500,
    unit: "ml",
    consumed_volume_ml: 500,
    nutrition_basis: "per_100ml",
    nutrition_dataset_version: "fixture-1",
    nutrition_facts: nutritionFacts,
  }],
};

describe("meal intake measurement schema", () => {
  it("allows a uniquely selected meal to be deleted without redundant audit prose", () => {
    expect(Value.Check(MealParametersSchema, {
      action: "delete",
      meal_handle: `wfh_${"a".repeat(32)}`,
    })).toBe(true);
  });

  it("accepts per-100ml evidence with consumed volume", () => {
    expect(Value.Check(MealParametersSchema, soyRequest)).toBe(true);
  });

  it("allows ordinary meal records to omit occurred_at", () => {
    const { occurred_at: _occurredAt, ...request } = soyRequest;

    expect(Value.Check(MealParametersSchema, request)).toBe(true);
    expect(Value.Check(MealParametersSchema, {
      ...request,
      action: "preview_record",
    })).toBe(true);
  });

  it("allows clear intake to omit analytical meal and location labels", () => {
    const {
      meal_type: _mealType,
      location_type: _locationType,
      ...request
    } = soyRequest;

    expect(Value.Check(MealParametersSchema, request)).toBe(true);
    expect(Value.Check(MealParametersSchema, {
      ...request,
      action: "preview_record",
    })).toBe(true);
  });

  it("allows cooking records to omit occurred_at", () => {
    expect(Value.Check(MealParametersSchema, {
      action: "record_cooking",
      meal_type: "dinner",
      source_text: "cooked fried rice",
      dish: {
        raw_name: "fried rice",
        normalized_name: "fried rice",
        unit: "portion",
        consumed_quantity: 1,
        ingredients: [soyRequest.items[0]],
      },
    })).toBe(true);
  });

  it("leaves direct-nutrition basis relationships to runtime validation", () => {
    const item = {
      ...soyRequest.items[0],
      nutrition_basis: undefined,
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
  });

  it("leaves basis-measure relationships to runtime validation", () => {
    const item = {
      ...soyRequest.items[0],
      consumed_volume_ml: undefined,
      consumed_weight_g: 500,
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
  });

  it("leaves a basis without direct nutrition to runtime validation", () => {
    const item = {
      ...soyRequest.items[0],
      nutrition_facts: undefined,
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
  });

  it("rejects a zero matching measure for a scaling basis", () => {
    const item = {
      ...soyRequest.items[0],
      consumed_volume_ml: 0,
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(false);
  });

  it("preserves an independent mass measurement for volume-based facts", () => {
    const item = {
      ...soyRequest.items[0],
      consumed_weight_g: 510,
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
  });

  it("accepts a pantry-resolved item without caller nutrition", () => {
    const item = {
      raw_name: "苹果",
      normalized_name: "apple",
      amount: 1,
      unit: "piece",
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
  });

  it("accepts only opaque pantry product handles on meal items", () => {
    const item = {
      ...soyRequest.items[0],
      inventory_match_handle: "wfh_abcdefghijklmnopqrstuv",
    };
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
    expect(Value.Check(
      MealParametersSchema,
      {
        ...soyRequest,
        items: [{ ...item, inventory_match_handle: "wfh_invented" }],
      },
    )).toBe(false);
  });

  it("keeps top-level meal items closed", () => {
    const item = {
      ...soyRequest.items[0],
      unexpected: true,
    };
    expect(Value.Check(MealParametersSchema, {
      ...soyRequest,
      items: [item],
    })).toBe(false);
  });

  it("defers nested ingredient structure to bounded runtime validation", () => {
    const nested = {
      ...soyRequest.items[0],
      ingredients: [{
        raw_name: "sugar",
        normalized_name: "sugar",
        unexpected: true,
      }],
    };
    expect(Value.Check(MealParametersSchema, {
      ...soyRequest,
      items: [nested],
    })).toBe(true);
  });

  it("accepts either an ordinary or cooking draft for meal update", () => {
    const { action: _action, ...ordinaryDraft } = soyRequest;
    const cookingDraft = {
      occurred_at: "2026-07-30T18:00:00+08:00",
      meal_type: "dinner",
      source_text: "修正炒饭",
      dish: {
        raw_name: "炒饭",
        normalized_name: "fried rice",
        unit: "piece",
        consumed_quantity: 1,
        ingredients: [{
          raw_name: "150克米饭",
          normalized_name: "rice",
          amount: 150,
          unit: "g",
          consumed_weight_g: 150,
          nutrition_basis: "per_100g",
          nutrition_facts: nutritionFacts,
        }],
      },
    };
    const target = { meal_handle: "wfh_abcdefghijklmnopqrstuv" };

    expect(Value.Check(MealParametersSchema, {
      action: "update",
      ...target,
      draft: ordinaryDraft,
    })).toBe(true);
    expect(Value.Check(MealParametersSchema, {
      action: "update",
      ...target,
      draft: cookingDraft,
    })).toBe(true);
    expect(Value.Check(MealParametersSchema, {
      action: "update",
      ...target,
      draft: { ...ordinaryDraft, dish: cookingDraft.dish },
    })).toBe(false);
  });

  it("lets an ordinary update omit unchanged meal-level fields", () => {
    const target = { meal_handle: "wfh_abcdefghijklmnopqrstuv" };

    expect(Value.Check(MealParametersSchema, {
      action: "update",
      ...target,
      draft: { items: soyRequest.items },
    })).toBe(true);
  });

  it("requires complete positive preparation-loss evidence", () => {
    const loss = {
      kind: "fat",
      quantity: 4,
      unit: "g",
      nutrition_facts: {
        ...nutritionFacts,
        calories: 36,
        protein: 0,
        fat: 4,
        carbohydrate: 0,
        hydration_ml: undefined,
      },
    };
    const item = {
      ...soyRequest.items[0],
      preparation_losses: [loss],
    };

    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [item] },
    )).toBe(true);
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [{
        ...item,
        preparation_losses: [{
          kind: "fat", quantity: 4, unit: "g",
        }],
      }] },
    )).toBe(false);
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [{
        ...item,
        preparation_losses: [{ ...loss, quantity: 0 }],
      }] },
    )).toBe(false);
    expect(Value.Check(
      MealParametersSchema,
      { ...soyRequest, items: [{
        ...item,
        preparation_losses: [{ ...loss, kind: "steam" }],
      }] },
    )).toBe(false);
  });
});
