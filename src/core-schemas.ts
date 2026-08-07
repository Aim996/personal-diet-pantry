import { Type, type TProperties, type TSchema } from "typebox";
import { DEFAULT_TOOL_ACTIONS } from "./generated/tool-contracts.js";


const text = (maxLength = 1000) => Type.String({ minLength: 1, maxLength });
const decimal = Type.Union([
  Type.Number({ minimum: 0 }),
  Type.String({ pattern: "^(?:\\d+)(?:\\.\\d+)?$|^\\.\\d+$" }),
]);
const positive = Type.Union([
  Type.Number({ exclusiveMinimum: 0 }),
  Type.String({ pattern: "^(?:0*[1-9]\\d*)(?:\\.\\d+)?$|^0*\\.\\d*[1-9]\\d*$" }),
]);
const handle = Type.String({
  pattern: "^wfh_[A-Za-z0-9_-]+$",
  minLength: 24,
  maxLength: 128,
});
const date = Type.String({ format: "date" });
const dateTime = Type.String({ format: "date-time" });
const localDateTime = Type.String({
  pattern: "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(?::\\d{2})?$",
  maxLength: 32,
});
const unit = text(40);

function strict(properties: TProperties, options: Record<string, unknown> = {}) {
  return Type.Object(properties, { additionalProperties: false, ...options });
}

function branch(
  action: string,
  properties: TProperties,
  options: Record<string, unknown> = {},
) {
  return strict({ action: Type.Literal(action), ...properties }, options);
}

function actionUnion(branches: TSchema[]) {
  return Type.Union(branches);
}

const calendarWindow = strict({
  unit: Type.Union([Type.Literal("day"), Type.Literal("week"), Type.Literal("month")]),
  offset: Type.Optional(Type.Integer({ minimum: -10000, maximum: 10000 })),
  segment: Type.Optional(text(64)),
});
const rollingWindow = strict({
  value: positive,
  unit: Type.Union([Type.Literal("minute"), Type.Literal("hour"), Type.Literal("day"), Type.Literal("week")]),
});
const localRange = strict({ start: localDateTime, end: localDateTime });
const naturalWindow = strict({
  text: Type.String({
    minLength: 1,
    maxLength: 500,
    description: "Copy the user's natural time expression verbatim; the plugin resolves it in the profile timezone.",
  }),
});
const timeFields = {
  occurred_on: Type.Optional(date),
  calendar_window: Type.Optional(calendarWindow),
  rolling_window: Type.Optional(rollingWindow),
  local_range: Type.Optional(localRange),
  natural_window: Type.Optional(naturalWindow),
};

const nutrition = strict({
  calories: Type.Optional(decimal),
  protein: Type.Optional(decimal),
  fat: Type.Optional(decimal),
  carbohydrate: Type.Optional(decimal),
  fiber: Type.Optional(Type.Union([decimal, Type.Null()])),
  sodium: Type.Optional(Type.Union([decimal, Type.Null()])),
  hydration_ml: Type.Optional(Type.Union([decimal, Type.Null()])),
  source: text(500),
  source_grade: Type.Union([
    Type.Literal("A"), Type.Literal("B"), Type.Literal("C"),
    Type.Literal("D"), Type.Literal("unknown"),
  ]),
  uncertainty: Type.Optional(text(500)),
});
const quantityEstimate = strict({
  suggested: positive,
  lower: positive,
  upper: positive,
  unit,
});
const mealItem = strict({
  raw_name: text(200),
  normalized_name: text(200),
  amount: Type.Optional(positive),
  unit: Type.Optional(unit),
  portion_expression: Type.Optional(text(200)),
  quantity_estimate: Type.Optional(quantityEstimate),
  consumed_weight_g: Type.Optional(positive),
  consumed_volume_ml: Type.Optional(positive),
  consumed_servings: Type.Optional(positive),
  raw_weight_g: Type.Optional(positive),
  inventory_deduction_weight_g: Type.Optional(positive),
  edible_ratio: Type.Optional(positive),
  cooking_yield: Type.Optional(positive),
  nutrition_basis: Type.Optional(Type.Union([
    Type.Literal("per_100g"), Type.Literal("per_100ml"),
    Type.Literal("per_serving"), Type.Literal("consumed_total"),
  ])),
  nutrition_dataset_version: Type.Optional(text(120)),
  nutrition_facts: Type.Optional(nutrition),
  nutrition_estimate: Type.Optional(nutrition),
  inventory_match_handle: Type.Optional(handle),
});
const mealFacts = {
  occurred_at: Type.Optional(dateTime),
  meal_type: Type.Optional(text(40)),
  source_text: text(1000),
  location_type: Type.Optional(text(40)),
  items: Type.Array(mealItem, { minItems: 1, maxItems: 30 }),
};
const mealUpdateFacts = {
  occurred_at: Type.Optional(dateTime),
  meal_type: Type.Optional(text(40)),
  source_text: text(1000),
  location_type: Type.Optional(text(40)),
  items: Type.Array(mealItem, { minItems: 1, maxItems: 30 }),
};
const flattenedMealUpdateFacts = {
  occurred_at: Type.Optional(dateTime),
  meal_type: Type.Optional(text(40)),
  source_text: Type.Optional(text(1000)),
  location_type: Type.Optional(text(40)),
  items: Type.Optional(Type.Array(mealItem, { minItems: 1, maxItems: 30 })),
};
const leftover = strict({
  food_name: text(200),
  normalized_name: text(200),
  quantity: positive,
  unit,
  storage_location: text(120),
  expiry_date: Type.Optional(date),
  expires_at: Type.Optional(dateTime),
});
const cookingDish = strict({
  raw_name: text(200),
  normalized_name: text(200),
  unit,
  consumed_quantity: positive,
  ingredients: Type.Array(mealItem, { minItems: 1, maxItems: 30 }),
  leftover: Type.Optional(leftover),
});
const mealTarget = {
  meal_handle: Type.Optional(handle),
  selector: Type.Optional(strict({
    occurred_at: Type.String({ minLength: 1, maxLength: 64 }),
    source_text: text(1000),
  })),
};

