import { describe, expect, it } from "vitest";

import * as bridge from "../src/bridge.js";


describe("Python executable selection", () => {
  const selector = (
    bridge as typeof bridge & {
      selectPythonExecutable?: (
        configured: string | undefined,
        environment: string | undefined,
        platform: NodeJS.Platform,
      ) => string;
    }
  ).selectPythonExecutable;

  it("defaults to python3 on Linux when no override is configured", () => {
    expect(selector).toBeTypeOf("function");
    expect(selector?.(undefined, undefined, "linux")).toBe("python3");
  });

  it("keeps the explicit executable as the highest-priority override", () => {
    expect(selector?.("/opt/custom/python", "/env/python", "linux")).toBe(
      "/opt/custom/python",
    );
  });

  it("uses PYTHON before the platform default", () => {
    expect(selector?.(undefined, "/env/python", "linux")).toBe("/env/python");
  });

  it("keeps python as the Windows default", () => {
    expect(selector?.(undefined, undefined, "win32")).toBe("python");
  });
});
