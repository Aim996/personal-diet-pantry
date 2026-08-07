# 食序管家工具参考（v0.7.4.0）

本页是 Personal Diet Pantry 连续开发版本的工具契约索引。**SQLite 是正式事实来源**；工具成功返回的结构化结果才可说明发生了什么。生成的 Markdown 报告、JSON/CSV 导出或文件系统内容均不是业务事实来源，也不得用来替代七个工具或直接编辑 SQLite。当前 action、首选能力路线、读写属性与测试映射以 `contracts/tools.yaml` 为事实源，并生成 Skill、TypeScript 与 Python 清单。

所有工具调用都使用严格的 action schema；`context`（若适用）仅是会话上下文，不是业务字段。`*_handle`、`*_code`、`commit_handle`、`operation_handle` 和 `item_handle` 都是不透明值：只能在工具返回后传给其匹配的下一步，绝不能猜测、拼接或展示给普通用户。

## 共同约定

### 固定路由

| 用户意图 | 固定工具与优先 action | 不应改走 |
| --- | --- | --- |
| 已发生的饮食、营养饮料、做饭并食用 | `diet_meal record`；完整做饭、食用并有剩菜时用 `diet_meal record_cooking` | 不用 `diet_water` 重复记录食物所含水分；外食不扣家庭库存 |
| 已发生的纯白水 | `diet_water record` | 不用 `diet_meal record` |
| 当前体重或体重历史维护 | `diet_weight` | 不混入饮食、饮水或营养报告 |
| 按用户原话定位库存商品 | `diet_pantry search` | 不先读取宽泛库存；只有明确浏览完整库存才分页 `query` |
| 入库、库存状态或数量修正 | `diet_pantry` | 不用事务工具伪造库存变更 |
| 查找、撤销、重做既有操作 | `diet_transaction` | 先用 `get_recent` 找到人可辨识的目标，再 `undo`/`redo` |
| 进度、行动洞察或报告 | `diet_report progress`（进度）、`diet_report insights`（跨域优先级）及相应报告 action | 不读取生成报告文件；饭后不额外查询库存或报告来重建回执 |
| 偏好、目标、自检、备份、迁移、历史营养补录 | `diet_system` | 不直接操作数据库或数据目录 |

计划、可能发生、否认发生的事情一律不写入。清晰且已授权的事实使用下表中的直接正式 action，工具成功前不得说“已记录”“已入库”“已扣库存”或“已保存”。

### 直接 action 与 preview/commit

- 对清晰的已发生饮食、纯白水、体重和入库，分别直接使用 `record`、`record_cooking`、`record`、`record`、`add`；不要为了再次确认而先 preview。
- `preview_record` → `commit_record`、`preview_add` → `commit_add` 仅用于真实歧义或兼容旧流程。若 `diet_meal preview_record` 传入 `intent: "record"`，插件会把它标准化为 `record`，不是两阶段预览。
- 元数据、营养关联和手工扣减没有对应的直接正式 action，必须分别走其固定的 `preview_*` → `commit_*` 配对。预览本身不构成正式写入；只有匹配 commit 成功才是写入。
- 只把工具返回的真实 handle 传给配对 commit；不可跨对象、跨流程复用。`restore` 还必须传 `confirmed: true`。

### 读写标记与成功判断

- **读**：只读取、计算或准备结果，不改变正式 SQLite 事实。预览也归为读。
- **生成报告工件**：读取 SQLite 后生成或覆盖派生 Markdown，不修改 SQLite；成功结果只返回报告元数据，不返回报告正文。
- **写**：会建立、修改、删除、撤销/重做正式事实，或执行系统维护。`backup` 写入备份工件，但不改写业务事实。
- 每个结果保留兼容字段 `ok`，并以 `outcome` 明确语义：`write_committed`、`preview_ready`、`read_completed`、`no_op` 或 `failed`。成功结果包含 `data`；可另含 `warnings`、`requires_confirmation` 和 `confirmation_options`。只有 `write_committed` 能证明本次正式写入，`no_op` 表示状态已满足但没有新增事务。

### 错误熔断与重试

1. 后续动作没有固定次数上限，但每一步必须取得新事实、修正错误明确指出的字段、消除歧义、核验不确定结果，或采用当前可见说明与 Schema 证明等价的能力。
2. 以“能力 + 工具 + action + 规范化参数 + 错误特征”形成尝试指纹；相同指纹不得原样重复。工具缺失才允许解析等价能力，库存不足、低置信度或不可撤销等业务结果不得换工具绕过。
3. `NUTRITION_ESTIMATE_REQUIRED`：为原动作补充完整 C/D `nutrition_estimate`；若没有新证据则停止，不能说已经记录。
4. `DATABASE_INTEGRITY_ERROR`：本轮停止所有写工具调用；执行可用的 `diet_system self_check` 核验状态，不得重放原写入、改用 preview/add 或调用 `repair` 绕过错误。
5. `INVALID_INPUT`：仅当诊断包含 `field`、`reason`、`expected` 且 `retryable: true` 时，只修正被点名字段；不得无关重建参数或切换业务能力。
6. 写入超时、响应损坏或结果未知时，先按操作标识查询状态；预览或句柄过期时读取最新必要状态并按原始意图重建，不盲目重放。

