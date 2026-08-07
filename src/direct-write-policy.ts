export type DirectWriteDomain = "meal" | "water" | "weight" | "pantry";

export type DirectWriteDecision =
  | {
      kind: "direct";
      domain: DirectWriteDomain;
      action: "record" | "add";
      route?: "inventory_consumption";
      defaults?: Record<string, string>;
    }
  | {
      kind: "clarify";
      domain: "meal";
      action: "preview_record";
    }
  | { kind: "compound"; domains: DirectWriteDomain[] }
  | { kind: "unhandled" };

const NO_WRITE_PATTERN =
  /(?:算了|取消|不记了?|别记|不用记|差点|险些|本来想|最后没|没有吃|没吃|没有喝|没喝|打算|准备|计划|等会|待会|稍后|明天再|可能会|想要吃|想吃|想喝)/u;
const OTHER_PERSON_PATTERN =
  /^(?:(?:我(?:的)?)(?:孩子|儿子|女儿|老婆|老公|妈妈|妈|爸爸|爸|朋友|同事|室友|家人)|他|她|孩子|儿子|女儿|老婆|老公|妈妈|妈|爸爸|爸|朋友|同事|室友|家人)(?:刚才|刚刚|今天|昨天|已经|也)?[^。]*(?:吃|喝|啃|炫|尝|嗦|称|买)/u;
const STATUS_QUERY_PATTERN =
  /(?:记上了?吗|记了?吗|记录了?吗|有没有记|是否记|刚才.*(?:记|录).*(?:吗|没有|没|成功))[？?。！!]*$/u;

const MEAL_CONSUMPTION_PATTERN =
  /(?:吃(?:了|过)?|啃(?:了|过)?|炫(?:了|过)?|嗦(?:了|过)?|干掉了?|整了|进食了?|用餐了?|尝(?:了|过)?)/u;
const DRINK_CONSUMPTION_PATTERN = /(?:喝(?:了|过)?|饮用(?:了)?)/u;
const NUTRITIOUS_DRINK_PATTERN =
  /(?:奶|咖啡|茶|果汁|饮料|豆浆|蛋白粉|酒|汤|粥|奶昔|可乐|汽水|椰汁|酸奶)/u;
const PLAIN_WATER_PATTERN =
  /(?:白水|矿泉水|饮用水|温水|凉水|开水|饮水|(?<!碳)水(?!煮|果|产|饺))/u;
const WEIGHT_PATTERN = /(?:体重|称重|称了|称过|称了下|上秤|秤上)/u;
const PANTRY_ADD_PATTERN =
  /(?:买了|购入|采购|入库|收到了?|囤了|放进?冰箱|放进?冷冻)/u;
const INVENTORY_CONSUMPTION_PATTERN =
  /(?:库存(?:里|中|的)?|冰箱里|冷冻室里|冰柜里|橱柜里|刚买的|新买的)/u;

const OPEN_PORTION_PATTERN =
  /(?:一点(?:儿)?|点儿|一些|吃了些|喝了些|几口|几粒|几颗|几块|几片|几勺|几把|一小?把|十来|差不多|大概|左右|尝了|尝过)/u;
const NATURAL_QUANTITY_PATTERN =
  /(?:半|[一二两俩三四五六七八九十百千万\d]+(?:\.\d+)?)?\s*(?:个|根|颗|粒|只|枚|块|片|碗|盘|份|包|袋|盒|瓶|杯|勺|串|条|斤|公斤|千克|克|kg|g|毫升|升|ml|l)(?=$|[\s，。！？、,!?;；:]|[\u4e00-\u9fff])/iu;
const PHYSICAL_QUANTITY_PATTERN =
  /\d+(?:\.\d+)?\s*(?:kg|g|ml|l|公斤|千克|克|毫升|升)(?=$|[\s，。！？、,!?;；:]|[\u4e00-\u9fff])/iu;
const COLLOQUIAL_TWO_PATTERN = /(?:俩|两个|两根|两袋|两盒|两瓶|两包)/u;
const NUMBER_PATTERN = /\d{2,3}(?:\.\d+)?/u;

function sortedDomains(domains: Set<DirectWriteDomain>): DirectWriteDomain[] {
  return [...domains].sort();
}

function hasExecutableQuantity(text: string): boolean {
  return NATURAL_QUANTITY_PATTERN.test(text) ||
    PHYSICAL_QUANTITY_PATTERN.test(text) ||
    COLLOQUIAL_TWO_PATTERN.test(text);
}

/**
 * Resolve only the small set of everyday writes whose authorization and
 * quantity are explicit in the current user sentence. Everything else stays
 * on the existing guarded route instead of being guessed here.
 */
