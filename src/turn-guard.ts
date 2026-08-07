import { TOOL_NAMES } from "./generated/tool-contracts.js";
import { classifyDirectWrite } from "./direct-write-policy.js";

export type DietDomain = keyof typeof TOOL_NAMES;

export type TurnIntentMode =
  | "read_only"
  | "single_domain_write"
  | "multi_domain_write"
  | "workflow_confirmation"
  | "ambiguous";

export type TurnIntent = {
  mode: TurnIntentMode;
  domains: DietDomain[];
  writeScope?: "domain" | "create" | "targeted";
  allowedActions?: string[];
  contextualTarget?: true;
  requiresTrustedSessionTarget?: true;
  completedConsumption?: true;
  requiresExplicitOccurredAt?: true;
  operationStatusQuery?: true;
  finalizedSupplementalWrite?: true;
  directWrite?: true;
  directWriteDefaults?: Record<string, string>;
};

export type TurnAuthorization =
  | { allowed: true }
  | {
      allowed: false;
      code:
        | "READ_ONLY_TURN"
        | "COMPOUND_WRITE_REQUIRES_SPLIT"
        | "DOMAIN_NOT_AUTHORIZED"
        | "CONFIRMATION_HANDLE_REQUIRED"
        | "WRITE_NOT_AUTHORIZED"
        | "OCCURRED_AT_REQUIRED"
        | "STATUS_QUERY_ROUTE_REQUIRED"
        | "TURN_TOOL_BUDGET_EXHAUSTED";
      message: string;
    };

const TOOL_DOMAINS = new Map<string, DietDomain>(
  Object.entries(TOOL_NAMES).map(([domain, name]) => [name, domain as DietDomain]),
);

const CONFIRMATION_PATTERN =
  /^(?:(?:好|好的|可以|行|确认|确定|同意)(?:了|吧|哈|呢)?(?:[，,\s]*(?:就)?按(?:这个|上面|刚才的)?(?:记|记录|提交|执行|删除))?|(?:确认|确定|同意)(?:就)?(?:记上|记下|提交|执行)|(?:就)?按(?:这个|上面|刚才的)(?:记|记录|提交|执行|删除)|(?:就)?记上|记吧|提交|继续)[。！!吧哈呢\s]*$/u;

const SIMPLE_CONFIRMATION_PATTERN =
  /^(?:1|没问题|好[，,\s]*就按这个)[。！!吧哈呢\s]*$/u;

const NATURAL_WORKFLOW_CONFIRMATION_PATTERN =
  /^(?:确认|确定|同意)[，,\s]*(?!一下)(?:(?![？?]).){0,120}(?:入库|补记|补录|记上|记下|记录|提交|执行)[。！!吧哈呢\s]*$/u;

const FINALIZED_SUPPLEMENTAL_RECORD_PATTERN =
  /(?:就按[^。！？?]{0,60}(?:记|记录)(?:上|下)?(?:吧)?|(?:直接|现在)(?:帮我)?(?:记|记录|落账|写入))/u;

const SUPPLEMENTAL_DATA_PATTERN =
  /(?:\d+(?:\.\d+)?\s*(?:克|g|千克|kg|毫升|ml|升|l|粒|个|盒|包|根|份|千卡|kcal)|每\s*(?:100\s*(?:克|g|毫升|ml)|份|盒|包)|热量|蛋白(?:质)?|脂肪|碳水|纤维)/iu;

const STATUS_QUERY_PATTERN =
  /(?:记上了?吗|记了?吗|记录了?吗|有没有记|是否记|刚才.*(?:记|录).*(?:吗|没有|没)|(?:吃|喝|买|称).*(?:什么|啥|哪些|多少|几|没有|吗|么)|(?:查|查询|看看|看下|显示|告诉我|核对|确认一下).*(?:记录|库存|进度|体重|饮水|餐|吃|喝)|(?:库存|进度|体重|饮水).*(?:多少|怎样|怎么样|如何|吗|呢|剩|还有))/u;