export const MealParametersSchema = actionUnion([
  branch("record", mealFacts),
  branch("preview_record", mealFacts),
  branch("commit_record", { commit_handle: handle }),
  branch("query", { ...timeFields, meal_type: Type.Optional(text(40)) }),
  branch("update", {
    ...mealTarget,
    draft: Type.Optional(strict(mealUpdateFacts)),
    ...flattenedMealUpdateFacts,
  }, {
    anyOf: [
      { required: ["draft"] },
      { required: ["items", "source_text"] },
    ],
  }),
  branch("delete", {
    ...mealTarget,
    source_text: text(1000),
  }),
  branch("record_cooking", {
    occurred_at: Type.Optional(dateTime),
    meal_type: text(40),
    source_text: text(1000),
    dish: cookingDish,
  }),
  branch("record_prepared", {
    prepared_food_handle: handle,
    quantity: Type.Optional(positive),
    unit: Type.Optional(unit),
    source_text: text(1000),
    occurred_at: Type.Optional(dateTime),
    meal_type: Type.Optional(text(40)),
  }),
]);

const waterRecordFacts = {
  amount: positive,
  unit: Type.Union([Type.Literal("ml"), Type.Literal("毫升"), Type.Literal("杯"), Type.Literal("瓶")]),
  occurred_at: Type.Optional(dateTime),
  source_text: text(1000),
};
const waterUpdateFacts = {
  ...waterRecordFacts,
  occurred_at: dateTime,
};
export const WaterParametersSchema = actionUnion([
  branch("record", waterRecordFacts),
  branch("query", timeFields),
  branch("update", { record_handle: handle, ...waterUpdateFacts }),
  branch("delete", { record_handle: handle, source_text: Type.Optional(text(1000)) }),
]);

const weightFacts = {
  weight: positive,
  unit: Type.Optional(Type.Union([Type.Literal("kg"), Type.Literal("公斤"), Type.Literal("斤")])),
  status_note: Type.Optional(Type.Union([text(80), Type.Null()])),
};
export const WeightParametersSchema = actionUnion([
  branch("record", weightFacts),
  branch("query", { ...timeFields, limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }),
  branch("update", {
    record_handle: handle,
    weight: Type.Optional(positive),
    unit: Type.Optional(text(20)),
    status_note: Type.Optional(Type.Union([text(80), Type.Null()])),
  }),
  branch("delete", {
    record_handle: Type.Optional(handle),
    commit_handle: Type.Optional(handle),
  }, {
    oneOf: [
      { required: ["record_handle"], not: { required: ["commit_handle"] } },
      { required: ["commit_handle"], not: { required: ["record_handle"] } },
    ],
  }),
]);

