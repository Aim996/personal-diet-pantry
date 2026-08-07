import { createHash, randomUUID } from "node:crypto";

import {
  BridgeError,
  callPython,
  type CallPythonOptions,
  type JsonObject,
  type PythonCallResult,
} from "./bridge.js";
import { FORMAL_MUTATION_ACTIONS } from "./generated/tool-contracts.js";
import {
  runtimeSessionIdentity,
  semanticFingerprint,
  type RuntimeIdentity,
} from "./runtime-tool.js";

type PythonRunner = (
  request: JsonObject,
  options?: CallPythonOptions,
) => Promise<PythonCallResult>;

interface ReliabilityDependencies {
  clock?: () => Date;
  operationIdFactory?: () => string;
  runner?: PythonRunner;
  runtimeIdentity?: RuntimeIdentity;
}

const FORMAL_MUTATIONS = new Set(
  FORMAL_MUTATION_ACTIONS.map((value) => value.replace(".", ":")),
);

const OUTCOME_UNCERTAIN_BRIDGE_ERRORS = new Set([
  "INVALID_RESPONSE",
  "TIMEOUT",
]);
const FAILURE_CACHE_TTL_MS = 5 * 60 * 1_000;
const FAILURE_CACHE_LIMIT = 256;
type FailureCacheEntry = {
  errorCode: string;
  expiresAt: number;
  result: PythonCallResult;
};
const failureCache = new Map<string, FailureCacheEntry>();

export async function callPythonReliably(
  request: JsonObject,
  options: CallPythonOptions = {},
  dependencies: ReliabilityDependencies = {},
): Promise<PythonCallResult> {
  const runner = dependencies.runner ?? callPython;
  const now = dependencies.clock?.() ?? new Date();
  const failureKey = failureCacheKey(
    request,
    dependencies.runtimeIdentity ?? {},
  );
  const cached = failureKey === undefined
    ? undefined
    : cachedFailure(failureKey, now.getTime());
  if (cached !== undefined) {
    return cloneResult(cached);
  }
  if (!isFormalMutation(request)) {
    const result = await runner(request, options);
    rememberFailure(failureKey, result, now.getTime());
    return result;
  }

  const operationId =
    dependencies.operationIdFactory?.() ?? `op_${randomUUID()}`;
  const requestFingerprint = createHash("sha256")
    .update(canonicalJson(request), "utf8")
    .digest("hex");
  const { source_session_key: _untrustedSession, source_model: _untrustedModel, test_run_id: _untrustedTestRun, ...publicRequest } = request;
  const identity = dependencies.runtimeIdentity ?? {};
  const sourceSessionKey = identity.sessionKey ?? identity.sessionId;
  const mutationRequest: JsonObject = {
    ...publicRequest,
    _internal: {
      operation_id: operationId,
      request_fingerprint: requestFingerprint,
      semantic_fingerprint: semanticFingerprint(
        publicRequest,
        identity,
        now,
      ),
      ...(sourceSessionKey === undefined ? {} : { source_session_key: sourceSessionKey }),
      ...(identity.modelRef === undefined ? {} : { source_model: identity.modelRef }),
      ...(identity.testRunId === undefined ? {} : { test_run_id: identity.testRunId }),
    },
  };
  try {
    const result = sanitizeResult(
      await runner(mutationRequest, options),
      operationId,
      requestFingerprint,
    );
    rememberFailure(failureKey, result, now.getTime());
    return result;
  } catch (error) {
    if (!isOutcomeUncertainBridgeError(error)) {
      throw sanitizeError(error, operationId, requestFingerprint);
    }
    const status = await lookupStatus(
      runner,
      options,
      operationId,
      requestFingerprint,
    );
    if (status.result !== undefined) {
      return sanitizeResult(
        status.result,
        operationId,
        requestFingerprint,
      );
    }
    if (options.signal?.aborted) {
      throw new BridgeError("ABORTED", "Python request was aborted");
    }
    throw uncertainOutcome(error, operationId, requestFingerprint);
  }
}

function failureCacheKey(
  request: JsonObject,
  identity: RuntimeIdentity,
): string | undefined {
  const session = runtimeSessionIdentity(identity);
  if (session === undefined) return undefined;
  const { domain, action } = request;
  if (typeof domain !== "string" || typeof action !== "string") {
    return undefined;
  }
  return createHash("sha256")
    .update(canonicalJson({
      session,
      domain,
      action,
      payload: isObject(request.payload) ? request.payload : {},
    }), "utf8")
    .digest("hex");
}

function cachedFailure(
  key: string,
  nowMs: number,
): PythonCallResult | undefined {
  const entry = failureCache.get(key);
  if (entry === undefined) return undefined;
  if (entry.expiresAt <= nowMs) {
    failureCache.delete(key);
    return undefined;
  }
  failureCache.delete(key);
  failureCache.set(key, entry);
  return entry.result;
}

