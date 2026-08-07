import {
  Type,
  type TObjectOptions,
  type TProperties,
  type TSchema,
  type TSchemaOptions,
} from "typebox";

const strictObject = <Properties extends TProperties>(
  properties: Properties,
  options: TObjectOptions = {},
) => Type.Object(properties, { additionalProperties: false, ...options });

const MAX_PUBLIC_STRING_LENGTH = 16 * 1024;
const MAX_PUBLIC_COLLECTION_MEMBERS = 1000;
const MAX_TOTAL_ITEMS = MAX_PUBLIC_COLLECTION_MEMBERS;
const MAX_MEAL_ITEMS = 100;
const MAX_INGREDIENT_CHILDREN = 50;

function applyPublicBounds<Schema extends TSchema>(schema: Schema): Schema {
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (typeof value !== "object" || value === null) {
      return;
    }
    const node = value as Record<string, unknown>;
    if (node.type === "string" && node.maxLength === undefined) {
      node.maxLength = MAX_PUBLIC_STRING_LENGTH;
    }
    if (node.type === "array" && node.maxItems === undefined) {
      node.maxItems = MAX_PUBLIC_COLLECTION_MEMBERS;
    }
    if (node.type === "object" && node.maxProperties === undefined) {
      node.maxProperties = MAX_PUBLIC_COLLECTION_MEMBERS;
    }
    Object.values(node).forEach(visit);
  };
  visit(schema);
  return schema;
}

const BoundedJsonValueRef = {
  $ref: "#/$defs/pdpJsonValue",
} as TSchema;

const BoundedJsonValueSchema = Type.Union(
  [
    Type.String({ maxLength: MAX_PUBLIC_STRING_LENGTH }),
    Type.Number(),
    Type.Boolean(),
    Type.Null(),
    Type.Array(BoundedJsonValueRef, {
      maxItems: MAX_PUBLIC_COLLECTION_MEMBERS,
    }),
    Type.Object(
      {},
      {
        maxProperties: MAX_PUBLIC_COLLECTION_MEMBERS,
        propertyNames: Type.String({
          maxLength: MAX_PUBLIC_STRING_LENGTH,
        }),
        additionalProperties: BoundedJsonValueRef,
      },
    ),
  ],
);

const BoundedJsonObjectSchema = Type.Object(
  {},
  {
    maxProperties: MAX_PUBLIC_COLLECTION_MEMBERS,
    propertyNames: Type.String({
      maxLength: MAX_PUBLIC_STRING_LENGTH,
    }),
    additionalProperties: BoundedJsonValueRef,
  },
);

export const DateSchema = Type.String({
  format: "date",
  description: "ISO 8601 calendar date.",
});
export const DateTimeSchema = Type.String({
  format: "date-time",
  description: "ISO 8601 date-time with timezone.",
});
const PolicyIdentifierSchema = Type.String({
  minLength: 1,
  maxLength: 64,
  pattern: "^[a-z][a-z0-9_-]*$",
  description: "Open policy identifier validated against the runtime registry.",
});
const NamespacedPolicyKeySchema = Type.String({
  minLength: 3,
  maxLength: 160,
  pattern: "^[a-z][a-z0-9_-]*(?:\\.[a-z][a-z0-9_-]*)+$",
  description: "Namespaced policy key validated against the runtime registry.",
});
const LocalDateTimeSchema = Type.String({
  minLength: 16,
  maxLength: 32,
  pattern: "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(?::\\d{2}(?:\\.\\d{1,6})?)?$",
  description: "ISO local date-time without a timezone; the profile timezone is applied.",
});
const CalendarWindowSchema = strictObject({
  unit: PolicyIdentifierSchema,
  offset: Type.Optional(Type.Integer({ minimum: -10000, maximum: 10000 })),
  segment: Type.Optional(PolicyIdentifierSchema),
});
const RollingWindowSchema = strictObject({
  value: Type.Number({ exclusiveMinimum: 0, maximum: 10000 }),
  unit: PolicyIdentifierSchema,
});
const LocalRangeSchema = strictObject({
  start: LocalDateTimeSchema,
  end: LocalDateTimeSchema,
});
const NaturalWindowSchema = strictObject({
  text: Type.String({
    minLength: 1,
    maxLength: 500,
    description: "Verbatim user time expression; the plugin resolves registered calendar anchors and segments in the profile timezone.",
  }),
});
const TemporalQueryFields = {
  occurred_on: Type.Optional(DateSchema),
  calendar_window: Type.Optional(CalendarWindowSchema),
  rolling_window: Type.Optional(RollingWindowSchema),
  local_range: Type.Optional(LocalRangeSchema),
  natural_window: Type.Optional(NaturalWindowSchema),
};
const temporalFieldNames = [
  "occurred_on",
  "calendar_window",
  "rolling_window",
  "local_range",
  "natural_window",
] as const;

function temporalQueryOptions(required: boolean): TObjectOptions {
  const alternatives: Record<string, unknown>[] = temporalFieldNames.map(
    (selected) => ({
      required: [selected],
      not: {
        anyOf: temporalFieldNames
          .filter((field) => field !== selected)
          .map((field) => ({ required: [field] })),
      },
    }),
  );
  if (!required) {
    alternatives.push({
      not: {
        anyOf: temporalFieldNames.map((field) => ({ required: [field] })),
      },
    });
  }
  return { oneOf: alternatives };
}
const DeferredDateTimeSchema = Type.Union([
  Type.Null(),
  Type.String({
    maxLength: MAX_PUBLIC_STRING_LENGTH,
    description: "Expiry input validated at the plugin execution boundary.",
  }),
]);
const ExpiryChoiceOptions = {
  oneOf: [
    {
      required: ["expiry_date"],
      not: { required: ["expires_at"] },
    },
    {
      required: ["expires_at"],
      not: { required: ["expiry_date"] },
    },
  ],
};
export const PositiveQuantitySchema = Type.Union([
  Type.Number({ exclusiveMinimum: 0 }),
  Type.String({ pattern: "^(?:0*[1-9]\\d*)(?:\\.\\d+)?$|^0*\\.\\d*[1-9]\\d*$" }),
]);
export const ConfidenceSchema = Type.Union([
  Type.Number({ minimum: 0, maximum: 1 }),
  Type.String({ pattern: "^(?:0(?:\\.\\d+)?|1(?:\\.0+)?)$" }),
]);
export const ConfirmationSchema = Type.Literal(true, {
  description: "Explicit user confirmation for a destructive operation.",
});

