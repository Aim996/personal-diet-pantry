import { describe, expect, it } from "vitest";

import plugin from "../src/index.js";

type Hook = (event: Record<string, unknown>, context: Record<string, unknown>) => unknown;

function fakePluginApi(options: { persistRunContext?: boolean } = {}) {
  const persistRunContext = options.persistRunContext ?? true;
  const tools: Array<unknown> = [];
  const hooks = new Map<string, Hook>();
  const contexts = new Map<string, unknown>();
  const key = (runId: string, namespace: string) => `${runId}:${namespace}`;
  return {
    tools,
    hooks,
    api: {
      pluginConfig: {},
      registerTool(tool: unknown) {
        tools.push(tool);
      },
      on(name: string, handler: Hook) {
        hooks.set(name, handler);
      },
      runContext: {
        setRunContext(patch: { runId: string; namespace: string; value?: unknown; unset?: boolean }) {
          if (!persistRunContext) {
            return false;
          }
          if (patch.unset) {
            contexts.delete(key(patch.runId, patch.namespace));
          } else {
            contexts.set(key(patch.runId, patch.namespace), patch.value);
          }
          return true;
        },
        getRunContext(params: { runId: string; namespace: string }) {
          return contexts.get(key(params.runId, params.namespace));
        },
        clearRunContext(params: { runId: string; namespace?: string }) {
          if (params.namespace !== undefined) {
            contexts.delete(key(params.runId, params.namespace));
          }
        },
      },
    },
  };
}

