import { execFileSync } from "node:child_process";
import {
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";


const projectRoot = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
);

function packedFiles(): string[] {
  const output = process.platform === "win32"
    ? execFileSync(
        process.env.ComSpec ?? "cmd.exe",
        ["/d", "/s", "/c", "npm pack --dry-run --json"],
        {
          cwd: projectRoot,
          encoding: "utf8",
          windowsHide: true,
        },
      )
    : execFileSync(
        "npm",
        ["pack", "--dry-run", "--json"],
        {
          cwd: projectRoot,
          encoding: "utf8",
        },
      );
  const result = JSON.parse(output) as Array<{
    files: Array<{ path: string }>;
  }>;
  return result[0]!.files.map((item) => item.path).sort();
}

function filesBelow(root: string): string[] {
  return readdirSync(root).flatMap((name) => {
    const path = resolve(root, name);
    return statSync(path).isDirectory()
      ? filesBelow(path)
      : [relative(projectRoot, path).replaceAll("\\", "/")];
  });
}

describe("installable package contents", () => {
  it("contains the runtime allowlist and no source or user data", () => {
    const files = packedFiles();
    const pkg = JSON.parse(
      readFileSync(resolve(projectRoot, "package.json"), "utf8"),
    ) as { files: string[] };

    expect(pkg.files).toContain("skills/personal-diet-pantry");
    expect(pkg.files).toContain("LICENSE");
    expect(pkg.files).not.toContain("UPDATE-v0.7.4.0.zh-CN.md");
    expect(pkg.files).not.toContain("UPDATE-v0.7.4.2.zh-CN.md");
    expect(pkg.files).not.toContain("GITHUB-WORKFLOW.zh-CN.md");
    expect(files).toEqual(
      expect.arrayContaining([
        "dist/index.js",
        "dist/generated/tool-contracts.js",
        "migrations/013_intake_data_correctness.sql",
        "migrations/021_package_semantics_and_product_operations.sql",
        "migrations/022_pantry_default_provenance.sql",
        "migrations/023_goal_update_preview.sql",
        "python/personal_diet_pantry/package_semantics.py",
        "python/personal_diet_pantry/pantry_defaults.py",
        "python/personal_diet_pantry/service.py",
        "skills/personal-diet-pantry/SKILL.md",
        "templates/en/daily-report.md",
        "templates/en/weekly-report.md",
        "templates/en/monthly-report.md",
        "templates/zh-CN/daily-report.md",
        "templates/zh-CN/weekly-report.md",
        "templates/zh-CN/monthly-report.md",
        "LICENSE",
        "UPDATE-v0.7.5.4.zh-CN.md",
      ]),
    );
    expect(files).toEqual(
      expect.arrayContaining(
        filesBelow(
          resolve(projectRoot, "skills", "personal-diet-pantry"),
        ),
      ),
    );
    expect(
      files.filter((path) =>
        [
          "src/",
          "tests/",
          "src-tests/",
          "contracts/",
          "node_modules/",
        ].some((prefix) => path.startsWith(prefix)),
      ),
    ).toEqual([]);
    expect(files).not.toContain("UPDATE-v0.7.4.0.zh-CN.md");
    expect(files).not.toContain("UPDATE-v0.7.4.2.zh-CN.md");
    expect(files).not.toContain("GITHUB-WORKFLOW.zh-CN.md");
    expect(files).not.toContain("dist/direct-write-policy.js");
    expect(files).not.toContain("dist/direct-write-policy.d.ts");
    expect(
      files.filter((path) =>
        /(?:^|\/)(?:reports?|backups?)(?:\/|$)/i.test(path)
        || /\.(?:db|sqlite|sqlite3)$/i.test(path),
      ),
    ).toEqual([]);
  }, 60_000);
});