const NonNegativeQuantitySchema = Type.Union([
  Type.Number({ minimum: 0 }),
  Type.String({ pattern: "^(?:\\d+)(?:\\.\\d+)?$|^\\.\\d+$" }),
]);
const PositiveRatioSchema = Type.Union([
  Type.Number({ exclusiveMinimum: 0, maximum: 1 }),
  Type.String({
    pattern: "^(?:0*\\.\\d*[1-9]\\d*|0*[1](?:\\.0+)?)$",
  }),
]);
const HandleSchema = Type.String({
  pattern: "^wfh_[A-Za-z0-9_-]+$",
  minLength: 24,
  maxLength: 128,
  description: "Opaque workflow handle returned by an earlier tool call.",
});
const MaintenanceHandleSchema = Type.String({
  pattern: "^mop_[0-9a-f]{32}$",
  minLength: 36,
  maxLength: 36,
  description: "Opaque maintenance operation handle.",
});
const OperationKeySchema = Type.String({
  pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
  minLength: 1,
  maxLength: 128,
  description: "Stable caller key for one maintenance intent.",
});
const MaintenanceOperationFields = {
  operation_key: Type.Optional(OperationKeySchema),
};
const ContextSchema = strictObject({
  session_started_at: Type.Optional(DateTimeSchema),
});
const IntentSchema = Type.Literal("record");
const MealTypeSchema = Type.Union([
  Type.Literal("breakfast"),
  Type.Literal("lunch"),
  Type.Literal("dinner"),
  Type.Literal("snack"),
  Type.Literal("other"),
]);
const LocationTypeSchema = Type.Union([
  Type.Literal("home"),
  Type.Literal("restaurant"),
  Type.Literal("takeout"),
  Type.Literal("unknown"),
]);
const PantryUnitSchema = Type.Union([
  Type.Literal("g"),
  Type.Literal("gram"),
  Type.Literal("grams"),
  Type.Literal("ml"),
  Type.Literal("milliliter"),
  Type.Literal("milliliters"),
  Type.Literal("piece"),
  Type.Literal("pieces"),
  Type.Literal("portion"),
  Type.Literal("portions"),
  Type.Literal("pack"),
  Type.Literal("packs"),
]);
const WaterUnitSchema = Type.Union([
  Type.Literal("ml"),
  Type.Literal("毫升"),
  Type.Literal("cup"),
  Type.Literal("杯"),
  Type.Literal("glass"),
  Type.Literal("杯子"),
  Type.Literal("bottle"),
  Type.Literal("瓶"),
]);
const WeightUnitSchema = Type.Union([
  Type.Literal("kg"),
  Type.Literal("jin"),
  Type.Literal("lb"),
]);
const PantryStatusSchema = Type.Union([
  Type.Literal("active"),
  Type.Literal("opened"),
  Type.Literal("frozen"),
  Type.Literal("thawed"),
  Type.Literal("discarded"),
  Type.Literal("expired"),
  Type.Literal("consumed"),
]);
const PantrySelectorSchema = Type.Union([
  Type.Literal("cold_storage"),
  Type.Literal("frozen"),
  Type.Literal("newest"),
]);
const CurrencySchema = Type.String({
  minLength: 3,
  maxLength: 3,
  pattern: "^[A-Z]{3}$",
});
const WasteCategorySchema = Type.Union([
  Type.Literal("spoilage"),
  Type.Literal("expired"),
  Type.Literal("overprepared"),
  Type.Literal("quality"),
  Type.Literal("other"),
  Type.Literal("unspecified"),
]);
const ImportNameSchema = Type.String({
  minLength: 6,
  maxLength: 165,
  pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,159}\\.(json|zip)$",
});
const DeletionScopeSchema = Type.Union([
  Type.Literal("raw_source_text"),
  Type.Literal("preferences"),
  Type.Literal("intake_range"),
  Type.Literal("business_facts_keep_config"),
  Type.Literal("all_business"),
]);
const RuleTypeSchema = Type.Union([
  Type.Literal("portion"),
  Type.Literal("water_unit"),
  Type.Literal("recipe"),
  Type.Literal("home_source"),
  Type.Literal("meal_time"),
  Type.Literal("food_alias"),
  Type.Literal("batch_preference"),
  Type.Literal("nutrition_label"),
  Type.Literal("reminder"),
  Type.Literal("reply_style"),
]);
const NonWaterRuleTypeSchema = Type.Union([
  Type.Literal("portion"),
  Type.Literal("recipe"),
  Type.Literal("home_source"),
  Type.Literal("meal_time"),
  Type.Literal("food_alias"),
  Type.Literal("batch_preference"),
  Type.Literal("nutrition_label"),
  Type.Literal("reminder"),
  Type.Literal("reply_style"),
]);
const WaterUnitOutcomeSchema = strictObject({
  milliliters: Type.Number({ exclusiveMinimum: 0, maximum: 5000 }),
});

