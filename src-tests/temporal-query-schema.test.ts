import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import {
  MealParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
} from "../src/schemas.js";


const schemas = [
  MealParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
];

describe("generic temporal query descriptors", () => {
  it("accepts the same calendar, rolling, and local range modes in all query domains", () => {
    const descriptors = [
      {
        calendar_window: {
          unit: "day",
          offset: -1,
          segment: "post_workout",
        },
      },
      { rolling_window: { value: 3, unit: "hour" } },
      {
        local_range: {
          start: "2026-08-03T22:00:00",
          end: "2026-08-04T02:00:00",
        },
      },
    ];

    for (const schema of schemas) {
      for (const descriptor of descriptors) {
        expect(Value.Check(schema, { action: "query", ...descriptor })).toBe(true);
      }
    }
  });

  it("keeps the legacy occurred_on mode", () => {
    for (const schema of schemas) {
      expect(Value.Check(schema, {
        action: "query",
        occurred_on: "2026-08-03",
      })).toBe(true);
    }
  });

  it("rejects simultaneous temporal modes and malformed open policy keys", () => {
    for (const schema of schemas) {
      expect(Value.Check(schema, {
        action: "query",
        occurred_on: "2026-08-03",
        rolling_window: { value: 3, unit: "hour" },
      })).toBe(false);
      expect(Value.Check(schema, {
        action: "query",
        calendar_window: { unit: "bad key", offset: 0 },
      })).toBe(false);
    }
  });

  it("preserves optional unbounded meal and weight queries but requires a water range", () => {
    expect(Value.Check(MealParametersSchema, { action: "query" })).toBe(true);
    expect(Value.Check(WeightParametersSchema, { action: "query", limit: 20 })).toBe(true);
    expect(Value.Check(WaterParametersSchema, { action: "query" })).toBe(false);
  });
});
