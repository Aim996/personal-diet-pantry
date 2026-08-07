import { Value } from "typebox/value";
import { describe, expect, it } from "vitest";

import { SystemParametersSchema } from "../src/schemas.js";

describe("maintenance schemas", () => {
  it("accepts bounded operation keys on maintenance actions", () => {
    expect(
      Value.Check(SystemParametersSchema, {
        action: "backup",
        label: "nightly",
        operation_key: "nightly-20260730",
      }),
    ).toBe(true);
  });

  it("accepts maintenance status and bounded history queries", () => {
    expect(
      Value.Check(SystemParametersSchema, {
        action: "maintenance_status",
        operation_handle: "mop_0123456789abcdef0123456789abcdef",
      }),
    ).toBe(true);
    expect(
      Value.Check(SystemParametersSchema, {
        action: "maintenance_history",
        limit: 20,
      }),
    ).toBe(true);
    expect(
      Value.Check(SystemParametersSchema, {
        action: "maintenance_history",
        limit: 21,
      }),
    ).toBe(false);
  });
});