失败、待确认或未接受的结果都不是成功写入。例如 `diet_meal` 在缺少 `items` 时返回 `outcome: "failed"` 和可修复的 `INVALID_INPUT` 字段提示；补全完整 `items` 后才可用新参数重试。相同会话里的相同确定性失败可短时复用；数据库忙、句柄过期、超时和结果不确定不缓存。

## `diet_meal`：饮食与做饭

用于已发生的饮食、完整做饭转换、熟食剩菜再次食用、餐次查询和餐次维护。`record`/`record_cooking`/`record_prepared` 成功的关键回执为 `daily_progress` 与 `inventory_effects`：前者给出本次提交后的受限进度和真实增量，后者只给出本次变动的库存行。来自库存且已选定商品时，餐项可携带 `inventory_match_handle`；服务直接复用句柄完成身份校验、批次扣减和营养快照缩放，不再次按名称搜索。不要再调用库存或报告工具来确认这次提交。

餐食公共 Schema 以根级 `$defs/$ref` 复用嵌套定义，保留八层原料并拒绝第九层，所有既有 action 保持可用；序列化 `MealParametersSchema` 的回归预算小于 125,000 字节。

### v0.6.1 直接营养输入契约

餐项携带直接营养事实时必须同时声明 `nutrition_basis`：

| 口径 | 配套食用维度 | 含义 |
| --- | --- | --- |
| `per_100g` | `consumed_weight_g` | 输入值按每 100 克缩放 |
| `per_100ml` | `consumed_volume_ml` | 输入值按每 100 毫升缩放 |
| `per_serving` | `consumed_servings` | 输入值按份数缩放 |
| `consumed_total` | 不要求额外缩放维度 | 输入值就是本次消费总量 |

口径和维度不匹配时写入前失败。明确体积饮品的隐性水分不得超过消费体积；按
`per_100g` 计算的食品水分不得超过食用质量。成功写入会保存规范化输入、缩放
系数、来源等级、规则/数据版本、份量证据和质量状态，以便重放与审计。