const nutritionProfile = strict({
  normalized_name: text(200),
  brand: Type.Optional(text(200)),
  product_key: Type.Optional(text(200)),
  serving_basis: Type.Union([
    Type.Literal("per_100g"), Type.Literal("per_100ml"),
    Type.Literal("per_serving"), Type.Literal("consumed_total"),
  ]),
  nutrition: strict({
    calories_kcal: Type.Optional(Type.Union([decimal, Type.Null()])),
    protein_g: Type.Optional(Type.Union([decimal, Type.Null()])),
    fat_g: Type.Optional(Type.Union([decimal, Type.Null()])),
    carbohydrate_g: Type.Optional(Type.Union([decimal, Type.Null()])),
    fiber_g: Type.Optional(Type.Union([decimal, Type.Null()])),
    sodium_mg: Type.Optional(Type.Union([decimal, Type.Null()])),
    hydration_ml: Type.Optional(Type.Union([decimal, Type.Null()])),
  }),
  source_text: text(1000),
  source_grade: Type.Optional(text(20)),
});
const pantryTarget = { batch_handle: handle };
const pantryAdd = {
  food_name: text(200),
  normalized_name: Type.Optional(text(200)),
  quantity: Type.Optional(positive),
  unit,
  display_quantity: Type.Optional(positive),
  display_unit: Type.Optional(unit),
  base_quantity_per_display_unit: Type.Optional(positive),
  added_at: Type.Optional(dateTime),
  expiry_date: Type.Optional(date),
  expires_at: Type.Optional(dateTime),
  source_text: Type.Optional(text(1000)),
  storage_location: Type.Optional(text(120)),
  average_unit_weight_g: Type.Optional(positive),
  price_minor: Type.Optional(Type.Integer({ minimum: 0 })),
  currency: Type.Optional(text(8)),
  nutrition_profile: Type.Optional(nutritionProfile),
};
export const PantryParametersSchema = actionUnion([
  branch("add", pantryAdd),
  branch("preview_add", pantryAdd),
  branch("commit_add", { commit_handle: handle }),
  branch("query", {
    normalized_name: Type.Optional(text(200)),
    storage_location: Type.Optional(text(120)),
    missing_expiry_only: Type.Optional(Type.Boolean()),
    include_details: Type.Optional(Type.Boolean()),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
    offset: Type.Optional(Type.Integer({ minimum: 0 })),
  }),
  branch("search", {
    search_text: text(200),
    unit: Type.Optional(unit),
    storage_location: Type.Optional(text(120)),
    nutrition_mode: Type.Optional(Type.Union([Type.Literal("none"), Type.Literal("summary"), Type.Literal("full")])),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 5 })),
  }),
  branch("adjust", { ...pantryTarget, quantity: decimal, source_text: text(1000), reason: Type.Optional(text(300)) }),
  branch("deduct", { inventory_match_handle: handle, quantity: positive, unit, source_text: text(1000), reason: Type.Optional(text(300)) }),
  branch("discard", {
    batch_handle: Type.Optional(handle),
    inventory_match_handle: Type.Optional(handle),
    quantity: Type.Optional(positive),
    unit: Type.Optional(unit),
    source_text: text(1000),
    reason: Type.Optional(text(300)),
  }),
  branch("open", { ...pantryTarget, opened_at: Type.Optional(dateTime), source_text: text(1000) }),
  branch("freeze", { ...pantryTarget, frozen_at: Type.Optional(dateTime), source_text: text(1000) }),
  branch("thaw", { ...pantryTarget, thawed_at: Type.Optional(dateTime), source_text: text(1000) }),
  branch("preview_update_metadata", {
    ...pantryTarget,
    average_unit_weight_g: Type.Optional(positive),
    expiry_date: Type.Optional(date),
    expires_at: Type.Optional(dateTime),
    source_text: Type.Optional(text(1000)),
  }),
  branch("commit_update_metadata", { commit_handle: handle }),
  branch("preview_link_nutrition", { ...pantryTarget, nutrition_profile: nutritionProfile }),
  branch("commit_link_nutrition", { commit_handle: handle }),
]);

export const TransactionParametersSchema = actionUnion([
  branch("get_recent", {
    operation: Type.Optional(Type.Union([Type.Literal("undo"), Type.Literal("redo")])),
    operation_type: Type.Optional(text(80)),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
  }),
  branch("undo", { operation_handle: handle }),
  branch("redo", { operation_handle: handle }),
]);

export const ReportParametersSchema = actionUnion([
  branch("progress", { report_date: Type.Optional(date) }),
  branch("expiring_inventory", { report_date: Type.Optional(date), within_days: Type.Optional(Type.Integer({ minimum: 1, maximum: 365 })) }),
  branch("insights", {
    report_date: Type.Optional(date),
    period: Type.Optional(Type.Union([Type.Literal("daily"), Type.Literal("weekly"), Type.Literal("monthly")])),
    within_days: Type.Optional(Type.Integer({ minimum: 1, maximum: 30 })),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
  }),
]);

export const SystemParametersSchema = actionUnion([
  branch("query_goals", {}),
  branch("update_goals", {
    calories_kcal: Type.Integer({ minimum: 1 }),
    protein_g: Type.Integer({ minimum: 1 }),
    fat_g: Type.Integer({ minimum: 1 }),
    carbohydrate_g: Type.Integer({ minimum: 1 }),
    fiber_g: Type.Integer({ minimum: 1 }),
    sodium_mg: Type.Integer({ minimum: 1 }),
    water_ml: Type.Integer({ minimum: 1 }),
    timezone_name: text(80),
    source_text: text(1000),
  }),
  branch("query_preferences", { include_inactive: Type.Optional(Type.Boolean()) }),
  branch("update_preferences", {
    rule_type: text(80),
    subject: text(200),
    outcome: Type.Object({}, { additionalProperties: true, maxProperties: 20 }),
    evidence: Type.Optional(Type.Object({}, { additionalProperties: true, maxProperties: 20 })),
    source_text: text(1000),
  }),
  branch("forget_preference", { rule_type: text(80), subject: text(200), source_text: text(1000) }),
]);

export const CORE_TOOL_ACTIONS = DEFAULT_TOOL_ACTIONS;