const NutritionFields = {
  calories: NonNegativeQuantitySchema,
  protein: NonNegativeQuantitySchema,
  fat: NonNegativeQuantitySchema,
  carbohydrate: NonNegativeQuantitySchema,
  fiber: NonNegativeQuantitySchema,
  sodium: Type.Optional(Type.Union([
    NonNegativeQuantitySchema,
    Type.Null(),
  ])),
  hydration_ml: Type.Optional(Type.Union([
    NonNegativeQuantitySchema,
    Type.Null(),
  ])),
  source: Type.String({ minLength: 1 }),
  uncertainty: Type.Optional(Type.String({ minLength: 1 })),
};
const NutritionFactsSchema = strictObject({
  ...NutritionFields,
  source_grade: Type.Union([
    Type.Literal("A"),
    Type.Literal("B"),
    Type.Literal("C"),
    Type.Literal("D"),
  ]),
});
const PartialNutritionFactsSchema = strictObject({
  calories: Type.Optional(NonNegativeQuantitySchema),
  protein: Type.Optional(NonNegativeQuantitySchema),
  fat: Type.Optional(NonNegativeQuantitySchema),
  carbohydrate: Type.Optional(NonNegativeQuantitySchema),
  fiber: Type.Optional(Type.Union([
    NonNegativeQuantitySchema,
    Type.Null(),
  ])),
  sodium: Type.Optional(Type.Union([
    NonNegativeQuantitySchema,
    Type.Null(),
  ])),
  hydration_ml: Type.Optional(Type.Union([
    NonNegativeQuantitySchema,
    Type.Null(),
  ])),
  source: Type.String({ minLength: 1 }),
  uncertainty: Type.Optional(Type.String({ minLength: 1 })),
  source_grade: Type.Union([
    Type.Literal("A"),
    Type.Literal("B"),
    Type.Literal("C"),
    Type.Literal("D"),
  ]),
});
const PreparationLossSchema = strictObject({
  kind: Type.Union([
    Type.Literal("bone"),
    Type.Literal("shell"),
    Type.Literal("skin"),
    Type.Literal("fat"),
    Type.Literal("other"),
  ]),
  quantity: PositiveQuantitySchema,
  unit: Type.Literal("g"),
  nutrition_facts: NutritionFactsSchema,
});
const QuantityEstimateSchema = strictObject({
  suggested: PositiveQuantitySchema,
  lower: PositiveQuantitySchema,
  upper: PositiveQuantitySchema,
  unit: Type.String({ minLength: 1, maxLength: 40 }),
  evidence_type: PolicyIdentifierSchema,
  policy_key: NamespacedPolicyKeySchema,
});
const NutritionEstimateSchema = strictObject({
  ...NutritionFields,
  source_grade: Type.Union([Type.Literal("C"), Type.Literal("D")]),
});
const NutritionBasisSchema = Type.Union([
  Type.Literal("per_100g"),
  Type.Literal("per_100ml"),
  Type.Literal("per_serving"),
  Type.Literal("consumed_total"),
]);
const ConfidenceSignalsSchema = strictObject({
  source_confidence: Type.Optional(ConfidenceSchema),
  name_match_confidence: Type.Optional(ConfidenceSchema),
  quantity_confidence: Type.Optional(ConfidenceSchema),
  batch_uniqueness: Type.Optional(ConfidenceSchema),
  context_consistency: Type.Optional(ConfidenceSchema),
  personal_rule_confidence: Type.Optional(ConfidenceSchema),
});
const LeftoverSchema = strictObject(
  {
    food_name: Type.String({ minLength: 1 }),
    normalized_name: Type.String({ minLength: 1 }),
    quantity: PositiveQuantitySchema,
    unit: PantryUnitSchema,
    storage_location: Type.String({ minLength: 1 }),
    expiry_date: Type.Optional(DateSchema),
    expires_at: Type.Optional(DeferredDateTimeSchema),
  },
  ExpiryChoiceOptions,
);
const MealItemSchema = applyPublicBounds(
  strictObject({
    raw_name: Type.String({ minLength: 1 }),
    normalized_name: Type.String({ minLength: 1 }),
    inventory_match_handle: Type.Optional(HandleSchema),
    amount: Type.Optional(NonNegativeQuantitySchema),
    unit: Type.Optional(Type.String({ minLength: 1 })),
    portion_expression: Type.Optional(Type.String({ minLength: 1 })),
    quantity_estimate: Type.Optional(QuantityEstimateSchema),
    consumed_weight_g: Type.Optional(PositiveQuantitySchema),
    consumed_volume_ml: Type.Optional(PositiveQuantitySchema),
    consumed_servings: Type.Optional(PositiveQuantitySchema),
    raw_weight_g: Type.Optional(NonNegativeQuantitySchema),
    inventory_deduction_weight_g: Type.Optional(NonNegativeQuantitySchema),
    edible_ratio: Type.Optional(PositiveRatioSchema),
    cooking_yield: Type.Optional(PositiveQuantitySchema),
    nutrition_basis: Type.Optional(NutritionBasisSchema),
    nutrition_dataset_version: Type.Optional(Type.String({ minLength: 1 })),
    nutrition_facts: Type.Optional(PartialNutritionFactsSchema),
    preparation_losses: Type.Optional(
      Type.Array(PreparationLossSchema, { maxItems: 8 }),
    ),
    brand: Type.Optional(Type.String({ minLength: 1 })),
    nutrition_estimate: Type.Optional(NutritionEstimateSchema),
    source_confidence: Type.Optional(ConfidenceSchema),
    name_match_confidence: Type.Optional(ConfidenceSchema),
    quantity_confidence: Type.Optional(ConfidenceSchema),
    batch_uniqueness: Type.Optional(ConfidenceSchema),
    context_consistency: Type.Optional(ConfidenceSchema),
    personal_rule_confidence: Type.Optional(ConfidenceSchema),
    confidence_signals: Type.Optional(ConfidenceSignalsSchema),
    leftover: Type.Optional(LeftoverSchema),
    ingredients: Type.Optional(
      Type.Array(Type.Unknown(), { maxItems: MAX_INGREDIENT_CHILDREN }),
    ),
  }, {
    dependentRequired: {
      quantity_estimate: ["portion_expression", "amount", "unit"],
    },
  }),
);