相同业务餐食的确认或网络重试由 `intake_fingerprint` 幂等处理，不应创建重复
活动餐食。`inventory_effects` 只来自本次已提交的 `pantry_movements`；空数组
表示本次没有扣减或新增库存。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `record` | 清晰、已发生的普通饮食；**写**。 | `occurred_at`、`meal_type`、`source_text`、`location_type`、完整 `items`；已通过库存搜索选定商品时可在对应餐项传返回的 `inventory_match_handle`。 | 正式餐次结果；可用 `daily_progress`、`inventory_effects` 生成回执。 | 计划/未发生；纯白水；完整做饭加剩菜；外食扣家庭库存；猜测句柄。 |
| `record_cooking` | 一次原子完成完整菜品的原料扣减、已食用部分记录和可选剩菜入库；**写**。 | `occurred_at`、`meal_type`、`source_text`；`dish` 必须含 `raw_name`、`normalized_name`、`unit`、`consumed_quantity`、非空 `ingredients`，可带 `leftover`。 | 正式做饭餐次、`daily_progress`、`inventory_effects`。 | 仅“做了/煮了”却未明确吃；用多个 meal/pantry 提交模拟原子转换。 |
| `record_prepared` | 记录已经吃掉的库存熟食剩菜；**写**。 | 定向库存搜索返回的 `prepared_food_handle`、`source_text`；可选 `quantity` 与配套 `unit`。省略数量时吃掉该熟食批次的全部剩余量。 | 复用熟食快照的正式餐次、`daily_progress`、只针对熟食批次的 `inventory_effects`。 | 重算菜谱营养；再次扣原材料；猜测或跨熟食复用 handle。 |
| `save_recipe` | 保存或更新一份可复用菜谱；**写**。 | `name`、1–30 个 `ingredients`、正数 `yield_quantity`、`yield_unit`、`source_text`，可选 `notes`。 | 菜谱档案；不会生成历史餐食。 | 把一次偶然用餐自动学习成菜谱；用菜谱代替实际做饭记录。 |
| `suggest_recipes` | 按菜谱和当前库存给出最多三项候选；**读**。 | 可选 `limit`（1–3）、`max_missing_items`（0–30）。 | 含库存覆盖、已有/缺少食材和理由的 `candidate_only` 候选。 | 宣称候选已做、已吃或已扣库存。 |
| `preview_meal_plan` | 生成有界的餐次候选预览；**读**。 | 可选 `meal_type`、`limit`（1–3）、`max_missing_items`。 | 最多三项只读候选，不落库。 | 把预览当成正式计划、餐食或采购清单。 |
| `preview_record` | 为有界份量估算、真实歧义或旧兼容流程准备餐次；**读，零业务写入**。 | 非空 `items`；模糊项携带原始 `portion_expression` 以及同单位、有序的 `lower_bound`、`suggested_amount`、`upper_bound`、策略与证据。未说明时间时省略 `occurred_at`。 | 一次返回整顿饭的 `resolution.quantity_estimates`、`requires_confirmation` 与 `commit_handle`；多个估算不拆成多次预览。 | 清晰且无需估算的已发生饮食；缺少/空 `items`；把预览说成已记录或已扣库存；传 `intent: "record"` 后期待它仍是预览。 |
| `commit_record` | 提交匹配的待确认餐次；**写**。 | 工具返回的 `commit_handle`；纯确认时传 `confirmed: true`。 | 正式餐次结果及其 `daily_progress`、`inventory_effects`、`resolution.confirmed_estimate(s)`。 | 没有当前未过期待确认记录；重建/修改预览内容；确认不安全库存匹配或数据库错误。 |
| `query` | 读取一个通用时间范围内的餐次；**读**。 | `occurred_on`、`calendar_window`、`rolling_window`、`local_range` 最多一个；可选 `meal_type`。明确或自然时间范围不应无故缩窄为 dinner。 | 结构化餐次，以及实际本地/UTC `scope`、`complete` 和当地时间投影。 | 同时传多个时间描述符；用它确认刚成功的提交；读取报告文件；把失败当作空结果。 |
| `update` | 原子替换一个已定位的普通餐食或完整做饭事件；**写**。 | `meal_handle` 或 `selector`（`occurred_at`、`source_text`）之一；`draft` 必须是完整普通餐食草稿，或含 `dish`、原料、已吃数量与可选剩菜的完整做饭草稿，二者不可混合。 | 已更新的正式餐次结果；做饭修正同步恢复旧原料扣减并回收未被后续使用的旧派生剩菜。 | 猜测 handle；用补充事实代替完整草稿；旧剩菜已有后续变动时强行修正；用来创建新餐次。 |
| `delete` | 删除一个已定位餐次；**写**。 | `meal_handle` 或 `selector`，`source_text`，可选 `intent: "record"`。 | 已删除的正式结果。 | 目标不明确；没有用户删除意图。 |
| `nutrition_estimate` | 为单个食物取得/校验 C/D 营养估算；**读**。 | `normalized_name`、`consumed_weight_g`，可选 `brand`、完整 `estimate`。 | 结构化营养估算，不是餐次写入。 | 以此宣称餐次已记录；将其作为纯白水记录。 |

`items` 中的营养估算使用 `nutrition_estimate`（C 或 D）、或营养事实；液体食品可有 `hydration_ml`，但它不是第二条白水事件。同一餐的多种液体各自保留 `consumed_volume_ml` 与 `per_100ml` 口径，不能先合并体积再缩放。明确去骨、去壳、去皮、剔脂或其他加工损耗可写入最多 8 条 `preparation_losses`；每条都要求正数克重与完整的 consumed-total 营养事实，系统在份量缩放后扣除损耗并保存审计证据。剩菜必须带食物名、规范名、数量、单位、储存位置和具体 `expires_at`；后续食用准备食品时只扣该准备食品，不再扣原料。

餐食、白水和体重查询共同使用时间描述符；UTC 是存储事实，当地时间字段和 `timezone_name` 是公共投影。日历窗口按用户 IANA 时区解释，明确 `local_range` 使用半开区间；不存在或歧义的夏令时墙钟时间会被拒绝，而不是猜测。

## `diet_water`：纯白水

仅管理纯白水摄入；奶、豆浆、茶、咖啡、汤、粥等随餐营养或液体水分属于 `diet_meal`。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `record` | 记录清晰、已发生的纯白水；**写**。 | 正数 `amount`、白水 `unit`、`occurred_at`、`source_text`。 | 正式白水记录及相关结构化结果。 | 食物/营养饮料或未发生的喝水计划。 |
| `query` | 读取一个时间范围内的白水记录；**读**。 | `occurred_on`、`calendar_window`、`rolling_window`、`local_range` 恰好一个。 | 结构化白水记录、`scope`、`complete` 和当地时间投影。 | 同时传多个时间描述符；用来重建 meal 返回的 `daily_progress`。 |
| `update` | 修改一条白水记录；**写**。 | `record_handle` 与完整白水记录字段。 | 已更新的正式白水结果。 | 猜测 handle；没有明确修改目标。 |
| `delete` | 删除一条白水记录；**写**。 | `record_handle`，可选 `deleted_at`、`source_text`。 | 已删除的正式白水结果。 | 目标不明。`source_text` 遗漏时插件才会补默认值，不应自行捏造。 |