const WRITE_STATUS_QUESTION_PATTERN =
  /(?:记|记录|删除|删掉|删|撤销|重做|修改|改)[^。？！?]*(?:了吗|了么|了没|没有)[？?。！!]?$/u;

const OPERATION_STATUS_QUERY_PATTERN =
  /(?:(?:刚才|刚刚)[^。！？?]{0,16}(?:记上|记入|记录|写入)[^。！？?]{0,8}(?:吗|么|没有|没|成功)|(?:有没有|是否)[^。！？?]{0,8}(?:记上|记入|记录|写入)[^。！？?]{0,8}(?:刚才|刚刚))/u;

const COMMAND_WRITE_PATTERN =
  /(?:帮我(?:补记|补录|记|记录|录入|添加|修改|改|删除|删|移除|撤销|重做|设置|更新)|补记|补录|记一下|记录一下|录入|入库|加到|改成|改一下|修改为|更正|纠正|删除|删掉|移除|撤销|重做|扔掉|丢掉|丢弃|退货|送给|赠送|移到|冷冻|解冻|设置为|更新为|没记.*(?:补|记|录))/u;

const TARGETED_WRITE_PATTERN =
  /(?:帮我(?:修改|改|删除|删|移除|撤销|重做)|改成|改一下|修改为|更正|纠正|删除|删掉|移除|撤销|重做|扔掉|丢掉|丢弃|退货|送给|赠送|移到|冷冻|解冻)/u;

const CREATE_WRITE_PATTERN =
  /(?:帮我(?:补记|补录|记|记录|录入|添加)|补记|补录|记一下|记录一下|录入|入库|加到|没记.*(?:补|记|录))/u;

const CONDITIONAL_CREATE_PATTERN =
  /(?:没记|没有记|未记录)[\s\S]*(?:补记|补录|帮我(?:记|记录))/u;

const MEAL_RECORD_SHORTHAND_PATTERN =
  /(?:^|[，,。；;\s])(?:记|记录)(?:一下|下)?\s*(?:一个|一份|一顿|一餐|点|些|几口)/u;

const CONTEXTUAL_CORRECTION_PATTERN =
  /^(?:不对|不是)(?:[，,\s]*(?:是|应该是))?|^(?:应该是|(?:其实|实际(?:上)?)(?:[^，,。；;]{0,12})?是)/u;

const RECENT_CONTEXT_TARGET_PATTERN = /(?:刚才|刚刚)/u;

const COMPLETED_FACT_PATTERN =
  /(?:吃了|吃过|喝了|喝过|买了|购入|称了|称重(?:是|为|到)?|上秤(?:是|为)?)/u;

const NO_WRITE_PATTERN =
  /(?:算了|取消|不记了?|别记|不用记|差点|险些|本来想|最后没|没有吃|没吃|没有喝|没喝|打算|准备|计划|等会|待会|稍后|明天再|可能会|想要吃|想吃|想喝)/u;

const COMPLETED_CONSUMPTION_PATTERN = /(?:吃了|吃过|喝了|喝过|饮用)/u;
const COARSE_HISTORICAL_TIME_PATTERN =
  /(?:(?:今天|昨天|前天)?(?:早上|早晨|上午|中午|下午|傍晚|晚上|夜里|凌晨)|今早|昨早|今晚|昨晚|昨夜)/u;

const OTHER_PERSON_ONLY_PATTERN =
  /^(?:(?:我(?:的)?)(?:孩子|儿子|女儿|老婆|老公|妈妈|妈|爸爸|爸|朋友|同事|室友|家人)|他|她|孩子|儿子|女儿|老婆|老公|妈妈|妈|爸爸|爸|朋友|同事|室友|家人)(?:刚才|今天|昨天|已经|也)?[^。]*(?:吃|喝|称|买)/u;

const ATOMIC_COOKING_LEFTOVER_PATTERN =
  /(?:炒|煮|炖|烤|蒸|做)[^。；;]*(?:吃|用餐)[^。；;]*(?:剩|余)[^。；;]*(?:冰箱|冷藏|冷冻|留)/u;