describe("full plugin turn guard wiring", () => {
  it("registers the seven tools and the five safety hooks", () => {
    const host = fakePluginApi();

    plugin.register(host.api as never);

    expect(host.tools).toHaveLength(7);
    expect([...host.hooks.keys()].sort()).toEqual([
      "after_tool_call",
      "before_message_write",
      "before_prompt_build",
      "before_tool_call",
      "reply_payload_sending",
    ]);
  });

  it.each([
    "确认入库。",
    "确认，2盒UAT23验收燕麦奶按预览入库。",
    "确认，按上面补记。",
  ])("routes a natural confirmation to the unchanged live commit only: %s", async (prompt) => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;

    const result = await beforeRun(
      { prompt, messages: [] },
      { runId: `run-confirm-${prompt}`, sessionKey: "session:confirmation" },
    );

    expect(result).toMatchObject({
      appendContext: expect.stringMatching(/live preview.*commit_record.*commit_add/i),
    });
    expect(result).toMatchObject({
      appendContext: expect.stringMatching(/do not.*(?:record|add).*new write/i),
    });
  });

  it.each([
    "大概10粒，就按10克记吧。",
    "标签每100克：70千卡、蛋白3克、脂肪2克、碳水10克；就按整盒180克记，直接记录。",
  ])("routes an explicitly finalized supplemental fact to one direct meal write: %s", async (prompt) => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;

    const result = await beforeRun(
      { prompt, messages: [] },
      { runId: `run-final-supplement-${prompt}`, sessionKey: "session:final-supplement" },
    );

    expect(result).toMatchObject({
      appendContext: expect.stringMatching(/diet_meal record.*exactly once/i),
    });
    expect(result).toMatchObject({
      appendContext: expect.stringMatching(/do not.*preview.*confirmation/i),
    });
    expect(result).toMatchObject({
      appendContext: expect.stringMatching(/inventory_match_handle.*nutrition facts/i),
    });
  });

  it.each([
    ["刚啃了根玉米", /diet_meal record exactly once.*do not.*preview.*confirmation/is],
    ["刚称了下106.8", /default.*kg.*diet_weight record exactly once/is],
    ["刚买了俩苹果，放冰箱了", /diet_pantry add exactly once.*do not ask.*production.*expiry/is],
    ["刚喝了137毫升水", /diet_water record exactly once.*water-only receipt/is],
  ])("injects one deterministic action for an ordinary fact: %s", async (
    prompt,
    expected,
  ) => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;

    const result = await beforeRun(
      { prompt, messages: [] },
      { runId: `run-direct-${prompt}`, sessionKey: `session-direct-${prompt}` },
    );

    expect(result).toMatchObject({ appendContext: expected });
  });

  it("blocks preview when the current sentence authorizes a direct meal record", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const context = {
      runId: "run-direct-corn",
      sessionKey: "session-direct-corn",
    };

    await beforeRun({ prompt: "刚啃了根玉米", messages: [] }, context);

    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "preview_record", items: [] },
        runId: context.runId,
      },
      { ...context, toolName: "diet_meal" },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/没有授权|not authorized/i),
    });
  });

  it("routes an exact fact after a live meal preview to one replacement preview", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const sessionKey = "session:replacement-preview";

    await beforeRun(
      { prompt: "昨天15点吃了点米饭，帮我补记。", messages: [] },
      { runId: "run-preview-origin", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "preview_record" },
        result: {
          details: {
            ok: true,
            outcome: "preview_ready",
            data: {
              preview: {
                workflow: { commit_handle: "wfh_preview_origin_abcdefghijkl" },
              },
            },
          },
        },
        runId: "run-preview-origin",
      },
      { runId: "run-preview-origin", sessionKey },
    );

    expect(await beforeRun(
      { prompt: "实际是100克。", messages: [] },
      { runId: "run-preview-replacement", sessionKey },
    )).toMatchObject({
      appendContext: expect.stringMatching(/preview_record exactly once.*replacement preview/i),
    });

    const replacement = await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "preview_record",
          source_text: "昨天15点吃了点米饭，帮我补记。",
          items: [{
            raw_name: "米饭",
            normalized_name: "米饭",
            consumed_weight_g: 100,
            portion_expression: "一点｜约100克（估算）",
            quantity_estimate: {
              suggested: 100,
              lower: 50,
              upper: 150,
              unit: "g",
            },
          }],
        },
        runId: "run-preview-replacement",
      },
      { runId: "run-preview-replacement", sessionKey },
    ) as { params: { items: Array<Record<string, unknown>> } };
    expect(replacement).toMatchObject({
      params: {
        items: [{ portion_expression: "100克" }],
      },
    });
    expect(replacement.params.items[0]).not.toHaveProperty("quantity_estimate");
  });

  it("does not revive a meal preview after the user cancels it", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const sessionKey = "session:cancelled-preview";

    await beforeRun(
      { prompt: "昨天15点吃了点米饭，帮我补记。", messages: [] },
      { runId: "run-cancelled-preview-origin", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "preview_record" },
        result: {
          details: {
            ok: true,
            outcome: "preview_ready",
            data: {},
          },
        },
        runId: "run-cancelled-preview-origin",
      },
      { runId: "run-cancelled-preview-origin", sessionKey },
    );

    await beforeRun(
      { prompt: "算了，不记了。", messages: [] },
      { runId: "run-cancelled-preview-stop", sessionKey },
    );
    const laterCorrection = await beforeRun(
      { prompt: "实际是100克。", messages: [] },
      { runId: "run-cancelled-preview-later", sessionKey },
    );

    expect(laterCorrection).toMatchObject({
      appendContext: expect.stringMatching(/no verified same-session meal target/i),
    });
    expect(laterCorrection).not.toMatchObject({
      appendContext: expect.stringMatching(/replacement preview/i),
    });
  });

  it("uses the same-session pending receipt when final delivery omits the run id", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;
    const sessionKey = "session:receipt-fallback";
    const receipt = "已记录！花生 10粒｜10克｜57 kcal\n\n🔥 热量 ░░░░░░░░░░ 3%";

    await beforeRun(
      { prompt: "大概10粒，就按10克记吧。", messages: [] },
      { runId: "run-receipt-fallback", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { rendered_receipt: receipt },
          },
        },
        runId: "run-receipt-fallback",
      },
      { runId: "run-receipt-fallback", sessionKey },
    );

    expect(await beforeSend(
      {
        kind: "final",
        sessionKey,
        payload: { text: "已记上，今日进度见上。" },
      },
      { sessionKey, channelId: "webchat" },
    )).toEqual({ payload: { text: receipt } });
  });

  it("does not reuse a pending receipt after a new read-only run starts", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;
    const sessionKey = "session:receipt-next-run";

    await beforeRun(
      { prompt: "吃了个玉米。", messages: [] },
      { runId: "run-write-before-read", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { rendered_receipt: "正式餐食回执" },
          },
        },
        runId: "run-write-before-read",
      },
      { runId: "run-write-before-read", sessionKey },
    );
    await beforeRun(
      { prompt: "今天吃了什么？", messages: [] },
      { runId: "run-read-after-write", sessionKey },
    );

    expect(await beforeSend(
      {
        kind: "final",
        runId: "run-read-after-write",
        sessionKey,
        payload: { text: "今天还没有记录。" },
      },
      { runId: "run-read-after-write", sessionKey },
    )).toBeUndefined();
  });

  it.each([
    "8月3号晚上到4号天亮前吃了什么？",
    "昨儿夜里到天亮前，我有吃过啥吗？",
  ])("renders every record in a complete meal-history window without deduplication: %s", async (prompt) => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;
    const runId = `run-complete-history-${prompt}`;
    const sessionKey = `session:complete-history:${prompt}`;

    await beforeRun({ prompt, messages: [] }, { runId, sessionKey });
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "query", natural_window: { text: prompt } },
        result: {
          details: {
            ok: true,
            outcome: "read_completed",
            data: {
              scope: {
                start_local: "2026-08-03T18:00:00+08:00",
                end_local: "2026-08-04T06:00:00+08:00",
                timezone: "Asia/Shanghai",
                complete: true,
              },
              meals: [
                {
                  occurred_at_local: "2026-08-03T20:20:00+08:00",
                  total_calories: "850",
                  source_text: "同一锅分出的第一顿",
                  items: [{
                    raw_name: "同一锅饭",
                    portion_expression: "1份",
                  }],
                  workflow: { meal_handle: "wfh_first_duplicate_1234567890" },
                },
                {
                  occurred_at_local: "2026-08-03T20:20:00+08:00",
                  total_calories: "850",
                  source_text: "同一锅分出的第二顿",
                  items: [{
                    raw_name: "同一锅饭",
                    portion_expression: "1份",
                  }],
                  workflow: { meal_handle: "wfh_second_duplicate_123456789" },
                },
              ],
            },
          },
        },
        runId,
      },
      { runId, sessionKey },
    );

    expect(await beforeTool(
      {
        toolName: "diet_report",
        params: { action: "progress" },
        runId,
      },
      { runId, sessionKey },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/already returned.*do not call another diet tool/i),
    });

    const result = await beforeSend(
      {
        kind: "final",
        runId,
        sessionKey,
        payload: { text: "这一时段共1笔：同一锅饭。" },
      },
      { runId, sessionKey },
    );
    const text = (result as { payload: { text: string } }).payload.text;
    expect(text).toBe(
      "2026-08-03 18:00 至 2026-08-04 06:00（Asia/Shanghai）共记录 2 笔：\n\n" +
        "1. 2026-08-03 20:20｜同一锅饭 1份｜850 kcal\n" +
        "2. 2026-08-03 20:20｜同一锅饭 1份｜850 kcal",
    );
    expect(text.match(/同一锅饭/g)).toHaveLength(2);
    expect(text).not.toContain("wfh_");
  });

  it("writes the same deterministic complete meal-history list into the WebUI transcript", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeWrite = host.hooks.get("before_message_write")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;
    const runId = "run-webui-history";
    const sessionKey = "session:webui-history";
    const expected =
      "2026-08-03 18:00 至 2026-08-04 06:00（Asia/Shanghai）共记录 2 笔：\n\n" +
      "1. 2026-08-03 20:20｜同一锅饭 1份｜850 kcal\n" +
      "2. 2026-08-03 20:20｜同一锅饭 1份｜850 kcal";

    await beforeRun(
      { prompt: "8月3号傍晚六点到4号早上六点，我吃了啥？", messages: [] },
      { runId, sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "query", natural_window: { text: "8月3号傍晚六点到4号早上六点" } },
        result: {
          details: {
            ok: true,
            outcome: "read_completed",
            data: {
              scope: {
                start_local: "2026-08-03T18:00:00+08:00",
                end_local: "2026-08-04T06:00:00+08:00",
                timezone: "Asia/Shanghai",
                complete: true,
              },
              meals: [
                {
                  occurred_at_local: "2026-08-03T20:20:00+08:00",
                  total_calories: "850",
                  items: [{ raw_name: "同一锅饭", portion_expression: "1份" }],
                },
                {
                  occurred_at_local: "2026-08-03T20:20:00+08:00",
                  total_calories: "850",
                  items: [{ raw_name: "同一锅饭", portion_expression: "1份" }],
                },
              ],
            },
          },
        },
        runId,
      },
      { runId, sessionKey },
    );

    const originalMessage = {
      role: "assistant",
      content: [{ type: "text", text: "可能只有一笔，而且两顿像是重复。" }],
      stopReason: "stop",
    };
    expect(beforeWrite(
      { sessionKey, message: originalMessage },
      { sessionKey, agentId: "main" },
    )).toEqual({
      message: {
        ...originalMessage,
        content: [{ type: "text", text: expected }],
      },
    });

    const outbound = await beforeSend(
      {
        kind: "final",
        runId,
        sessionKey,
        payload: { text: "自由文本仍说一笔。" },
      },
      { runId, sessionKey },
    );
    expect(outbound).toMatchObject({ payload: { text: expected } });
  });

  it.each([
    [{ role: "user", content: [{ type: "text", text: "用户消息" }] }],
    [{ role: "toolResult", content: [{ type: "text", text: "工具结果" }] }],
    [{
      role: "assistant",
      content: [
        { type: "text", text: "正在查询" },
        { type: "toolCall", id: "call-1", name: "diet_meal", arguments: {} },
      ],
      stopReason: "toolUse",
    }],
  ])("never rewrites a non-final transcript message: %j", async (message) => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeWrite = host.hooks.get("before_message_write")!;
    const runId = `run-webui-negative-${String(message.role)}`;
    const sessionKey = `session:webui-negative:${String(message.role)}`;

    await beforeRun(
      { prompt: "昨晚到今天凌晨吃了什么？", messages: [] },
      { runId, sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "query", natural_window: { text: "昨晚到今天凌晨" } },
        result: {
          details: {
            ok: true,
            outcome: "read_completed",
            data: {
              scope: {
                start_local: "2026-08-06T18:00:00+08:00",
                end_local: "2026-08-07T06:00:00+08:00",
                timezone: "Asia/Shanghai",
                complete: true,
              },
              meals: [],
            },
          },
        },
        runId,
      },
      { runId, sessionKey },
    );

    expect(beforeWrite(
      { sessionKey, message },
      { sessionKey, agentId: "main" },
    )).toBeUndefined();
  });

  it("does not take over incomplete windows or non-history meal reads", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;

    await beforeRun(
      { prompt: "今天吃了什么？", messages: [] },
      { runId: "run-incomplete-history", sessionKey: "session:incomplete-history" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "query" },
        result: {
          details: {
            ok: true,
            outcome: "read_completed",
            data: {
              scope: {
                start_local: "2026-08-07T00:00:00+08:00",
                end_local: "2026-08-07T09:00:00+08:00",
                timezone: "Asia/Shanghai",
                complete: false,
              },
              meals: [],
            },
          },
        },
        runId: "run-incomplete-history",
      },
      { runId: "run-incomplete-history", sessionKey: "session:incomplete-history" },
    );
    expect(await beforeSend(
      {
        kind: "final",
        runId: "run-incomplete-history",
        sessionKey: "session:incomplete-history",
        payload: { text: "今天目前没有餐食。" },
      },
      { runId: "run-incomplete-history", sessionKey: "session:incomplete-history" },
    )).toBeUndefined();

    await beforeRun(
      { prompt: "昨晚那顿蛋白质有多少？", messages: [] },
      { runId: "run-nutrition-analysis", sessionKey: "session:nutrition-analysis" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "query" },
        result: {
          details: {
            ok: true,
            outcome: "read_completed",
            data: {
              scope: {
                start_local: "2026-08-06T18:00:00+08:00",
                end_local: "2026-08-07T06:00:00+08:00",
                timezone: "Asia/Shanghai",
                complete: true,
              },
              meals: [],
            },
          },
        },
        runId: "run-nutrition-analysis",
      },
      { runId: "run-nutrition-analysis", sessionKey: "session:nutrition-analysis" },
    );
    expect(await beforeSend(
      {
        kind: "final",
        runId: "run-nutrition-analysis",
        sessionKey: "session:nutrition-analysis",
        payload: { text: "蛋白质是20克。" },
      },
      { runId: "run-nutrition-analysis", sessionKey: "session:nutrition-analysis" },
    )).toBeUndefined();
  });

  it("replaces a paraphrased pure-write final reply with the same-run receipt", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;
    const receipt = "已更新！玉米 1个｜可食部（玉米粒）80克｜89.6 kcal\n\n🔥 热量 ██░░░░░░░░ 18%";

    await beforeRun(
      { prompt: "其实是80克。" },
      { runId: "run-receipt", channelId: "webchat" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "update" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { rendered_receipt: receipt },
          },
        },
        runId: "run-receipt",
      },
      { toolName: "diet_meal", runId: "run-receipt" },
    );

    expect(await beforeSend(
      {
        kind: "final",
        runId: "run-receipt",
        payload: { text: "已改好：玉米按80克记。" },
      },
      { runId: "run-receipt", channelId: "webchat" },
    )).toEqual({ payload: { text: receipt } });
  });

  it("reuses the exact same-session meal handle for an immediate correction", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const sessionKey = "agent:main:dashboard:correction";
    const realHandle = "wfh_real_same_session_meal_123456789";

    await beforeRun(
      { prompt: "吃了个玉米。", messages: [] },
      { runId: "run-record", sessionKey, sessionId: "session-a" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: {
              meal: { workflow: { meal_handle: realHandle } },
              rendered_receipt: "正式新增回执",
            },
          },
        },
        runId: "run-record",
      },
      { toolName: "diet_meal", runId: "run-record", sessionKey, sessionId: "session-a" },
    );

    const correctionPromptContext = await beforeRun(
      { prompt: "其实是80克。", messages: [] },
      { runId: "run-correct", sessionKey, sessionId: "session-a" },
    );
    expect(correctionPromptContext).toMatchObject({
      appendContext: expect.stringMatching(/directly call diet_meal update/i),
    });
    expect(correctionPromptContext).toMatchObject({
      appendContext: expect.stringMatching(/do not query/i),
    });
    const corrected = await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "update",
          selector: {
            occurred_at: "2026-08-06T06:09:00",
            source_text: "吃了个玉米",
          },
          source_text: "其实是80克",
          items: [{ raw_name: "玉米", normalized_name: "玉米" }],
        },
        runId: "run-correct",
      },
      { toolName: "diet_meal", runId: "run-correct", sessionKey, sessionId: "session-a" },
    );

    expect(corrected).toEqual({
      params: {
        action: "update",
        meal_handle: realHandle,
        source_text: "其实是80克",
        items: [{ raw_name: "玉米", normalized_name: "玉米" }],
      },
    });

    await beforeRun(
      { prompt: "不对，是75克。", messages: [] },
      { runId: "run-correct-guessed", sessionKey, sessionId: "session-a" },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "update",
          meal_handle: "wfh_model_guessed_nonexistent_123456",
          source_text: "不对，是75克",
          items: [{ raw_name: "玉米", normalized_name: "玉米" }],
        },
        runId: "run-correct-guessed",
      },
      {
        toolName: "diet_meal",
        runId: "run-correct-guessed",
        sessionKey,
        sessionId: "session-a",
      },
    )).toEqual({
      params: {
        action: "update",
        meal_handle: realHandle,
        source_text: "不对，是75克",
        items: [{ raw_name: "玉米", normalized_name: "玉米" }],
      },
    });
  });

  it("binds an explicit same-session whole-meal deletion but never crosses sessions", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const realHandle = "wfh_real_isolated_meal_1234567890";

    await beforeRun(
      { prompt: "吃了个玉米。", messages: [] },
      { runId: "run-origin", sessionKey: "session:origin" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { meal: { workflow: { meal_handle: realHandle } } },
          },
        },
        runId: "run-origin",
      },
      { toolName: "diet_meal", runId: "run-origin", sessionKey: "session:origin" },
    );

    await beforeRun(
      { prompt: "其实是80克。", messages: [] },
      { runId: "run-other-session", sessionKey: "session:other" },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "update", source_text: "其实是80克", items: [] },
        runId: "run-other-session",
      },
      { toolName: "diet_meal", runId: "run-other-session", sessionKey: "session:other" },
    )).toMatchObject({ block: true });

    const deleteContext = await beforeRun(
      { prompt: "是，把整条玉米记录删掉。", messages: [] },
      { runId: "run-delete", sessionKey: "session:origin" },
    );
    expect(deleteContext).toMatchObject({
      appendContext: expect.stringMatching(/diet_meal delete/i),
    });
    expect(deleteContext).toMatchObject({
      appendContext: expect.stringMatching(/do not.*transaction undo/i),
    });
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "delete", source_text: "是，把整条玉米记录删掉" },
        runId: "run-delete",
      },
      { toolName: "diet_meal", runId: "run-delete", sessionKey: "session:origin" },
    )).toEqual({
      params: {
        action: "delete",
        meal_handle: realHandle,
        source_text: "是，把整条玉米记录删掉",
      },
    });

    await beforeRun(
      { prompt: "删除刚才那条。", messages: [] },
      { runId: "run-delete-other", sessionKey: "session:other" },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "delete", source_text: "删除刚才那条" },
        runId: "run-delete-other",
      },
      {
        toolName: "diet_meal",
        runId: "run-delete-other",
        sessionKey: "session:other",
      },
    )).toMatchObject({ block: true });
  });

  it("keeps colloquial same-session delete authority after an unnecessary read", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const realHandle = "wfh_real_colloquial_meal_1234567890";
    const sessionKey = "session:colloquial-delete";

    await beforeRun(
      { prompt: "吃了个玉米。", messages: [] },
      { runId: "run-colloquial-origin", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { meal: { workflow: { meal_handle: realHandle } } },
          },
        },
        runId: "run-colloquial-origin",
      },
      {
        toolName: "diet_meal",
        runId: "run-colloquial-origin",
        sessionKey,
      },
    );

    await beforeRun(
      { prompt: "把刚才那个玉米删了。", messages: [] },
      { runId: "run-colloquial-delete", sessionKey },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "query", date: "today" },
        runId: "run-colloquial-delete",
      },
      {
        toolName: "diet_meal",
        runId: "run-colloquial-delete",
        sessionKey,
      },
    )).toBeUndefined();
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "delete", source_text: "把刚才那个玉米删了" },
        runId: "run-colloquial-delete",
      },
      {
        toolName: "diet_meal",
        runId: "run-colloquial-delete",
        sessionKey,
      },
    )).toEqual({
      params: {
        action: "delete",
        meal_handle: realHandle,
        source_text: "把刚才那个玉米删了",
      },
    });
  });

  it("never upgrades a fresh-session query result into authority for a contextual delete", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const queriedHandle = "wfh_query_candidate_meal_1234567890";
    const sessionKey = "session:fresh-delete";

    await beforeRun(
      { prompt: "把刚才那个玉米删了。", messages: [] },
      { runId: "run-fresh-delete", sessionKey },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "query", rolling_window: { value: 1, unit: "day" } },
        runId: "run-fresh-delete",
      },
      { toolName: "diet_meal", runId: "run-fresh-delete", sessionKey },
    )).toBeUndefined();
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "delete",
          meal_handle: queriedHandle,
          source_text: "把刚才那个玉米删了",
        },
        runId: "run-fresh-delete",
      },
      { toolName: "diet_meal", runId: "run-fresh-delete", sessionKey },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/同会话|候选/),
    });
  });

  it("never upgrades a fresh-session query handle into an immediate correction", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const sessionKey = "session:fresh-correction";

    await beforeRun(
      { prompt: "其实是80克。", messages: [] },
      { runId: "run-fresh-correction", sessionKey },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "query", date: "today" },
        runId: "run-fresh-correction",
      },
      { toolName: "diet_meal", runId: "run-fresh-correction", sessionKey },
    )).toBeUndefined();
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "update",
          meal_handle: "wfh_query_candidate_update_123456789",
          source_text: "其实是80克",
          items: [],
        },
        runId: "run-fresh-correction",
      },
      { toolName: "diet_meal", runId: "run-fresh-correction", sessionKey },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/同会话|候选/),
    });
  });

  it("preserves a clock-qualified historical delete after a read returns an opaque handle", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const historicalHandle = "wfh_historical_meal_1234567890";
    const sessionKey = "session:historical-delete";

    await beforeRun(
      { prompt: "删除昨晚22:00那条UAT饭团。", messages: [] },
      { runId: "run-historical-delete", sessionKey },
    );
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "query",
          start_at: "2026-08-06T22:00:00+08:00",
          end_at: "2026-08-06T22:01:00+08:00",
        },
        runId: "run-historical-delete",
      },
      { toolName: "diet_meal", runId: "run-historical-delete", sessionKey },
    )).toBeUndefined();
    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: {
          action: "delete",
          meal_handle: historicalHandle,
          source_text: "删除昨晚22:00那条UAT饭团",
        },
        runId: "run-historical-delete",
      },
      { toolName: "diet_meal", runId: "run-historical-delete", sessionKey },
    )).toBeUndefined();
  });

  it("never applies a cached meal target to a water correction", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const sessionKey = "session:cross-domain-target";

    await beforeRun(
      { prompt: "吃了个玉米。", messages: [] },
      { runId: "run-cross-domain-origin", sessionKey },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: {
              meal: {
                workflow: {
                  meal_handle: "wfh_cross_domain_meal_1234567890",
                },
              },
            },
          },
        },
        runId: "run-cross-domain-origin",
      },
      { toolName: "diet_meal", runId: "run-cross-domain-origin", sessionKey },
    );

    expect(await beforeRun(
      { prompt: "把刚才那杯水改成300ml。", messages: [] },
      { runId: "run-water-correction", sessionKey },
    )).toBeUndefined();
  });

  it("never reuses a receipt across runs or over a mixed read/write question", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const beforeSend = host.hooks.get("reply_payload_sending")!;

    await beforeRun(
      { prompt: "吃了个玉米，家里牛奶还剩多少？" },
      { runId: "run-mixed", channelId: "webchat" },
    );
    await afterTool(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: {
          details: {
            ok: true,
            outcome: "write_committed",
            data: { rendered_receipt: "正式回执" },
          },
        },
        runId: "run-mixed",
      },
      { toolName: "diet_meal", runId: "run-mixed" },
    );

    expect(await beforeSend(
      {
        kind: "final",
        runId: "run-mixed",
        payload: { text: "回执加库存回答" },
      },
      { runId: "run-mixed", channelId: "webchat" },
    )).toBeUndefined();
    expect(await beforeSend(
      {
        kind: "final",
        runId: "another-run",
        payload: { text: "普通回答" },
      },
      { runId: "another-run", channelId: "webchat" },
    )).toBeUndefined();
  });

  it("allows a clear WebUI write from the synchronized prompt-build hook", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "刚喝了300毫升水，帮我记一下。" },
      { runId: "run-webui-write", channelId: "webchat" },
    );

    expect(await beforeTool(
      {
        toolName: "diet_water",
        params: { action: "record" },
        runId: "run-webui-write",
      },
      { toolName: "diet_water", runId: "run-webui-write" },
    )).toBeUndefined();
  });

  it("allows the trusted current-turn write when the host refuses runContext persistence", async () => {
    const host = fakePluginApi({ persistRunContext: false });
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "刚喝了137毫升水，帮我记一下。" },
      { runId: "run-host-context-refused", channelId: "webchat" },
    );

    expect(await beforeTool(
      {
        toolName: "diet_water",
        params: { action: "record", amount: 137, unit: "ml" },
        runId: "run-host-context-refused",
      },
      { toolName: "diet_water", runId: "run-host-context-refused" },
    )).toBeUndefined();
  });

  it("never carries locally cached write authority into another run", async () => {
    const host = fakePluginApi({ persistRunContext: false });
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "刚喝了137毫升水，帮我记一下。" },
      { runId: "run-authorized", channelId: "webchat" },
    );

    expect(await beforeTool(
      {
        toolName: "diet_water",
        params: { action: "record", amount: 137, unit: "ml" },
        runId: "run-different",
      },
      { toolName: "diet_water", runId: "run-different" },
    )).toMatchObject({ block: true });
  });

  it("keeps the two-failure circuit breaker when host runContext is unavailable", async () => {
    const host = fakePluginApi({ persistRunContext: false });
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const beforeTool = host.hooks.get("before_tool_call")!;
    const afterTool = host.hooks.get("after_tool_call")!;
    const runId = "run-local-circuit-breaker";

    await beforeRun(
      { prompt: "吃了个玉米。" },
      { runId, channelId: "webchat" },
    );
    for (let index = 0; index < 2; index += 1) {
      await afterTool(
        {
          toolName: "diet_meal",
          params: { action: "preview_record" },
          result: { details: { ok: false } },
          runId,
        },
        { toolName: "diet_meal", runId },
      );
    }

    expect(await beforeTool(
      {
        toolName: "diet_meal",
        params: { action: "preview_record" },
        runId,
      },
      { toolName: "diet_meal", runId },
    )).toMatchObject({ block: true });
  });

  it("blocks a query-triggered write using trusted run context", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "刚才记上了吗？" },
      { runId: "run-query", channelId: "webchat" },
    );
    const blocked = await before(
      {
        toolName: "diet_pantry",
        params: { action: "add" },
        runId: "run-query",
      },
      { toolName: "diet_pantry", runId: "run-query" },
    );

    expect(blocked).toMatchObject({
      block: true,
      blockReason: expect.stringContaining("查询"),
    });
  });

  it("fails closed when a write has no trusted run identity", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const before = host.hooks.get("before_tool_call")!;

    const blocked = await before(
      { toolName: "diet_meal", params: { action: "record" } },
      { toolName: "diet_meal" },
    );

    expect(blocked).toMatchObject({ block: true });
  });

  it("locks a contextual write to the first business domain", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "如果刚才没记上，就帮我补记。" },
      { runId: "run-context", channelId: "webchat" },
    );
    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        runId: "run-context",
      },
      { toolName: "diet_meal", runId: "run-context" },
    )).toBeUndefined();
    expect(await before(
      {
        toolName: "diet_water",
        params: { action: "record" },
        runId: "run-context",
      },
      { toolName: "diet_water", runId: "run-context" },
    )).toMatchObject({ block: true });
  });

  it("enforces the two-failure circuit breaker across tool calls", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const after = host.hooks.get("after_tool_call")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "吃了个玉米。" },
      { runId: "run-failures", channelId: "webchat" },
    );
    for (let index = 0; index < 2; index += 1) {
      await after(
        {
          toolName: "diet_meal",
          params: { action: "record" },
          result: { details: { ok: false } },
          runId: "run-failures",
        },
        { toolName: "diet_meal", runId: "run-failures" },
      );
    }
    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "query" },
        runId: "run-failures",
      },
      { toolName: "diet_meal", runId: "run-failures" },
    )).toBeUndefined();

    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        runId: "run-failures",
      },
      { toolName: "diet_meal", runId: "run-failures" },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringContaining("失败两次"),
    });
  });

  it("does not let an inserted successful read reset a terminal failure", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const after = host.hooks.get("after_tool_call")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "吃了个玉米。" },
      { runId: "run-no-read-reset", channelId: "webchat" },
    );
    await after(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: { details: { ok: false } },
        runId: "run-no-read-reset",
      },
      { toolName: "diet_meal", runId: "run-no-read-reset" },
    );
    await after(
      {
        toolName: "diet_meal",
        params: { action: "query" },
        result: { details: { ok: true } },
        runId: "run-no-read-reset",
      },
      { toolName: "diet_meal", runId: "run-no-read-reset" },
    );
    await after(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        result: { details: { ok: false } },
        runId: "run-no-read-reset",
      },
      { toolName: "diet_meal", runId: "run-no-read-reset" },
    );

    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "record" },
        runId: "run-no-read-reset",
      },
      { toolName: "diet_meal", runId: "run-no-read-reset" },
    )).toMatchObject({ block: true });
  });

  it("injects expired-consumption authority only for a completed intake fact", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "我已经吃了一个过期水煮蛋，帮我记一下。" },
      { runId: "run-expired-completed", channelId: "webchat" },
    );
    const allowed = await before(
      {
        toolName: "diet_meal",
        params: { action: "record", items: [] },
        runId: "run-expired-completed",
      },
      { toolName: "diet_meal", runId: "run-expired-completed" },
    );
    expect(allowed).toMatchObject({
      params: { _turn_completed_consumption: true },
    });

    await beforeRun(
      { prompt: "帮我记一下明天想吃的过期水煮蛋。" },
      { runId: "run-expired-future", channelId: "webchat" },
    );
    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "record", items: [] },
        runId: "run-expired-future",
      },
      { toolName: "diet_meal", runId: "run-expired-future" },
    )).toMatchObject({ block: true });
  });

  it("makes one completed-inventory search self-contained and blocks a second pantry read", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;
    const after = host.hooks.get("after_tool_call")!;

    const route = await beforeRun(
      { prompt: "刚喝了一盒库存里的UAT18原味燕麦奶。" },
      { runId: "run-inventory-meal", sessionKey: "session:inventory-meal" },
    );
    expect(route).toMatchObject({
      appendContext: expect.stringMatching(
        /diet_pantry search exactly once.*diet_meal record exactly once.*do not.*confirmation/is,
      ),
    });
    expect(await before(
      {
        toolName: "diet_pantry",
        params: {
          action: "search",
          search_text: "UAT18原味燕麦奶",
          nutrition_mode: "none",
        },
        runId: "run-inventory-meal",
      },
      {
        toolName: "diet_pantry",
        runId: "run-inventory-meal",
        sessionKey: "session:inventory-meal",
      },
    )).toEqual({
      params: {
        action: "search",
        search_text: "UAT18原味燕麦奶",
        nutrition_mode: "summary",
      },
    });

    await after(
      {
        toolName: "diet_pantry",
        params: {
          action: "search",
          search_text: "UAT18原味燕麦奶",
          nutrition_mode: "summary",
        },
        result: { details: { ok: true, data: { candidates: [{}] } } },
        runId: "run-inventory-meal",
      },
      {
        toolName: "diet_pantry",
        runId: "run-inventory-meal",
        sessionKey: "session:inventory-meal",
      },
    );
    expect(await before(
      {
        toolName: "diet_pantry",
        params: {
          action: "query",
          normalized_name: "燕麦奶",
          include_details: true,
        },
        runId: "run-inventory-meal",
      },
      {
        toolName: "diet_pantry",
        runId: "run-inventory-meal",
        sessionKey: "session:inventory-meal",
      },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringContaining("库存凭证"),
    });

    expect(await before(
      {
        toolName: "diet_meal",
        params: {
          action: "record",
          items: [{
            raw_name: "UAT18原味燕麦奶",
            normalized_name: "燕麦奶",
            amount: 1,
            unit: "盒",
            inventory_match_handle: "wfh_inventory_oat_milk_1234567890",
          }],
        },
        runId: "run-inventory-meal",
      },
      {
        toolName: "diet_meal",
        runId: "run-inventory-meal",
        sessionKey: "session:inventory-meal",
      },
    )).toEqual({
      params: {
        action: "record",
        items: [{
          raw_name: "UAT18原味燕麦奶",
          normalized_name: "燕麦奶",
          amount: 1,
          unit: "盒",
          inventory_match_handle: "wfh_inventory_oat_milk_1234567890",
        }],
        _turn_completed_consumption: true,
      },
    });

    await beforeRun(
      { prompt: "家里还有多少燕麦奶？" },
      { runId: "run-pantry-read", sessionKey: "session:pantry-read" },
    );
    expect(await before(
      {
        toolName: "diet_pantry",
        params: {
          action: "search",
          search_text: "燕麦奶",
          nutrition_mode: "none",
        },
        runId: "run-pantry-read",
      },
      {
        toolName: "diet_pantry",
        runId: "run-pantry-read",
        sessionKey: "session:pantry-read",
      },
    )).toBeUndefined();
  });

  it("requires an explicit timestamp for a named coarse-time water fact", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "早上喝了250ml水，帮我记一下。" },
      { runId: "run-coarse-water", channelId: "webchat" },
    );
    expect(await before(
      {
        toolName: "diet_water",
        params: { action: "record", amount: 250, unit: "ml" },
        runId: "run-coarse-water",
      },
      { toolName: "diet_water", runId: "run-coarse-water" },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringContaining("时间"),
    });
    expect(await before(
      {
        toolName: "diet_water",
        params: {
          action: "record",
          amount: 250,
          unit: "ml",
          occurred_at: "2026-08-05T07:30:00+08:00",
        },
        runId: "run-coarse-water",
      },
      { toolName: "diet_water", runId: "run-coarse-water" },
    )).toBeUndefined();
  });

  it("fills the current user sentence for an authorized pantry discard", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;

    await beforeRun(
      { prompt: "确认退货" },
      { runId: "run-pantry-return", channelId: "webchat" },
    );

    expect(await before(
      {
        toolName: "diet_pantry",
        params: {
          action: "discard",
          inventory_match_handle: "wfh_return_oat_milk_1234567890",
          quantity: 2,
          unit: "盒",
          reason: "退货",
        },
        runId: "run-pantry-return",
      },
      { toolName: "diet_pantry", runId: "run-pantry-return" },
    )).toEqual({
      params: {
        action: "discard",
        inventory_match_handle: "wfh_return_oat_milk_1234567890",
        quantity: 2,
        unit: "盒",
        reason: "退货",
        source_text: "确认退货",
      },
    });
  });

  it("binds a recent-operation status check to one transaction read route", async () => {
    const host = fakePluginApi();
    plugin.register(host.api as never);
    const beforeRun = host.hooks.get("before_prompt_build")!;
    const before = host.hooks.get("before_tool_call")!;
    const after = host.hooks.get("after_tool_call")!;

    expect(await beforeRun(
      { prompt: "刚才记上了吗？" },
      { runId: "run-operation-status", channelId: "webchat" },
    )).toMatchObject({
      appendContext: expect.stringMatching(/diet_transaction get_recent once/i),
    });

    expect(await before(
      {
        toolName: "diet_meal",
        params: { action: "query", rolling_window: { value: 6, unit: "hour" } },
        runId: "run-operation-status",
      },
      { toolName: "diet_meal", runId: "run-operation-status" },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/diet_transaction get_recent/i),
    });

    expect(await before(
      {
        toolName: "diet_transaction",
        params: { action: "get_recent", limit: 3 },
        runId: "run-operation-status",
      },
      { toolName: "diet_transaction", runId: "run-operation-status" },
    )).toBeUndefined();

    await after(
      {
        toolName: "diet_transaction",
        params: { action: "get_recent", limit: 3 },
        result: { details: { ok: true, outcome: "read_completed", data: {} } },
        runId: "run-operation-status",
      },
      { toolName: "diet_transaction", runId: "run-operation-status" },
    );

    expect(await before(
      {
        toolName: "diet_transaction",
        params: { action: "get_recent", limit: 3 },
        runId: "run-operation-status",
      },
      { toolName: "diet_transaction", runId: "run-operation-status" },
    )).toMatchObject({
      block: true,
      blockReason: expect.stringMatching(/already completed/i),
    });
  });
});