## `diet_weight`：体重与 7 日趋势

只管理个人体重事实。`record` 的测量时间由服务端可信系统时钟生成，schema 不接受用户自定义时间。未给单位时默认 `kg`；也支持 `jin`/斤和 `lb`。状态是最多 80 字符的可选自由文本，不是枚举。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `record` | 记录当前体重；**写**。 | `weight`（正数），可选 `unit`、`status_note`。 | 正式体重记录、7 日均值，以及有前一窗口数据时的趋势；附后续维护所需不透明 handle。 | 计划体重；用户试图指定历史时间；把裸数字解释成体重时存在其他明确业务上下文。 |
| `query` | 读取最近或指定时间范围内的体重；**读**。 | 可选一个通用时间描述符与 `limit`（1–100）。 | 记录、`scope`、`complete`、当地时间投影，以及适用时的 7 日均值/趋势。 | 同时传多个时间描述符；用月均、BMI 或目标体重替代本版本口径。 |
| `update` | 修改已定位体重的重量或状态；**写**。 | 工具返回的 `record_handle`，以及 `weight` 或 `status_note` 至少一个；`unit` 只能跟随 `weight`。 | 已更新记录和重新计算的统计；未提供的字段保持原值，原测量时间不变。 | 猜测 handle；用 update 改写测量时间；只传单位。 |
| `delete` | 软删除已定位体重；**写**。 | 工具返回的 `record_handle`。 | 已删除记录和重新计算的统计。 | 目标不明确；直接使用数据库 ID。 |

当前窗口为 `(now - 7 天, now]`，对比窗口为 `(now - 14 天, now - 7 天]`。均值与差值保留 0.1kg；前一窗口无数据时省略趋势，不显示伪造的零变化。普通用户回复不展示 handle、内部 ID 或克制整数。

## `diet_pantry`：库存批次

