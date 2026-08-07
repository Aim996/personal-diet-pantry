import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { normalizeToolPayload } from "../src/index.js";
import { PantryParametersSchema } from "../src/schemas.js";


const withoutExpiry = {
  action: "add",
  food_name: "苹果",
  quantity: "2",
  unit: "pieces",
  source_text: "刚买了俩苹果，放冰箱了",
};


describe("v0.7.5 pantry defaults at the plugin boundary", () => {
  it("accepts an ordinary add without production or expiry dates", () => {
    expect(Value.Check(PantryParametersSchema, withoutExpiry)).toBe(true);
  });

  it("does not manufacture an expiry requirement during normalization", () => {
    const { action, ...payload } = withoutExpiry;
    const normalized = normalizeToolPayload("pantry", action, payload, {
      now: "2026-08-07T19:20:00+08:00",
    });

    expect(normalized.error).toBeUndefined();
    expect(normalized.payload).not.toHaveProperty("expiry_date");
    expect(normalized.payload).not.toHaveProperty("expires_at");
    expect(normalized.payload.added_at).toBe("2026-08-07T19:20:00+08:00");
  });

  it("still rejects two conflicting explicit expiry facts", () => {
    expect(Value.Check(PantryParametersSchema, {
      ...withoutExpiry,
      expiry_date: "2026-08-12",
      expires_at: "2026-08-12T23:59:59+08:00",
    })).toBe(false);
  });
});
