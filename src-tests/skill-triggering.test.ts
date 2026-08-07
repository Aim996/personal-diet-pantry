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
      "## 先理解，再行动",
    );
    const readinessIndex = skill.indexOf("## 调用原则");

    expect(contractIndex).toBeGreaterThan(0);
    expect(readinessIndex).toBeGreaterThan(contractIndex);
  });

  test("documents positive and negative oral-language boundaries", () => {
    for (const phrase of [
      "口语、错别字、省略主语、自然单位",
      "清楚、已发生且信息足够的单一事实",
      "普通正向输入由智能体",
      "不是封闭短语表",
    ]) {
      expect(skill).toContain(phrase);
    }

    for (const phrase of [
      "体重写入需要明确的称重语义或重量单位",
      "完全孤立的数字不能写体重",
      "应问单位或含义",
      "否定、没发生、未来计划和他人行为优先于",
      "保持零写入",
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

    expect(skill).toContain("渠道变化不能成为绕过 Skill 的理由");
    expect(skill).toContain("Telegram");
    expect(skill).toContain("WebUI");
  });

  test("defines self-contained runtime and minimal-call contracts", () => {
    for (const phrase of [
      "信息已完整就完成原意",
      "只有未改变预览的纯确认",
      "新事实，不等于确认旧预览",
      "成功或终止后立即停止",
      "同一轮",
      "Runtime reference reads are exactly 0",
    ]) {
      expect(skill).toContain(phrase);
    }

    expect(skill).toContain("运行时不得打开 `references/`");
    expect(skill).toContain("runtime agents must not read or route through it");
    expect(skill).not.toContain("genuinely cross-domain intent loads at most two");
  });

  test("defines an open capability map and terminal failure boundaries", () => {
    for (const phrase of [
      "## 能力地图",
      "不规定每句话必须经过哪条固定流水线",
      "相同失败指纹不能原样重试",
      "结构化的字段、原因和期望值",
    ]) {
      expect(skill).toContain(phrase);
    }

    expect(skill).not.toContain("Use exactly one primary route");
    expect(skill).not.toContain("## Preferred capability routes");
    expect(skill).not.toContain("retry the same action once");
    expect(skill).not.toContain("allow at most one correction");
  });

  test("binds recent-operation status and displayed time to deterministic evidence", () => {
    expect(skill).toContain("只用一次 `diet_transaction get_recent`");
    expect(skill).toContain("存在 `*_at_local` 时不展示 UTC companion");
    expect(skill).toContain("不要再扫描 meal、pantry、report、文件或其他业务工具");
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
