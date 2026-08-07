import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

import { callPythonReliably } from "./reliability.js";
import { validateInputLimits } from "./input-limits.js";
import { resolveWorkflowHandles, toolTextContent, toToolResult } from "./response.js";
import { TOOL_NAMES } from "./generated/tool-contracts.js";
import {
  MealParametersSchema,
  PantryParametersSchema,
  ReportParametersSchema,
  SystemParametersSchema,
  TransactionParametersSchema,
  WaterParametersSchema,
  WeightParametersSchema,
} from "./core-schemas.js";
import { PluginConfigSchema } from "./schemas.js";
import {
  authorizeTurnTool,
  classifyTurnIntent,
  dietDomainForTool,
  isDietReadOperation,
  queryLike,
  type DietDomain,
  type TurnIntent,
} from "./turn-guard.js";
import {
  classifyDirectWrite,
  directWriteInstruction,
} from "./direct-write-policy.js";

const domainTools = [
  {
    domain: "meal",
    name: TOOL_NAMES.meal,
    label: "Diet Meals",
    description:
      "Record clear past-tense intake with record. Use record_cooking for a complete dish: atomically deduct the full recipe, log the eaten fraction, and save leftovers. Results return daily_progress and inventory_effects for a concise reply.",
    parameters: MealParametersSchema,
  },
  {
    domain: "water",
    name: TOOL_NAMES.water,
    label: "Diet Water",
    description: "Record, query, update, or delete water intake.",
    parameters: WaterParametersSchema,
  },
  {
    domain: "weight",
    name: TOOL_NAMES.weight,
    label: "Diet Weight",
    description:
      "Record body weight at trusted system time, query recent measurements and seven-day trend, or correct/delete a selected record. Explicit weighing wording plus a plausible unitless number defaults to kg.",
    parameters: WeightParametersSchema,
  },
  {
    domain: "pantry",
    name: TOOL_NAMES.pantry,
    label: "Diet Pantry",
    description:
      "Add, query, enrich, adjust, or deduct pantry inventory. For a clear item and quantity, add directly: production and expiry dates are optional, and omitted storage/expiry use marked backend estimates. Preview only a genuinely ambiguous item or quantity.",
    parameters: PantryParametersSchema,
  },
  {
    domain: "transaction",
    name: TOOL_NAMES.transaction,
    label: "Diet Transactions",
    description: "Find, undo, or redo recent diet transactions.",
    parameters: TransactionParametersSchema,
  },
  {
    domain: "report",
    name: TOOL_NAMES.report,
    label: "Diet Reports",
    description: "Build diet reports. action progress returns six SQLite progress metrics; action insights returns bounded nutrition, data-quality, and expiring-inventory priorities without reading a report file.",
    parameters: ReportParametersSchema,
  },
  {
    domain: "system",
    name: TOOL_NAMES.system,
    label: "Diet System",
    description: "Initialize, inspect, repair, back up, and configure the diet system; update goals and run nutrition backfill with this tool.",
    parameters: SystemParametersSchema,
  },
] as const;

type PantryPackageNormalization = {
  payload: Record<string, unknown>;
  action?: string;
  correctionWarning?: string;
  error?: Record<string, unknown>;
};

type ParsedDecimal = {
  coefficient: bigint;
  scale: number;
};

function parseDecimalQuantity(value: number | string): ParsedDecimal {
  const match = String(value)
    .toLowerCase()
    .match(/^(?:(\d+)(?:\.(\d+))?|\.(\d+))(?:e([+-]?\d+))?$/);
  if (match === null) {
    throw new Error(`Invalid decimal quantity: ${value}`);
  }
  const integer = match[1] ?? "0";
  const fraction = match[2] ?? match[3] ?? "";
  const exponent = Number.parseInt(match[4] ?? "0", 10);
  let coefficient = BigInt(`${integer}${fraction}`);
  let scale = fraction.length - exponent;
  if (scale < 0) {
    coefficient *= 10n ** BigInt(-scale);
    scale = 0;
  }
  while (scale > 0 && coefficient % 10n === 0n) {
    coefficient /= 10n;
    scale -= 1;
  }
  return { coefficient, scale };
}

function decimalQuantitiesEqual(
  left: number | string,
  right: number | string,
): boolean {
  const leftDecimal = parseDecimalQuantity(left);
  const rightDecimal = parseDecimalQuantity(right);
  return (
    leftDecimal.coefficient === rightDecimal.coefficient &&
    leftDecimal.scale === rightDecimal.scale
  );
}

function compareDecimalQuantities(
  left: number | string,
  right: number | string,
): number {
  const leftDecimal = parseDecimalQuantity(left);
  const rightDecimal = parseDecimalQuantity(right);
  const scale = Math.max(leftDecimal.scale, rightDecimal.scale);
  const leftCoefficient = leftDecimal.coefficient *
    10n ** BigInt(scale - leftDecimal.scale);
  const rightCoefficient = rightDecimal.coefficient *
    10n ** BigInt(scale - rightDecimal.scale);
  return leftCoefficient < rightCoefficient
    ? -1
    : leftCoefficient > rightCoefficient
    ? 1
    : 0;
}