export function classifyDirectWrite(rawText: string): DirectWriteDecision {
  const text = rawText.trim();
  if (
    text === "" ||
    NO_WRITE_PATTERN.test(text) ||
    OTHER_PERSON_PATTERN.test(text) ||
    STATUS_QUERY_PATTERN.test(text)
  ) {
    return { kind: "unhandled" };
  }

  const domains = new Set<DirectWriteDomain>();
  const hasWeight = (WEIGHT_PATTERN.test(text) && NUMBER_PATTERN.test(text)) ||
    /(?:^|[，,。；;]\s*)我?\s*\d{2,3}(?:\.\d+)?\s*(?:kg|公斤|斤|磅|lb)(?:$|[，,。；;\s])/iu.test(text);
  const hasPlainWater = DRINK_CONSUMPTION_PATTERN.test(text) &&
    PLAIN_WATER_PATTERN.test(text) &&
    !NUTRITIOUS_DRINK_PATTERN.test(text);
  const hasFoodMeal = MEAL_CONSUMPTION_PATTERN.test(text);
  const hasMeal = hasFoodMeal ||
    (DRINK_CONSUMPTION_PATTERN.test(text) && NUTRITIOUS_DRINK_PATTERN.test(text));
  const hasPantry = PANTRY_ADD_PATTERN.test(text);

  if (hasWeight) domains.add("weight");
  if (hasPlainWater) domains.add("water");
  if (hasMeal && (hasFoodMeal || !hasPlainWater)) domains.add("meal");
  if (hasPantry) domains.add("pantry");

  if (domains.size > 1) {
    return { kind: "compound", domains: sortedDomains(domains) };
  }

  if (domains.has("weight")) {
    const hasExplicitUnit = /(?:kg|公斤|斤|磅|lb)\b/iu.test(text);
    return {
      kind: "direct",
      domain: "weight",
      action: "record",
      ...(hasExplicitUnit ? {} : { defaults: { unit: "kg" } }),
    };
  }

  if (domains.has("water")) {
    return hasExecutableQuantity(text)
      ? { kind: "direct", domain: "water", action: "record" }
      : { kind: "unhandled" };
  }

  if (domains.has("pantry")) {
    return hasExecutableQuantity(text)
      ? { kind: "direct", domain: "pantry", action: "add" }
      : { kind: "unhandled" };
  }

  if (domains.has("meal")) {
    if (OPEN_PORTION_PATTERN.test(text) || !hasExecutableQuantity(text)) {
      return { kind: "clarify", domain: "meal", action: "preview_record" };
    }
    return {
      kind: "direct",
      domain: "meal",
      action: "record",
      ...(INVENTORY_CONSUMPTION_PATTERN.test(text)
        ? { route: "inventory_consumption" as const }
        : {}),
    };
  }

  return { kind: "unhandled" };
}

export function directWriteInstruction(
  decision: DirectWriteDecision,
): string | undefined {
  if (decision.kind === "clarify") {
    return "[Private diet routing] This is a completed meal with a genuinely open portion. Build one useful bounded estimate and call diet_meal preview_record exactly once. Keep business data unchanged until the user answers one short clarification. Do not call record, query history, or ask multiple questions.";
  }
  if (decision.kind !== "direct") {
    return undefined;
  }
  if (decision.domain === "meal") {
    if (decision.route === "inventory_consumption") {
      return "[Private diet routing] This is completed consumption explicitly tied to home inventory. Call diet_pantry search exactly once with the user's product wording and nutrition_mode summary, then call diet_meal record exactly once with the returned inventory_match_handle. Do not call pantry query, do not call pantry deduct separately, and do not ask for confirmation. The meal transaction owns both intake and inventory deduction.";
    }
    return "[Private diet routing] This is a completed meal fact with an executable natural quantity. Call diet_meal record exactly once. Do not call preview_record, do not ask for confirmation, and do not ask for an exact weight when a common edible-portion estimate is sufficient. Preserve the user's natural count; exclude inedible core, peel, shell, bone, or pit from nutrition and label the estimated edible portion in the item.";
  }
  if (decision.domain === "weight") {
    return "[Private diet routing] This is an explicit completed body-weight measurement. When the current sentence omits the unit, default it to kg. Call diet_weight record exactly once. Do not ask for the unit, do not preview, and do not ask for confirmation.";
  }
  if (decision.domain === "pantry") {
    return "[Private diet routing] This is an ordinary completed pantry intake. Call diet_pantry add exactly once. Do not ask the user for a production date or expiry when omitted. Pass the food, quantity, natural unit, explicit location if present, and source_text; the backend deterministically infers storage and estimated expiry without a confirmation round.";
  }
  return "[Private diet routing] This is a completed plain-water fact with an explicit amount. Call diet_water record exactly once. Do not ask for confirmation. Use the tool's compact water-only receipt and do not expand it into meal nutrition metrics.";
}