function rememberFailure(
  key: string | undefined,
  result: PythonCallResult,
  nowMs: number,
): void {
  if (key === undefined) return;
  const response = result.response;
  const error = isObject(response.error) ? response.error : undefined;
  const errorCode = error?.code;
  if (
    response.ok !== false
    || response.outcome !== "failed"
    || typeof errorCode !== "string"
  ) {
    return;
  }
  failureCache.delete(key);
  failureCache.set(key, {
    errorCode,
    expiresAt: nowMs + FAILURE_CACHE_TTL_MS,
    result: cloneResult(result),
  });
  while (failureCache.size > FAILURE_CACHE_LIMIT) {
    const oldest = failureCache.keys().next().value;
    if (typeof oldest !== "string") break;
    failureCache.delete(oldest);
  }
}

function cloneResult(result: PythonCallResult): PythonCallResult {
  return {
    response: JSON.parse(JSON.stringify(result.response)) as JsonObject,
    diagnostics: { ...result.diagnostics },
  };
}

function isFormalMutation(request: JsonObject): boolean {
  const { domain, action } = request;
  return (
    typeof domain === "string" &&
    typeof action === "string" &&
    FORMAL_MUTATIONS.has(`${domain}:${action}`)
  );
}

function isOutcomeUncertainBridgeError(
  error: unknown,
): error is BridgeError {
  return (
    error instanceof BridgeError &&
    OUTCOME_UNCERTAIN_BRIDGE_ERRORS.has(error.code)
  );
}

async function lookupStatus(
  runner: PythonRunner,
  options: CallPythonOptions,
  operationId: string,
  requestFingerprint: string,
): Promise<{
  status: "absent" | "committed" | "unknown";
  result?: PythonCallResult;
}> {
  try {
    const result = await runner(
      {
        _internal: {
          kind: "operation_status",
          operation_id: operationId,
          request_fingerprint: requestFingerprint,
        },
      },
      options,
    );
    const data = result.response.data;
    if (isObject(data) && data.status === "committed") {
      return { status: "committed", result };
    }
    if (isObject(data) && data.status === "absent") {
      return { status: "absent" };
    }
  } catch {
    // An inaccessible status store is unknown and must never trigger a retry.
  }
  return { status: "unknown" };
}

function sanitizeResult(
  result: PythonCallResult,
  operationId: string,
  requestFingerprint: string,
): PythonCallResult {
  return {
    response: redactValue(
      result.response,
      operationId,
      requestFingerprint,
    ) as JsonObject,
    diagnostics: {
      exitCode: result.diagnostics.exitCode,
      stderr: redactText(
        result.diagnostics.stderr,
        operationId,
        requestFingerprint,
      ),
    },
  };
}

function sanitizeError(
  error: unknown,
  operationId: string,
  requestFingerprint: string,
): unknown {
  return error instanceof BridgeError
    ? new BridgeError(
        error.code,
        redactText(error.message, operationId, requestFingerprint),
        {
          exitCode: error.diagnostics.exitCode,
          stderr: redactText(
            error.diagnostics.stderr,
            operationId,
            requestFingerprint,
          ),
        },
        { terminationConfirmed: error.terminationConfirmed },
      )
    : error;
}

function uncertainOutcome(
  error: unknown,
  operationId: string,
  requestFingerprint: string,
): BridgeError {
  const safe = sanitizeError(error, operationId, requestFingerprint);
  const diagnostics =
    safe instanceof BridgeError
      ? safe.diagnostics
      : { exitCode: null, stderr: "" };
  return new BridgeError(
    "PROCESS_ERROR",
    "The operation outcome could not be confirmed. Query current state before another change.",
    diagnostics,
  );
}

function redactValue(
  value: unknown,
  operationId: string,
  requestFingerprint: string,
): unknown {
  if (typeof value === "string") {
    return redactText(value, operationId, requestFingerprint);
  }
  if (Array.isArray(value)) {
    return value.map((child) =>
      redactValue(child, operationId, requestFingerprint),
    );
  }
  if (!isObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [
      key,
      redactValue(child, operationId, requestFingerprint),
    ]),
  );
}

function redactText(
  value: string,
  operationId: string,
  requestFingerprint: string,
): string {
  return value
    .replaceAll(operationId, "[internal operation]")
    .replaceAll(requestFingerprint, "[internal fingerprint]")
    .replace(/\b[0-9a-f]{64}\b/giu, "[internal fingerprint]")
    .replace(/\btxn_[A-Za-z0-9_-]+\b/giu, "[internal transaction]")
    .replace(
      /\b(?:operation|transaction|database|meal|batch|water|record|row)_id\s*[:=]\s*[A-Za-z0-9_-]+\b/giu,
      "[internal identifier]",
    );
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((child) =>
      child === undefined ? null : canonicalValue(child),
    );
  }
  if (!isObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.keys(value)
      .filter((key) => value[key] !== undefined)
      .sort()
      .map((key) => [key, canonicalValue(value[key])]),
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
