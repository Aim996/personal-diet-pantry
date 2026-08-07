import { normalizeToolParameterSchema } from "@openclaw/ai/internal/openai";
import { describe, expect, it } from "vitest";

import { MealParametersSchema } from "../src/schemas.js";

const normalized = normalizeToolParameterSchema(MealParametersSchema, {
  modelProvider: "openai",
  modelId: "deepseek-v4-flash",
});
const serialized = JSON.stringify(normalized);

describe("meal public schema budget", () => {
  it("fits the DeepSeek model-facing budget without intersections", () => {
    expect(Buffer.byteLength(serialized, "utf8")).toBeLessThan(160_000);
    expect(serialized).toContain("per_100ml");
    expect(serialized).toContain("consumed_volume_ml");
    expect(serialized).not.toContain('"allOf"');
  });

  it("keeps every public meal action after OpenClaw normalization", () => {
    const actionValues = (normalized as {
      properties: { action: { enum: string[] } };
    }).properties.action.enum;
    expect(new Set(actionValues)).toEqual(new Set([
      "record", "record_prepared", "record_cooking", "save_recipe",
      "suggest_recipes", "preview_meal_plan", "nutrition_estimate",
      "preview_record", "commit_record", "query", "update", "delete",
    ]));
  });
});