const CookingDishSchema = strictObject({
  raw_name: Type.String({ minLength: 1 }),
  normalized_name: Type.String({ minLength: 1 }),
  unit: PantryUnitSchema,
  consumed_quantity: PositiveQuantitySchema,
  leftover: Type.Optional(LeftoverSchema),
  ingredients: Type.Array(MealItemSchema, {
    minItems: 1,
    maxItems: MAX_MEAL_ITEMS,
  }),
});

const CookingMealDraftSchema = strictObject({
  occurred_at: Type.Optional(DateTimeSchema),
  meal_type: MealTypeSchema,
  source_text: Type.String({ minLength: 1 }),
  dish: CookingDishSchema,
});

const MealDraftSchema = strictObject({
  intent: Type.Optional(IntentSchema),
  occurred_at: Type.Optional(DateTimeSchema),
  meal_type: Type.Optional(MealTypeSchema),
  source_text: Type.String({ minLength: 1 }),
  location_type: Type.Optional(LocationTypeSchema),
  items: Type.Array(MealItemSchema, {
    minItems: 1,
    maxItems: MAX_MEAL_ITEMS,
  }),
});
const MealUpdateDraftSchema = strictObject({
  intent: Type.Optional(IntentSchema),
  occurred_at: Type.Optional(DateTimeSchema),
  meal_type: Type.Optional(MealTypeSchema),
  source_text: Type.Optional(Type.String({ minLength: 1 })),
  location_type: Type.Optional(LocationTypeSchema),
  items: Type.Array(MealItemSchema, {
    minItems: 1,
    maxItems: MAX_MEAL_ITEMS,
  }),
});
const CookingMealUpdateDraftSchema = strictObject({
  occurred_at: Type.Optional(DateTimeSchema),
  meal_type: Type.Optional(MealTypeSchema),
  source_text: Type.Optional(Type.String({ minLength: 1 })),
  dish: CookingDishSchema,
});
const MealPreviewDraftFields = {
  ...MealDraftSchema.properties,
  items: Type.Optional(
    Type.Array(MealItemSchema, {
      minItems: 1,
      maxItems: MAX_MEAL_ITEMS,
    }),
  ),
};
const MealSelectorSchema = strictObject({
  occurred_at: DateTimeSchema,
  source_text: Type.String({ minLength: 1 }),
});

const WeightMetadataFields = {
  total_weight_g: Type.Optional(PositiveQuantitySchema),
  average_unit_weight_g: Type.Optional(PositiveQuantitySchema),
  weight_basis: Type.Optional(
    Type.Union([
      Type.Literal("net"),
      Type.Literal("gross"),
      Type.Literal("shell_on"),
      Type.Literal("edible"),
    ]),
  ),
  weight_source: Type.Optional(Type.String({ minLength: 1 })),
  weight_confidence: Type.Optional(
    Type.Union([
      Type.Literal("confirmed"),
      Type.Literal("derived"),
      Type.Literal("estimated"),
    ]),
  ),
};

const PackageHierarchyItemSchema = strictObject(
  {
    quantity: Type.Optional(PositiveQuantitySchema),
    per_parent: Type.Optional(PositiveQuantitySchema),
    unit: Type.String({ minLength: 1, maxLength: 40 }),
  },
  {
    anyOf: [
      { required: ["quantity"] },
      { required: ["per_parent"] },
    ],
  },
);

const PackageSemanticFields = {
  display_quantity: Type.Optional(PositiveQuantitySchema),
  display_unit: Type.Optional(
    Type.String({ minLength: 1, maxLength: 40 }),
  ),
  base_quantity_per_display_unit: Type.Optional(PositiveQuantitySchema),
  package_hierarchy: Type.Optional(
    Type.Array(PackageHierarchyItemSchema, { minItems: 1, maxItems: 4 }),
  ),
};

const packageDependentRequired = {
  display_quantity: ["display_unit", "base_quantity_per_display_unit"],
  display_unit: ["display_quantity", "base_quantity_per_display_unit"],
  base_quantity_per_display_unit: ["display_quantity", "display_unit"],
  package_count: ["quantity_per_package", "package_unit"],
  quantity_per_package: ["package_count", "package_unit"],
  package_unit: ["package_count", "quantity_per_package"],
};

const NullableNutritionQuantitySchema = Type.Union([
  NonNegativeQuantitySchema,
  Type.Null(),
]);
const LabelNutritionSchema = strictObject({
  energy_kj: Type.Optional(NullableNutritionQuantitySchema),
  calories_kcal: Type.Optional(NullableNutritionQuantitySchema),
  protein_g: Type.Optional(NullableNutritionQuantitySchema),
  fat_g: Type.Optional(NullableNutritionQuantitySchema),
  carbohydrate_g: Type.Optional(NullableNutritionQuantitySchema),
  sodium_mg: Type.Optional(NullableNutritionQuantitySchema),
  fiber_g: Type.Optional(NullableNutritionQuantitySchema),
  sugar_g: Type.Optional(NullableNutritionQuantitySchema),
  saturated_fat_g: Type.Optional(NullableNutritionQuantitySchema),
  hydration_ml: Type.Optional(NullableNutritionQuantitySchema),
});
const NutritionProfileDraftSchema = strictObject({
  normalized_name: Type.String({ minLength: 1 }),
  brand: Type.Optional(Type.String()),
  product_key: Type.Optional(Type.String()),
  serving_basis: Type.Union([
    Type.Literal("per_100g"),
    Type.Literal("per_100ml"),
    Type.Literal("per_serving"),
  ]),
  nutrition: LabelNutritionSchema,
  source_text: Type.String({ minLength: 1 }),
  source_grade: Type.Union([
    Type.Literal("A"),
    Type.Literal("B"),
    Type.Literal("C"),
    Type.Literal("D"),
    Type.Literal("unknown"),
  ]),
});

