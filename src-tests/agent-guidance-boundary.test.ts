import { describe, expect, it } from "vitest";

import { classifyTurnIntent } from "../src/turn-guard.js";


describe("v0.7.5.0 agent guidance boundary", () => {
  it.each([
    "吃了个玉米",
    "刚喝了137毫升水",
    "刚称了下106.8",
    "买了两盒酸奶",
    "鸡胸肉吃了100克。",
  ])("leaves an ordinary positive fact agent-directed: %s", (text) => {
    const intent = classifyTurnIntent(text);
    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(intent).not.toHaveProperty("allowedActions");
  });

  it.each([
    "差点吃了个玉米，最后没吃",
    "明早想吃个苹果",
    "我老婆喝了杯水",
    "刚才记上了吗？",
  ])("keeps an explicit no-write message read-only: %s", (text) => {
    expect(classifyTurnIntent(text)).toMatchObject({ mode: "read_only" });
  });
});
