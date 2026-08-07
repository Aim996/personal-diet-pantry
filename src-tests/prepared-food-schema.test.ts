import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { MealParametersSchema } from "../src/schemas.js";


describe("direct prepared-food schema", () => {
  it("accepts a compact prepared handle request", () => {
    expect(Value.Check(MealParametersSchema, {
      action: "record_prepared",
      prepared_food_handle: "wfh_abcdefghijklmnopqrstuv",
      source_text: "刚把冰箱那盒猫耳朵吃了",
    })).toBe(true);
  });

  it("requires quantity and unit together", () => {
    expect(Value.Check(MealParametersSchema, {
      action: "record_prepared",
      prepared_food_handle: "wfh_abcdefghijklmnopqrstuv",
      quantity: "90",
      source_text: "吃了半盒猫耳朵",
    })).toBe(false);
  });
});
