import { describe, expect, it } from "vitest";

import { sanitizePublicResponse, toolTextContent } from "../src/response.js";


describe("public response filtering", () => {
  it("removes private fields recursively from objects and arrays", () => {
    const privatePayload = {
      ok: true,
      outcome: "write_committed",
      data: {
        transaction_id: "secret",
        nested: [
          { preview_token: "secret" },
          { safe: "visible" },
        ],
        absolute_path: "E:\\private\\data.db",
      },
    };

    expect(sanitizePublicResponse(privatePayload)).toEqual({
      ok: true,
      outcome: "write_committed",
      data: {
        nested: [{}, { safe: "visible" }],
      },
    });
  });

  it("uses the committed backend receipt as the exact user-facing tool text", () => {
    const details = {
      ok: true,
      outcome: "write_committed",
      data: {
        rendered_receipt: "已记录！玉米 1个（估算可食部约90g）｜88 kcal\n\n🔥 热量 ████░░░░░░ 42%",
      },
    };

    expect(toolTextContent(details)).toBe(details.data.rendered_receipt);
  });

  it("does not trust a receipt attached to a failed response", () => {
    const details = {
      ok: false,
      outcome: "failed",
      data: { rendered_receipt: "已记录！不应显示" },
      error: { code: "INVALID_INPUT" },
    };

    expect(toolTextContent(details)).toBe(JSON.stringify(details));
  });

  it("preserves safe recovery fields on failed responses", () => {
    expect(sanitizePublicResponse({
      ok: false,
      outcome: "failed",
      data: {},
      error: {
        code: "INVALID_INPUT",
        message: "The request is invalid",
        field: "unit",
        reason: "unsupported_conversion",
        expected: "the stored base unit or display unit",
        retryable: true,
        batch_id: 42,
      },
    })).toEqual({
      ok: false,
      outcome: "failed",
      data: {},
      error: {
        code: "INVALID_INPUT",
        message: "The request is invalid",
        field: "unit",
        reason: "unsupported_conversion",
        expected: "the stored base unit or display unit",
        retryable: true,
      },
    });
  });

  it("shows local business time to the agent without competing UTC audit fields", () => {
    const details = {
      ok: true,
      outcome: "read_completed",
      data: {
        meals: [{
          occurred_at: "2026-08-06T19:36:52Z",
          occurred_at_local: "2026-08-07T03:36:52+08:00",
          timezone_name: "Asia/Shanghai",
          created_at: "2026-08-06T19:36:53Z",
          updated_at: "2026-08-06T19:37:22Z",
          deleted_at: null,
          items: [{ raw_name: "玉米" }],
        }],
        scope: {
          start_utc: "2026-08-06T13:37:55Z",
          end_utc: "2026-08-06T19:37:55Z",
          start_local: "2026-08-06T21:37:55+08:00",
          end_local: "2026-08-07T03:37:55+08:00",
          timezone: "Asia/Shanghai",
        },
      },
    };

    const agentText = JSON.parse(toolTextContent(details));
    expect(agentText.data.meals[0]).toMatchObject({
      occurred_at_local: "2026-08-07T03:36:52+08:00",
      timezone_name: "Asia/Shanghai",
    });
    expect(agentText.data.meals[0]).not.toHaveProperty("occurred_at");
    expect(agentText.data.meals[0]).not.toHaveProperty("created_at");
    expect(agentText.data.meals[0]).not.toHaveProperty("updated_at");
    expect(agentText.data.meals[0]).not.toHaveProperty("deleted_at");
    expect(agentText.data.scope).toMatchObject({
      start_local: "2026-08-06T21:37:55+08:00",
      end_local: "2026-08-07T03:37:55+08:00",
    });
    expect(agentText.data.scope).not.toHaveProperty("start_utc");
    expect(agentText.data.scope).not.toHaveProperty("end_utc");

    expect(details.data.meals[0].occurred_at).toBe("2026-08-06T19:36:52Z");
    expect(details.data.scope.start_utc).toBe("2026-08-06T13:37:55Z");
  });

  it("preserves the only available UTC business time when no local projection exists", () => {
    const details = {
      ok: true,
      outcome: "read_completed",
      data: {
        meals: [{ occurred_at: "2026-08-06T19:36:52Z" }],
      },
    };

    expect(JSON.parse(toolTextContent(details)).data.meals[0]).toEqual({
      occurred_at: "2026-08-06T19:36:52Z",
    });
  });
});
