import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const readJson = (name: string) =>
  JSON.parse(readFileSync(new URL(`../${name}`, import.meta.url), "utf8"));

describe("release version contract", () => {
  it("publishes a SemVer package version and a four-part product version", () => {
    const pkg = readJson("package.json");
    const plugin = readJson("openclaw.plugin.json");
    expect(pkg.version).toBe("0.9.2");
    expect(pkg.productVersion).toBe("0.7.5.2");
    expect(plugin.version).toBe("0.9.2");
    expect(pkg.engines.node).toBe(
      ">=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0",
    );
  });
});
