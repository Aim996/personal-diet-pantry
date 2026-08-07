import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { normalizeToolPayload } from "../src/index.js";
import { PantryParametersSchema } from "../src/schemas.js";


const canonicalAdd = {
  action: "add",
  food_name: "青禾无糖豆花",
  quantity: "360",
  unit: "g",
  display_quantity: "2",
  display_unit: "盒",
  base_quantity_per_display_unit: "180",
  package_hierarchy: [
    { quantity: "2", unit: "提" },
    { per_parent: "6", unit: "盒" },
  ],
  added_at: "2026-08-02T08:00:00+08:00",
  expires_at: "2026-08-03T23:59:59+08:00",
  source_text: "两盒豆花，一盒180克",
};


describe("pantry package semantics at the TypeScript boundary", () => {
  it("accepts complete canonical package facts and rejects partial facts", () => {
    expect(Value.Check(PantryParametersSchema, canonicalAdd)).toBe(true);
    const { display_unit: _displayUnit, ...partial } = canonicalAdd;
    expect(Value.Check(PantryParametersSchema, {
      ...partial,
    })).toBe(false);
    expect(Value.Check(PantryParametersSchema, {
      ...canonicalAdd,
      package_hierarchy: [{ unit: "箱", unexpected: "value" }],
    })).toBe(false);
  });

  it("keeps canonical package facts after normalization", () => {
    const { action, ...payload } = canonicalAdd;
    const result = normalizeToolPayload("pantry", action, payload, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      quantity: "360",
      unit: "g",
      display_quantity: "2",
      display_unit: "盒",
      base_quantity_per_display_unit: "180",
      package_hierarchy: canonicalAdd.package_hierarchy,
    });
  });

  it("derives pantry quantity from canonical package facts", () => {
    const result = normalizeToolPayload("pantry", "add", {
      food_name: "小象无糖豆浆",
      unit: "ml",
      display_quantity: "2",
      display_unit: "盒",
      base_quantity_per_display_unit: "250",
      expires_at: "2026-08-03T23:59:59+08:00",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload.quantity).toBe("500");
    expect(result.payload.unit).toBe("ml");
  });

  it("normalizes a natural package count plus per-package gram weight once", () => {
    const result = normalizeToolPayload("pantry", "preview_add", {
      food_name: "山姆原味酸奶",
      quantity: 2,
      unit: "盒",
      average_unit_weight_g: 200,
      storage_location: "冰箱",
      expiry_date: "2026-08-15",
      source_text: "刚买了两盒山姆原味酸奶，每盒200克，放冰箱。",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      quantity: "400",
      unit: "g",
      display_quantity: 2,
      display_unit: "盒",
      base_quantity_per_display_unit: 200,
    });
    expect(result.payload).not.toHaveProperty("average_unit_weight_g");
  });

  it("derives an explicit Chinese liquid package relation from the user's source text", () => {
    const result = normalizeToolPayload("pantry", "preview_add", {
      food_name: "UAT19原味燕麦奶",
      quantity: 2,
      unit: "盒",
      storage_location: "冰箱",
      expiry_date: "2026-08-20",
      source_text: "买了2盒UAT19原味燕麦奶，每盒250毫升，放冰箱。",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      quantity: "500",
      unit: "ml",
      display_quantity: 2,
      display_unit: "盒",
      base_quantity_per_display_unit: "250",
    });
  });

  it("canonicalizes an explicit Chinese base unit without losing package facts", () => {
    const result = normalizeToolPayload("pantry", "preview_add", {
      food_name: "UAT19原味燕麦奶",
      quantity: 500,
      unit: "毫升",
      display_quantity: 2,
      display_unit: "盒",
      base_quantity_per_display_unit: 250,
      expiry_date: "2026-08-20",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      quantity: "500",
      unit: "ml",
      display_quantity: 2,
      display_unit: "盒",
      base_quantity_per_display_unit: 250,
    });
  });

  it("treats commit_add as immutable handle consumption", () => {
    const result = normalizeToolPayload("pantry", "commit_add", {
      commit_handle: "wfh_abcdefghijklmnopqrstuv",
      quantity: 500,
      unit: "毫升",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toEqual({
      commit_handle: "wfh_abcdefghijklmnopqrstuv",
    });
  });

  it("does not multiply an already canonical package payload again", () => {
    const result = normalizeToolPayload("pantry", "preview_add", {
      food_name: "山姆原味酸奶",
      quantity: "400",
      unit: "g",
      display_quantity: 2,
      display_unit: "盒",
      base_quantity_per_display_unit: 200,
      expiry_date: "2026-08-15",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload.quantity).toBe("400");
    expect(result.payload.unit).toBe("g");
  });

  it("still requires an explicit base unit for packaged stock", () => {
    const result = normalizeToolPayload("pantry", "add", {
      food_name: "小象无糖豆浆",
      display_quantity: "2",
      display_unit: "盒",
      base_quantity_per_display_unit: "250",
      expires_at: "2026-08-03T23:59:59+08:00",
    }, {});

    expect(result.error).toMatchObject({
      code: "INVALID_INPUT",
      field: "unit",
      reason: "required",
    });
  });

  it("rejects an explicit quantity that conflicts with package facts", () => {
    const result = normalizeToolPayload("pantry", "add", {
      food_name: "小象无糖豆浆",
      quantity: "450",
      unit: "ml",
      display_quantity: "2",
      display_unit: "盒",
      base_quantity_per_display_unit: "250",
      expires_at: "2026-08-03T23:59:59+08:00",
    }, {});

    expect(result.error).toEqual({
      code: "INVALID_INPUT",
      message: "The request is invalid",
      field: "quantity",
      reason: "incompatible",
      expected: "500 from display_quantity × base_quantity_per_display_unit",
      retryable: true,
    });
    expect(result.payload.quantity).toBe("450");
  });

  it("maps complete v0.7.2 package fields to canonical facts", () => {
    const result = normalizeToolPayload("pantry", "add", {
      food_name: "青禾无糖豆花",
      unit: "g",
      package_count: "2",
      quantity_per_package: "180",
      package_unit: "g",
      expires_at: "2026-08-03T23:59:59+08:00",
    }, {});

    expect(result.error).toBeUndefined();
    expect(result.payload).toMatchObject({
      quantity: "360",
      unit: "g",
      display_quantity: "2",
      display_unit: "pack",
      base_quantity_per_display_unit: "180",
    });
    expect(result.payload).not.toHaveProperty("package_count");
    expect(result.payload).not.toHaveProperty("quantity_per_package");
    expect(result.payload).not.toHaveProperty("package_unit");
  });
});
