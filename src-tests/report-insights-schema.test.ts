import { describe, expect, it } from "vitest";

import { ReportParametersSchema } from "../src/schemas.js";


describe("report insights schema", () => {
  it("publishes insights as a bounded action", () => {
    const branches = (
      ReportParametersSchema as unknown as {
        anyOf: Array<{
          properties: {
            action: { const: string };
            period?: { anyOf: Array<{ const: string }> };
            within_days?: { minimum: number; maximum: number };
            limit?: { minimum: number; maximum: number };
          };
        }>;
      }
    ).anyOf;
    const branch = branches.find(
      (item) => item.properties.action.const === "insights",
    );

    expect(branch).toBeDefined();
    expect(
      branch?.properties.period?.anyOf.map((item) => item.const),
    ).toEqual(["daily", "weekly", "monthly"]);
    expect(branch?.properties.within_days?.minimum).toBe(1);
    expect(branch?.properties.within_days?.maximum).toBe(30);
    expect(branch?.properties.limit?.minimum).toBe(1);
    expect(branch?.properties.limit?.maximum).toBe(10);
  });
});