const PantryAddSchema = strictObject({
  food_name: Type.Optional(Type.String({ minLength: 1 })),
  normalized_name: Type.Optional(Type.String({ minLength: 1 })),
  quantity: Type.Optional(PositiveQuantitySchema),
  unit: Type.Optional(PantryUnitSchema),
  package_count: Type.Optional(PositiveQuantitySchema),
  quantity_per_package: Type.Optional(PositiveQuantitySchema),
  package_unit: Type.Optional(
    Type.Union([Type.Literal("g"), Type.Literal("ml")]),
  ),
  ...PackageSemanticFields,
  added_at: Type.Optional(DateTimeSchema),
  source_text: Type.Optional(Type.String({ minLength: 1 })),
  batch_code: Type.Optional(Type.String({ minLength: 1 })),
  storage_location: Type.Optional(Type.String({ minLength: 1 })),
  purchase_date: Type.Optional(Type.String({ minLength: 1 })),
  expiry_date: Type.Optional(DateSchema),
  expires_at: Type.Optional(DeferredDateTimeSchema),
  price: Type.Optional(NonNegativeQuantitySchema),
  price_minor: Type.Optional(
    Type.Integer({ minimum: 0, maximum: 9_000_000_000_000_000 }),
  ),
  currency: Type.Optional(CurrencySchema),
  source: Type.Optional(Type.String({ minLength: 1 })),
  notes: Type.Optional(Type.String()),
  ...WeightMetadataFields,
});

const RecipeIngredientSchema = strictObject({
  food_name: Type.String({ minLength: 1, maxLength: 120 }),
  normalized_name: Type.Optional(
    Type.String({ minLength: 1, maxLength: 120 }),
  ),
  quantity: PositiveQuantitySchema,
  unit: PantryUnitSchema,
});
const ShoppingListItemDraftSchema = strictObject({
  food_name: Type.String({ minLength: 1, maxLength: 120 }),
  normalized_name: Type.Optional(
    Type.String({ minLength: 1, maxLength: 120 }),
  ),
  quantity: PositiveQuantitySchema,
  unit: PantryUnitSchema,
  reason: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
});
const ShoppingListStatusSchema = Type.Union([
  Type.Literal("active"),
  Type.Literal("cancelled"),
  Type.Literal("completed"),
]);

const TransactionFilterFields = {
  operation: Type.Optional(
    Type.Union([Type.Literal("undo"), Type.Literal("redo")]),
  ),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
  session_started_at: Type.Optional(DateTimeSchema),
  operation_type: Type.Optional(Type.String({ minLength: 1 })),
  date_start: Type.Optional(DateSchema),
  date_end: Type.Optional(DateSchema),
  meal_type: Type.Optional(MealTypeSchema),
  normalized_food_name: Type.Optional(Type.String({ minLength: 1 })),
};

function actionBranch<Action extends string, Properties extends TProperties>(
  action: Action,
  properties: Properties,
  options: TObjectOptions = {},
) {
  return strictObject(
    {
      action: Type.Literal(action),
      ...properties,
      context: Type.Optional(ContextSchema),
    },
    options,
  );
}

function boundedActionUnion<Branches extends TSchema[]>(
  branches: [...Branches],
  options: TSchemaOptions = {},
) {
  branches.forEach((branch) => applyPublicBounds(branch));
  const allowedProperties: Record<string, TSchema> = {};
  for (const branch of branches) {
    const properties = (branch as { properties?: Record<string, TSchema> })
      .properties;
    for (const key of Object.keys(properties ?? {})) {
      allowedProperties[key] = Type.Unknown();
    }
  }
  return Type.Union(branches, {
    ...options,
    properties: allowedProperties,
    additionalProperties: false,
  });
}

function mealTargetAction<
  Action extends string,
  Properties extends TProperties,
>(
  action: Action,
  properties: Properties,
) {
  return actionBranch(
    action,
    {
      meal_handle: Type.Optional(HandleSchema),
      selector: Type.Optional(MealSelectorSchema),
      ...properties,
    },
    {
      anyOf: [
        { required: ["meal_handle"] },
        { required: ["selector"] },
      ],
    },
  );
}

function pantryTargetAction<
  Action extends string,
  Properties extends TProperties,
>(
  action: Action,
  properties: Properties,
  options: TObjectOptions = {},
) {
  return actionBranch(
    action,
    {
      batch_handle: Type.Optional(HandleSchema),
      batch_code: Type.Optional(Type.String({ minLength: 1 })),
      ...properties,
    },
    {
      ...options,
      anyOf: [
        { required: ["batch_handle"] },
        { required: ["batch_code"] },
      ],
    },
  );
}

export const MealParametersSchema = boundedActionUnion([
  actionBranch("record", MealDraftSchema.properties),
  actionBranch("record_prepared", {
    prepared_food_handle: HandleSchema,
    quantity: Type.Optional(PositiveQuantitySchema),
    unit: Type.Optional(Type.String({ minLength: 1, maxLength: 40 })),
    source_text: Type.String({ minLength: 1 }),
    occurred_at: Type.Optional(DateTimeSchema),
    meal_type: Type.Optional(MealTypeSchema),
  }, {
    dependentRequired: {
      quantity: ["unit"],
      unit: ["quantity"],
    },
  }),
  actionBranch("record_cooking", {
    occurred_at: Type.Optional(DateTimeSchema),
    meal_type: MealTypeSchema,
    source_text: Type.String({ minLength: 1 }),
    dish: CookingDishSchema,
  }),
  actionBranch("save_recipe", {
    name: Type.String({ minLength: 1, maxLength: 120 }),
    ingredients: Type.Array(RecipeIngredientSchema, {
      minItems: 1,
      maxItems: 30,
    }),
    yield_quantity: PositiveQuantitySchema,
    yield_unit: PantryUnitSchema,
    notes: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
    source_text: Type.String({ minLength: 1, maxLength: 1000 }),
  }),
  actionBranch("suggest_recipes", {
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 3 })),
    max_missing_items: Type.Optional(
      Type.Integer({ minimum: 0, maximum: 30 }),
    ),
  }),
  actionBranch("preview_meal_plan", {
    meal_type: Type.Optional(MealTypeSchema),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 3 })),
    max_missing_items: Type.Optional(
      Type.Integer({ minimum: 0, maximum: 30 }),
    ),
  }),
  actionBranch("preview_record", MealPreviewDraftFields),
  actionBranch("commit_record", {
    commit_handle: HandleSchema,
    confirmed: Type.Optional(Type.Boolean()),
  }),
  actionBranch(
    "query",
    {
      ...TemporalQueryFields,
      meal_type: Type.Optional(MealTypeSchema),
    },
    temporalQueryOptions(false),
  ),
  mealTargetAction(
    "update",
    { draft: Type.Union([MealUpdateDraftSchema, CookingMealUpdateDraftSchema]) },
  ),
  mealTargetAction(
    "delete",
    {
      intent: Type.Optional(IntentSchema),
      source_text: Type.Optional(Type.String({ minLength: 1 })),
    },
  ),
  actionBranch(
    "nutrition_estimate",
    {
      normalized_name: Type.String({ minLength: 1 }),
      brand: Type.Optional(Type.String({ minLength: 1 })),
      consumed_weight_g: NonNegativeQuantitySchema,
      estimate: Type.Optional(NutritionEstimateSchema),
    },
  ),
]);

