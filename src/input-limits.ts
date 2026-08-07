const MAX_INPUT_DEPTH = 32;
const MAX_INPUT_STRING_LENGTH = 16 * 1024;
const MAX_INPUT_COLLECTION_MEMBERS = 1000;

type PendingValue = {
  value: unknown;
  depth: number;
};

/**
 * Enforce limits JSON Schema cannot express portably, without recursion.
 *
 * The public parameter envelope is depth zero, so a dynamic outcome/evidence
 * value may contain exactly 32 nested JSON containers.
 */
export function validateInputLimits(input: unknown): void {
  const pending: PendingValue[] = [{ value: input, depth: 0 }];
  while (pending.length > 0) {
    const current = pending.pop()!;
    const { value, depth } = current;
    if (typeof value === "string") {
      if (value.length > MAX_INPUT_STRING_LENGTH) {
        throw new TypeError("Input string exceeds the maximum length");
      }
      continue;
    }
    if (Array.isArray(value)) {
      if (depth > MAX_INPUT_DEPTH) {
        throw new TypeError("Input exceeds the maximum nesting depth");
      }
      if (value.length > MAX_INPUT_COLLECTION_MEMBERS) {
        throw new TypeError("Input array has too many items");
      }
      for (const item of value) {
        pending.push({ value: item, depth: depth + 1 });
      }
      continue;
    }
    if (typeof value !== "object" || value === null) {
      continue;
    }
    if (depth > MAX_INPUT_DEPTH) {
      throw new TypeError("Input exceeds the maximum nesting depth");
    }
    const entries = Object.entries(value);
    if (entries.length > MAX_INPUT_COLLECTION_MEMBERS) {
      throw new TypeError("Input object has too many members");
    }
    for (const [key, item] of entries) {
      if (key.length > MAX_INPUT_STRING_LENGTH) {
        throw new TypeError("Input object key exceeds the maximum length");
      }
      pending.push({ value: item, depth: depth + 1 });
    }
  }
}
