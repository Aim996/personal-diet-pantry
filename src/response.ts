import { randomBytes } from "node:crypto";

const PRIVATE_KEYS = new Set([
  "_internal",
  "database_id",
  "databaseId",
  "id",
  "meal_id",
  "mealId",
  "batch_id",
  "batchId",
  "record_id",
  "recordId",
  "transaction_id",
  "transactionId",
  "original_transaction_id",
  "preview_token",
  "previewToken",
  "token",
]);
const PRIVATE_COMPACT_KEYS = new Set([
  "absolutepath",
  "semanticfingerprint",
  "sourcesessionkey",
  "sourcemodel",
  "testrunid",
]);
const WORKFLOW_HANDLE_LIMIT = 1_000;
const workflowHandles = new Map<string, string>();

export function toToolResult(result: unknown): Record<string, unknown> {
  const output = sanitizePublicResponse(result);
  if (!isObject(output)) {
    return { ok: false, data: {}, error: { code: "INVALID_RESPONSE" } };
  }
  const internal = isObject(result) && isObject(result._internal)
    ? result._internal
    : undefined;
  const previewToken = internal?.previewToken ?? internal?.preview_token;
  if (typeof previewToken === "string" && previewToken.length > 0) {
    const commitHandle = issueWorkflowHandle(previewToken);
    const currentWorkflow = isObject(output.workflow) ? output.workflow : {};
    output.workflow = { ...currentWorkflow, commitHandle };
  }
  return output;
}

export function sanitizePublicResponse(value: unknown): unknown {
  return scrubValue(value);
}

export function resolveWorkflowHandles<T>(value: T): T {
  return resolveValue(value) as T;
}

export function toolTextContent(details: Record<string, unknown>): string {
  const data = isObject(details.data) ? details.data : undefined;
  const receipt = data?.rendered_receipt;
  if (
    details.ok === true &&
    details.outcome === "write_committed" &&
    typeof receipt === "string" &&
    receipt.length > 0
  ) {
    return receipt;
  }
  return JSON.stringify(projectAgentResponse(details));
}

function projectAgentResponse(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(projectAgentResponse);
  }
  if (!isObject(value)) {
    return value;
  }

  const hiddenKeys = new Set<string>();
  if (typeof value.occurred_at_local === "string") {
    hiddenKeys.add("occurred_at");
    hiddenKeys.add("created_at");
    hiddenKeys.add("updated_at");
    hiddenKeys.add("deleted_at");
  }
  if (typeof value.measured_at_local === "string") {
    hiddenKeys.add("measured_at");
    hiddenKeys.add("created_at");
    hiddenKeys.add("updated_at");
    hiddenKeys.add("deleted_at");
  }
  if (
    typeof value.start_local === "string" &&
    typeof value.end_local === "string"
  ) {
    hiddenKeys.add("start_utc");
    hiddenKeys.add("end_utc");
  }

  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !hiddenKeys.has(key))
      .map(([key, child]) => [key, projectAgentResponse(child)]),
  );
}

function issueWorkflowHandle(privateToken: string): string {
  const handle = `wfh_${randomBytes(24).toString("base64url")}`;
  workflowHandles.set(handle, privateToken);
  if (workflowHandles.size > WORKFLOW_HANDLE_LIMIT) {
    const oldestHandle = workflowHandles.keys().next().value;
    if (typeof oldestHandle === "string") {
      workflowHandles.delete(oldestHandle);
    }
  }
  return handle;
}

function scrubValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(scrubValue);
  }
  if (!isObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !isPrivateKey(key))
      .map(([key, child]) => [key, scrubValue(child)]),
  );
}

function isPrivateKey(key: string): boolean {
  const lower = key.toLowerCase();
  const compact = lower.replaceAll("_", "").replaceAll("-", "");
  return (
    PRIVATE_KEYS.has(key) ||
    PRIVATE_COMPACT_KEYS.has(compact) ||
    lower === "id" ||
    lower.endsWith("_id") ||
    lower.endsWith("_token") ||
    lower === "confidence" ||
    lower.endsWith("_confidence") ||
    compact.endsWith("confidence") ||
    [
      "confidencesignals",
      "signals",
      "candidatejson",
      "rawcandidate",
      "rawcandidates",
      "diagnostic",
      "diagnostics",
      "internaldiagnostics",
    ].includes(compact) ||
    key.endsWith("Id") ||
    key.endsWith("Token")
  );
}

function resolveValue(value: unknown): unknown {
  if (typeof value === "string") {
    return workflowHandles.get(value) ?? value;
  }
  if (Array.isArray(value)) {
    return value.map(resolveValue);
  }
  if (!isObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, resolveValue(child)]),
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