const WaterRecordFields = {
  amount: PositiveQuantitySchema,
  unit: WaterUnitSchema,
  occurred_at: Type.Optional(DateTimeSchema),
  source_text: Type.String({ minLength: 1 }),
};
const WaterUpdateFields = {
  ...WaterRecordFields,
  occurred_at: DateTimeSchema,
};
export const WaterParametersSchema = boundedActionUnion([
  actionBranch("record", WaterRecordFields),
  actionBranch(
    "query",
    TemporalQueryFields,
    temporalQueryOptions(true),
  ),
  actionBranch(
    "update",
    { record_handle: HandleSchema, ...WaterUpdateFields },
  ),
  actionBranch(
    "delete",
    {
      record_handle: HandleSchema,
      deleted_at: Type.Optional(DateTimeSchema),
      source_text: Type.Optional(Type.String({ minLength: 1 })),
    },
  ),
]);

const WeightRecordFields = {
  weight: PositiveQuantitySchema,
  unit: Type.Optional(WeightUnitSchema),
  status_note: Type.Optional(
    Type.Union([
      Type.String({ minLength: 1, maxLength: 80 }),
      Type.Null(),
    ]),
  ),
};
export const WeightParametersSchema = boundedActionUnion([
  actionBranch("record", WeightRecordFields),
  actionBranch(
    "query",
    {
      ...TemporalQueryFields,
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
    },
    temporalQueryOptions(false),
  ),
  actionBranch("update", {
    record_handle: HandleSchema,
    weight: Type.Optional(PositiveQuantitySchema),
    unit: Type.Optional(WeightUnitSchema),
    status_note: Type.Optional(
      Type.Union([
        Type.String({ minLength: 1, maxLength: 80 }),
        Type.Null(),
      ]),
    ),
  }, {
    anyOf: [
      { required: ["weight"] },
      {
        required: ["status_note"],
        not: { required: ["unit"] },
      },
    ],
  }),
  actionBranch("delete", {
    record_handle: Type.Optional(HandleSchema),
    commit_handle: Type.Optional(HandleSchema),
  }, {
    oneOf: [
      { required: ["record_handle"], not: { required: ["commit_handle"] } },
      { required: ["commit_handle"], not: { required: ["record_handle"] } },
    ],
  }),
]);