用于库存批次的定向搜索、入库、查询、状态修改、营养关联和扣减。用户原始商品说法必须走 `search_text`；已知精确规范名或用户明确浏览库存时才使用 `query`。每个新批次必须在 `expiry_date` 与 `expires_at` 中二选一；用户给日历日期时优先原样传 `expiry_date`，由工具按配置时区解析，不能手工拼时区偏移。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `search` | 按原始名称定向解析商品；**读**。 | `search_text`；可选 `unit`、`statuses`、`storage_location`、`limit`（1–5，默认 5）、`nutrition_mode`（`none | summary | full`，默认 `none`）。 | 最多 5 个聚合商品候选；每项含数量、匹配原因、`inventory_kind`、正式 `relations`、营养可用状态及不透明 `inventory_match_handle`。`summary` 在同一次调用返回有界摘要；`full` 只用于用户明确请求完整营养标签。 | 无名称浏览完整库存；从名称或数量推断熟食/原料包含关系；把多个不同商品自动选成一个；普通定位默认读取营养；猜测或展示句柄。 |
| `add` | 清晰入库；**写**。 | 必需 `food_name`、正数 `quantity`、`unit`，以及 `expiry_date`/`expires_at` 二选一；可选 `display_quantity`、`display_unit`、`base_quantity_per_display_unit`、`package_hierarchy`、`nutrition_profile`、成对的 `price_minor`/`currency` 及其他元数据。 | 同时保留基础数量与包装展示语义的新批次。 | 计划入库；包装字段不一致；同时传两种到期字段；只有金额或只有币种。 |
| `preview_add` | 真实歧义或兼容的入库预览；**读**。 | 与 `add` 相同的数量、包装和日期字段；严格 schema **不接受** `nutrition_profile`。 | 预览及可能的 `commit_handle`。 | 清晰入库时反复确认；传入 `nutrition_profile`；用它为既有批次关联营养。 |
| `commit_add` | 提交匹配的入库预览；**写**。 | 工具返回的 `commit_handle`。 | 新增正式库存批次结果。 | 没有匹配 preview；编造或过期 handle。 |
| `preview_update_metadata` | 预览已定位批次的重量、到期等元数据改动；**读**。 | `batch_handle` 或 `batch_code`，以及要更新的重量或 `expiry_date`/`expires_at` 二选一。 | 元数据预览及可能的 `commit_handle`。 | 以此直接正式写入；猜测批次；手工换算日历日。 |
| `commit_update_metadata` | 提交元数据预览；**写**。 | 匹配的 `commit_handle`。 | 已更新的批次元数据。 | 未预览或 handle 不匹配。 |
| `preview_link_nutrition` | 为既有批次预览关联/替换结构化营养；**读**。 | `batch_handle` 或 `batch_code`、完整 `nutrition_profile`，可选 `linked_at`。 | 营养关联预览及可能的 `commit_handle`。 | 把标签文字塞进 notes；用于全新入库（应在 `add` 带 `nutrition_profile`）。 |
| `commit_link_nutrition` | 提交营养关联预览；**写**。 | 匹配的 `commit_handle`。 | 已关联营养的批次结果。 | 未预览或猜测 handle。 |
| `query` | 已知规范名时查询紧凑库存，或在用户明确要求浏览完整库存时分页；**读**。 | 可选 `normalized_name`、`missing_expiry_only`、`include_details`、`limit`（1–20）、`offset`、`statuses`。兼容 `food_name` 仍可接收，但用户原话应改走 `search`。 | 紧凑库存结果；详情仅在单一 `normalized_name` 且 `include_details: true` 时使用。 | 用用户模糊原话做精确查询；默认展示完整库存；饭后仅为确认成功而查询。 |
| `adjust` | 调整已定位批次数量；**写**。 | `batch_handle` 或 `batch_code`、非负 `quantity`、`source_text`，可选 `reason`。 | 已调整的正式批次结果。 | 不明确的批次；用其绕过需要 preview/commit 的扣减流程。 |
| `discard` | 丢弃一个精确批次或已搜索商品的一部分；**写**。 | 整批丢弃用 `batch_handle`；商品级丢弃用 `inventory_match_handle`、正数 `quantity`、`unit`；均需 `source_text`，可选 `reason`、`waste_category`。 | 原子丢弃结果；商品级可跨同商品批次按配置顺序扣减并分摊成本。 | 因到期自动丢弃；手选同商品物理批次；库存不足时接受部分扣减。 |
| `open` | 将已定位批次标为已开封；**写**。 | `batch_handle` 或 `batch_code`、`source_text`，可选 `opened_at`。 | 已开封的正式批次结果。 | 批次不明。 |
| `freeze` | 将已定位批次标为冷冻；**写**。 | `batch_handle` 或 `batch_code`、`source_text`，可选 `frozen_at`。 | 已冷冻的正式批次结果。 | 批次不明。 |
| `thaw` | 将已定位批次标为解冻；**写**。 | `batch_handle` 或 `batch_code`、`source_text`，可选 `thawed_at`。 | 已解冻的正式批次结果。 | 批次不明。 |
| `deduct` | 扣减已定向搜索的商品；**写**。 | `inventory_match_handle`、正数 `quantity`、`unit`、`source_text`，可选 `reason`。 | 在一个事务内跨同商品合格批次执行配置的已开封/临期顺序；库存不足整笔失败。 | 手工计算 FEFO；选择物理批次；用不受支持的包装换算；代替餐食本身的原子库存扣减。 |
| `preview_deduct` | 预览按名称、数量和库存选择策略扣减；**读**。 | `normalized_name`、正数 `quantity`、`unit`、`source_text`，可选 `selector`、`reason`。 | 扣减预览及可能的 `commit_handle`。 | 清晰餐次的库存扣减（由 `diet_meal` 原子完成）；用来猜测批次。 |
| `commit_deduct` | 提交扣减预览；**写**。 | 匹配的 `commit_handle`。 | 正式扣减结果。 | 未预览或 handle 无效。 |
| `preview_shopping_list` | 校验一份购物清单候选；**读**。 | `title`、`source_text`、1–50 个含名称/数量/单位的 `items`。 | 只读预览与一次性 `commit_handle`。 | 预览时创建库存或声称已经购买。 |
| `commit_shopping_list` | 提交完全匹配的购物清单预览；**写**。 | 工具返回的 `commit_handle`。 | active 清单与 pending 条目；相同 handle 重放返回同一结果。 | 修改预览后提交；把清单条目直接写成库存。 |
| `query_shopping_list` | 查询购物清单；**读**。 | 可选 `status`、`limit`（1–20）。 | 清单、条目及后续取消所需不透明 handle。 | 猜测内部 ID；将 purchased 状态解释为已经入库。 |
| `cancel_shopping_list` | 取消已定位的活动清单；**写**。 | 查询返回的 `shopping_list_handle`、`source_text`。 | cancelled 清单及条目。 | 未查询定位；取消库存批次；用清单取消替代已发生的丢弃。 |

同一商品的多个物理批次在 `search` 中聚合为一个候选；多个不同商品才需要用户选择。营养档案、批次链接和独立批次快照仍规范化保存在同一个 `diet.sqlite`，只在响应边界按 `nutrition_mode` 组合。包装规格持久保存，商品级操作可把用户的盒/瓶/个等展示单位确定性换成基础单位；没有唯一换算证据时返回可修复错误，不能由模型猜测。`add` 与 `preview_add` 仅在 `added_at`、`source_text` 遗漏时有插件默认值。补全保质期时，先用 `query(missing_expiry_only: true, offset: 0)` 发现目标，再对唯一名称详细查询取真实批次 handle，随后 `preview_update_metadata` → `commit_update_metadata`；每次提交后重新从 `offset: 0` 查询，绝不猜 handle。

