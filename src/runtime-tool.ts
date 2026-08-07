import { createHash } from "node:crypto";

import type { JsonObject } from "./bridge.js";

export type RuntimeIdentity = {
  sessionKey?: string;
  sessionId?: string;
  modelRef?: string;
  testRunId?: string;
};

export function runtimeSessionIdentity(
  identity: RuntimeIdentity,
): string | undefined {
  const candidate = identity.sessionKey ?? identity.sessionId;
  return typeof candidate === "string" && candidate.trim() !== ""
    ? candidate
    : undefined;
}

export function semanticFingerprint(
  request: JsonObject,
  identity: RuntimeIdentity,
  observedAt: Date = new Date(),
): string {
  const payload = objectValue(request.payload);
  const domain = textValue(request.domain) ?? "unknown";
  const action = textValue(request.action) ?? "unknown";
  return createHash("sha256")
    .update(JSON.stringify({
      session: runtimeSessionIdentity(identity) ?? "unknown-session",
      domain,
      action: semanticActionFamily(domain, action),
      sourceText: normalizedSourceText(payload),
      occurredMinute: normalizedOccurredMinute(
        domain,
        action,
        payload,
        observedAt,
      ),
      target: normalizedBusinessTarget(payload),
    }), "utf8")
    .digest("hex");
}

function semanticActionFamily(domain: string, action: string): string {
  if (domain === "meal" && ["record", "preview_record", "commit_record"].includes(action)) return "record";
  if (domain === "pantry" && ["add", "preview_add", "commit_add"].includes(action)) return "add";
  return action;
}

function normalizedSourceText(payload: Record<string, unknown>): string {
  return (textValue(payload.source_text) ?? "").normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase();
}

function normalizedOccurredMinute(
  domain: string,
  action: string,
  payload: Record<string, unknown>,
  observedAt: Date,
): string | undefined {
  if (domain === "weight" && action === "record") {
    return observedAt.toISOString().slice(0, 16);
  }
  const occurredAt = textValue(payload.occurred_at);
  if (occurredAt === undefined) return undefined;
  const parsed = Date.parse(occurredAt);
  return Number.isNaN(parsed) ? occurredAt : new Date(parsed).toISOString().slice(0, 16);
}

function normalizedBusinessTarget(payload: Record<string, unknown>): unknown {
  return semanticValue(payload);
}

const NON_SEMANTIC_KEYS = new Set([
  "_internal",
  "confirmed",
  "confidence_signals",
  "evidence",
  "nutrition_estimate",
  "nutrition_facts",
  "nutrition_profile",
  "operation_id",
  "request_fingerprint",
  "semantic_fingerprint",
  "source_grade",
  "source_text",
  "uncertainty",
]);

function semanticValue(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) {
    return value.map((child) => semanticValue(child));
  }
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(object)
        .filter((childKey) =>
          object[childKey] !== undefined
          && !NON_SEMANTIC_KEYS.has(childKey)
          && !childKey.endsWith("_confidence")
        )
        .sort()
        .map((childKey) => [
          childKey,
          semanticValue(object[childKey], childKey),
        ]),
    );
  }
  if (typeof value !== "string") {
    return value;
  }
  if (key?.endsWith("_at")) {
    return normalizedMinute(value);
  }
  const normalized = value.normalize("NFKC").trim().replace(/\s+/gu, " ");
  return [
    "food_name",
    "normalized_name",
    "raw_name",
    "subject",
  ].includes(key ?? "")
    ? normalized.toLowerCase()
    : normalized;
}

function normalizedMinute(value: string): string {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed)
    ? value
    : new Date(parsed).toISOString().slice(0, 16);
}

function objectValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}