function multiplyDecimalQuantities(
  left: number | string,
  right: number | string,
): string {
  const leftDecimal = parseDecimalQuantity(left);
  const rightDecimal = parseDecimalQuantity(right);
  let coefficient = leftDecimal.coefficient * rightDecimal.coefficient;
  let scale = leftDecimal.scale + rightDecimal.scale;
  while (scale > 0 && coefficient % 10n === 0n) {
    coefficient /= 10n;
    scale -= 1;
  }
  const digits = coefficient.toString().padStart(scale + 1, "0");
  const decimal =
    scale === 0
      ? digits
      : `${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
  return decimal;
}

function canonicalPantryBaseUnit(value: unknown): "g" | "ml" | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const aliases: Record<string, "g" | "ml"> = {
    g: "g",
    gram: "g",
    grams: "g",
    克: "g",
    ml: "ml",
    milliliter: "ml",
    milliliters: "ml",
    毫升: "ml",
  };
  return aliases[value.trim().toLowerCase()];
}

function escapedRegularExpression(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function explicitPerDisplayRelation(
  sourceText: unknown,
  displayUnit: unknown,
): { factor: string; baseUnit: "g" | "ml" } | undefined {
  if (
    typeof sourceText !== "string" ||
    typeof displayUnit !== "string" ||
    !displayUnit.trim()
  ) {
    return undefined;
  }
  const pattern = new RegExp(
    `每\\s*${escapedRegularExpression(displayUnit.trim())}\\s*` +
      `([0-9]+(?:\\.[0-9]+)?)\\s*(毫升|ml|克|g)(?![a-z])`,
    "giu",
  );
  const relations = new Map<string, { factor: string; baseUnit: "g" | "ml" }>();
  for (const match of sourceText.matchAll(pattern)) {
    const factor = match[1];
    const baseUnit = canonicalPantryBaseUnit(match[2]);
    if (factor === undefined || baseUnit === undefined) {
      continue;
    }
    const parsed = parseDecimalQuantity(factor);
    if (parsed.coefficient <= 0n) {
      continue;
    }
    relations.set(`${parsed.coefficient}:${parsed.scale}:${baseUnit}`, {
      factor,
      baseUnit,
    });
  }
  return relations.size === 1 ? [...relations.values()][0] : undefined;
}

function requiredInputError(
  field: string,
  expected: string,
): Record<string, unknown> {
  return {
    code: "INVALID_INPUT",
    message: "The request is invalid",
    field,
    reason: "required",
    expected,
    retryable: true,
  };
}

type MealInputError = {
  field: string;
  reason: "required" | "incompatible" | "unsupported_value";
  expected: string;
};

function invalidMealInputError(
  field: string,
  reason: MealInputError["reason"],
  expected: string,
): Record<string, unknown> {
  return {
    code: "INVALID_INPUT",
    message: "The request is invalid",
    field,
    reason,
    expected,
    retryable: true,
  };
}

const MEAL_TYPE_ALIASES: Record<string, string> = {
  breakfast: "breakfast",
  早餐: "breakfast",
  早饭: "breakfast",
  lunch: "lunch",
  午餐: "lunch",
  午饭: "lunch",
  中饭: "lunch",
  dinner: "dinner",
  晚餐: "dinner",
  晚饭: "dinner",
  snack: "snack",
  加餐: "snack",
  零食: "snack",
  夜宵: "snack",
  宵夜: "snack",
  点心: "snack",
  other: "other",
  其他: "other",
  其它: "other",
  饮品: "other",
  饮料: "other",
};

const LOCATION_TYPE_ALIASES: Record<string, string> = {
  home: "home",
  家: "home",
  家里: "home",
  在家: "home",
  家庭: "home",
  restaurant: "restaurant",
  餐馆: "restaurant",
  餐厅: "restaurant",
  饭店: "restaurant",
  堂食: "restaurant",
  食堂: "restaurant",
  takeout: "takeout",
  外卖: "takeout",
  打包: "takeout",
  unknown: "unknown",
  未知: "unknown",
  不清楚: "unknown",
};

function normalizeMealEnumField(
  target: Record<string, unknown>,
  field: "meal_type" | "location_type",
  path: string,
): Record<string, unknown> | undefined {
  const value = target[field];
  if (value === undefined) {
    return undefined;
  }
  const aliases = field === "meal_type"
    ? MEAL_TYPE_ALIASES
    : LOCATION_TYPE_ALIASES;
  const normalized = typeof value === "string"
    ? aliases[value.trim().toLowerCase()]
    : undefined;
  if (normalized === undefined) {
    return invalidMealInputError(
      path,
      "unsupported_value",
      field === "meal_type"
        ? "breakfast, lunch, dinner, snack, or other"
        : "home, restaurant, takeout, or unknown",
    );
  }
  target[field] = normalized;
  return undefined;
}

function normalizeMealEnumFields(
  payload: Record<string, unknown>,
  action: unknown,
): { error?: Record<string, unknown> } {
  if (action === "record" || action === "preview_record") {
    payload.meal_type ??= "other";
    payload.location_type ??= "unknown";
  }
  if (action === "update") {
    const draft = payload.draft;
    if (draft !== null && typeof draft === "object" && !Array.isArray(draft)) {
      const normalizedDraft = { ...(draft as Record<string, unknown>) };
      payload.draft = normalizedDraft;
      const mealError = normalizeMealEnumField(
        normalizedDraft,
        "meal_type",
        "draft.meal_type",
      );
      if (mealError !== undefined) {
        return { error: mealError };
      }
      const locationError = normalizeMealEnumField(
        normalizedDraft,
        "location_type",
        "draft.location_type",
      );
      return locationError === undefined ? {} : { error: locationError };
    }
    return {};
  }

  const mealError = normalizeMealEnumField(payload, "meal_type", "meal_type");
  if (mealError !== undefined) {
    return { error: mealError };
  }
  const locationError = normalizeMealEnumField(
    payload,
    "location_type",
    "location_type",
  );
  return locationError === undefined ? {} : { error: locationError };
}

function isPositiveDecimalQuantity(value: unknown): boolean {
  if (typeof value !== "number" && typeof value !== "string") {
    return false;
  }
  try {
    return parseDecimalQuantity(value).coefficient > 0n;
  } catch {
    return false;
  }
}

function firstMealNutritionError(
  items: unknown,
  prefix: string,
): MealInputError | undefined {
  if (!Array.isArray(items)) {
    return undefined;
  }
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const itemPath = `${prefix}[${index}]`;
    const hasFacts = record.nutrition_facts !== undefined &&
      record.nutrition_facts !== null;
    const hasEstimate = record.nutrition_estimate !== undefined &&
      record.nutrition_estimate !== null;
    const hasBasis = record.nutrition_basis !== undefined &&
      record.nutrition_basis !== null;

    if (hasFacts && hasEstimate) {
      return {
        field: `${itemPath}.nutrition_estimate`,
        reason: "incompatible",
        expected: "exactly one of nutrition_facts or nutrition_estimate",
      };
    }
    if ((hasFacts || hasEstimate) && !hasBasis) {
      return {
        field: `${itemPath}.nutrition_basis`,
        reason: "required",
        expected: "a nutrition basis for direct nutrition evidence",
      };
    }
    if (!hasFacts && !hasEstimate && hasBasis) {
      return {
        field: `${itemPath}.nutrition_basis`,
        reason: "incompatible",
        expected: "nutrition_facts or nutrition_estimate with the basis",
      };
    }

    if (hasFacts) {
      const facts = record.nutrition_facts;
      if (
        facts !== null && typeof facts === "object" && !Array.isArray(facts) &&
        ![
          "calories",
          "protein",
          "fat",
          "carbohydrate",
          "fiber",
          "sodium",
          "hydration_ml",
        ].some((field) => {
          const value = (facts as Record<string, unknown>)[field];
          return value !== undefined && value !== null;
        })
      ) {
        return {
          field: `${itemPath}.nutrition_facts`,
          reason: "required",
          expected: "at least one known nutrition field; omit unknown fields",
        };
      }
    }

    if (hasFacts || hasEstimate) {
      const measures: Record<string, string | undefined> = {
        per_100g: "consumed_weight_g",
        per_100ml: "consumed_volume_ml",
        per_serving: "consumed_servings",
        consumed_total: undefined,
      };
      const basis = record.nutrition_basis;
      if (typeof basis !== "string" || !(basis in measures)) {
        return {
          field: `${itemPath}.nutrition_basis`,
          reason: "incompatible",
          expected: "per_100g, per_100ml, per_serving, or consumed_total",
        };
      }
      const measure = measures[basis];
      if (
        measure !== undefined &&
        !isPositiveDecimalQuantity(record[measure])
      ) {
        return {
          field: `${itemPath}.${measure}`,
          reason: "required",
          expected: `a positive ${measure} for ${basis}`,
        };
      }
    }

    const nested = firstMealNutritionError(
      record.ingredients,
      `${itemPath}.ingredients`,
    );
    if (nested !== undefined) {
      return nested;
    }
  }
  return undefined;
}

function structurallyEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => structurallyEqual(value, right[index]));
  }
  if (
    left === null || right === null ||
    typeof left !== "object" || typeof right !== "object"
  ) {
    return false;
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return leftKeys.length === rightKeys.length &&
    leftKeys.every((key, index) =>
      key === rightKeys[index] &&
      structurallyEqual(leftRecord[key], rightRecord[key])
    );
}

function normalizeDuplicateNutritionEvidence(items: unknown): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  return items.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const record = { ...(item as Record<string, unknown>) };
    if (
      record.nutrition_facts !== undefined &&
      record.nutrition_estimate !== undefined &&
      structurallyEqual(record.nutrition_facts, record.nutrition_estimate)
    ) {
      delete record.nutrition_facts;
    }
    if (Array.isArray(record.ingredients)) {
      record.ingredients = normalizeDuplicateNutritionEvidence(record.ingredients);
    }
    return record;
  });
}

function normalizeCredibleEstimatesAsFacts(items: unknown): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  return items.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const record = { ...(item as Record<string, unknown>) };
    const estimate = record.nutrition_estimate;
    if (
      record.nutrition_facts === undefined &&
      estimate !== null &&
      typeof estimate === "object" &&
      !Array.isArray(estimate)
    ) {
      const sourceGrade = (estimate as Record<string, unknown>).source_grade;
      if (sourceGrade === "A" || sourceGrade === "B") {
        record.nutrition_facts = estimate;
        delete record.nutrition_estimate;
      }
    }
    if (Array.isArray(record.ingredients)) {
      record.ingredients = normalizeCredibleEstimatesAsFacts(record.ingredients);
    }
    return record;
  });
}

const APPROXIMATE_MEASURE_MARKERS = [
  "约",
  "大约",
  "大概",
  "估计",
  "估算",
  "差不多",
  "左右",
  "可能",
];

function sourceDeclaresExactConsumedMeasure(
  sourceText: unknown,
  value: number | string,
  measureField: string,
): boolean {
  if (typeof sourceText !== "string") {
    return false;
  }
  const expression = measureField === "consumed_weight_g"
    ? /(\d+(?:\.\d+)?)\s*(?:克|g)/gi
    : /(\d+(?:\.\d+)?)\s*(?:毫升|ml)/gi;
  for (const match of sourceText.matchAll(expression)) {
    const start = match.index ?? 0;
    const nearby = sourceText.slice(
      Math.max(0, start - 8),
      start + match[0].length + 4,
    );
    if (APPROXIMATE_MEASURE_MARKERS.some((marker) => nearby.includes(marker))) {
      continue;
    }
    if (decimalQuantitiesEqual(match[1]!, value)) {
      return true;
    }
  }
  return false;
}

function exactPortionMeasure(
  portionExpression: unknown,
  previousValue: number | string,
  exactValue: number | string,
  measureField: string,
): unknown {
  if (typeof portionExpression !== "string") {
    return portionExpression;
  }
  const pattern = measureField === "consumed_weight_g"
    ? /(大约|大概|约|估计|估算|差不多|可能)?\s*(\d+(?:\.\d+)?)\s*(克|g)(左右)?(?:\s*[（(](估算|估计)[）)])?/gi
    : /(大约|大概|约|估计|估算|差不多|可能)?\s*(\d+(?:\.\d+)?)\s*(毫升|ml)(左右)?(?:\s*[（(](估算|估计)[）)])?/gi;
  return portionExpression.replace(
    pattern,
    (matched, prefix, numeric, unit, suffix, parenthetical) => {
      const isPrevious = decimalQuantitiesEqual(numeric, previousValue);
      const isExact = decimalQuantitiesEqual(numeric, exactValue);
      const markedApproximate = Boolean(prefix || suffix || parenthetical);
      if (!isPrevious && !isExact) {
        return matched;
      }
      if (!markedApproximate && isExact) {
        return matched;
      }
      return `${String(exactValue)}${unit}`;
    },
  );
}

function canonicalExactPortionExpression(
  portionExpression: unknown,
  exactValue: number | string,
  measureField: string,
  sourceText: unknown,
): unknown {
  if (
    typeof portionExpression !== "string" ||
    !sourceDeclaresExactConsumedMeasure(sourceText, exactValue, measureField)
  ) {
    return portionExpression;
  }
  const measurePattern = measureField === "consumed_weight_g"
    ? /(?:大约|大概|约|估计|估算|差不多|可能)?\s*\d+(?:\.\d+)?\s*(克|g)(?:左右)?(?:\s*[（(](?:估算|估计)[）)])?/giu
    : /(?:大约|大概|约|估计|估算|差不多|可能)?\s*\d+(?:\.\d+)?\s*(毫升|ml)(?:左右)?(?:\s*[（(](?:估算|估计)[）)])?/giu;
  let expression = portionExpression.replace(
    measurePattern,
    (_matched, unit: string) => `${String(exactValue)}${unit}`,
  );
  expression = expression
    .replace(/(?:大约|大概|约|估计|估算|差不多|可能|左右)/gu, "")
    .replace(/[（(]\s*[）)]/gu, "")
    .replace(/\s+/gu, " ")
    .trim();

  const source = typeof sourceText === "string" ? sourceText : "";
  const compactSource = source.replace(/\s+/gu, "");
  const vagueOnly = /^(?:一点|一些|少许|几口|几粒|几颗|一小把|小半碗|半碗)$/u;
  const vaguePrefix = /^(?:一点|一些|少许|几口|几粒|几颗|一小把)(?:\s*[，,、])?\s*/u;
  const granularCount = /(\d+(?:\.\d+)?)\s*(粒|颗|口|把)/gu;
  const parts = expression
    .split("｜")
    .map((part) => part.trim())
    .map((part) => part.replace(granularCount, (matched) =>
      compactSource.includes(matched.replace(/\s+/gu, "")) ? matched : ""))
    .map((part) => part.replace(vaguePrefix, ""))
    .map((part) => part.replace(/\s+/gu, " ").trim())
    .filter((part) => part !== "" && !vagueOnly.test(part));
  const canonicalUnit = measureField === "consumed_weight_g" ? "克" : "ml";
  if (parts.length === 0) {
    return `${String(exactValue)}${canonicalUnit}`;
  }
  if (!parts.some((part) => new RegExp(`${String(exactValue)}\\s*(?:${canonicalUnit}|${measureField === "consumed_weight_g" ? "g" : "毫升"})`, "iu").test(part))) {
    parts.push(`${String(exactValue)}${canonicalUnit}`);
  }
  return [...new Set(parts)].join("｜");
}

function reconcileEstimatedConsumedMeasures(
  items: unknown,
  dropStaleEstimate: boolean,
  sourceText: unknown,
): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  return items.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const record = { ...(item as Record<string, unknown>) };
    const estimate = record.quantity_estimate;
    if (
      estimate !== null &&
      typeof estimate === "object" &&
      !Array.isArray(estimate)
    ) {
      const estimateRecord = estimate as Record<string, unknown>;
      const unit = estimateRecord.unit;
      const measureField = unit === "g" || unit === "克"
        ? "consumed_weight_g"
        : unit === "ml" || unit === "毫升"
        ? "consumed_volume_ml"
        : undefined;
      const suggested = estimateRecord.suggested;
      if (
        measureField !== undefined &&
        (typeof suggested === "number" || typeof suggested === "string")
      ) {
        const current = record[measureField];
        if (current === undefined || current === null) {
          record[measureField] = suggested;
        } else if (
          dropStaleEstimate &&
          (typeof current === "number" || typeof current === "string") &&
          !decimalQuantitiesEqual(current, suggested) &&
          sourceDeclaresExactConsumedMeasure(sourceText, current, measureField)
        ) {
          record.portion_expression = canonicalExactPortionExpression(
            exactPortionMeasure(
              record.portion_expression,
              suggested,
              current,
              measureField,
            ),
            current,
            measureField,
            sourceText,
          );
          delete record.quantity_estimate;
        }
      }
    }
    for (const measureField of [
      "consumed_weight_g",
      "consumed_volume_ml",
    ] as const) {
      const current = record[measureField];
      if (
        dropStaleEstimate &&
        (typeof current === "number" || typeof current === "string") &&
        sourceDeclaresExactConsumedMeasure(sourceText, current, measureField)
      ) {
        record.portion_expression = canonicalExactPortionExpression(
          exactPortionMeasure(
            record.portion_expression,
            current,
            current,
            measureField,
          ),
          current,
          measureField,
          sourceText,
        );
        const activeEstimate = record.quantity_estimate;
        if (
          activeEstimate !== null &&
          typeof activeEstimate === "object" &&
          !Array.isArray(activeEstimate)
        ) {
          const estimateUnit = (activeEstimate as Record<string, unknown>).unit;
          const estimateMeasureField = estimateUnit === "g" || estimateUnit === "克"
            ? "consumed_weight_g"
            : estimateUnit === "ml" || estimateUnit === "毫升"
            ? "consumed_volume_ml"
            : undefined;
          if (estimateMeasureField === measureField) {
            delete record.quantity_estimate;
          }
        }
      }
    }
    if (Array.isArray(record.ingredients)) {
      record.ingredients = reconcileEstimatedConsumedMeasures(
        record.ingredients,
        dropStaleEstimate,
        sourceText,
      );
    }
    return record;
  });
}

const EXACT_MASS_FACTORS_TO_GRAMS: Readonly<Record<string, number>> = {
  g: 1,
  gram: 1,
  grams: 1,
  克: 1,
  kg: 1000,
  kilogram: 1000,
  kilograms: 1000,
  千克: 1000,
  公斤: 1000,
};

const EXACT_VOLUME_FACTORS_TO_ML: Readonly<Record<string, number>> = {
  ml: 1,
  milliliter: 1,
  milliliters: 1,
  毫升: 1,
  l: 1000,
  liter: 1000,
  liters: 1000,
  litre: 1000,
  litres: 1000,
  升: 1000,
};

type ExactPackageConversion = {
  classifier: string;
  dimension: "weight" | "volume";
  baseQuantity: string;
};

const EXACT_PACKAGE_CONVERSION_PATTERN =
  /(?:每|一)\s*(?<classifier>盒|瓶|袋|包|杯|罐|支|根|个|片|块|枚|颗)\s*(?:就是|是|=|等于|装(?:有)?|有|为)?\s*(?<quantity>\d+(?:\.\d+)?)\s*(?<physicalUnit>毫升|ml|升|l|克|g|千克|公斤|kg)/giu;

function exactPackageConversions(sourceText: unknown): ExactPackageConversion[] {
  if (typeof sourceText !== "string" || sourceText.trim() === "") {
    return [];
  }
  const conversions: ExactPackageConversion[] = [];
  for (const match of sourceText.matchAll(EXACT_PACKAGE_CONVERSION_PATTERN)) {
    const classifier = match.groups?.classifier;
    const quantity = match.groups?.quantity;
    const physicalUnit = match.groups?.physicalUnit?.toLowerCase();
    if (
      classifier === undefined || quantity === undefined ||
      physicalUnit === undefined || !isPositiveDecimalQuantity(quantity)
    ) {
      continue;
    }
    const weightFactor = EXACT_MASS_FACTORS_TO_GRAMS[physicalUnit];
    const volumeFactor = EXACT_VOLUME_FACTORS_TO_ML[physicalUnit];
    if (weightFactor !== undefined) {
      conversions.push({
        classifier,
        dimension: "weight",
        baseQuantity: weightFactor === 1
          ? quantity
          : String(multiplyDecimalQuantities(quantity, weightFactor)),
      });
    } else if (volumeFactor !== undefined) {
      conversions.push({
        classifier,
        dimension: "volume",
        baseQuantity: volumeFactor === 1
          ? quantity
          : String(multiplyDecimalQuantities(quantity, volumeFactor)),
      });
    }
  }
  return conversions;
}

function uniqueCompatiblePackageQuantity(
  conversions: ReadonlyArray<ExactPackageConversion>,
  classifier: string,
  dimension: "weight" | "volume",
): string | undefined {
  const quantities = new Set(
    conversions
      .filter((entry) =>
        entry.classifier === classifier && entry.dimension === dimension
      )
      .map((entry) => entry.baseQuantity),
  );
  return quantities.size === 1 ? quantities.values().next().value : undefined;
}

function inferDirectNutritionConsumedMeasures(
  items: unknown,
  sourceText?: unknown,
): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  const packageConversions = exactPackageConversions(sourceText);
  return items.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const record = { ...(item as Record<string, unknown>) };
    const basis = record.nutrition_basis;
    const amount = record.amount;
    const unit = typeof record.unit === "string"
      ? record.unit.trim().toLowerCase()
      : undefined;
    const measureField = basis === "per_100g"
      ? "consumed_weight_g"
      : basis === "per_100ml"
      ? "consumed_volume_ml"
      : undefined;
    const factors = basis === "per_100g"
      ? EXACT_MASS_FACTORS_TO_GRAMS
      : basis === "per_100ml"
      ? EXACT_VOLUME_FACTORS_TO_ML
      : undefined;
    if (
      measureField !== undefined &&
      factors !== undefined &&
      (record[measureField] === undefined || record[measureField] === null) &&
      (typeof amount === "number" || typeof amount === "string") &&
      unit !== undefined &&
      isPositiveDecimalQuantity(amount) &&
      factors[unit] !== undefined
    ) {
      const factor = factors[unit]!;
      record[measureField] = factor === 1
        ? amount
        : multiplyDecimalQuantities(amount, factor);
    }
    if (
      measureField !== undefined &&
      (record[measureField] === undefined || record[measureField] === null) &&
      (typeof amount === "number" || typeof amount === "string") &&
      unit !== undefined &&
      isPositiveDecimalQuantity(amount)
    ) {
      const dimension = basis === "per_100g" ? "weight" : "volume";
      const perPackage = uniqueCompatiblePackageQuantity(
        packageConversions,
        unit,
        dimension,
      );
      if (perPackage !== undefined) {
        record[measureField] = multiplyDecimalQuantities(amount, perPackage);
      }
    }
    if (Array.isArray(record.ingredients)) {
      record.ingredients = inferDirectNutritionConsumedMeasures(
        record.ingredients,
        sourceText,
      );
    }
    return record;
  });
}

function reconcileMealToolParamsWithTurnSource(
  params: Record<string, unknown>,
  sourceText: unknown,
): Record<string, unknown> {
  const action = params.action;
  if (action === "record" || action === "preview_record") {
    const reconciled = reconcileEstimatedConsumedMeasures(
      params.items,
      true,
      sourceText,
    );
    return reconciled === params.items ? params : { ...params, items: reconciled };
  }
  if (
    action === "update" &&
    params.draft !== null &&
    typeof params.draft === "object" &&
    !Array.isArray(params.draft)
  ) {
    const draft = params.draft as Record<string, unknown>;
    const reconciled = reconcileEstimatedConsumedMeasures(
      draft.items,
      true,
      sourceText,
    );
    return reconciled === draft.items
      ? params
      : { ...params, draft: { ...draft, items: reconciled } };
  }
  return params;
}

const MEAL_DRAFT_FIELDS = [
  "occurred_at",
  "meal_type",
  "source_text",
  "location_type",
  "items",
] as const;

function normalizeFlattenedMealUpdate(
  payload: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const flattened = MEAL_DRAFT_FIELDS.filter(
    (field) => payload[field] !== undefined,
  );
  if (flattened.length === 0) {
    return undefined;
  }
  if (payload.draft !== undefined) {
    return invalidMealInputError(
      "draft",
      "incompatible",
      "meal facts either inside draft or flattened once, never both",
    );
  }
  const draft: Record<string, unknown> = {};
  for (const field of flattened) {
    draft[field] = payload[field];
    delete payload[field];
  }
  payload.draft = draft;
  return undefined;
}

function firstMealQuantityEstimateError(
  items: unknown,
  prefix: string,
): MealInputError | undefined {
  if (!Array.isArray(items)) {
    return undefined;
  }
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const itemPath = `${prefix}[${index}]`;
    const rawEstimate = record.quantity_estimate;
    if (
      rawEstimate !== undefined &&
      rawEstimate !== null &&
      typeof rawEstimate === "object" &&
      !Array.isArray(rawEstimate)
    ) {
      const estimate = rawEstimate as Record<string, unknown>;
      const suggested = estimate.suggested;
      const lower = estimate.lower;
      const upper = estimate.upper;
      try {
        if (
          (typeof suggested !== "number" && typeof suggested !== "string") ||
          (typeof lower !== "number" && typeof lower !== "string") ||
          (typeof upper !== "number" && typeof upper !== "string") ||
          compareDecimalQuantities(lower, suggested) > 0 ||
          compareDecimalQuantities(suggested, upper) > 0
        ) {
          return {
            field: `${itemPath}.quantity_estimate`,
            reason: "incompatible",
            expected: "0 < lower <= suggested <= upper",
          };
        }
        const matchesDisplayQuantity =
          (typeof record.amount === "number" || typeof record.amount === "string") &&
          decimalQuantitiesEqual(suggested, record.amount) &&
          estimate.unit === record.unit;
        const consumedMeasure = estimate.unit === "g" || estimate.unit === "克"
          ? record.consumed_weight_g
          : estimate.unit === "ml" || estimate.unit === "毫升"
          ? record.consumed_volume_ml
          : undefined;
        const matchesConsumedMeasure =
          (typeof consumedMeasure === "number" || typeof consumedMeasure === "string") &&
          decimalQuantitiesEqual(suggested, consumedMeasure);
        if (!matchesDisplayQuantity && !matchesConsumedMeasure) {
          return {
            field: `${itemPath}.quantity_estimate`,
            reason: "incompatible",
            expected: "suggested/unit matching the item amount/unit or consumed weight/volume",
          };
        }
      } catch {
        return {
          field: `${itemPath}.quantity_estimate`,
          reason: "incompatible",
          expected: "positive finite decimal estimate bounds",
        };
      }
    }
    const nested = firstMealQuantityEstimateError(
      record.ingredients,
      `${itemPath}.ingredients`,
    );
    if (nested !== undefined) {
      return nested;
    }
  }
  return undefined;
}

function attachRegisteredQuantityEvidence(items: unknown): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  return items.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return item;
    }
    const record = { ...(item as Record<string, unknown>) };
    const estimate = record.quantity_estimate;
    if (
      estimate !== null &&
      typeof estimate === "object" &&
      !Array.isArray(estimate)
    ) {
      const estimateRecord = estimate as Record<string, unknown>;
      const isRedundantExactDisplayQuantity = (() => {
        const { suggested, lower, upper, unit } = estimateRecord;
        const amount = record.amount;
        if (
          (typeof suggested !== "number" && typeof suggested !== "string") ||
          (typeof lower !== "number" && typeof lower !== "string") ||
          (typeof upper !== "number" && typeof upper !== "string") ||
          (typeof amount !== "number" && typeof amount !== "string") ||
          typeof unit !== "string" ||
          unit !== record.unit
        ) {
          return false;
        }
        try {
          return decimalQuantitiesEqual(lower, suggested) &&
            decimalQuantitiesEqual(suggested, upper) &&
            decimalQuantitiesEqual(suggested, amount);
        } catch {
          return false;
        }
      })();
      if (isRedundantExactDisplayQuantity) {
        delete record.quantity_estimate;
      } else {
        const isStandardCountWeight =
          (estimateRecord.unit === "g" || estimateRecord.unit === "克") &&
          (typeof record.consumed_weight_g === "number" ||
            typeof record.consumed_weight_g === "string") &&
          (typeof estimateRecord.suggested === "number" ||
            typeof estimateRecord.suggested === "string") &&
          decimalQuantitiesEqual(
            estimateRecord.suggested,
            record.consumed_weight_g,
          ) &&
          (record.unit !== estimateRecord.unit ||
            (typeof record.amount !== "number" &&
              typeof record.amount !== "string") ||
            !decimalQuantitiesEqual(estimateRecord.suggested, record.amount));
        record.quantity_estimate = {
          ...estimateRecord,
          evidence_type: estimateRecord.evidence_type ?? (
            isStandardCountWeight ? "standard_portion" : "household_range"
          ),
          policy_key: estimateRecord.policy_key ?? (
            isStandardCountWeight
              ? "portion.standard_count_weight"
              : "portion.generic.small_amount"
          ),
        };
      }
    }
    if (Array.isArray(record.ingredients)) {
      record.ingredients = attachRegisteredQuantityEvidence(record.ingredients);
    }
    return record;
  });
}

const EXPIRY_EXPECTED =
  "one ISO 8601 calendar expiry_date or timezone-aware expires_at";

function invalidExpiryInputError(
  field: string,
  reason: "required" | "invalid_format",
): Record<string, unknown> {
  return {
    code: "INVALID_INPUT",
    message: "The request is invalid",
    field,
    reason,
    expected: EXPIRY_EXPECTED,
    retryable: true,
  };
}

function invalidDateTimeInputError(
  field: string,
  reason: "required" | "invalid_format",
): Record<string, unknown> {
  return {
    code: "INVALID_INPUT",
    message: "The request is invalid",
    field,
    reason,
    expected: "one timezone-aware ISO 8601 date-time",
    retryable: true,
  };
}

function expiryInputReason(
  value: unknown,
): "required" | "invalid_format" | undefined {
  if (value === undefined || value === null) {
    return "required";
  }
  if (typeof value !== "string" || value.trim() === "") {
    return typeof value === "string" ? "required" : "invalid_format";
  }
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/,
  );
  if (match === null) {
    return "invalid_format";
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[7] ?? "0");
  const offsetMinute = Number(match[8] ?? "0");
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31,
    leap ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth[month - 1]! ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    offsetHour > 23 ||
    offsetMinute > 59 ||
    Number.isNaN(Date.parse(value))
  ) {
    return "invalid_format";
  }
  return undefined;
}

function calendarDateInputReason(
  value: unknown,
): "required" | "invalid_format" | undefined {
  if (value === undefined || value === null) {
    return "required";
  }
  if (typeof value !== "string" || value.trim() === "") {
    return typeof value === "string" ? "required" : "invalid_format";
  }
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match === null) {
    return "invalid_format";
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31, leap ? 29 : 28, 31, 30, 31, 30,
    31, 31, 30, 31, 30, 31,
  ];
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth[month - 1]!
  ) {
    return "invalid_format";
  }
  return undefined;
}

function expiryChoiceError(
  value: Record<string, unknown>,
  prefix = "",
): { field: string; reason: "required" | "invalid_format" } | undefined {
  const hasDate = value.expiry_date !== undefined && value.expiry_date !== null;
  const hasTimestamp = value.expires_at !== undefined && value.expires_at !== null;
  if (hasDate === hasTimestamp) {
    return {
      field: `${prefix}${hasDate ? "expiry_date" : "expires_at"}`,
      reason: hasDate ? "invalid_format" : "required",
    };
  }
  const field = hasDate ? "expiry_date" : "expires_at";
  const reason = hasDate
    ? calendarDateInputReason(value.expiry_date)
    : expiryInputReason(value.expires_at);
  return reason === undefined
    ? undefined
    : { field: `${prefix}${field}`, reason };
}

function exactDecimalStringError(field: string): Record<string, unknown> {
  return {
    code: "INVALID_INPUT",
    message: "The request is invalid",
    field,
    reason: "not_representable",
    expected:
      "an exact decimal string for values beyond JavaScript's safe numeric precision",
    retryable: true,
  };
}

function preservesDecimalIntentAsJsonNumber(value: number): boolean {
  if (!Number.isFinite(value)) {
    return false;
  }
  if (Number.isInteger(value)) {
    return Number.isSafeInteger(value);
  }
  const { coefficient } = parseDecimalQuantity(value);
  return coefficient.toString().replace("-", "").length <= 15;
}

function firstLeftoverExpiryError(
  items: unknown,
  prefix: string,
): { field: string; reason: "required" | "invalid_format" } | undefined {
  if (!Array.isArray(items)) {
    return undefined;
  }
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const itemPath = `${prefix}[${index}]`;
    const leftover = record.leftover;
    if (
      leftover !== null &&
      typeof leftover === "object" &&
      !Array.isArray(leftover)
    ) {
      const error = expiryChoiceError(
        leftover as Record<string, unknown>,
        `${itemPath}.leftover.`,
      );
      if (error !== undefined) {
        return error;
      }
    }
    const nested = firstLeftoverExpiryError(
      record.ingredients,
      `${itemPath}.ingredients`,
    );
    if (nested !== undefined) {
      return nested;
    }
  }
  return undefined;
}

export function normalizeToolPayload(
  domain: string,
  action: unknown,
  payload: Record<string, unknown>,
  requestContext: Record<string, unknown>,
): PantryPackageNormalization {
  const normalized = { ...payload };
  if (domain === "meal") {
    if (action === "update" || action === "delete") {
      const selector = normalized.selector;
      if (
        selector !== null &&
        typeof selector === "object" &&
        !Array.isArray(selector)
      ) {
        const occurredAtReason = expiryInputReason(
          (selector as Record<string, unknown>).occurred_at,
        );
        if (occurredAtReason !== undefined) {
          return {
            payload: normalized,
            error: invalidDateTimeInputError(
              "selector.occurred_at",
              occurredAtReason,
            ),
          };
        }
      }
    }
    if (action === "update") {
      const flattenedError = normalizeFlattenedMealUpdate(normalized);
      if (flattenedError !== undefined) {
        return { payload: normalized, error: flattenedError };
      }
    }
    const enumNormalization = normalizeMealEnumFields(normalized, action);
    if (enumNormalization.error !== undefined) {
      return { payload: normalized, error: enumNormalization.error };
    }
    if (action === "commit_record") {
      normalized.confirmed = true;
    }
    let items: unknown;
    let prefix = "items";
    let expiryItems: unknown;
    let expiryPrefix = "items";
    if (action === "record" || action === "preview_record") {
      items = normalized.items;
      expiryItems = items;
    } else if (action === "record_cooking") {
      const dish = normalized.dish;
      if (dish !== null && typeof dish === "object" && !Array.isArray(dish)) {
        items = (dish as Record<string, unknown>).ingredients;
        prefix = "dish.ingredients";
        expiryItems = [dish];
        expiryPrefix = "dish";
      }
    } else if (
      action === "update" &&
      normalized.draft !== null &&
      typeof normalized.draft === "object" &&
      !Array.isArray(normalized.draft)
    ) {
      const draft = normalized.draft as Record<string, unknown>;
      if (Array.isArray(draft.items)) {
        items = draft.items;
        prefix = "draft.items";
        expiryItems = items;
        expiryPrefix = prefix;
      } else if (
        draft.dish !== null &&
        typeof draft.dish === "object" &&
        !Array.isArray(draft.dish)
      ) {
        const dish = draft.dish as Record<string, unknown>;
        items = dish.ingredients;
        prefix = "draft.dish.ingredients";
        expiryItems = [dish];
        expiryPrefix = "draft.dish";
      }
    }
    const updateSourceText = action === "update" &&
        normalized.draft !== null &&
        typeof normalized.draft === "object" &&
        !Array.isArray(normalized.draft)
      ? (normalized.draft as Record<string, unknown>).source_text
      : undefined;
    const normalizationSourceText = action === "update"
      ? updateSourceText
      : normalized.source_text;
    const normalizedItems = inferDirectNutritionConsumedMeasures(
      reconcileEstimatedConsumedMeasures(
        normalizeCredibleEstimatesAsFacts(
          normalizeDuplicateNutritionEvidence(items),
        ),
        action === "update",
        updateSourceText,
      ),
      normalizationSourceText,
    );
    if (action === "record" || action === "preview_record") {
      normalized.items = normalizedItems;
      items = normalizedItems;
    } else if (
      action === "update" &&
      normalized.draft !== null &&
      typeof normalized.draft === "object" &&
      !Array.isArray(normalized.draft)
    ) {
      const draft = normalized.draft as Record<string, unknown>;
      if (Array.isArray(draft.items)) {
        normalized.draft = { ...draft, items: normalizedItems };
        items = normalizedItems;
      }
    }
    const quantityEstimateError = firstMealQuantityEstimateError(items, prefix);
    if (quantityEstimateError !== undefined) {
      return {
        payload: normalized,
        error: invalidMealInputError(
          quantityEstimateError.field,
          quantityEstimateError.reason,
          quantityEstimateError.expected,
        ),
      };
    }
    const nutritionError = firstMealNutritionError(items, prefix);
    if (nutritionError !== undefined) {
      return {
        payload: normalized,
        error: invalidMealInputError(
          nutritionError.field,
          nutritionError.reason,
          nutritionError.expected,
        ),
      };
    }
    const expiryError = firstLeftoverExpiryError(expiryItems, expiryPrefix);
    if (expiryError !== undefined) {
      return {
        payload: normalized,
        error: invalidExpiryInputError(
          expiryError.field,
          expiryError.reason,
        ),
      };
    }
    if (action === "record" || action === "preview_record") {
      normalized.items = attachRegisteredQuantityEvidence(normalized.items);
    } else if (action === "record_cooking") {
      const dish = normalized.dish;
      if (dish !== null && typeof dish === "object" && !Array.isArray(dish)) {
        normalized.dish = {
          ...(dish as Record<string, unknown>),
          ingredients: attachRegisteredQuantityEvidence(
            (dish as Record<string, unknown>).ingredients,
          ),
        };
      }
    } else if (
      action === "update" &&
      normalized.draft !== null &&
      typeof normalized.draft === "object" &&
      !Array.isArray(normalized.draft)
    ) {
      const draft = normalized.draft as Record<string, unknown>;
      if (Array.isArray(draft.items)) {
        normalized.draft = {
          ...draft,
          items: attachRegisteredQuantityEvidence(draft.items),
        };
      } else if (
        draft.dish !== null &&
        typeof draft.dish === "object" &&
        !Array.isArray(draft.dish)
      ) {
        const dish = draft.dish as Record<string, unknown>;
        normalized.draft = {
          ...draft,
          dish: {
            ...dish,
            ingredients: attachRegisteredQuantityEvidence(dish.ingredients),
          },
        };
      }
    }
    return {
      payload: normalized,
    };
  }
  if (domain === "water" && action === "delete") {
    if (normalized.source_text === undefined) {
      normalized.source_text = "OpenClaw water record deletion";
    }
    return { payload: normalized };
  }
  if (domain !== "pantry") {
    return { payload: normalized };
  }
  if (action === "commit_add") {
    return {
      payload: { commit_handle: normalized.commit_handle },
    };
  }
  if (action === "add" || action === "preview_add") {
    const legacyPackageFields = [
      "package_count",
      "quantity_per_package",
      "package_unit",
    ] as const;
    const canonicalPackageFields = [
      "display_quantity",
      "display_unit",
      "base_quantity_per_display_unit",
    ] as const;
    const suppliedLegacyPackageFields = legacyPackageFields.filter(
      (field) => normalized[field] !== undefined,
    );
    const suppliedCanonicalPackageFields = canonicalPackageFields.filter(
      (field) => normalized[field] !== undefined,
    );
    const canonicalPantryUnits = new Set([
      "g", "gram", "grams", "ml", "milliliter", "milliliters",
      "piece", "pieces", "portion", "portions", "pack", "packs",
    ]);
    const naturalDisplayUnit = normalized.unit;
    const naturalDisplayQuantity = normalized.quantity;
    const canonicalBaseUnit = canonicalPantryBaseUnit(naturalDisplayUnit);
    if (canonicalBaseUnit !== undefined) {
      normalized.unit = canonicalBaseUnit;
    }
    const averageUnitWeight = normalized.average_unit_weight_g;
    const hasOnlyConvertibleWeightMetadata =
      normalized.total_weight_g === undefined &&
      normalized.weight_basis === undefined &&
      normalized.weight_source === undefined &&
      normalized.weight_confidence === undefined;
    if (
      suppliedLegacyPackageFields.length === 0 &&
      suppliedCanonicalPackageFields.length === 0 &&
      canonicalBaseUnit === undefined &&
      typeof naturalDisplayUnit === "string" &&
      !canonicalPantryUnits.has(naturalDisplayUnit.trim().toLowerCase()) &&
      (typeof naturalDisplayQuantity === "number" ||
        typeof naturalDisplayQuantity === "string") &&
      (typeof averageUnitWeight === "number" ||
        typeof averageUnitWeight === "string") &&
      hasOnlyConvertibleWeightMetadata
    ) {
      normalized.display_quantity = naturalDisplayQuantity;
      normalized.display_unit = naturalDisplayUnit;
      normalized.base_quantity_per_display_unit = averageUnitWeight;
      normalized.quantity = multiplyDecimalQuantities(
        naturalDisplayQuantity,
        averageUnitWeight,
      );
      normalized.unit = "g";
      delete normalized.average_unit_weight_g;
      suppliedCanonicalPackageFields.push(...canonicalPackageFields);
    }
    const sourceRelation = explicitPerDisplayRelation(
      normalized.source_text,
      naturalDisplayUnit,
    );
    if (
      suppliedLegacyPackageFields.length === 0 &&
      suppliedCanonicalPackageFields.length === 0 &&
      canonicalBaseUnit === undefined &&
      typeof naturalDisplayUnit === "string" &&
      (typeof naturalDisplayQuantity === "number" ||
        typeof naturalDisplayQuantity === "string") &&
      sourceRelation !== undefined
    ) {
      normalized.display_quantity = naturalDisplayQuantity;
      normalized.display_unit = naturalDisplayUnit;
      normalized.base_quantity_per_display_unit = sourceRelation.factor;
      normalized.quantity = multiplyDecimalQuantities(
        naturalDisplayQuantity,
        sourceRelation.factor,
      );
      normalized.unit = sourceRelation.baseUnit;
      suppliedCanonicalPackageFields.push(...canonicalPackageFields);
    }
    if (
      suppliedLegacyPackageFields.length > 0 &&
      suppliedLegacyPackageFields.length < legacyPackageFields.length
    ) {
      return {
        payload: normalized,
        error: requiredInputError(
          "package_specification",
          "package_count, quantity_per_package, and package_unit together",
        ),
      };
    }
    if (
      suppliedCanonicalPackageFields.length > 0 &&
      suppliedCanonicalPackageFields.length < canonicalPackageFields.length
    ) {
      return {
        payload: normalized,
        error: requiredInputError(
          "package_specification",
          "display_quantity, display_unit, and base_quantity_per_display_unit together",
        ),
      };
    }
    if (
      suppliedLegacyPackageFields.length === legacyPackageFields.length &&
      suppliedCanonicalPackageFields.length === 0
    ) {
      normalized.display_quantity = normalized.package_count;
      normalized.display_unit = "pack";
      normalized.base_quantity_per_display_unit =
        normalized.quantity_per_package;
    }
    const requiredFields = [
      ["food_name", "non-empty text"],
      ["unit", "g, ml, piece, portion, or pack"],
    ] as const;
    const missingRequiredField = requiredFields.find(
      ([field]) => normalized[field] === undefined,
    );
    if (missingRequiredField !== undefined) {
      const [field, expected] = missingRequiredField;
      return {
        payload: normalized,
        error: requiredInputError(field, expected),
      };
    }
    const decimalNumberFields = [
      "quantity",
      "package_count",
      "quantity_per_package",
      "display_quantity",
      "base_quantity_per_display_unit",
    ] as const;
    const inexactNumberField = decimalNumberFields.find((field) => {
      const value = normalized[field];
      return (
        typeof value === "number" &&
        !preservesDecimalIntentAsJsonNumber(value)
      );
    });
    if (inexactNumberField !== undefined) {
      return {
        payload: normalized,
        error: exactDecimalStringError(inexactNumberField),
      };
    }
    const hasCanonicalPackage = canonicalPackageFields.every(
      (field) => normalized[field] !== undefined,
    );
    if (hasCanonicalPackage) {
      const displayQuantity = normalized.display_quantity as number | string;
      const quantityPerDisplayUnit =
        normalized.base_quantity_per_display_unit as
        | number
        | string;
      const exactQuantity = multiplyDecimalQuantities(
        displayQuantity,
        quantityPerDisplayUnit,
      );
      const legacyPackageUnit = normalized.package_unit as
        | "g"
        | "ml"
        | undefined;
      const exactUnit = legacyPackageUnit ?? normalized.unit;
      if (normalized.unit !== exactUnit) {
        return {
          payload: normalized,
          error: invalidMealInputError(
            "unit",
            "incompatible",
            `${exactUnit} from the legacy package_unit`,
          ),
        };
      }
      if (
        normalized.quantity !== undefined &&
        !decimalQuantitiesEqual(
          normalized.quantity as number | string,
          exactQuantity,
        )
      ) {
        return {
          payload: normalized,
          error: invalidMealInputError(
            "quantity",
            "incompatible",
            `${exactQuantity} from display_quantity × base_quantity_per_display_unit`,
          ),
        };
      }
      normalized.quantity = exactQuantity;
      normalized.unit = exactUnit;
    } else if (normalized.quantity === undefined) {
      return {
        payload: normalized,
        error: requiredInputError(
          "quantity",
          "positive number or decimal string",
        ),
      };
    }
    if ("expires_at" in normalized || "expiry_date" in normalized) {
      const expiryError = expiryChoiceError(normalized);
      if (expiryError !== undefined) {
        return {
          payload: normalized,
          error: invalidExpiryInputError(
            expiryError.field,
            expiryError.reason,
          ),
        };
      }
    }
    legacyPackageFields.forEach((field) => delete normalized[field]);
    if (normalized.added_at === undefined) {
      normalized.added_at =
        typeof requestContext.now === "string"
          ? requestContext.now
          : new Date().toISOString();
    }
    if (normalized.source_text === undefined) {
      normalized.source_text = `OpenClaw pantry add: ${normalized.food_name} ${normalized.quantity} ${normalized.unit}`;
    }
    return { payload: normalized };
  }
  if (action === "query") {
    if (
      normalized.normalized_name === undefined &&
      typeof normalized.food_name === "string"
    ) {
      normalized.normalized_name = normalized.food_name;
    }
    delete normalized.food_name;
    if (
      normalized.include_details === true &&
      normalized.normalized_name === undefined
    ) {
      normalized.include_details = false;
    }
  }
  if (action === "preview_update_metadata") {
    if ("expires_at" in normalized || "expiry_date" in normalized) {
      const expiryError = expiryChoiceError(normalized);
      if (expiryError !== undefined) {
        return {
          payload: normalized,
          error: invalidExpiryInputError(
            expiryError.field,
            expiryError.reason,
          ),
        };
      }
    }
    delete normalized.food_name;
    delete normalized.normalized_name;
    if (normalized.source_text === undefined) {
      normalized.source_text = "OpenClaw pantry metadata update";
    }
  }
  if (
    action === "preview_link_nutrition" &&
    normalized.linked_at === undefined
  ) {
    normalized.linked_at =
      typeof requestContext.now === "string"
        ? requestContext.now
        : new Date().toISOString();
  }
  return { payload: normalized };
}

async function executeDomainRequest(
  definition: (typeof domainTools)[number],
  params: Record<string, unknown>,
  config: { dataDir?: string; testRunId?: string },
  runtime: { signal?: AbortSignal; toolCallId: string; sessionKey?: string; sessionId?: string; modelRef?: string },
): Promise<Record<string, unknown>> {
  validateInputLimits(params);
  const { action, context: requestContext = {}, ...payload } = params;
  const payloadRecord = payload as Record<string, unknown>;
  if (definition.domain === "meal" && (action === "record" || action === "preview_record") && (!Array.isArray(payloadRecord.items) || payloadRecord.items.length === 0)) {
    return toToolResult({ ok: false, outcome: "failed", data: {}, error: { code: "INVALID_INPUT", message: "The request is invalid", field: "items", reason: "required", expected: "one or more consumed meal items", retryable: true }, warnings: ["Meal items were omitted; no data was written."], requires_confirmation: false, confirmation_options: [] });
  }
  const normalizedAction = definition.domain === "meal" && action === "preview_record" && payloadRecord.intent === "record" ? "record" : action;
  const normalization = normalizeToolPayload(definition.domain, normalizedAction, payloadRecord, requestContext as Record<string, unknown>);
  if (normalization.error !== undefined) {
    return toToolResult({ ok: false, outcome: "failed", data: {}, error: normalization.error, warnings: [], requires_confirmation: false, confirmation_options: [] });
  }
  const request = resolveWorkflowHandles({
    domain: definition.domain,
    action: normalization.action ?? normalizedAction,
    payload: normalization.payload,
    context: requestContext,
  });
  const result = await callPythonReliably(request, {
    ...(config.dataDir === undefined ? {} : { dataDir: config.dataDir }),
    signal: runtime.signal,
  }, {
    runtimeIdentity: {
      sessionKey: runtime.sessionKey,
      sessionId: runtime.sessionId,
      modelRef: runtime.modelRef,
      testRunId: config.testRunId,
    },
  });
  const toolResult = toToolResult(result.response);
  if (normalization.correctionWarning !== undefined && toolResult.ok === true) {
    const warnings = Array.isArray(toolResult.warnings) ? toolResult.warnings : [];
    toolResult.warnings = [...warnings, normalization.correctionWarning];
  }
  return toolResult;
}

type TurnGuardState = {
  intent: TurnIntent;
  terminalFailures: number;
  sourceText?: string;
  sessionIdentity?: string;
  contextualMealHandle?: string;
  completedInventorySearch?: boolean;
  operationStatusReadCompleted?: boolean;
  mealHistoryReadCompleted?: boolean;
  renderedReceipt?: string;
  replaceFinalWithReceipt?: boolean;
  renderedMealHistoryReply?: string;
  replaceFinalWithMealHistory?: boolean;
  replacementMealPreview?: boolean;
};

type RecentMealTarget = {
  handle: string;
  expiresAt: number;
};

type RecentMealPreview = {
  expiresAt: number;
};

type PendingReceipt = {
  receipt: string;
  expiresAt: number;
};

type PendingTranscriptReply = {
  reply: string;
  expiresAt: number;
};

const MEAL_HISTORY_LIST_QUERY_PATTERN =
  /(?:(?:吃|喝|摄入)(?:了|过|到)?[^。！？?]{0,16}(?:什么|啥|哪些)|(?:什么|啥|哪些)[^。！？?]{0,16}(?:吃|喝|摄入)(?:了|过|到)?)/u;

function mealHistoryListQueryLike(text: string, intent: TurnIntent): boolean {
  return intent.mode === "read_only" && MEAL_HISTORY_LIST_QUERY_PATTERN.test(text);
}

function successfulReadData(result: unknown): Record<string, unknown> | undefined {
  if (result === null || typeof result !== "object" || Array.isArray(result)) {
    return undefined;
  }
  const resultRecord = result as Record<string, unknown>;
  const candidate = resultRecord.details !== null &&
      typeof resultRecord.details === "object" &&
      !Array.isArray(resultRecord.details)
    ? resultRecord.details as Record<string, unknown>
    : resultRecord;
  if (candidate.ok !== true || candidate.outcome !== "read_completed") {
    return undefined;
  }
  const data = candidate.data;
  return data !== null && typeof data === "object" && !Array.isArray(data)
    ? data as Record<string, unknown>
    : undefined;
}

function localMinute(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const match = value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/u);
  return match === null ? undefined : `${match[1]} ${match[2]}`;
}

function mealItemSummary(value: unknown): string | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const item = value as Record<string, unknown>;
  const name = typeof item.raw_name === "string" && item.raw_name.trim() !== ""
    ? item.raw_name.trim()
    : typeof item.normalized_name === "string" && item.normalized_name.trim() !== ""
    ? item.normalized_name.trim()
    : undefined;
  if (name === undefined) {
    return undefined;
  }
  const portion = typeof item.portion_expression === "string" &&
      item.portion_expression.trim() !== ""
    ? item.portion_expression.trim()
    : (typeof item.amount === "number" || typeof item.amount === "string") &&
        typeof item.unit === "string"
    ? `${String(item.amount)}${item.unit}`
    : (typeof item.consumed_weight_g === "number" ||
          typeof item.consumed_weight_g === "string")
    ? `${String(item.consumed_weight_g)}克`
    : (typeof item.consumed_volume_ml === "number" ||
          typeof item.consumed_volume_ml === "string")
    ? `${String(item.consumed_volume_ml)}ml`
    : undefined;
  return portion === undefined ? name : `${name} ${portion}`;
}

function completeMealHistoryReply(result: unknown): string | undefined {
  const data = successfulReadData(result);
  const scope = data?.scope;
  const meals = data?.meals;
  if (
    scope === null || typeof scope !== "object" || Array.isArray(scope) ||
    !Array.isArray(meals)
  ) {
    return undefined;
  }
  const scopeRecord = scope as Record<string, unknown>;
  if (scopeRecord.complete !== true) {
    return undefined;
  }
  const start = localMinute(scopeRecord.start_local);
  const end = localMinute(scopeRecord.end_local);
  const timezone = typeof scopeRecord.timezone === "string" &&
      scopeRecord.timezone.trim() !== ""
    ? scopeRecord.timezone.trim()
    : undefined;
  if (start === undefined || end === undefined || timezone === undefined) {
    return undefined;
  }
  const header = `${start} 至 ${end}（${timezone}）`;
  if (meals.length === 0) {
    return `${header}没有餐食记录。`;
  }
  const lines: string[] = [];
  for (let index = 0; index < meals.length; index += 1) {
    const value = meals[index];
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      return undefined;
    }
    const meal = value as Record<string, unknown>;
    const occurredAt = localMinute(meal.occurred_at_local);
    const items = Array.isArray(meal.items)
      ? meal.items.map(mealItemSummary).filter((item): item is string => item !== undefined)
      : [];
    const fallback = typeof meal.source_text === "string" && meal.source_text.trim() !== ""
      ? meal.source_text.trim()
      : undefined;
    const description = items.length > 0 ? items.join("、") : fallback;
    if (occurredAt === undefined || description === undefined) {
      return undefined;
    }
    const calories = typeof meal.total_calories === "number" ||
        typeof meal.total_calories === "string"
      ? `｜${String(meal.total_calories)} kcal`
      : "";
    lines.push(`${index + 1}. ${occurredAt}｜${description}${calories}`);
  }
  return `${header}共记录 ${meals.length} 笔：\n\n${lines.join("\n")}`;
}

const TURN_GUARD_NAMESPACE = "diet-turn-guard";
const TURN_GUARD_LOCAL_TTL_MS = 30 * 60 * 1000;
const TURN_GUARD_LOCAL_LIMIT = 512;
const RECENT_MEAL_TARGET_TTL_MS = 30 * 60 * 1000;
const RECENT_MEAL_TARGET_LIMIT = 512;
const RECENT_MEAL_PREVIEW_TTL_MS = 30 * 60 * 1000;
const PENDING_RECEIPT_TTL_MS = 5 * 60 * 1000;
const SESSION_STATE_LIMIT = 512;

const SOURCE_TEXT_ACTIONS: Partial<Record<DietDomain, ReadonlySet<string>>> = {
  pantry: new Set([
    "adjust",
    "deduct",
    "discard",
    "open",
    "freeze",
    "thaw",
    "preview_update_metadata",
  ]),
};

function acceptsTurnSourceText(
  domain: DietDomain | undefined,
  action: string,
): boolean {
  return domain !== undefined && SOURCE_TEXT_ACTIONS[domain]?.has(action) === true;
}

function dietToolResultFailed(result: unknown, error: string | undefined): boolean {
  if (typeof error === "string" && error !== "") {
    return true;
  }
  if (result === null || typeof result !== "object" || Array.isArray(result)) {
    return false;
  }
  const record = result as Record<string, unknown>;
  if (record.ok === false) {
    return true;
  }
  const details = record.details;
  return details !== null && typeof details === "object" &&
    !Array.isArray(details) && (details as Record<string, unknown>).ok === false;
}

function committedRenderedReceipt(result: unknown): string | undefined {
  const data = committedWriteData(result);
  if (data === undefined) {
    return undefined;
  }
  const receipt = data.rendered_receipt;
  return typeof receipt === "string" && receipt.length > 0
    ? receipt
    : undefined;
}

function toolResultOutcome(result: unknown): string | undefined {
  if (result === null || typeof result !== "object" || Array.isArray(result)) {
    return undefined;
  }
  const record = result as Record<string, unknown>;
  const candidate = record.details !== null &&
      typeof record.details === "object" &&
      !Array.isArray(record.details)
    ? record.details as Record<string, unknown>
    : record;
  return typeof candidate.outcome === "string" ? candidate.outcome : undefined;
}

function committedWriteData(
  result: unknown,
): Record<string, unknown> | undefined {
  if (result === null || typeof result !== "object" || Array.isArray(result)) {
    return undefined;
  }
  const resultRecord = result as Record<string, unknown>;
  const candidate = resultRecord.details !== null &&
      typeof resultRecord.details === "object" &&
      !Array.isArray(resultRecord.details)
    ? resultRecord.details as Record<string, unknown>
    : resultRecord;
  if (candidate.ok !== true || candidate.outcome !== "write_committed") {
    return undefined;
  }
  const data = candidate.data;
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    return undefined;
  }
  return data as Record<string, unknown>;
}

function committedMealHandle(result: unknown): string | undefined {
  const data = committedWriteData(result);
  const meal = data?.meal;
  if (meal === null || typeof meal !== "object" || Array.isArray(meal)) {
    return undefined;
  }
  const workflow = (meal as Record<string, unknown>).workflow;
  if (
    workflow === null ||
    typeof workflow !== "object" ||
    Array.isArray(workflow)
  ) {
    return undefined;
  }
  const handle = (workflow as Record<string, unknown>).meal_handle;
  return typeof handle === "string" && handle.startsWith("wfh_")
    ? handle
    : undefined;
}

function registerTurnGuardHooks(api: OpenClawPluginApi): void {
  const localStates = new Map<
    string,
    { state: TurnGuardState; expiresAt: number }
  >();
  const recentMealTargets = new Map<string, RecentMealTarget>();
  const recentMealPreviews = new Map<string, RecentMealPreview>();
  const pendingReceipts = new Map<string, PendingReceipt>();
  const pendingTranscriptReplies = new Map<string, PendingTranscriptReply>();

  const sessionIdentity = (context: {
    sessionKey?: unknown;
    sessionId?: unknown;
  }): string | undefined =>
    typeof context.sessionKey === "string" && context.sessionKey !== ""
      ? context.sessionKey
      : typeof context.sessionId === "string" && context.sessionId !== ""
      ? context.sessionId
      : undefined;

  const pruneRecentMealTargets = (now: number): void => {
    for (const [identity, entry] of recentMealTargets) {
      if (entry.expiresAt <= now) {
        recentMealTargets.delete(identity);
      }
    }
    while (recentMealTargets.size > RECENT_MEAL_TARGET_LIMIT) {
      const oldestIdentity = recentMealTargets.keys().next().value;
      if (typeof oldestIdentity !== "string") {
        break;
      }
      recentMealTargets.delete(oldestIdentity);
    }
  };

  const pruneSessionState = (now: number): void => {
    for (const [identity, entry] of recentMealPreviews) {
      if (entry.expiresAt <= now) {
        recentMealPreviews.delete(identity);
      }
    }
    for (const [identity, entry] of pendingReceipts) {
      if (entry.expiresAt <= now) {
        pendingReceipts.delete(identity);
      }
    }
    for (const [identity, entry] of pendingTranscriptReplies) {
      if (entry.expiresAt <= now) {
        pendingTranscriptReplies.delete(identity);
      }
    }
    for (const map of [recentMealPreviews, pendingReceipts, pendingTranscriptReplies]) {
      while (map.size > SESSION_STATE_LIMIT) {
        const oldestIdentity = map.keys().next().value;
        if (typeof oldestIdentity !== "string") {
          break;
        }
        map.delete(oldestIdentity);
      }
    }
  };

  const pruneLocalStates = (now: number): void => {
    for (const [runId, entry] of localStates) {
      if (entry.expiresAt <= now) {
        localStates.delete(runId);
      }
    }
    while (localStates.size > TURN_GUARD_LOCAL_LIMIT) {
      const oldestRunId = localStates.keys().next().value;
      if (typeof oldestRunId !== "string") {
        break;
      }
      localStates.delete(oldestRunId);
    }
  };

  const saveState = (runId: string, state: TurnGuardState): void => {
    const now = Date.now();
    localStates.delete(runId);
    localStates.set(runId, {
      state,
      expiresAt: now + TURN_GUARD_LOCAL_TTL_MS,
    });
    pruneLocalStates(now);
    api.runContext.setRunContext({
      runId,
      namespace: TURN_GUARD_NAMESPACE,
      value: state,
    });
  };

  const loadState = (runId: string): TurnGuardState | undefined => {
    const now = Date.now();
    pruneLocalStates(now);
    const local = localStates.get(runId);
    if (local !== undefined) {
      local.expiresAt = now + TURN_GUARD_LOCAL_TTL_MS;
      return local.state;
    }
    return api.runContext.getRunContext({
      runId,
      namespace: TURN_GUARD_NAMESPACE,
    }) as TurnGuardState | undefined;
  };

  api.on("before_prompt_build", (event, context) => {
    const runId = context.runId;
    if (runId === undefined) {
      return;
    }
    let intent = classifyTurnIntent(event.prompt);
    const identity = sessionIdentity(context);
    const now = Date.now();
    pruneRecentMealTargets(now);
    pruneSessionState(now);
    if (identity !== undefined) {
      pendingReceipts.delete(identity);
      pendingTranscriptReplies.delete(identity);
      if (intent.mode === "read_only") {
        recentMealPreviews.delete(identity);
      }
    }
    const recentMealTarget = identity === undefined
      ? undefined
      : recentMealTargets.get(identity);
    const replacementMealPreview = identity !== undefined &&
      recentMealPreviews.has(identity) &&
      intent.requiresTrustedSessionTarget === true &&
      intent.allowedActions?.includes("update") === true;
    if (replacementMealPreview) {
      intent = {
        mode: "single_domain_write",
        domains: ["meal"],
        writeScope: "domain",
        allowedActions: ["preview_record"],
      };
    }
    const contextualMealHandle = intent.requiresTrustedSessionTarget === true
      ? recentMealTarget?.handle
      : undefined;
    saveState(runId, {
      intent,
      terminalFailures: 0,
      sourceText: event.prompt.trim().slice(0, 1000),
      sessionIdentity: identity,
      contextualMealHandle,
      replaceFinalWithReceipt: !queryLike(event.prompt),
      replaceFinalWithMealHistory: mealHistoryListQueryLike(event.prompt, intent),
      replacementMealPreview,
    });
    if (intent.operationStatusQuery === true) {
      return {
        appendContext:
          "[Private diet routing] This is a recent diet operation status check. Call diet_transaction get_recent once with a small limit. Do not scan meal, pantry, report, files, or another business tool. If the result cannot prove the outcome, say it is uncertain or ask one short clarification. Never replay a write.",
      };
    }
    if (intent.mode === "workflow_confirmation") {
      return {
        appendContext:
          "[Private diet routing] This is a pure confirmation of the unchanged live preview. Use only that visible live preview and its existing commit handle, then call the matching commit action exactly once: diet_meal commit_record or diet_pantry commit_add. Do not call record or add to create a new write, do not rebuild the preview, and do not ask for another equivalent confirmation. If no unchanged live preview and handle are visible in this conversation, make zero writes and ask for the minimum missing fact.",
      };
    }
    const directWriteDecision = classifyDirectWrite(event.prompt);
    const directWriteRouting = directWriteInstruction(directWriteDecision);
    if (
      directWriteRouting !== undefined &&
      (intent.directWrite === true || directWriteDecision.kind === "clarify")
    ) {
      return { appendContext: directWriteRouting };
    }
    if (intent.finalizedSupplementalWrite === true) {
      return {
        appendContext:
          "[Private diet routing] The user supplied final meal data and explicitly authorized recording it. Merge the new exact data with the still-visible completed consumption event and call diet_meal record exactly once. Do not create a replacement preview or ask for confirmation. If the visible prior step matched inventory whose nutrition was unavailable, reuse its unchanged inventory_match_handle with the supplied nutrition facts in this same diet_meal record; pass only label fields the user supplied, omit missing fields, and never invent zero. Do not call diet_pantry or deduct inventory separately. If the completed event or required identity is no longer visible, make zero writes and ask only for that missing fact.",
      };
    }
    if (replacementMealPreview) {
      return {
        appendContext:
          "[Private diet routing] The user changed a fact in the still-visible live meal preview without final write authorization. Merge the new exact fact into that visible proposal and call diet_meal preview_record exactly once to create one replacement preview. Do not query existing meals, do not update a committed meal, do not commit the stale handle, and do not ask the user to repeat the original event. The replacement preview remains zero-business-write and needs one confirmation.",
      };
    }
    if (contextualMealHandle !== undefined) {
      if (intent.allowedActions?.includes("delete") === true) {
        return {
          appendContext:
            "[Private diet routing] A verified same-session current meal target is already bound for this explicit whole-record deletion. Directly call diet_meal delete once with the user's deletion sentence as source_text. Do not query, search, call diet_transaction undo, ask for confirmation, or call another diet tool; the plugin will bind the exact current meal handle.",
        };
      }
      return {
        appendContext:
          "[Private diet routing] A verified same-session meal target is already bound for this immediate correction. Directly call diet_meal update once with the complete corrected items and the user's correction sentence as source_text. Do not query, search, ask for confirmation, or call another diet tool; the plugin will bind the exact handle.",
      };
    }
    if (intent.requiresTrustedSessionTarget === true) {
      return {
        appendContext:
          "[Private diet routing] This contextual correction or deletion has no verified same-session meal target. You may make at most one narrow read to show the smallest matching candidate set, but you must not update or delete any queried candidate in this turn. Ask the user to identify the target; do not guess the newest record.",
      };
    }
  });

  api.on("before_tool_call", (event, context) => {
    if (!Object.values(TOOL_NAMES).includes(event.toolName as never)) {
      return;
    }
    const runId = event.runId ?? context.runId;
    const state = runId === undefined ? undefined : loadState(runId);
    const fallbackIntent: TurnIntent = {
      mode: "ambiguous",
      domains: [],
    };
    let nextParams = event.params;
    let paramsChanged = false;
    if (state?.mealHistoryReadCompleted === true) {
      return {
        block: true,
        blockReason:
          "The complete meal-history range already returned in this run; use that exact result and do not call another diet tool.",
      };
    }
    if (
      state?.sourceText !== undefined &&
      event.toolName === TOOL_NAMES.meal
    ) {
      const reconciled = reconcileMealToolParamsWithTurnSource(
        nextParams,
        state.sourceText,
      );
      if (reconciled !== nextParams) {
        nextParams = reconciled;
        paramsChanged = true;
      }
    }
    if (
      state?.intent.requiresTrustedSessionTarget === true &&
      event.toolName === TOOL_NAMES.meal &&
      (event.params.action === "update" || event.params.action === "delete") &&
      typeof state.contextualMealHandle !== "string"
    ) {
      return {
        block: true,
        blockReason:
          "当前会话没有可验证的最近餐食凭证；查询结果只能作为候选展示，不能在本轮直接升级为修改或删除权限。请展示最少候选并等待用户明确选择。",
      };
    }
    if (
      state?.intent.contextualTarget === true &&
      event.toolName === TOOL_NAMES.meal &&
      (event.params.action === "update" || event.params.action === "delete") &&
      typeof state.contextualMealHandle === "string"
    ) {
      const reboundParams: Record<string, unknown> = {
        ...event.params,
        meal_handle: state.contextualMealHandle,
      };
      delete reboundParams.selector;
      nextParams = reboundParams;
      paramsChanged = true;
    }
    const nextAction = typeof nextParams.action === "string"
      ? nextParams.action
      : "";
    const nextDomain = dietDomainForTool(event.toolName);
    if (
      state?.intent.operationStatusQuery === true &&
      state.operationStatusReadCompleted === true
    ) {
      return {
        block: true,
        blockReason:
          "The recent diet operation status lookup already completed in this run; do not call another diet tool.",
      };
    }
    if (
      state?.sourceText !== undefined &&
      nextParams.source_text === undefined &&
      acceptsTurnSourceText(nextDomain, nextAction)
    ) {
      nextParams = {
        ...nextParams,
        source_text: state.sourceText,
      };
      paramsChanged = true;
    }
    if (
      state?.intent.completedConsumption === true &&
      state.completedInventorySearch === true &&
      event.toolName === TOOL_NAMES.pantry &&
      (nextAction === "search" || nextAction === "query")
    ) {
      return {
        block: true,
        blockReason:
          "本轮已取得自包含库存凭证；请直接把该凭证交给餐食记录，不要再次查询库存。",
      };
    }
    if (
      state?.intent.completedConsumption === true &&
      event.toolName === TOOL_NAMES.pantry &&
      nextAction === "search" &&
      nextParams.nutrition_mode !== "summary" &&
      nextParams.nutrition_mode !== "full"
    ) {
      nextParams = {
        ...nextParams,
        nutrition_mode: "summary",
      };
      paramsChanged = true;
    }
    const decision = authorizeTurnTool(
      state?.intent ?? fallbackIntent,
      event.toolName,
      nextParams,
      state?.terminalFailures ?? 0,
    );
    if (!decision.allowed) {
      return { block: true, blockReason: decision.message };
    }
    if (
      state !== undefined &&
      state.intent.mode === "single_domain_write" &&
      state.intent.domains.length === 0 &&
      !isDietReadOperation(event.toolName, nextParams)
    ) {
      const lockedDomain = dietDomainForTool(event.toolName);
      if (lockedDomain !== undefined) {
        saveState(runId!, {
          ...state,
          intent: {
            ...state.intent,
            domains: [lockedDomain],
          },
        });
      }
    }
    const action = typeof nextParams.action === "string"
      ? nextParams.action
      : "";
    if (
      state?.intent.completedConsumption === true &&
      event.toolName === TOOL_NAMES.meal &&
      (action === "record" || action === "preview_record")
    ) {
      return {
        params: {
          ...nextParams,
          _turn_completed_consumption: true,
        },
      };
    }
    if (paramsChanged) {
      return { params: nextParams };
    }
  });

  api.on("after_tool_call", (event, context) => {
    if (!Object.values(TOOL_NAMES).includes(event.toolName as never)) {
      return;
    }
    const runId = event.runId ?? context.runId;
    if (runId === undefined) {
      return;
    }
    const state = loadState(runId);
    if (state === undefined) {
      return;
    }
    const failed = dietToolResultFailed(event.result, event.error);
    const action = typeof event.params.action === "string"
      ? event.params.action
      : "";
    const receipt = committedRenderedReceipt(event.result);
    const mealHistoryReply = event.toolName === TOOL_NAMES.meal &&
        action === "query" &&
        state.replaceFinalWithMealHistory === true &&
        !failed
      ? completeMealHistoryReply(event.result)
      : undefined;
    const mealHandle = event.toolName === TOOL_NAMES.meal &&
        action !== "query" && action !== "delete"
      ? committedMealHandle(event.result)
      : undefined;
    const identity = sessionIdentity(context) ?? state.sessionIdentity;
    const now = Date.now();
    pruneSessionState(now);
    if (
      identity !== undefined &&
      event.toolName === TOOL_NAMES.meal &&
      action === "preview_record" &&
      !failed &&
      toolResultOutcome(event.result) === "preview_ready"
    ) {
      recentMealPreviews.delete(identity);
      recentMealPreviews.set(identity, {
        expiresAt: now + RECENT_MEAL_PREVIEW_TTL_MS,
      });
    }
    if (
      identity !== undefined &&
      receipt !== undefined &&
      state.replaceFinalWithReceipt === true
    ) {
      pendingReceipts.delete(identity);
      pendingReceipts.set(identity, {
        receipt,
        expiresAt: now + PENDING_RECEIPT_TTL_MS,
      });
    }
    if (identity !== undefined && mealHistoryReply !== undefined) {
      pendingReceipts.delete(identity);
      pendingReceipts.set(identity, {
        receipt: mealHistoryReply,
        expiresAt: now + PENDING_RECEIPT_TTL_MS,
      });
      pendingTranscriptReplies.delete(identity);
      pendingTranscriptReplies.set(identity, {
        reply: mealHistoryReply,
        expiresAt: now + PENDING_RECEIPT_TTL_MS,
      });
    }
    if (
      identity !== undefined &&
      event.toolName === TOOL_NAMES.meal &&
      !failed &&
      receipt !== undefined
    ) {
      recentMealPreviews.delete(identity);
    }
    if (
      event.toolName === TOOL_NAMES.meal &&
      action === "delete" &&
      !failed &&
      identity !== undefined
    ) {
      recentMealTargets.delete(identity);
    }
    if (mealHandle !== undefined && identity !== undefined) {
      const now = Date.now();
      recentMealTargets.delete(identity);
      recentMealTargets.set(identity, {
        handle: mealHandle,
        expiresAt: now + RECENT_MEAL_TARGET_TTL_MS,
      });
      pruneRecentMealTargets(now);
    }
    saveState(runId, {
      ...state,
      terminalFailures: failed
        ? state.terminalFailures + 1
        : state.terminalFailures,
      completedInventorySearch: state.completedInventorySearch === true ||
        (state.intent.completedConsumption === true &&
          event.toolName === TOOL_NAMES.pantry &&
          action === "search" &&
          !failed),
      operationStatusReadCompleted:
        state.operationStatusReadCompleted === true ||
        (state.intent.operationStatusQuery === true &&
          event.toolName === TOOL_NAMES.transaction &&
          action === "get_recent"),
      mealHistoryReadCompleted:
        state.mealHistoryReadCompleted === true || mealHistoryReply !== undefined,
      renderedReceipt: receipt ?? state.renderedReceipt,
      renderedMealHistoryReply:
        mealHistoryReply ?? state.renderedMealHistoryReply,
      replaceFinalWithReceipt:
        state.renderedReceipt !== undefined && receipt === undefined
          ? false
          : state.replaceFinalWithReceipt,
    });
  });

  api.on("before_message_write", (event, context) => {
    const identity = sessionIdentity(event) ?? sessionIdentity(context);
    if (identity === undefined) {
      return;
    }
    const now = Date.now();
    pruneSessionState(now);
    const pending = pendingTranscriptReplies.get(identity);
    if (pending === undefined) {
      return;
    }
    const message = event.message;
    if (
      message === null || typeof message !== "object" || Array.isArray(message)
    ) {
      return;
    }
    const record = message as unknown as Record<string, unknown>;
    if (
      record.role !== "assistant" || record.stopReason !== "stop" ||
      !Array.isArray(record.content) ||
      record.content.some((part) =>
        part !== null && typeof part === "object" && !Array.isArray(part) &&
        (part as Record<string, unknown>).type === "toolCall"
      )
    ) {
      return;
    }
    pendingTranscriptReplies.delete(identity);
    return {
      message: {
        ...message,
        content: [{ type: "text", text: pending.reply }],
      },
    };
  });

  api.on("reply_payload_sending", (event, context) => {
    if (event.kind !== "final") {
      return;
    }
    const runId = event.runId ?? context.runId;
    const state = runId === undefined ? undefined : loadState(runId);
    const identity = sessionIdentity(event) ?? sessionIdentity(context);
    const now = Date.now();
    pruneSessionState(now);
    const pending = identity === undefined ? undefined : pendingReceipts.get(identity);
    const receipt = state?.replaceFinalWithReceipt === true &&
        state.renderedReceipt !== undefined
      ? state.renderedReceipt
      : state?.replaceFinalWithMealHistory === true &&
          state.renderedMealHistoryReply !== undefined
      ? state.renderedMealHistoryReply
      : runId === undefined
      ? pending?.receipt
      : undefined;
    if (receipt === undefined) {
      return;
    }
    if (identity !== undefined) {
      pendingReceipts.delete(identity);
    }
    return {
      payload: {
        ...event.payload,
        text: receipt,
      },
    };
  });
}

const pluginEntry: ReturnType<typeof defineToolPlugin> = defineToolPlugin({
  id: "personal-diet-pantry",
  name: "Personal Diet Pantry",
  description: "Local meal, hydration, body-weight, pantry, reporting, and recovery tools.",
  configSchema: PluginConfigSchema,
  tools: (tool) =>
    domainTools.map((definition) =>
      tool({
        name: definition.name,
        label: definition.label,
        description: definition.description,
        parameters: definition.parameters,
        factory: ({ config, toolContext }: {
          config: { dataDir?: string; testRunId?: string };
          toolContext?: { sessionKey?: string; sessionId?: string; activeModel?: { modelRef?: string; modelId?: string } };
        }) => ({
          name: definition.name,
          label: definition.label,
          description: definition.description,
          parameters: definition.parameters,
          execute: async (toolCallId: string, params: unknown, signal?: AbortSignal) => {
            const details = await executeDomainRequest(definition, params as Record<string, unknown>, config, {
              signal,
              toolCallId,
              sessionKey: toolContext?.sessionKey,
              sessionId: toolContext?.sessionId,
              modelRef: toolContext?.activeModel?.modelRef ?? toolContext?.activeModel?.modelId,
            });
            return { content: [{ type: "text", text: toolTextContent(details) }], details };
          },
        }),
      } as never),
    ),
});

const registerTools = pluginEntry.register;
pluginEntry.register = (api) => {
  registerTools(api);
  registerTurnGuardHooks(api);
};

export default pluginEntry;
