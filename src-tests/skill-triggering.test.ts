import { readFileSync } from "node:fs";

import { describe, expect, test } from "vitest";

const skill = readFileSync(
  new URL("../skills/personal-diet-pantry/SKILL.md", import.meta.url),
  "utf8",
);
const pantryReference = readFileSync(
  new URL(
    "../skills/personal-diet-pantry/references/pantry-and-expiry.md",
    import.meta.url,
  ),
  "utf8",
);

const frontmatterMatch = skill.match(/^---\r?\n([\s\S]*?)\r?\n---/);
const frontmatter = frontmatterMatch?.[1] ?? "";
const description =
  frontmatter.match(/^description:\s*(.+)$/m)?.[1]?.trim() ?? "";

describe("personal-diet-pantry natural-language activation", () => {
  test("uses only the required activation metadata", () => {
    const keys = frontmatter
      .split(/\r?\n/)
      .filter((line) => line.trim().length > 0)
      .map((line) => line.split(":", 1)[0]);

    expect(frontmatterMatch).not.toBeNull();
    expect(keys).toEqual(["name", "description"]);
    expect([...description].length).toBeLessThan(1024);
    expect(description).toMatch(/^Use when /);
    expect(description).toContain("meals");
    expect(description).toContain("plain water");
    expect(description).toContain("cooking or leftovers");
    expect(description).toContain("pantry stock or expiry");
    expect(description).toContain("body weight");
    expect(description).toContain("without naming the Skill");
    expect(description).toContain("any diet_* task");
    expect(description).toContain("events explicitly not done");
    expect(description).toContain(
      "MUST read this SKILL.md before any reply or diet_* tool",
    );
  });

  test("places the activation contract before readiness", () => {
    const contractIndex = skill.indexOf(
      "## Natural-language activation",
    );
    const readinessIndex = skill.indexOf("## Readiness");

    expect(contractIndex).toBeGreaterThan(0);
    expect(readinessIndex).toBeGreaterThan(contractIndex);
  });

  test("documents positive and negative oral-language boundaries", () => {
    for (const phrase of [
      "Chinese colloquial wording",
      "omitted subjects",
      "spoken quantities",
      "clearly completed",
    ]) {
      expect(skill).toContain(phrase);
    }

    for (const phrase of [
      "explicit body-weight wording",
      "A bare number without explicit body-weight wording",
      "must not create a body-weight record",
      "zero tool calls",
      "Non-occurrence always wins over weight",
    ]) {
      expect(skill).toContain(phrase);
    }
  });

  test("keeps intent routing and channel-safe rendering inside the Skill", () => {
    for (const tool of [
      "diet_meal",
      "diet_water",
      "diet_weight",
      "diet_pantry",
      "diet_transaction",
      "diet_report",
      "diet_system",
    ]) {
      expect(skill).toContain(tool);
    }

    expect(skill).toContain("Never bypass it to call a `diet_*` tool");
    expect(skill).toContain("Telegram");
    expect(skill).toContain("WebUI");
  });

  test("defines self-contained runtime and minimal-call contracts", () => {
    for (const phrase of [
      "write readiness",
      "pure affirmation",
      "Supplemental facts are not confirmation",
      "one terminal result -> one reply",
      "same turn",
      "Runtime reference reads are exactly 0",
    ]) {
      expect(skill).toContain(phrase);
    }

    expect(skill).toContain("do not open `references/`");
    expect(skill).toContain("runtime agents must not read or route through it");
    expect(skill).not.toContain("genuinely cross-domain intent loads at most two");
  });

  test("defines preferred routes and terminal failure boundaries", () => {
    for (const phrase of [
      "## Preferred capability routes",
      "Use exactly one primary route",
      "one tool call",
      "unchanged failure fingerprint",
      "structured",
    ]) {
      expect(skill).toContain(phrase);
    }

    expect(skill).not.toContain("retry the same action once");
    expect(skill).not.toContain("allow at most one correction");
  });

  test("binds recent-operation status and displayed time to deterministic evidence", () => {
    expect(skill).toContain(
      "recent diet operation status | exactly one `diet_transaction get_recent`",
    );
    expect(skill).toContain(
      "Never display the UTC companion when a `*_at_local` field is present.",
    );
    expect(skill).toContain(
      "Do not scan meal, pantry, report, files, or another business tool",
    );
  });

  test("requires explicit consent before loading full pantry nutrition", () => {
    for (const phrase of [
      "Allowed `nutrition_mode` values are exactly `none | summary | full`.",
      "`full` is allowed only when the user explicitly asks for the complete/full nutrition label.",
      "A single-field nutrition question must not silently promote `summary` to `full`.",
      "If `summary` omits that field, say it is unavailable and ask whether to load the full label.",
    ]) {
      expect(pantryReference).toContain(phrase);
    }
  });
});
