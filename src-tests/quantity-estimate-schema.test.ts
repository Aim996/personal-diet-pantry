import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { MealParametersSchema } from "../src/schemas.js";


const baseItem = {
  raw_name: "花生",
  normalized_name: "peanut",
  amount: 25,
  unit: "g",
  portion_expression: "一点",
};

const baseRequest = {
  action: "record",
  occurred_at: "2026-08-04T10:00:00+08:00",
  meal_type: "snack",
  source_text: "吃了一点花生",
  location_type: "home",
};

describe("quantity estimate input contract", () => {
  it("accepts bounded registered and future namespaced policy identifiers", () => {
    for (const policyKey of [
      "portion.generic.small_amount",
      "portion.future.image_estimate",
    ]) {
      expect(Value.Check(MealParametersSchema, {
        ...baseRequest,
        items: [{
          ...baseItem,
          quantity_estimate: {
            suggested: 25,
            lower: 10,
            upper: 40,
            unit: "g",
            evidence_type: "household_range",
            policy_key: policyKey,
          },
        }],
      })).toBe(true);
    }
  });

  it("requires expression, amount, and unit with estimate metadata", () => {
    for (const missing of ["portion_expression", "amount", "unit"] as const) {
      const item = { ...baseItem } as Record<string, unknown>;
      delete item[missing];
      item.quantity_estimate = {
        suggested: 25,
        lower: 10,
        upper: 40,
        unit: "g",
        evidence_type: "household_range",
        policy_key: "portion.generic.small_amount",
      };
      expect(Value.Check(MealParametersSchema, {
        ...baseRequest,
        items: [item],
      })).toBe(false);
    }
  });

  it("rejects non-positive bounds and malformed identifiers", () => {
    for (const quantityEstimate of [
      {
        suggested: 25,
        lower: 0,
        upper: 40,
        unit: "g",
        evidence_type: "household_range",
        policy_key: "portion.generic.small_amount",
      },
      {
        suggested: 25,
        lower: 10,
        upper: 40,
        unit: "g",
        evidence_type: "bad key",
        policy_key: "not-namespaced",
      },
    ]) {
      expect(Value.Check(MealParametersSchema, {
        ...baseRequest,
        items: [{ ...baseItem, quantity_estimate: quantityEstimate }],
      })).toBe(false);
    }
  });
});
