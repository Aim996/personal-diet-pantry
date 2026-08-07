import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { normalizeToolPayload } from "../src/index.js";
import { PantryParametersSchema } from "../src/schemas.js";


const base = {
  action: "add",
  food_name: "豆花",
  quantity: "180",
  unit: "g",
  added_at: "2026-08-02T08:00:00+08:00",
  source_text: "豆花8月5日到期",
};


describe("local calendar expiry input", () => {
  it("accepts one calendar date without constructing a timezone offset", () => {
    const request = { ...base, expiry_date: "2026-08-05" };
    expect(Value.Check(PantryParametersSchema, request)).toBe(true);
    const { action, ...payload } = request;
    const normalized = normalizeToolPayload("pantry", action, payload, {});
    expect(normalized.error).toBeUndefined();
    expect(normalized.payload.expiry_date).toBe("2026-08-05");
    expect(normalized.payload).not.toHaveProperty("expires_at");
  });

  it("rejects simultaneous calendar and timestamp expiry", () => {
    expect(Value.Check(PantryParametersSchema, {
      ...base,
      expiry_date: "2026-08-05",
      expires_at: "2026-08-05T23:59:59-05:00",
    })).toBe(false);
  });

  it("rejects an impossible calendar date", () => {
    const { action, ...payload } = {
      ...base,
      expiry_date: "2026-02-30",
    };
    const normalized = normalizeToolPayload("pantry", action, payload, {});
    expect(normalized.error).toMatchObject({
      code: "INVALID_INPUT",
      field: "expiry_date",
      reason: "invalid_format",
    });
  });
});