结构化金额只使用最小货币单位整数，例如 `price_minor: 1299, currency: "CNY"`；金额与三位大写币种必须同时出现。不同币种只分别统计，接口不会返回跨币种合计。旧 `price` 字段只为历史兼容保留，不会在未知币种时被猜测转换。浪费分类可为 `spoilage`、`expired`、`overprepared`、`quality`、`other` 或 `unspecified`；未给出时记为 `unspecified`。

## `diet_transaction`：事务历史

用于寻找、撤销和重做已经发生的可识别操作。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `get_recent` | 读取近期事务以定位用户可辨识的目标；**读**。 | 可选 `operation`、`operation_type`、日期范围、`meal_type`、`normalized_food_name`、`limit`（1–100）。 | 近期事务及可供后续使用的真实 `operation_handle`。 | 跳过它并猜测 undo/redo 目标。 |
| `undo` | 撤销已定位操作；**写**。 | `operation_handle`。 | 已撤销的正式事务结果。 | 未先定位；猜测 handle；数据库写入已阻断。 |
| `redo` | 重做已定位操作；**写**。 | `operation_handle`。 | 已重做的正式事务结果。 | 未先定位；猜测 handle；数据库写入已阻断。 |

## `diet_report`：报告与进度

`today`、`daily`、`weekly`、`monthly` 读取 SQLite 后生成或覆盖派生 Markdown 报告工件，但不修改 SQLite；成功返回只含 `report.kind`、`report.name`、`report.relative_path` 元数据，不含报告正文或结构化营养指标。`progress`、`insights` 与 `expiring_inventory` 是只读查询，不生成报告文件。普通业务回复仍不能读取、打开或依赖生成文件。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `today` | 生成/覆盖目标日期的日报 Markdown；**生成报告工件，不改 SQLite**。 | 可选 `report_date` 或 `date`。 | `report.kind: "daily"`、`report.name`、`report.relative_path` 元数据，不含正文。 | 把它当作餐次/库存写入确认，或把返回值描述成营养指标。 |
| `daily` | 生成/覆盖指定日期的日报 Markdown；**生成报告工件，不改 SQLite**。 | 可选 `report_date` 或 `date`。 | `report.kind: "daily"`、`report.name`、`report.relative_path` 元数据，不含正文。 | 读取 Markdown 文件来拼普通业务回复，或声称返回报告正文。 |
| `weekly` | 生成/覆盖以指定日期为基准的周报 Markdown；**生成报告工件，不改 SQLite**。 | 可选 `report_date` 或 `date`。 | `report.kind: "weekly"`、`report.name`、`report.relative_path` 元数据，不含正文。 | 用于业务写入或迁移，或声称返回结构化营养指标。 |
| `monthly` | 生成/覆盖以指定日期为基准的月报 Markdown；**生成报告工件，不改 SQLite**。 | 可选 `report_date` 或 `date`。 | `report.kind: "monthly"`、`report.name`、`report.relative_path` 元数据，不含正文。 | 用于业务写入或迁移，或声称返回结构化营养指标。 |
| `progress` | 从 SQLite 读取指定日的当前进度；**读，不生成文件**。 | 可选 `report_date` 或 `date`。 | `local_date`、`goals_confirmed`、`known_minimum`、`incomplete_meal_count`、`metrics`/`daily_progress`、`aggregate` 和 `nutrition_quality` 等结构化进度数据。maximum goal 超标时指标直接给出精确 `over_by`；未超标为 `0`，minimum goal 或目标未确认为 `null`。`nutrition_quality` 分开给出字段完整性、计算状态和来源状态。 | 饭后已有 `daily_progress` 时再次调用来重建同一回执；从百分比反推超标克数；把字段完整误称为计算可信。 |
| `insights` | 合并营养完整性、临期库存、待确认库存关联及已确认目标差距，返回最多三条行动优先级；**读，不生成文件**。 | 可选 `period`（`daily`/`weekly`/`monthly`）、不晚于当前本地日期的日期、`within_days`（1–30，默认 7）和 `limit`（1–10，默认 5）。 | 周期、目标来源、热量/蛋白/脂肪/碳水/纤维/钠/水七项指标、显式 `nutrition_data_state`、有界 `expiring_inventory` 和最多三项 `priorities`。未确认目标时目标与差值为 `null`，且没有 `goal_gap`。 | 医疗诊断；未来日期；把配置默认目标当成用户确认目标；读取报告文件后再次拼接相同结论。 |
| `expiring_inventory` | 从 SQLite 查找已过期及近期到期库存；**读，不生成文件**。 | 可选日期和 `within_days`（1–365，默认 7）。 | 全部仍有库存的过期批次，以及窗口内未过期批次；返回 `range`、`state_counts`、`returned_count`、`complete`、`has_more`、`next_offset` 与 `batches`。 | 只返回窗口内过期品却宣称完整；自动将到期品吃掉、清空或丢弃。 |
| `cost_summary` | 汇总选定日期内的采购与已分摊成本；**读，不生成文件**。 | `date_start`、`date_end`，可选三位大写 `currency`。 | 各币种的采购、消费、浪费、调整金额及结构化价格覆盖率；没有跨币种总计。 | 猜测旧价格币种；把未定价批次当零成本；跨币种相加。 |
| `waste_summary` | 汇总真正的丢弃/过期事件；**读，不生成文件**。 | `date_start`、`date_end`，可选 `currency`。 | 分类事件数、按单位分开的数量、未定价事件数及各币种浪费金额。 | 把盘点修正当浪费；把不同单位数量直接相加。 |
| `trend_summary` | 读取有界成本与浪费趋势；**读，不生成文件**。 | `date_start`、`date_end`，跨度 1–730 天，可选 `currency`。 | 90 天内按日、其余按月的有界桶；金额仍按币种分开。 | 无限历史扫描；跨币种合计；从派生 Markdown 重建趋势。 |