export const PantryParametersSchema = boundedActionUnion([
  pantryTargetAction(
    "adjust",
    {
      quantity: NonNegativeQuantitySchema,
      source_text: Type.String({ minLength: 1 }),
      reason: Type.Optional(Type.String({ minLength: 1 })),
    },
  ),
  actionBranch("add", {
    ...PantryAddSchema.properties,
    nutrition_profile: Type.Optional(NutritionProfileDraftSchema),
  }, {
    not: { required: ["expiry_date", "expires_at"] },
    dependentRequired: {
      price_minor: ["currency"],
      currency: ["price_minor"],
      ...packageDependentRequired,
    },
  }),
  actionBranch("preview_add", PantryAddSchema.properties, {
    not: { required: ["expiry_date", "expires_at"] },
    dependentRequired: {
      price_minor: ["currency"],
      currency: ["price_minor"],
      ...packageDependentRequired,
    },
  }),
  actionBranch("commit_add", { commit_handle: HandleSchema }),
  pantryTargetAction(
    "preview_update_metadata",
    {
      ...WeightMetadataFields,
      expiry_date: Type.Optional(DateSchema),
      expires_at: Type.Optional(DeferredDateTimeSchema),
      source_text: Type.Optional(Type.String({ minLength: 1 })),
      food_name: Type.Optional(Type.String({ minLength: 1 })),
      normalized_name: Type.Optional(Type.String({ minLength: 1 })),
    },
    { not: { required: ["expiry_date", "expires_at"] } },
  ),
  actionBranch("commit_update_metadata", { commit_handle: HandleSchema }),
  pantryTargetAction(
    "preview_link_nutrition",
    {
      linked_at: Type.Optional(DateTimeSchema),
      nutrition_profile: NutritionProfileDraftSchema,
    },
  ),
  actionBranch("commit_link_nutrition", { commit_handle: HandleSchema }),
  actionBranch("preview_shopping_list", {
    title: Type.String({ minLength: 1, maxLength: 120 }),
    source_text: Type.String({ minLength: 1, maxLength: 1000 }),
    items: Type.Array(ShoppingListItemDraftSchema, {
      minItems: 1,
      maxItems: 50,
    }),
  }),
  actionBranch("commit_shopping_list", { commit_handle: HandleSchema }),
  actionBranch("query_shopping_list", {
    status: Type.Optional(ShoppingListStatusSchema),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
  }),
  actionBranch("search", {
    search_text: Type.String({ minLength: 1, maxLength: 120 }),
    unit: Type.Optional(PantryUnitSchema),
    storage_location: Type.Optional(
      Type.String({ minLength: 1, maxLength: 120 }),
    ),
    statuses: Type.Optional(
      Type.Array(PantryStatusSchema, { minItems: 1, uniqueItems: true }),
    ),
    nutrition_mode: Type.Optional(Type.Union([
      Type.Literal("none"),
      Type.Literal("summary"),
      Type.Literal("full"),
    ])),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 5 })),
  }),
  actionBranch("cancel_shopping_list", {
    shopping_list_handle: HandleSchema,
    source_text: Type.String({ minLength: 1, maxLength: 1000 }),
  }),
  actionBranch(
    "query",
    {
      normalized_name: Type.Optional(Type.String({ minLength: 1 })),
      food_name: Type.Optional(Type.String({ minLength: 1 })),
      storage_location: Type.Optional(Type.String({ minLength: 1 })),
      missing_expiry_only: Type.Optional(Type.Boolean()),
      include_details: Type.Optional(Type.Boolean()),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
      offset: Type.Optional(Type.Integer({ minimum: 0 })),
      statuses: Type.Optional(
        Type.Array(PantryStatusSchema, { minItems: 1, uniqueItems: true }),
      ),
    },
  ),
  actionBranch("discard", {
    batch_handle: Type.Optional(HandleSchema),
    batch_code: Type.Optional(Type.String({ minLength: 1 })),
    inventory_match_handle: Type.Optional(HandleSchema),
    quantity: Type.Optional(PositiveQuantitySchema),
    unit: Type.Optional(Type.String({ minLength: 1, maxLength: 40 })),
    discarded_at: Type.Optional(DateTimeSchema),
    source_text: Type.String({ minLength: 1 }),
    reason: Type.Optional(Type.String({ minLength: 1 })),
    waste_category: Type.Optional(WasteCategorySchema),
  }, {
    oneOf: [
      {
        anyOf: [
          { required: ["batch_handle"] },
          { required: ["batch_code"] },
        ],
        not: { required: ["inventory_match_handle"] },
      },
      {
        required: ["inventory_match_handle", "quantity", "unit"],
        not: {
          anyOf: [
            { required: ["batch_handle"] },
            { required: ["batch_code"] },
          ],
        },
      },
    ],
  }),
  actionBranch("deduct", {
    inventory_match_handle: HandleSchema,
    quantity: PositiveQuantitySchema,
    unit: Type.String({ minLength: 1, maxLength: 40 }),
    source_text: Type.String({ minLength: 1 }),
    reason: Type.Optional(Type.String({ minLength: 1 })),
  }),
  pantryTargetAction(
    "open",
    {
      opened_at: Type.Optional(DateTimeSchema),
      source_text: Type.String({ minLength: 1 }),
    },
  ),
  pantryTargetAction(
    "freeze",
    {
      frozen_at: Type.Optional(DateTimeSchema),
      source_text: Type.String({ minLength: 1 }),
    },
  ),
  pantryTargetAction(
    "thaw",
    {
      thawed_at: Type.Optional(DateTimeSchema),
      source_text: Type.String({ minLength: 1 }),
    },
  ),
  actionBranch(
    "preview_deduct",
    {
      normalized_name: Type.String({ minLength: 1 }),
      quantity: PositiveQuantitySchema,
      unit: PantryUnitSchema,
      source_text: Type.String({ minLength: 1 }),
      selector: Type.Optional(PantrySelectorSchema),
      reason: Type.Optional(Type.String({ minLength: 1 })),
    },
  ),
  actionBranch("commit_deduct", { commit_handle: HandleSchema }),
]);

export const TransactionParametersSchema = boundedActionUnion([
  actionBranch("get_recent", TransactionFilterFields),
  actionBranch("undo", { operation_handle: HandleSchema }),
  actionBranch("redo", { operation_handle: HandleSchema }),
]);

const ReportDateFields = {
  report_date: Type.Optional(DateSchema),
  date: Type.Optional(DateSchema),
};
const BackupLabelSchema = Type.String({
  minLength: 1,
  maxLength: 64,
  pattern: "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
});
export const ReportParametersSchema = boundedActionUnion([
  actionBranch("today", ReportDateFields),
  actionBranch("daily", ReportDateFields),
  actionBranch("weekly", ReportDateFields),
  actionBranch("monthly", ReportDateFields),
  actionBranch("progress", ReportDateFields),
  actionBranch("insights", {
    ...ReportDateFields,
    period: Type.Optional(
      Type.Union([
        Type.Literal("daily"),
        Type.Literal("weekly"),
        Type.Literal("monthly"),
      ]),
    ),
    within_days: Type.Optional(Type.Integer({ minimum: 1, maximum: 30 })),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
  }),
  actionBranch(
    "expiring_inventory",
    {
      report_date: Type.Optional(DateSchema),
      date: Type.Optional(DateSchema),
      within_days: Type.Optional(Type.Integer({ minimum: 1, maximum: 365 })),
    },
  ),
  actionBranch("cost_summary", {
    date_start: Type.Optional(DateSchema),
    date_end: Type.Optional(DateSchema),
    currency: Type.Optional(CurrencySchema),
  }, {
    dependentRequired: {
      date_start: ["date_end"],
      date_end: ["date_start"],
    },
  }),
  actionBranch("waste_summary", {
    date_start: Type.Optional(DateSchema),
    date_end: Type.Optional(DateSchema),
    currency: Type.Optional(CurrencySchema),
  }, {
    dependentRequired: {
      date_start: ["date_end"],
      date_end: ["date_start"],
    },
  }),
  actionBranch("trend_summary", {
    days: Type.Optional(Type.Integer({ minimum: 1, maximum: 730 })),
    currency: Type.Optional(CurrencySchema),
  }),
]);

