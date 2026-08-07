import { describe, expect, it } from "vitest";

import {
  FORMAL_MUTATION_ACTIONS,
  TOOL_ACTIONS,
  TOOL_NAMES,
} from "../src/generated/tool-contracts.js";

describe("generated tool contracts", () => {
  it("contains the complete seven-tool action inventory", () => {
    expect(TOOL_NAMES).toEqual({
      meal: "diet_meal",
      pantry: "diet_pantry",
      report: "diet_report",
      system: "diet_system",
      transaction: "diet_transaction",
      water: "diet_water",
      weight: "diet_weight",
    });
    expect(
      Object.values(TOOL_ACTIONS).reduce(
        (total, actions) => total + actions.length,
        0,
      ),
    ).toBe(75);
    expect(TOOL_ACTIONS.pantry).toContain("search");
    expect(TOOL_ACTIONS.system).toContain("maintenance_status");
  });

  it("marks only formal mutation actions", () => {
    expect(FORMAL_MUTATION_ACTIONS).toContain("meal.record");
    expect(FORMAL_MUTATION_ACTIONS).toContain("weight.record");
    expect(FORMAL_MUTATION_ACTIONS).not.toContain("system.backup");
  });
});
