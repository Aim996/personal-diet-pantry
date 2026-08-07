import { describe, expect, it } from "vitest";

import {
  authorizeTurnTool,
  classifyTurnIntent,
} from "../src/turn-guard.js";

describe("trusted turn write guard", () => {
  it("never turns a status query into a write", () => {
    const intent = classifyTurnIntent("刚才记上了吗？");

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_meal", { action: "query" })).toMatchObject({
      allowed: false,
      code: "STATUS_QUERY_ROUTE_REQUIRED",
    });
    expect(authorizeTurnTool(intent, "diet_transaction", {
      action: "get_recent",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_pantry", { action: "add" })).toMatchObject({
      allowed: false,
      code: "STATUS_QUERY_ROUTE_REQUIRED",
    });
  });

  it.each([
    ["我刚才说的玉米记一下了吗？", "diet_meal", "record"],
    ["刚才那杯水删除了吗？", "diet_water", "delete"],
    ["刚才撤销了吗？", "diet_transaction", "undo"],
  ])("keeps write-word status queries read-only: %s", (text, toolName, action) => {
    const intent = classifyTurnIntent(text);

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, toolName, {
      action,
      record_handle: "wfh_abcdefghijklmnopqrstuv",
      operation_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "READ_ONLY_TURN",
    });
  });

  it("allows an explicit conditional补记 after a status question", () => {
    const intent = classifyTurnIntent(
      "刚才玉米记上了吗？没记就帮我补记。",
    );

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
    })).toEqual({ allowed: true });
  });

  it("blocks every write in a multi-domain compound statement", () => {
    const intent = classifyTurnIntent(
      "我107.1公斤，喝了200毫升水，还吃了番茄豆腐菜。",
    );

    expect(intent).toMatchObject({
      mode: "multi_domain_write",
      domains: ["meal", "water", "weight"],
    });
    for (const [toolName, action] of [
      ["diet_weight", "record"],
      ["diet_water", "record"],
      ["diet_meal", "record"],
    ] as const) {
      expect(authorizeTurnTool(intent, toolName, { action })).toMatchObject({
        allowed: false,
        code: "COMPOUND_WRITE_REQUIRES_SPLIT",
      });
    }
  });

  it("lets the agent choose a safe create and still blocks destructive actions", () => {
    const intent = classifyTurnIntent("我吃了一个玉米。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_meal", { action: "record" })).toEqual({
      allowed: true,
    });
    expect(authorizeTurnTool(intent, "diet_water", { action: "record" })).toEqual({
      allowed: true,
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it("does not bind the real-world shorthand '记一个玉米' to a plugin-selected domain", () => {
    const intent = classifyTurnIntent("记一个玉米。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "add",
    })).toEqual({ allowed: true });
  });

  it("limits bare confirmation to a handle-bound commit", () => {
    const intent = classifyTurnIntent("确认，就按这个记。");

    expect(intent.mode).toBe("workflow_confirmation");
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "commit_record",
      commit_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", { action: "record" })).toMatchObject({
      allowed: false,
      code: "CONFIRMATION_HANDLE_REQUIRED",
    });
  });

  it.each([
    ["确认入库。", "diet_pantry", "commit_add"],
    ["确认，2盒UAT23验收燕麦奶按预览入库。", "diet_pantry", "commit_add"],
    ["确认，按上面补记。", "diet_meal", "commit_record"],
  ])(
    "treats a natural preview confirmation as commit-only: %s",
    (text, toolName, action) => {
      const intent = classifyTurnIntent(text);

      expect(intent.mode).toBe("workflow_confirmation");
      expect(authorizeTurnTool(intent, toolName, {
        action,
        commit_handle: "wfh_abcdefghijklmnopqrstuv",
      })).toEqual({ allowed: true });
      expect(authorizeTurnTool(intent, toolName, {
        action: toolName === "diet_pantry" ? "add" : "record",
      })).toMatchObject({
        allowed: false,
        code: "CONFIRMATION_HANDLE_REQUIRED",
      });
    },
  );

  it("does not confuse a confirmation-worded inventory query with workflow confirmation", () => {
    const intent = classifyTurnIntent("确认一下库存还有没有燕麦奶。");

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_pantry", { action: "add" }))
      .toMatchObject({ allowed: false, code: "READ_ONLY_TURN" });
  });

  it.each([
    "大概10粒，就按10克记吧。",
    "标签每100克：70千卡、蛋白3克、脂肪2克、碳水10克；就按整盒180克记，直接记录。",
  ])(
    "allows one final meal write when a supplemental fact includes explicit record authorization: %s",
    (text) => {
      const intent = classifyTurnIntent(text);

      expect(intent).toMatchObject({
        mode: "single_domain_write",
        domains: ["meal"],
        allowedActions: ["record"],
        finalizedSupplementalWrite: true,
      });
      expect(authorizeTurnTool(intent, "diet_meal", { action: "record" }))
        .toEqual({ allowed: true });
      expect(authorizeTurnTool(intent, "diet_meal", { action: "preview_record" }))
        .toMatchObject({ allowed: false, code: "WRITE_NOT_AUTHORIZED" });
      expect(authorizeTurnTool(intent, "diet_pantry", { action: "add" }))
        .toMatchObject({ allowed: false, code: "DOMAIN_NOT_AUTHORIZED" });
    },
  );

  it.each(["确认记上。", "就记上吧。"])(
    "binds a natural pantry confirmation to commit_add only: %s",
    (text) => {
      const intent = classifyTurnIntent(text);

      expect(intent.mode).toBe("workflow_confirmation");
      expect(authorizeTurnTool(intent, "diet_pantry", {
        action: "commit_add",
        commit_handle: "wfh_abcdefghijklmnopqrstuv",
      })).toEqual({ allowed: true });
      expect(authorizeTurnTool(intent, "diet_pantry", {
        action: "add",
        food_name: "原味燕麦奶",
      })).toMatchObject({
        allowed: false,
        code: "CONFIRMATION_HANDLE_REQUIRED",
      });
    },
  );

  it("does not treat a profile fact as permission to mutate preferences", () => {
    const intent = classifyTurnIntent("我在上海。");

    expect(authorizeTurnTool(intent, "diet_system", {
      action: "update_preferences",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it("does not put an explicitly named family member's intake on the user", () => {
    const intent = classifyTurnIntent("我老公刚喝了500ml水。");

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_water", {
      action: "record",
    })).toMatchObject({ allowed: false });
  });

  it("opens the circuit after two terminal failures in one run", () => {
    const intent = classifyTurnIntent("我吃了一个玉米。");

    expect(authorizeTurnTool(intent, "diet_meal", { action: "query" }, 2)).toEqual({
      allowed: true,
    });
    expect(authorizeTurnTool(intent, "diet_meal", { action: "record" }, 2)).toMatchObject({
      allowed: false,
      code: "TURN_TOOL_BUDGET_EXHAUSTED",
    });
  });

  it.each([
    ["两盒燕麦奶，每盒250ml，放冰箱。", "diet_pantry", "add"],
    ["刚喝了500ml水。", "diet_water", "record"],
    ["今早体重107.1公斤。", "diet_weight", "record"],
    ["吃个玉米。", "diet_meal", "record"],
  ])("leaves a normal %s create agent-directed", (text, toolName, action) => {
    const intent = classifyTurnIntent(text);

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, toolName, { action })).toEqual({
      allowed: true,
    });
  });

  it.each([
    ["把目标改成1900千卡。", "system", "diet_system", "update_goals"],
    ["这盒奶开封了。", "pantry", "diet_pantry", "open"],
    ["冰箱那盒坏豆花我丢了。", "pantry", "diet_pantry", "discard"],
    ["每日热量目标设为1900。", "system", "diet_system", "update_goals"],
  ])("keeps a protected %s write on its safety contract", (text, domain, toolName, action) => {
    const intent = classifyTurnIntent(text);

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [domain],
    });
    expect(authorizeTurnTool(intent, toolName, { action })).toEqual({
      allowed: true,
    });
  });

  it.each([
    ["今早体重107.1公斤。", "diet_weight", "update"],
    ["把目标改成1900千卡。", "diet_system", "forget_preference"],
  ])("does not widen a write into another same-domain action: %s", (
    text,
    toolName,
    action,
  ) => {
    const intent = classifyTurnIntent(text);

    expect(authorizeTurnTool(intent, toolName, {
      action,
      record_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it("keeps report reads available on a query-only turn", () => {
    const intent = classifyTurnIntent("我今天的营养进度怎么样？");

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_report", {
      action: "progress",
    })).toEqual({ allowed: true });
  });

  it("keeps an explicit undo available with an operation handle", () => {
    const intent = classifyTurnIntent("撤销刚才那条记录。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: ["transaction"],
    });
    expect(authorizeTurnTool(intent, "diet_transaction", {
      action: "undo",
      operation_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_transaction", {
      action: "redo",
      operation_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it("treats an undo target description as one transaction action", () => {
    const intent = classifyTurnIntent("撤销青柠原味酸奶入库这一笔。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: ["transaction"],
      allowedActions: ["undo"],
    });
    expect(authorizeTurnTool(intent, "diet_transaction", {
      action: "undo",
      operation_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
  });

  it("still blocks a real follow-up pantry write after undo", () => {
    const intent = classifyTurnIntent(
      "撤销刚才那笔，然后再入库两盒酸奶。",
    );

    expect(intent).toMatchObject({
      mode: "multi_domain_write",
      domains: ["pantry", "transaction"],
    });
  });

  it("keeps an explicit targeted pantry discard available", () => {
    const intent = classifyTurnIntent("把这盒燕麦奶扔掉。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: ["pantry"],
    });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "discard",
      item_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "freeze",
      item_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it.each([
    "确认丢弃青柠原味酸奶2盒。",
    "把青柠原味酸奶2盒丢弃。",
  ])("recognizes 丢弃 as one explicit pantry discard: %s", (text) => {
    const intent = classifyTurnIntent(text);

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: ["pantry"],
      allowedActions: ["discard"],
    });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "discard",
      inventory_match_handle: "wfh_abcdefghijklmnopqrstuv",
      quantity: 2,
      unit: "盒",
    })).toEqual({ allowed: true });
  });

  it("keeps completed pantry consumption on the meal transaction route", () => {
    const intent = classifyTurnIntent("刚喝了一盒库存里的酸奶。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
      completedConsumption: true,
    });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "search",
      search_text: "库存里的酸奶",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
      items: [{ inventory_match_handle: "wfh_abcdefghijklmnopqrstuv" }],
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "deduct",
      inventory_match_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({
      allowed: false,
      code: "WRITE_NOT_AUTHORIZED",
    });
  });

  it.each([
    "删除刚才那条餐食。",
    "是，把整条玉米记录删掉。",
    "把刚才那个玉米删了。",
    "把这条燕麦奶记录移除。",
  ])("marks a same-context whole meal deletion as a handle-bound target: %s", (text) => {
    const intent = classifyTurnIntent(text);

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [],
      writeScope: "targeted",
      allowedActions: expect.arrayContaining(["delete"]),
      contextualTarget: true,
    });
    expect(intent).toHaveProperty("requiresTrustedSessionTarget", true);
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_recent_meal_abcdefghijklmnop",
    })).toEqual({ allowed: true });
  });

  it.each([
    "刚才那个玉米删了吗？",
    "如果把刚才那个玉米删了会怎样？",
  ])("never authorizes a question containing the colloquial delete form: %s", (text) => {
    const intent = classifyTurnIntent(text);

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_recent_meal_abcdefghijklmnop",
    })).toMatchObject({
      allowed: false,
      code: "READ_ONLY_TURN",
    });
  });

  it("authorizes a handle-bound natural correction phrased with 其实是", () => {
    const intent = classifyTurnIntent("其实是80克。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [],
      writeScope: "targeted",
      contextualTarget: true,
    });
    expect(intent).toHaveProperty("requiresTrustedSessionTarget", true);
    expect(intent.allowedActions).toContain("update");
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "update",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
      draft: {},
    })).toEqual({ allowed: true });
  });

  it("keeps cooking, eating, and storing leftovers in the meal atomic flow", () => {
    const intent = classifyTurnIntent(
      "中午炒了番茄鸡蛋，吃了一半，剩下的放冰箱。",
    );

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: ["meal"],
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record_cooking",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "add",
    })).toMatchObject({ allowed: false });
  });

  it.each(["没问题。", "好，就按这个。", "记吧。", "1"])(
    "accepts a common pure confirmation only with a live handle: %s",
    (text) => {
      const intent = classifyTurnIntent(text);

      expect(intent.mode).toBe("workflow_confirmation");
      expect(authorizeTurnTool(intent, "diet_meal", {
        action: "commit_record",
        commit_handle: "wfh_abcdefghijklmnopqrstuv",
      })).toEqual({ allowed: true });
      expect(authorizeTurnTool(intent, "diet_meal", {
        action: "record",
      })).toMatchObject({ allowed: false });
    },
  );

  it("leaves an unnamed 250ml drink for agent clarification rather than plugin routing", () => {
    const intent = classifyTurnIntent("早上喝了250ml。");

    expect(intent).toEqual({ mode: "agent_directed", domains: [] });
    expect(authorizeTurnTool(intent, "diet_water", { action: "record" }))
      .toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", { action: "record" }))
      .toEqual({ allowed: true });
  });

  it("requires a resolved point in time for a named coarse historical water fact", () => {
    const intent = classifyTurnIntent("早上喝了250ml水，帮我记一下。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
      requiresExplicitOccurredAt: true,
    });
    expect(authorizeTurnTool(intent, "diet_water", {
      action: "record",
      amount: 250,
      unit: "ml",
    })).toMatchObject({
      allowed: false,
      code: "OCCURRED_AT_REQUIRED",
    });
    expect(authorizeTurnTool(intent, "diet_water", {
      action: "record",
      amount: 250,
      unit: "ml",
      occurred_at: "2026-08-05T07:30:00+08:00",
    })).toEqual({ allowed: true });
  });

  it("keeps future plans read-only even when phrased as a record command", () => {
    const intent = classifyTurnIntent("帮我记一下明天想吃的过期水煮蛋。");

    expect(intent.mode).toBe("read_only");
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
    })).toMatchObject({ allowed: false, code: "READ_ONLY_TURN" });
  });

  it("marks only a completed meal fact as trusted retrospective consumption", () => {
    expect(classifyTurnIntent("我已经吃了一个过期水煮蛋，帮我记一下。"))
      .toMatchObject({ completedConsumption: true });
    expect(classifyTurnIntent("我明天想吃一个过期水煮蛋。"))
      .not.toHaveProperty("completedConsumption", true);
  });

  it("lets the agent choose a zero-business-write preview for a vague continuation", () => {
    const intent = classifyTurnIntent("就十来粒吧。");

    expect(intent.mode).toBe("agent_directed");
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "preview_record",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({ allowed: false });
  });

  it("allows an explicit correction only when an opaque target is supplied", () => {
    const intent = classifyTurnIntent("其实就五粒，帮我改一下吧。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [],
      writeScope: "targeted",
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "update",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
    })).toMatchObject({ allowed: false });
  });

  it("accepts a natural correction only as a handle-bound update", () => {
    const intent = classifyTurnIntent("不对，是五粒。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [],
      writeScope: "targeted",
      contextualTarget: true,
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "update",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "update",
    })).toMatchObject({ allowed: false });
  });

  it("treats an explicit conditional补记 as a bounded contextual create", () => {
    const intent = classifyTurnIntent("如果刚才没记上，就帮我补记。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "record",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_abcdefghijklmnopqrstuv",
    })).toMatchObject({ allowed: false });
  });

  it.each([
    "明早打算吃香蕉。",
    "差点喝了水，最后没喝。",
    "算了，不记了。",
    "取消，别记这条。",
    "他刚喝了500ml水。",
  ])("keeps non-user or non-final facts at zero write: %s", (text) => {
    const intent = classifyTurnIntent(text);

    expect(authorizeTurnTool(intent, "diet_water", { action: "record" }))
      .toMatchObject({ allowed: false });
    expect(authorizeTurnTool(intent, "diet_meal", { action: "record" }))
      .toMatchObject({ allowed: false });
  });

  it("does not mark an explicitly older meal correction as contextual", () => {
    const intent = classifyTurnIntent("把昨天午餐改成80克。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      writeScope: "targeted",
    });
    expect(intent).not.toHaveProperty("contextualTarget", true);
  });

  it("keeps a clock-qualified historical meal deletion independent of session context", () => {
    const intent = classifyTurnIntent("删除昨晚22:00那条UAT饭团。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      writeScope: "targeted",
      allowedActions: expect.arrayContaining(["delete"]),
    });
    expect(intent).not.toHaveProperty("contextualTarget", true);
    expect(intent).not.toHaveProperty("requiresTrustedSessionTarget", true);
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "delete",
      meal_handle: "wfh_historical_meal_abcdefghijkl",
    })).toEqual({ allowed: true });
  });

  it("does not label a contextual water correction as a trusted meal target", () => {
    const intent = classifyTurnIntent("把刚才那杯水改成300ml。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      writeScope: "targeted",
      allowedActions: expect.arrayContaining(["update"]),
    });
    expect(intent).not.toHaveProperty("requiresTrustedSessionTarget", true);
  });

  it("treats a colloquial completed weigh-in as an immediate weight write", () => {
    const intent = classifyTurnIntent("刚称了106.66公斤。");

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_weight", {
      action: "record",
      weight: 106.66,
      unit: "kg",
    })).toEqual({ allowed: true });
  });

  it("allows a completed water write followed by a same-domain total query", () => {
    const intent = classifyTurnIntent(
      "刚喝了149毫升水，帮我记一下并告诉我今天一共喝了多少。",
    );

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
    expect(authorizeTurnTool(intent, "diet_water", {
      action: "record",
      amount: 149,
      unit: "ml",
      source_text: "刚喝了149毫升水",
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_water", {
      action: "query",
      natural_window: { text: "今天" },
    })).toEqual({ allowed: true });
  });

  it("does not mistake dietary carbohydrate text for a water write domain", () => {
    const intent = classifyTurnIntent(
      "补记昨天22:00吃了1个UAT22测试饭团，200千卡，蛋白质5克，脂肪4克，碳水35克，纤维2克。",
    );

    expect(intent).toMatchObject({
      mode: "agent_directed",
      domains: [],
    });
  });

  it("accepts a bounded noun phrase inside a same-session correction", () => {
    const intent = classifyTurnIntent("其实可食部是80克。");

    expect(intent).toMatchObject({
      mode: "single_domain_write",
      domains: [],
      writeScope: "targeted",
      contextualTarget: true,
      requiresTrustedSessionTarget: true,
    });
    expect(intent.allowedActions).toContain("update");
  });

  it("routes a recent operation status check through transaction history only", () => {
    const intent = classifyTurnIntent("刚才记上了吗？");

    expect(intent).toMatchObject({
      mode: "read_only",
      domains: [],
      operationStatusQuery: true,
    });
    expect(authorizeTurnTool(intent, "diet_transaction", {
      action: "get_recent",
      limit: 3,
    })).toEqual({ allowed: true });
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "query",
      rolling_window: { value: 6, unit: "hour" },
    })).toMatchObject({
      allowed: false,
      code: "STATUS_QUERY_ROUTE_REQUIRED",
    });
    expect(authorizeTurnTool(intent, "diet_pantry", {
      action: "query",
    })).toMatchObject({
      allowed: false,
      code: "STATUS_QUERY_ROUTE_REQUIRED",
    });
  });

  it("keeps an ordinary historical existence query on its business read route", () => {
    const intent = classifyTurnIntent("有没有记录今天的早餐？");

    expect(intent).toMatchObject({
      mode: "read_only",
      domains: [],
    });
    expect(intent.operationStatusQuery).toBeUndefined();
    expect(authorizeTurnTool(intent, "diet_meal", {
      action: "query",
      natural_window: { text: "今天" },
    })).toEqual({ allowed: true });
  });
});