const TARGET_DELETE_PATTERN = /(?:删除|删掉|删了|移除|扔掉|扔了|丢掉|丢了|丢弃|退货|送给|赠送)/u;
const TARGET_UPDATE_PATTERN =
  /(?:修改|改成|改一下|修改为|更正|纠正|不对|应该是|(?:其实|实际(?:上)?)(?:[^，,。；;]{0,12})?是)/u;
const TRANSACTION_PATTERN = /(?:撤销|重做|恢复刚才)/u;
const RECENT_MEAL_DELETE_TARGET_PATTERN = /(?:刚才|刚刚|这条|那条|整条)/u;
const EXPLICIT_CLOCK_TIME_PATTERN =
  /(?:\b(?:[01]?\d|2[0-3])[:：][0-5]\d\b|(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\s*(?:[01]?\d|2[0-3])点(?:半|[0-5]?\d分)?)/u;
const EXPLICIT_NON_MEAL_CONTEXT_TARGET_PATTERN =
  /(?:白水|矿泉水|饮用水|温水|凉水|开水|饮水|水(?!煮|果|产|饺)|体重|称重|上秤)/u;
const FOLLOWUP_MUTATION_AFTER_TRANSACTION_PATTERN =
  /(?:撤销|重做|恢复刚才)[\s\S]*(?:然后|再|并且|顺便|同时)[\s\S]*(?:补记|补录|记一下|记录一下|录入|入库|加到|买了|购入|采购|放进?冰箱|放进?冷冻|吃了|喝了|称重|上秤|修改|改成|删除|删掉|扔掉|丢掉|丢弃|退货|送给|赠送|移到|冷冻|解冻|设置|更新)/u;

export function queryLike(text: string): boolean {
  if (STATUS_QUERY_PATTERN.test(text) || WRITE_STATUS_QUESTION_PATTERN.test(text)) {
    return true;
  }
  if (!/[？?吗么呢]$/u.test(text)) {
    return false;
  }
  return /(?:什么|啥|哪些|多少|几|有没有|是否|怎么|怎样|如何|为何|为什么|记|查|看|剩|还有|吃|喝|体重|库存|进度)/u.test(text);
}

function writeDomains(text: string): DietDomain[] {
  const domains = new Set<DietDomain>();
  const weightFact =
    /(?:体重|称重|称了|称过|上秤|秤上)/u.test(text) ||
    /(?:^|[，,。；;]\s*)我?\s*\d{2,3}(?:\.\d+)?\s*(?:kg|KG|公斤|斤|磅|lb|LB)(?:$|[，,。；;\s])/u.test(text);
  if (weightFact) {
    domains.add("weight");
  }

  const waterFact =
    /(?:喝|饮|补|记|录)[^。；;]*(?:白水|矿泉水|饮用水|温水|凉水|开水|饮水|(?<!碳)水(?!煮|果|产|饺))/u.test(text) ||
    /(?:白水|矿泉水|饮用水|温水|凉水|开水|饮水)[^。；;]*(?:毫升|ml|升|l|杯|瓶)/iu.test(text);
  if (waterFact) {
    domains.add("water");
  }

  const ateFood = /(?:吃了|吃过|吃个|吃了一?|进食|用餐|加餐|补记[^。；;]*(?:早餐|午餐|晚餐|夜宵|食物|吃))/u.test(text);
  const drankSomething = /(?:喝了|喝过|饮用)/u.test(text);
  const namedNutritiousDrink =
    /(?:奶|咖啡|茶|果汁|饮料|豆浆|蛋白粉|酒|汤|粥|奶昔|可乐|汽水|椰汁|酸奶)/u.test(text);
  const onlyPlainWater = drankSomething && waterFact &&
    !/(?:奶|咖啡|茶|果汁|饮料|豆浆|蛋白粉|酒|汤|粥|奶昔)/u.test(text) &&
    !/(?:吃了|吃过|进食|用餐)/u.test(text);
  if (ateFood || (drankSomething && !onlyPlainWater && namedNutritiousDrink)) {
    domains.add("meal");
  }

  const pantryMutation =
    /(?:买了|购入|采购|入库|放进?冰箱|放进?冷冻|扔掉|扔了|丢掉|丢了|丢弃|退货|送给|赠送|移到|开封了|打开了|冷冻|解冻)/u.test(text);
  if (pantryMutation && !ATOMIC_COOKING_LEFTOVER_PATTERN.test(text)) {
    domains.add("pantry");
  }

  if (TRANSACTION_PATTERN.test(text)) {
    domains.add("transaction");
  }

  const systemMutation =
    /(?:设置|设为|修改|更新|改成|删除|忘掉)[^。；;]*(?:目标|忌口|偏好|过敏|资料)/u.test(text) ||
    /(?:目标|忌口|偏好|过敏|资料)[^。；;]*(?:设置|设为|修改|更新|改成|删除|忘掉)/u.test(text);
  if (systemMutation) {
    domains.add("system");
  }

  if (domains.size === 0 && MEAL_RECORD_SHORTHAND_PATTERN.test(text)) {
    domains.add("meal");
  }

  if (
    domains.has("transaction") &&
    !FOLLOWUP_MUTATION_AFTER_TRANSACTION_PATTERN.test(text)
  ) {
    return ["transaction"];
  }

  return [...domains].sort();
}

function allowedActionsFor(text: string, domain: DietDomain): string[] {
  if (domain === "meal") {
    if (ATOMIC_COOKING_LEFTOVER_PATTERN.test(text)) {
      return ["record_cooking", "record_prepared"];
    }
    if (TARGET_DELETE_PATTERN.test(text)) {
      return ["delete"];
    }
    if (TARGET_UPDATE_PATTERN.test(text)) {
      return ["update"];
    }
    return ["record", "preview_record"];
  }
  if (domain === "water" || domain === "weight") {
    if (TARGET_DELETE_PATTERN.test(text)) {
      return ["delete"];
    }
    if (TARGET_UPDATE_PATTERN.test(text)) {
      return ["update"];
    }
    return ["record"];
  }
  if (domain === "pantry") {
    if (/(?:扔掉|扔了|丢掉|丢了|丢弃|退货|送给|赠送)/u.test(text)) {
      return ["discard"];
    }
    if (/(?:开封了|打开了)/u.test(text)) {
      return ["open"];
    }
    if (/解冻/u.test(text)) {
      return ["thaw"];
    }
    if (/冷冻/u.test(text)) {
      return ["freeze"];
    }
    if (/移到/u.test(text)) {
      return ["adjust"];
    }
    return ["add", "preview_add"];
  }
  if (domain === "transaction") {
    return /重做/u.test(text) ? ["redo"] : ["undo"];
  }
  if (domain === "system") {
    if (/(?:忌口|偏好|过敏|资料)/u.test(text)) {
      return /(?:删除|忘掉)/u.test(text)
        ? ["forget_preference"]
        : ["update_preferences"];
    }
    return ["update_goals"];
  }
  return [];
}

function contextualTargetActions(text: string): string[] | undefined {
  if (TARGET_DELETE_PATTERN.test(text)) {
    return ["delete", "discard", "cancel_shopping_list"];
  }
  if (TARGET_UPDATE_PATTERN.test(text)) {
    return ["update", "adjust", "preview_update_metadata"];
  }
  if (/撤销/u.test(text)) {
    return ["undo"];
  }
  if (/重做/u.test(text)) {
    return ["redo"];
  }
  return undefined;
}

function requiresTrustedSessionTarget(text: string): boolean {
  if (EXPLICIT_NON_MEAL_CONTEXT_TARGET_PATTERN.test(text)) {
    return false;
  }
  if (
    TARGET_UPDATE_PATTERN.test(text) &&
    (CONTEXTUAL_CORRECTION_PATTERN.test(text) ||
      RECENT_CONTEXT_TARGET_PATTERN.test(text))
  ) {
    return true;
  }
  return TARGET_DELETE_PATTERN.test(text) &&
    RECENT_MEAL_DELETE_TARGET_PATTERN.test(text) &&
    !EXPLICIT_CLOCK_TIME_PATTERN.test(text);
}

export function classifyTurnIntent(rawText: string): TurnIntent {
  const text = rawText.trim();
  if (text === "") {
    return { mode: "ambiguous", domains: [] };
  }
  if (
    OPERATION_STATUS_QUERY_PATTERN.test(text) &&
    !CONDITIONAL_CREATE_PATTERN.test(text)
  ) {
    return {
      mode: "read_only",
      domains: [],
      operationStatusQuery: true,
    };
  }
  if (
    CONFIRMATION_PATTERN.test(text) ||
    SIMPLE_CONFIRMATION_PATTERN.test(text) ||
    NATURAL_WORKFLOW_CONFIRMATION_PATTERN.test(text)
  ) {
    return { mode: "workflow_confirmation", domains: [] };
  }
  if (
    FINALIZED_SUPPLEMENTAL_RECORD_PATTERN.test(text) &&
    SUPPLEMENTAL_DATA_PATTERN.test(text) &&
    !/[？?]/u.test(text) &&
    !NO_WRITE_PATTERN.test(text)
  ) {
    return {
      mode: "single_domain_write",
      domains: ["meal"],
      writeScope: "domain",
      allowedActions: ["record"],
      finalizedSupplementalWrite: true,
    };
  }

  const contextualDeleteCommand = TARGET_DELETE_PATTERN.test(text) &&
    RECENT_MEAL_DELETE_TARGET_PATTERN.test(text);
  const explicitCommand = COMMAND_WRITE_PATTERN.test(text) ||
    MEAL_RECORD_SHORTHAND_PATTERN.test(text) ||
    CONTEXTUAL_CORRECTION_PATTERN.test(text) ||
    contextualDeleteCommand;
  const completedFact = COMPLETED_FACT_PATTERN.test(text);
  const domains = writeDomains(text);
  if (OTHER_PERSON_ONLY_PATTERN.test(text)) {
    return { mode: "read_only", domains: [] };
  }
  if (
    queryLike(text) &&
    !CONDITIONAL_CREATE_PATTERN.test(text) &&
    !(explicitCommand && completedFact && domains.length === 1)
  ) {
    return { mode: "read_only", domains: [] };
  }
  if (NO_WRITE_PATTERN.test(text)) {
    return { mode: "read_only", domains: [] };
  }
  if (domains.length > 1) {
    return { mode: "multi_domain_write", domains, writeScope: "domain" };
  }
  const directWrite = ATOMIC_COOKING_LEFTOVER_PATTERN.test(text)
    ? { kind: "unhandled" as const }
    : classifyDirectWrite(text);
  if (directWrite.kind === "compound") {
    return {
      mode: "multi_domain_write",
      domains: directWrite.domains,
      writeScope: "domain",
    };
  }
  if (directWrite.kind === "direct") {
    return {
      mode: "single_domain_write",
      domains: [directWrite.domain],
      writeScope: "domain",
      allowedActions: [directWrite.action],
      directWrite: true,
      ...(directWrite.defaults === undefined
        ? {}
        : { directWriteDefaults: directWrite.defaults }),
      ...(directWrite.domain === "meal"
        ? { completedConsumption: true as const }
        : {}),
      ...(directWrite.domain === "water" &&
          COARSE_HISTORICAL_TIME_PATTERN.test(text)
        ? { requiresExplicitOccurredAt: true as const }
        : {}),
    };
  }
  if (directWrite.kind === "clarify") {
    return {
      mode: "single_domain_write",
      domains: [directWrite.domain],
      writeScope: "domain",
      allowedActions: [directWrite.action],
    };
  }
  if (!explicitCommand && !completedFact && domains.length === 0) {
    return { mode: "ambiguous", domains: [] };
  }
  if (domains.length === 0) {
    if (explicitCommand) {
      const trustedSessionTargetRequired = requiresTrustedSessionTarget(text);
      const contextualTarget = trustedSessionTargetRequired ||
        (RECENT_CONTEXT_TARGET_PATTERN.test(text) &&
          !TARGET_DELETE_PATTERN.test(text));
      return {
        mode: "single_domain_write",
        domains,
        writeScope: TARGETED_WRITE_PATTERN.test(text) ||
            contextualDeleteCommand ||
            CONTEXTUAL_CORRECTION_PATTERN.test(text)
          ? "targeted"
          : CREATE_WRITE_PATTERN.test(text)
          ? "create"
          : "domain",
        allowedActions: contextualTargetActions(text),
        ...(contextualTarget ? { contextualTarget: true as const } : {}),
        ...(trustedSessionTargetRequired
          ? { requiresTrustedSessionTarget: true as const }
          : {}),
      };
    }
    return { mode: "ambiguous", domains };
  }
  if (domains.length === 1) {
    const domain = domains[0];
    const allowedActions = allowedActionsFor(text, domain);
    const contextualTarget = allowedActions.includes("update") &&
      (CONTEXTUAL_CORRECTION_PATTERN.test(text) ||
        RECENT_CONTEXT_TARGET_PATTERN.test(text));
    return {
      mode: "single_domain_write",
      domains,
      writeScope: "domain",
      allowedActions,
      ...(contextualTarget ? { contextualTarget: true as const } : {}),
      ...(domain === "meal" && contextualTarget
        ? { requiresTrustedSessionTarget: true as const }
        : {}),
      ...(domain === "meal" && COMPLETED_CONSUMPTION_PATTERN.test(text)
        ? { completedConsumption: true as const }
        : {}),
      ...(domain === "water" && COARSE_HISTORICAL_TIME_PATTERN.test(text)
        ? { requiresExplicitOccurredAt: true as const }
        : {}),
    };
  }
  return { mode: "multi_domain_write", domains, writeScope: "domain" };
}

function isReadAction(domain: DietDomain, action: string): boolean {
  if (domain === "report") {
    return true;
  }
  if (domain === "meal" || domain === "water" || domain === "weight") {
    return action === "query" || action === "nutrition_estimate" ||
      action === "suggest_recipes";
  }
  if (domain === "pantry") {
    return action === "query" || action === "search" ||
      action === "query_shopping_list";
  }
  if (domain === "transaction") {
    return action === "get_recent";
  }
  return action === "query_goals" || action === "query_preferences" ||
    action === "query_nutrition_backfill" || action === "self_check" ||
    action === "maintenance_status" || action === "maintenance_history" ||
    action === "validate_database" || action === "validate_import";
}

function isPreviewAction(action: string): boolean {
  return action.startsWith("preview_");
}

function hasOpaqueTargetHandle(params: Record<string, unknown>): boolean {
  return Object.entries(params).some(([key, value]) =>
    key.endsWith("_handle") && typeof value === "string" &&
    value.startsWith("wfh_")
  );
}

function isContextualCreate(domain: DietDomain, action: string): boolean {
  return (domain === "meal" && [
    "record",
    "preview_record",
    "record_cooking",
    "record_prepared",
  ].includes(action)) ||
    (domain === "water" && action === "record") ||
    (domain === "weight" && action === "record") ||
    (domain === "pantry" && ["add", "preview_add"].includes(action));
}

function isTargetedAction(domain: DietDomain, action: string): boolean {
  if (domain === "meal" || domain === "water" || domain === "weight") {
    return action === "update" || action === "delete";
  }
  if (domain === "pantry") {
    return [
      "adjust",
      "deduct",
      "discard",
      "open",
      "freeze",
      "thaw",
      "preview_update_metadata",
      "preview_link_nutrition",
      "cancel_shopping_list",
    ].includes(action);
  }
  if (domain === "transaction") {
    return action === "undo" || action === "redo";
  }
  return false;
}

export function dietDomainForTool(toolName: string): DietDomain | undefined {
  return TOOL_DOMAINS.get(toolName);
}

export function isDietReadOperation(
  toolName: string,
  params: Record<string, unknown>,
): boolean {
  const domain = TOOL_DOMAINS.get(toolName);
  const action = typeof params.action === "string" ? params.action : "";
  return domain !== undefined && isReadAction(domain, action);
}

function hasWorkflowCommitHandle(
  domain: DietDomain,
  action: string,
  params: Record<string, unknown>,
): boolean {
  const hasHandle = (key: string) =>
    typeof params[key] === "string" && String(params[key]).startsWith("wfh_");
  if (action.startsWith("commit_")) {
    return hasHandle("commit_handle");
  }
  if (domain === "weight" && action === "delete") {
    return hasHandle("commit_handle");
  }
  if (domain === "transaction" && (action === "undo" || action === "redo")) {
    return hasHandle("operation_handle");
  }
  return false;
}

function denied(
  code: Exclude<TurnAuthorization, { allowed: true }>["code"],
  message: string,
): TurnAuthorization {
  return { allowed: false, code, message };
}

export function authorizeTurnTool(
  intent: TurnIntent,
  toolName: string,
  params: Record<string, unknown>,
  terminalFailures = 0,
): TurnAuthorization {
  const domain = TOOL_DOMAINS.get(toolName);
  if (domain === undefined) {
    return { allowed: true };
  }
  const action = typeof params.action === "string" ? params.action : "";
  if (intent.operationStatusQuery === true) {
    return domain === "transaction" && action === "get_recent"
      ? { allowed: true }
      : denied(
        "STATUS_QUERY_ROUTE_REQUIRED",
        "这是最近一次饮食操作结果查询；只允许调用 diet_transaction get_recent 一次，不能改查 Meal、Pantry、Report 或重放写入。",
      );
  }
  if (isReadAction(domain, action)) {
    return { allowed: true };
  }
  if (terminalFailures >= 2) {
    return denied(
      "TURN_TOOL_BUDGET_EXHAUSTED",
      "本轮饮食工具已连续失败两次，已停止继续调用；请根据最后一个错误补充或更正信息。",
    );
  }
  if (
    domain === "water" && action === "record" &&
    intent.requiresExplicitOccurredAt === true &&
    typeof params.occurred_at !== "string"
  ) {
    return denied(
      "OCCURRED_AT_REQUIRED",
      "这条历史饮水只有粗略时段；请先确认大致时间，本次尚未记录。",
    );
  }
  if (intent.mode === "read_only") {
    return denied(
      "READ_ONLY_TURN",
      "这是查询或核对请求，只能读取，不能自动补写或修改记录。",
    );
  }
  if (intent.mode === "multi_domain_write") {
    return denied(
      "COMPOUND_WRITE_REQUIRES_SPLIT",
      "这句话同时包含多个写入域；为避免部分提交，本轮保持零写入，请拆分后逐项确认。",
    );
  }
  if (intent.mode === "workflow_confirmation") {
    return hasWorkflowCommitHandle(domain, action, params)
      ? { allowed: true }
      : denied(
        "CONFIRMATION_HANDLE_REQUIRED",
        "确认只能提交当前有效的预览句柄，不能由一句裸确认创建新的写入。",
      );
  }
  if (intent.mode === "ambiguous" && isPreviewAction(action)) {
    return { allowed: true };
  }
  if (intent.mode !== "single_domain_write") {
    return denied(
      "WRITE_NOT_AUTHORIZED",
      "当前消息没有明确授权这项写入；已保持零写入。",
    );
  }
  if (intent.domains.length === 0) {
    if (intent.writeScope === "targeted") {
      return isTargetedAction(domain, action) &&
          hasOpaqueTargetHandle(params) &&
          intent.allowedActions?.includes(action) === true
        ? { allowed: true }
        : denied(
          "WRITE_NOT_AUTHORIZED",
          "这项更正或删除没有绑定到明确记录；已保持零写入。",
        );
    }
    if (intent.writeScope === "create" && isContextualCreate(domain, action)) {
      return { allowed: true };
    }
    return denied(
      "WRITE_NOT_AUTHORIZED",
      "当前消息没有明确授权这项写入；已保持零写入。",
    );
  }
  if (!intent.domains.includes(domain)) {
    return denied(
      "DOMAIN_NOT_AUTHORIZED",
      "当前消息没有授权修改这个业务域；已保持零写入。",
    );
  }
  if (intent.allowedActions?.includes(action) !== true) {
    return denied(
      "WRITE_NOT_AUTHORIZED",
      "当前消息没有授权这个具体操作；已保持零写入。",
    );
  }
  return { allowed: true };
}