export const SystemParametersSchema = Object.assign(boundedActionUnion([
  actionBranch("initialize", MaintenanceOperationFields),
  actionBranch("self_check", {}),
  actionBranch("maintenance_status", {
    operation_handle: MaintenanceHandleSchema,
  }),
  actionBranch("maintenance_history", {
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
  }),
  actionBranch("repair", {
    report_date: Type.Optional(DateSchema),
    ...MaintenanceOperationFields,
  }),
  actionBranch("validate_database", {}),
  actionBranch("backup", {
    label: Type.Optional(BackupLabelSchema),
    ...MaintenanceOperationFields,
  }),
  actionBranch(
    "restore",
    {
      backup_handle: HandleSchema,
      confirmed: ConfirmationSchema,
      ...MaintenanceOperationFields,
    },
  ),
  actionBranch("migrate", MaintenanceOperationFields),
  actionBranch("export_data", {
    format: Type.Optional(
      Type.Union([Type.Literal("json"), Type.Literal("csv")]),
    ),
    ...MaintenanceOperationFields,
  }),
  actionBranch("validate_import", {
    import_name: ImportNameSchema,
  }),
  actionBranch("import_data", {
    commit_handle: HandleSchema,
    confirmed: ConfirmationSchema,
    ...MaintenanceOperationFields,
  }),
  actionBranch(
    "preview_delete_data",
    {
      scope: DeletionScopeSchema,
      date_start: Type.Optional(DateSchema),
      date_end: Type.Optional(DateSchema),
    },
    {
      dependentRequired: {
        date_start: ["date_end"],
        date_end: ["date_start"],
      },
    },
  ),
  actionBranch("commit_delete_data", {
    commit_handle: HandleSchema,
    confirmed: ConfirmationSchema,
    ...MaintenanceOperationFields,
  }),
  actionBranch(
    "query_preferences",
    { include_inactive: Type.Optional(Type.Boolean()) },
  ),
  actionBranch("query_goals", {}),
  actionBranch("update_goals", {
    calories_kcal: Type.Integer({ minimum: 1 }), protein_g: Type.Integer({ minimum: 1 }), fat_g: Type.Integer({ minimum: 1 }),
    carbohydrate_g: Type.Integer({ minimum: 1 }), fiber_g: Type.Integer({ minimum: 1 }), sodium_mg: Type.Integer({ minimum: 1 }), water_ml: Type.Integer({ minimum: 1 }),
    timezone_name: Type.String({ minLength: 1 }), source_text: Type.String({ minLength: 1 }),
  }),
  actionBranch(
    "update_preferences",
    {
      rule_type: RuleTypeSchema,
      subject: Type.String({ minLength: 1 }),
      outcome: BoundedJsonObjectSchema,
      evidence: Type.Optional(BoundedJsonObjectSchema),
      source_text: Type.String({ minLength: 1 }),
    },
    {
      anyOf: [
        {
          properties: {
            rule_type: Type.Literal("water_unit"),
            outcome: WaterUnitOutcomeSchema,
          },
          required: ["rule_type", "outcome"],
        },
        {
          properties: { rule_type: NonWaterRuleTypeSchema },
          required: ["rule_type"],
        },
      ],
    },
  ),
  actionBranch(
    "forget_preference",
    {
      rule_type: RuleTypeSchema,
      subject: Type.String({ minLength: 1 }),
      source_text: Type.String({ minLength: 1 }),
    },
  ),
  actionBranch(
    "query_nutrition_backfill",
    {
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
      meal_handle: Type.Optional(HandleSchema),
      batch_handle: Type.Optional(HandleSchema),
    },
    {
      oneOf: [
        {
          not: {
            anyOf: [
              { required: ["meal_handle"] },
              { required: ["batch_handle"] },
            ],
          },
        },
        {
          required: ["meal_handle", "batch_handle"],
          not: { required: ["limit"] },
        },
      ],
    },
  ),
  actionBranch(
    "commit_nutrition_backfill",
    {
      meal_handle: HandleSchema,
      batch_handle: Type.Optional(HandleSchema),
      items: Type.Union([
        Type.Array(
          strictObject({
            item_handle: HandleSchema,
            nutrition_estimate: NutritionEstimateSchema,
          }),
          { minItems: 1, maxItems: MAX_TOTAL_ITEMS },
        ),
        Type.Array(
          strictObject({
            display_order: Type.Integer({ minimum: 0 }),
            nutrition_estimate: NutritionEstimateSchema,
          }),
          { minItems: 1, maxItems: MAX_TOTAL_ITEMS },
        ),
      ]),
    },
    {
      anyOf: [
        {
          properties: {
            items: Type.Array(
              strictObject({
                item_handle: HandleSchema,
                nutrition_estimate: NutritionEstimateSchema,
              }),
              { minItems: 1, maxItems: MAX_TOTAL_ITEMS },
            ),
          },
        },
        {
          properties: {
            items: Type.Array(
              strictObject({
                display_order: Type.Integer({ minimum: 0 }),
                nutrition_estimate: NutritionEstimateSchema,
              }),
              { minItems: 1, maxItems: MAX_TOTAL_ITEMS },
            ),
          },
          not: { required: ["batch_handle"] },
        },
      ],
    },
  ),
]), {
  $defs: {
    pdpJsonValue: BoundedJsonValueSchema,
  },
});

export const PluginConfigSchema = strictObject({
  dataDir: Type.Optional(
    Type.String({
      minLength: 1,
      maxLength: MAX_PUBLIC_STRING_LENGTH,
      description: "Isolated directory for the diet database and generated files.",
    }),
  ),
  testRunId: Type.Optional(
    Type.String({
      minLength: 1,
      maxLength: 128,
      pattern: "^[A-Za-z0-9][A-Za-z0-9._-]*$",
    }),
  ),
});
