import { describe, expect, it } from "vitest";

import {
  MealParametersSchema,
  PantryParametersSchema,
  ReportParametersSchema,
  SystemParametersSchema,
  TransactionParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
} from "../src/schemas.js";


type ActionSchema = {
  anyOf: Array<{
    properties: {
      action: { const: string };
    };
  }>;
};

function actions(schema: unknown): string[] {
  return (schema as ActionSchema).anyOf
    .map((branch) => branch.properties.action.const)
    .sort();
}

describe("all public action schemas", () => {
  it("matches the seven-domain public behavior inventory", () => {
    expect(actions(MealParametersSchema)).toEqual([
      "commit_record",
      "delete",
      "nutrition_estimate",
      "preview_meal_plan",
      "preview_record",
      "query",
      "record",
      "record_cooking",
      "record_prepared",
      "save_recipe",
      "suggest_recipes",
      "update",
    ]);
    expect(actions(WaterParametersSchema)).toEqual([
      "delete",
      "query",
      "record",
      "update",
    ]);
    expect(actions(WeightParametersSchema)).toEqual([
      "delete",
      "query",
      "record",
      "update",
    ]);
    expect(actions(PantryParametersSchema)).toEqual([
      "add",
      "adjust",
      "cancel_shopping_list",
      "commit_add",
      "commit_deduct",
      "commit_link_nutrition",
      "commit_shopping_list",
      "commit_update_metadata",
      "deduct",
      "discard",
      "freeze",
      "open",
      "preview_add",
      "preview_deduct",
      "preview_link_nutrition",
      "preview_shopping_list",
      "preview_update_metadata",
      "query",
      "query_shopping_list",
      "search",
      "thaw",
    ]);
    expect(actions(TransactionParametersSchema)).toEqual([
      "get_recent",
      "redo",
      "undo",
    ]);
    expect(actions(ReportParametersSchema)).toEqual([
      "cost_summary",
      "daily",
      "expiring_inventory",
      "insights",
      "monthly",
      "progress",
      "today",
      "trend_summary",
      "waste_summary",
      "weekly",
    ]);
    expect(actions(SystemParametersSchema)).toEqual([
      "backup",
      "commit_delete_data",
      "commit_nutrition_backfill",
      "export_data",
      "forget_preference",
      "import_data",
      "initialize",
      "maintenance_history",
      "maintenance_status",
      "migrate",
      "preview_delete_data",
      "query_goals",
      "query_nutrition_backfill",
      "query_preferences",
      "repair",
      "restore",
      "self_check",
      "update_goals",
      "update_preferences",
      "validate_database",
      "validate_import",
    ]);
  });
});