### 目标来源字段

餐次和饮水提交、`progress`、`insights`、`query_goals` 与 `update_goals` 都会同时返回 `goals_confirmed`、`goal_source`、`confirmed_at`：

- `configuration_default` 只是配置提供的计算默认值，不是用户目标；此时 `goals_confirmed=false`、`confirmed_at=null`，不得显示目标百分比或差距建议。
- `user_confirmed` 是唯一允许显示目标百分比和差距建议的状态；`confirmed_at` 是该次正式确认的 UTC 时间。
- 撤销或重做目标更新时，数值目标、`goal_source` 与 `confirmed_at` 作为同一事务一起恢复，不得单独推断确认状态。

## `diet_system`：初始化、维护、偏好与补录

用于系统级操作。`initialize`、`repair`、`migrate`、目标/偏好更新和补录提交均为正式写入或维护；备份会写入备份工件。恢复和任何批量删除都需要明确的目标与范围确认。

| action | 用途与读写性 | 关键输入 | 成功返回 | 禁止场景 |
| --- | --- | --- | --- | --- |
| `initialize` | 初始化数据存储；**写/维护**。 | 无。 | 初始化结果。 | 用作普通业务写入的替代。 |
| `self_check` | 读取系统健康和迁移状态；**读**。 | 无。 | `data.checks`，包含所需检查的等级；用于确认迁移、完整性、外键、schema 等。 | 每条消息都重复运行；用单个 PASS 解除写阻断。 |
| `repair` | 修复报告日期关联的问题；**写/维护**。 | 可选 `report_date`。 | 修复结果。 | 收到 `DATABASE_INTEGRITY_ERROR` 后立刻调用。 |
| `validate_database` | 验证数据库；**读**。 | 无。 | 数据库验证结果。 | 当作正式修复或写入成功证明。 |
| `backup` | 创建备份工件；**写（不改业务事实）**。 | 可选安全 `label`（1–64 字符，字母数字及单连字符段）。 | 新备份及其可用于恢复的真实 handle。 | 把 label 当路径、含 `..` 或伪造恢复目标。 |
| `restore` | 从指定备份恢复；**写/破坏性维护**。 | 工具返回的 `backup_handle` 与 `confirmed: true`。 | 已恢复的系统结果。 | 缺少明确目标和范围确认；猜测 handle。 |
| `migrate` | 执行数据库迁移；**写/维护**。 | 无。 | 迁移结果。 | 直接改 SQLite 或绕过迁移校验。 |
| `export_data` | 导出版本化 JSON 或 CSV ZIP；**生成派生工件，不改业务事实**。 | `format` 为 `json` 或 `csv`。 | 安全文件名、校验和、各域记录数及时间范围；不返回绝对路径。 | 导出凭据、内部 ID、路径、会话键或诊断；把导出当备份。 |
| `validate_import` | 校验导入并执行事务级 dry-run；**预览，不改业务事实**。 | `import_name` 为 `imports/` 下安全文件名。 | 精确 `import_handle`、记录数与校验结果。 | 路径穿越、超限归档、版本/校验和/关系不匹配或非空目标。 |
| `import_data` | 提交已校验导入；**写/维护**。 | 精确 `import_handle` 与 `confirmed: true`。 | 原子导入结果和记录数；重试返回同一结果。 | 跳过校验、替换文件后提交、部分导入或猜测 handle。 |
| `preview_delete_data` | 预览受限删除范围；**预览，不改业务事实**。 | 固定 `scope`，日期范围仅用于 `intake_range`。 | 精确 `delete_handle`、目标计数、摘要和保留项。 | 用模糊“全部”扩大范围，或把备份纳入普通删除。 |
| `commit_delete_data` | 提交精确删除预览；**写/不可逆维护**。 | 精确 `delete_handle` 与 `confirmed: true`。 | 已删除计数、范围与隐私凭证；重试返回同一结果。 | 预览过期/变化后提交、删除备份、直接改 SQLite。 |
| `query_preferences` | 查询偏好规则；**读**。 | 可选 `include_inactive`。 | `read_completed` 的结构化偏好列表；只有成功空列表表示当前没有保存偏好。 | 把 `failed` 当作空列表；失败后转 Exec、文件搜索或模型记忆；将偶发观察直接当激活偏好。 |
| `query_goals` | 读取 SQLite 营养目标；**读**。 | 无。 | 当前结构化营养目标。 | 猜测缺失目标或将查询当更新。 |
| `update_goals` | 更新完整营养目标；**写**。 | 正整数 `calories_kcal`、`protein_g`、`fat_g`、`carbohydrate_g`、`fiber_g`、`sodium_mg`、`water_ml`，以及 `timezone_name`、`source_text`。 | 已更新的正式目标结果。 | 不完整目标；数据库写入已阻断。 |
| `update_preferences` | 新建或更新偏好；**写**。 | `rule_type`、`subject`、对象 `outcome`、`source_text`，可选对象 `evidence`；`water_unit` 的 outcome 必须有正数 `milliliters`。 | 已更新的正式偏好结果。 | 将普通偶发观察立即作为激活规则；写阻断期间。 |
| `forget_preference` | 删除/失效一条偏好；**写**。 | `rule_type`、`subject`、`source_text`。 | 已处理的正式偏好结果。 | 目标不明确；写阻断期间。 |
| `query_nutrition_backfill` | 查找历史缺失/不完整营养的候选；**读**。 | 候选餐次查询可选 `limit`（1–10）；或同时给 `meal_handle`、`batch_handle` 读取同一餐次的后续项目批次（此时不能给 limit）。 | `partial`/`incomplete` 候选及真实 `meal_handle`、必要时 `batch_handle`/`next_batch_handle`。 | 用数据库 ID、猜 handle；一次候选查询要求超过 10 个餐次。 |
| `commit_nutrition_backfill` | 将完整 C/D 估算原子补入历史餐次；**写**。 | `meal_handle`、可选 `batch_handle`，以及每项的 `item_handle` + 完整 `nutrition_estimate`；兼容情形才可用唯一 `display_order`。单次/单批 `items` 为 1–1000 项。 | 正式补录结果；可能为 `pending` 并给 `remaining_item_count`/`next_batch_handle`。 | 改库存、数量、时间或原始描述；单次提交超过 1000 项；重复提交 item；以嵌套餐次的同级 `display_order` 代替 `item_handle`。 |

