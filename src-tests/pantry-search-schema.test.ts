import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { normalizeToolPayload } from "../src/index.js";
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

  it("accepts product-handle deduct and quantity discard", () => {
    const productTarget = {
      inventory_match_handle: "wfh_abcdefghijklmnopqrstuv",
      quantity: "3",
      unit: "盒",
      source_text: "用了三盒豆花",
    };
    expect(Value.Check(PantryParametersSchema, {
      action: "deduct",
      ...productTarget,
    })).toBe(true);
    expect(Value.Check(PantryParametersSchema, {
      action: "discard",
      ...productTarget,
      waste_category: "spoilage",
    })).toBe(true);
  });

  it("preserves storage location for targeted pantry queries", () => {
    const result = normalizeToolPayload("pantry", "query", {
      normalized_name: "小象无糖豆浆",
      storage_location: "fridge",
      include_details: true,
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      normalized_name: "小象无糖豆浆",
      storage_location: "fridge",
      include_details: true,
    });
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
