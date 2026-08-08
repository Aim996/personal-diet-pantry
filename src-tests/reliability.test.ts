import { describe, expect, it, vi } from "vitest";

import { BridgeError, type JsonObject } from "../src/bridge.js";
import { FORMAL_MUTATION_ACTIONS } from "../src/generated/tool-contracts.js";
import { callPythonReliably } from "../src/reliability.js";


describe("formal mutation reliability", () => {
  it("serializes concurrent formal writes that share one data directory", async () => {
    let active = 0;
    let maxActive = 0;
    const runner = async () => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 15));
      active -= 1;
      return {
        response: { ok: true, data: { status: "committed" } },
        diagnostics: { exitCode: 0, stderr: "" },
      };
    };

    await Promise.all([
      callPythonReliably(
        { domain: "pantry", action: "add", payload: { source_text: "苹果" } },
        { dataDir: "same-diet-data" },
        { operationIdFactory: () => "op_serial_1", runner },
      ),
      callPythonReliably(
        { domain: "pantry", action: "add", payload: { source_text: "酸奶" } },
        { dataDir: "same-diet-data" },
        { operationIdFactory: () => "op_serial_2", runner },
      ),
      callPythonReliably(
        { domain: "meal", action: "record", payload: { source_text: "玉米" } },
        { dataDir: "same-diet-data" },
        { operationIdFactory: () => "op_serial_3", runner },
      ),
    ]);

    expect(maxActive).toBe(1);
  });

  it("does not call Python twice for one identical session failure", async () => {
    const runner = vi.fn().mockResolvedValue({
      response: {
        ok: false,
        outcome: "failed",
        data: {},
        error: {
          code: "INVALID_INPUT",
          field: "quantity",
          reason: "required",
          expected: "positive number",
          retryable: true,
        },
      },
      diagnostics: { exitCode: 0, stderr: "" },
    });
    const request = {
      domain: "pantry",
      action: "discard",
      payload: { inventory_match_handle: "wfh_example" },
    };
    const dependencies = {
      runner,
      runtimeIdentity: { sessionKey: "failure-cache-same-session" },
    };

    const first = await callPythonReliably(request, {}, dependencies);
    const second = await callPythonReliably(request, {}, dependencies);

    expect(runner).toHaveBeenCalledTimes(1);
    expect(second).toEqual(first);
    expect(second).not.toBe(first);
  });

  it("calls Python again after the rejected field is corrected", async () => {
    const runner = vi.fn().mockResolvedValue({
      response: {
        ok: false,
        outcome: "failed",
        data: {},
        error: {
          code: "INVALID_INPUT",
          field: "unit",
          reason: "required",
          expected: "stored unit",
          retryable: true,
        },
      },
      diagnostics: { exitCode: 0, stderr: "" },
    });
    const dependencies = {
      runner,
      runtimeIdentity: { sessionKey: "failure-cache-corrected-session" },
    };

    await callPythonReliably({
      domain: "pantry",
      action: "discard",
      payload: { inventory_match_handle: "wfh_example", quantity: "2" },
    }, {}, dependencies);
    await callPythonReliably({
      domain: "pantry",
      action: "discard",
      payload: {
        inventory_match_handle: "wfh_example",
        quantity: "2",
        unit: "piece",
      },
    }, {}, dependencies);

    expect(runner).toHaveBeenCalledTimes(2);
  });

  it("blocks an identical immediate retry after any terminal failure", async () => {
    const runner = vi.fn().mockResolvedValue({
      response: {
        ok: false,
        outcome: "failed",
        data: {},
        error: { code: "DATABASE_BUSY", retryable: true },
      },
      diagnostics: { exitCode: 0, stderr: "" },
    });
    const request = {
      domain: "pantry",
      action: "discard",
      payload: { inventory_match_handle: "wfh_transient" },
    };
    const dependencies = {
      runner,
      runtimeIdentity: { sessionKey: "failure-cache-transient-session" },
    };

    await callPythonReliably(request, {}, dependencies);
    await callPythonReliably(request, {}, dependencies);

    expect(runner).toHaveBeenCalledTimes(1);
  });

  it("wraps body-weight writes in the operation receipt protocol", async () => {
    const calls: JsonObject[] = [];
    const runner = async (request: JsonObject) => {
      calls.push(request);
      return {
        response: { ok: true, data: { status: "committed" } },
        diagnostics: { exitCode: 0, stderr: "" },
      };
    };

    await callPythonReliably(
      {
        domain: "weight",
        action: "record",
        payload: { weight: 105, unit: "kg" },
      },
      {},
      {
        operationIdFactory: () => (
          "op_00000000-0000-4000-8000-000000000005"
        ),
        runner,
      },
    );

    expect(calls).toHaveLength(1);
    expect(calls[0]?._internal).toMatchObject({
      operation_id: "op_00000000-0000-4000-8000-000000000005",
    });
  });

  it("uses generated formal mutations for operation receipts only", async () => {
    const calls: JsonObject[] = [];
    const runner = async (request: JsonObject) => {
      calls.push(request);
      return {
        response: { ok: true, data: { status: "committed" } },
        diagnostics: { exitCode: 0, stderr: "" },
      };
    };

    for (const target of FORMAL_MUTATION_ACTIONS) {
      const [domain, action] = target.split(".");
      await callPythonReliably(
        { domain, action },
        {},
        { operationIdFactory: () => "op_generated_mutation", runner },
      );
    }
    await callPythonReliably(
      { domain: "pantry", action: "search" },
      {},
      { runner },
    );
    await callPythonReliably(
      { domain: "report", action: "progress" },
      {},
      { runner },
    );

    expect(
      calls
        .slice(0, FORMAL_MUTATION_ACTIONS.length)
        .every((request) => request._internal !== undefined),
    ).toBe(true);
    expect(
      calls
        .slice(FORMAL_MUTATION_ACTIONS.length)
        .every((request) => request._internal === undefined),
    ).toBe(true);
  });

  it("scopes body-weight semantic retries to the trusted current minute", async () => {
    const fingerprints: unknown[] = [];
    const runner = async (request: JsonObject) => {
      const internal = request._internal as JsonObject;
      fingerprints.push(internal.semantic_fingerprint);
      return {
        response: { ok: true, data: { status: "committed" } },
        diagnostics: { exitCode: 0, stderr: "" },
      };
    };
    const request = {
      domain: "weight",
      action: "record",
      payload: { weight: 105, unit: "kg", status_note: "空腹" },
    };

    await callPythonReliably(request, {}, {
      clock: () => new Date("2026-07-30T00:30:00Z"),
      operationIdFactory: () => "op_weight_day_one",
      runner,
      runtimeIdentity: { sessionKey: "same-session" },
    });
    await callPythonReliably(request, {}, {
      clock: () => new Date("2026-07-31T00:30:00Z"),
      operationIdFactory: () => "op_weight_day_two",
      runner,
      runtimeIdentity: { sessionKey: "same-session" },
    });

    expect(fingerprints).toHaveLength(2);
    expect(fingerprints[0]).not.toBe(fingerprints[1]);
  });

  it("does not execute a formal write twice after an uncertain outcome", async () => {
    const calls: JsonObject[] = [];
    const runner = async (request: JsonObject) => {
      calls.push(request);
      if (calls.length === 1) {
        throw new BridgeError(
          "TIMEOUT",
          "timed out after process termination",
          undefined,
          { terminationConfirmed: true },
        );
      }
      if (
        typeof request._internal === "object"
        && request._internal !== null
        && (request._internal as JsonObject).kind === "operation_status"
      ) {
        return {
          response: { ok: true, data: { status: "absent" } },
          diagnostics: { exitCode: 0, stderr: "" },
        };
      }
      return {
        response: { ok: true, data: { status: "committed" } },
        diagnostics: { exitCode: 0, stderr: "" },
      };
    };

    await expect(callPythonReliably(
      {
        domain: "meal",
        action: "record",
        payload: { source_text: "已吃早餐" },
      },
      {},
      {
        operationIdFactory: () => "op_test",
        runner,
      },
    )).rejects.toMatchObject({ code: "PROCESS_ERROR" });

    expect(calls).toHaveLength(2);
  });
});