历史营养补录有两个不同上限：首次候选查询的 `limit` 最多返回 10 个候选餐次；单个候选餐次的项目会按最多 1000 项拆分为补录批次，`commit_nutrition_backfill` 每次也最多接收 1000 个 `items`。中间项目批次只暂存估算，不写餐次、餐项、库存或事务日志；最后一批才以一次可撤销正式事务原子写入该餐次的全部营养。若 commit 返回 `pending`，使用返回的 `next_batch_handle` 继续查询；不需要重复向用户确认。

数据删除范围固定为 `raw_source_text`、`preferences`、`intake_range`、`business_facts_keep_config` 和 `all_business`。`intake_range` 必须同时提供本地日期起止；其余范围不得夹带日期。任何删除都先 preview 再 commit，提交绑定目标计数和摘要；已有备份不会随业务删除被静默移除。导入仅接受本版本生成且通过校验的 JSON 或 CSV ZIP，文件先放入专用 `imports/` 目录，校验与提交之间不得替换。

## 使用边界

- 写入准备度按当前所需能力判断，不因无关领域工具缺失而阻塞。首选能力不存在或明确改名时，只有可见说明和 Schema 能证明输入、输出及安全语义等价，才可选择替代能力；否则停止。安装后运行 `diet_system self_check` 检查数据库、迁移、schema 与配置健康；它不检查其他六类工具是否已注册。
- 餐次、库存和报告的普通回执只展示本次成功工具结果允许的简要事实，不展示内部 ID、文件路径、诊断、原始候选或置信度分数。
- 图片识别 Skill 只负责提取；由食序管家的结构化工具完成业务校验和写入。不要把图片文本或个人数据当作库存事实。
- v0.7.4.0 不承诺本页之外的 Docker、远程主机、凭据、模型或未实现能力，不会自动部署。本页示例字段均为接口名称，不包含真实用户、主机或凭据。
