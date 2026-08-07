import { readFileSync } from "node:fs";

import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { WeightParametersSchema } from "../src/schemas.js";


const handle = `wfh_${"a".repeat(32)}`;

describe("body-weight public schema", () => {
  it("accepts a bare kilogram record and an optional free status", () => {
    expect(Value.Check(
      WeightParametersSchema,
      { action: "record", weight: 105 },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "record",
        weight: "105",
        unit: "kg",
        status_note: "空腹",
      },
    )).toBe(true);
  });

  it("accepts explicit Chinese jin and pounds", () => {
    expect(Value.Check(
      WeightParametersSchema,
      { action: "record", weight: 105, unit: "jin" },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "record", weight: 105, unit: "lb" },
    )).toBe(true);
  });

  it("does not allow callers to supply measurement time or raw ids", () => {
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "record",
        weight: 105,
        measured_at: "2026-07-30T08:30:00+08:00",
      },
    )).toBe(false);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "delete", id: 1 },
    )).toBe(false);
  });

  it("bounds status, query limit, and handle-based changes", () => {
    expect(Value.Check(
      WeightParametersSchema,
      { action: "record", weight: 105, status_note: "x".repeat(81) },
    )).toBe(false);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "query", limit: 100 },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "query", limit: 101 },
    )).toBe(false);
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "update",
        record_handle: handle,
        weight: 104.8,
        status_note: null,
      },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "update",
        record_handle: handle,
        status_note: "睡前",
      },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "update",
        record_handle: handle,
      },
    )).toBe(false);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "delete", record_handle: handle },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "delete", commit_handle: handle },
    )).toBe(true);
    expect(Value.Check(
      WeightParametersSchema,
      { action: "delete" },
    )).toBe(false);
    expect(Value.Check(
      WeightParametersSchema,
      {
        action: "delete",
        record_handle: handle,
        commit_handle: handle,
      },
    )).toBe(false);
  });

  it("declares diet_weight in the plugin contract", () => {
    const plugin = JSON.parse(
      readFileSync(
        new URL("../openclaw.plugin.json", import.meta.url),
        "utf8",
      ),
    );
    expect(plugin.contracts.tools).toContain("diet_weight");
    expect(plugin.contracts.tools).toHaveLength(7);
  });
});
