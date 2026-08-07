# 食序管家 v0.7.3 Skill 完整性增强设计

日期：2026-08-02

目标版本：`0.7.3`

基线版本：`0.7.2`

状态：设计已确认，等待书面复核

## 1. 产品定位

食序管家是一个可复制、可安装、可迁移的完整 Skill 产品。`SKILL.md`、按需 references、能力契约、随包确定性工具、SQLite 迁移和回归测试共同组成 Skill；它不是某台软路由上的定制应用。

本版只做能改善 Skill 通用能力的修改：

- 让自然口语在新会话和不同库存数据下仍能稳定工作；
- 把模型不应自由发挥的换算、批次分配、日期和事务下沉到随 Skill 打包的确定性工具；
- 减少无效 reference 读取、工具盲试和重复调用；
- 保持 v0.7.2 数据、公共工具名称和已有工作流兼容。

本版不修改生产软路由、OpenClaw、Telegram、Gateway、Docker、Lucky 或用户当前数据库；部署在构建与离线回归全部通过后单独执行。

## 2. 已确认的真实问题

两轮 v0.7.2 现场 UAT 已提供足够失败基线：

1. 入库 Schema 已接受 `package_count`、`quantity_per_package` 和 `package_unit`，但 TypeScript 边界完成乘法后会删除这些字段，Python 与 SQLite 因而只能保存总克数或总毫升数。
2. 新会话只能看到总量，无法还原“几盒、几瓶、每盒多少”，模型可能把“三盒”错误换算成全部库存。
3. 普通扣减底层已经具备 `opened_first → earliest_expiry → earliest_added` 的确定性跨批次选择，但 `discard` 只接受单批次句柄并丢弃整批。
4. 同商品多批次被错误当成需要用户选择的歧义，随后出现 18 次失败调用和 180 秒超时。
5. 到期日由模型手写带偏移的时间戳，上海用户出现 `-05:00` 和跨日展示。
6. 熟食批次和营养快照已经正确保存，但再次食用缺少单步公共入口，现场需要 16 次 `diet_meal` 调用。
7. 泛化 `INVALID_INPUT`、空 items 的伪成功和宿主失败摘要漏报，使模型无法稳定自救。

这些问题的主要根因是 Skill 契约与确定性工具的公共接口不完整，不是继续增加自然语言提示词就能解决。

## 3. 方案选择

### 方案 A：只修改提示词

不能持久化包装事实，不能原子跨批次丢弃，也不能修复日期转换和熟食快照直达路径，不采用。

### 方案 B：Skill 优先的兼容式契约升级

保持现有分层，只补充包装数据、产品级句柄、跨批次动作、日历日期、熟食直达和结构化结果。改动可回归、可迁移、可复制，采用。

### 方案 C：重建独立库存应用或完整事件平台

会扩大项目边界并增加回归风险，不符合“完善 Skill”目标，不采用。

## 4. Skill 产品架构

### 4.1 主 Skill

`skills/personal-diet-pantry/SKILL.md` 只保留每轮都需要的规则：

- 自然口语触发和排除边界；
- 七个能力域的快速路由；
- 明确已完成动作直接写入，真正破坏性歧义才确认；
- 单领域只加载一份最相关 reference；
- 相同工具、action、规范化参数和错误不得原样重复；
- 用户回复只表达业务结果，不暴露工具名、字段名或调试过程。

### 4.2 按需 references

- `pantry-and-expiry.md`：包装事实、同商品多批次、FEFO、日期和库存展示；
- `meal-and-nutrition.md`：库存餐食、熟食快照复用和单位缩放；
- `cooking-and-leftovers.md`：做饭、剩菜和日历到期日；
- `reply-style-and-error-boundaries.md`：结果状态、有限自救和停止条件。

规则只在一个 reference 中定义，其他文件引用，不复制同义段落。

### 4.3 机器契约

`contracts/tools.yaml` 是能力、action、Schema、处理函数和契约测试的机器事实源。生成产物继续由脚本生成并随安装包发布，避免 Skill、Schema 和服务行为漂移。

### 4.4 确定性工具

TypeScript 负责公开 Schema、兼容输入归一化、重复失败缓存和安全响应；Python 负责包装换算、批次选择、事务、营养快照与时区转换；SQLite 保存正式业务事实。

模型负责理解用户意图，不负责自行拆批次、手算包装总量、构造 UTC 偏移或重建熟食营养。

## 5. 包装事实的数据模型

### 5.1 基础数量继续作为计算真值

现有字段保持含义不变：

```text
initial_quantity
remaining_quantity
unit
```

它们继续保存可计算基础量，例如 `900g`、`1500ml` 或 `18 piece`。

### 5.2 新增展示与换算事实

新增迁移 `021_package_semantics_and_product_operations.sql`，为 `pantry_batches` 增加：

```text
initial_display_quantity
display_unit
base_quantity_per_display_unit
package_hierarchy_json
```

语义：

- `initial_display_quantity`：最小可日常消费包装的初始数量；
- `display_unit`：用户使用的显示单位，例如盒、瓶、袋、个、份；
- `base_quantity_per_display_unit`：每个显示单位对应多少基础量；
- `package_hierarchy_json`：可选的多层包装，例如 2 提、每提 6 盒。

示例：

```text
两盒豆花，一盒180g
initial_quantity=360
unit=g
initial_display_quantity=2
display_unit=盒
base_quantity_per_display_unit=180
```

```text
两提椰子水，一提6盒，一盒125ml
initial_quantity=1500
unit=ml
initial_display_quantity=12
display_unit=盒
base_quantity_per_display_unit=125
package_hierarchy_json=[{"quantity":"2","unit":"提"},{"per_parent":"6","unit":"盒"}]
```

`remaining_display_quantity` 不重复存储，由 `remaining_quantity / base_quantity_per_display_unit` 计算，避免两个剩余量发生漂移。旧批次新增字段均为 `NULL`，查询时继续按原基础单位显示，不猜包装规格。

### 5.3 输入兼容

新增清晰的规范字段：

```text
display_quantity
display_unit
base_quantity_per_display_unit
package_hierarchy
```

v0.7.2 的 `package_count`、`quantity_per_package`、`package_unit` 继续接受，并在边界映射为规范字段。归一化后不得删除包装事实；Python 在写入前用 `Decimal` 校验：

```text
initial_quantity = display_quantity × base_quantity_per_display_unit
```

冲突时工具自动采用包装乘积并返回一条业务警告，但数据库只能提交一组一致值。

## 6. 产品级句柄与跨批次动作

### 6.1 复用现有库存匹配句柄

`diet_pantry search` 已返回 `inventory_match_handle`，其内部已经保存规范商品名和基础单位。本版将其明确为产品级操作句柄，不增加语义重复的新句柄。

同一商品的多个物理批次聚合为一个候选；多个不同商品才构成需要确认的歧义。

### 6.2 统一分配器

`deduct`、库存餐食扣减和产品级 `discard` 共用 `_selected_rows` 的确定性分配器：

```text
opened_first
→ earliest_expiry
→ earliest_added
```

模型提交产品句柄、数量和用户单位。插件先按批次包装事实把“3盒”换算成 `540g`，再原子分配：

```text
旧批次 -360g
新批次 -180g
```

若任一环节不满足，整个事务回滚，不能部分扣减。

### 6.3 动作语义

- 保留旧 `discard(batch_handle)`：明确整批丢弃；
- 扩展 `discard(inventory_match_handle, quantity, unit)`：按数量跨批次丢弃，写 `discard` movement 和浪费分类；
- 新增直接 `deduct(inventory_match_handle, quantity, unit)`：处理明确已经使用但不属于餐食的库存动作；
- 餐食继续通过 `diet_meal` 原子写入饮食与库存，不要求模型先单独扣库存。

清楚的“已经扔了/用了/吃了”直接写入；只有多个不同商品、数量缺失或单位无法换算时才询问。

## 7. 日历日期与用户时区

模型优先提交 `expiry_date: YYYY-MM-DD`。服务使用已配置的用户时区（当前通常为 `Asia/Shanghai`）转换为当地日历日结束，并存为 UTC。

`expires_at` 继续作为兼容入口，供明确的系统集成时间戳使用；同一请求不得同时提交两个字段。

适用范围：

- 库存入库；
- 库存元数据修正；
- 做饭剩菜；
- 餐食条目中的 leftover。

查询与回复统一按用户时区还原同一日历日期，禁止出现入库回复 8 月 5 日、换会话后显示 8 月 6 日。

## 8. 熟食营养快照的直达路径

当搜索结果唯一对应一个有效熟食批次，并存在 `prepared_food_profiles` 记录时，返回 `prepared_food_handle`。句柄绑定：

```text
pantry_batch_id
batch_version
available_quantity
unit
nutrition_snapshot_id
```

`diet_meal` 新增 `record_prepared`：

```text
prepared_food_handle
quantity（可选；缺省表示吃完该精确批次）
unit（quantity 存在时必需）
source_text
occurred_at（可选，缺省使用系统时间）
meal_type（可选，由当地时间推断）
```

工具一次完成：

1. 核验句柄和批次版本；
2. 按数量缩放保存的熟食营养快照；
3. 写入餐食与餐食条目；
4. 只扣熟食批次；
5. 不再次扣原料；
6. 返回可撤销的正式事务结果。

## 9. 公共结果和错误合同

保留现有 `ok` 兼容字段，同时增加统一 `outcome`：

```text
write_committed
preview_ready
read_completed
no_op
failed
```

约束：

- 正式写入只有数据库事务提交后才返回 `write_committed`；
- 空 items、零变化和已被过滤的请求返回 `no_op`，不得伪装成普通成功；
- 预览只返回 `preview_ready`；
- 查询返回 `read_completed`；
- 所有错误返回 `failed`。

可修复的 `INVALID_INPUT` 必须包含：

```text
field
reason
expected
retryable
```

无法安全公开原值时只返回接收值类型，不回显用户原文或内部标识。

宿主自己的 `toolSummary.failures` 不属于 Skill 可控制接口，本版不修改 OpenClaw；验收以插件 `outcome` 和原始调用结果为准。

## 10. 有限自救与调用成本

继续采用“允许有新证据的自救，不设置固定总调用次数”：

- 字段级错误允许修改被指出字段后再试；
- 目标、参数、证据或系统状态改变后允许继续；
- 完全相同的能力、action、规范化参数和错误签名不得再次访问 Python 或数据库；
- TypeScript 保存有界、短时、按会话隔离的失败指纹缓存；同一失败指纹再次出现时直接返回原结构化失败；
- 成功后立即停止，不再追加验证性写调用；
- 普通单领域请求只加载一个 reference；
- 库存操作先定向 search，不读取整个库存。

缓存只保存失败指纹和安全错误码，不保存用户库存、饮食内容或提示词。

## 11. 数据迁移与兼容

迁移必须：

1. 只新增可空字段和必要索引，不重写旧批次；
2. 保留 `initial_quantity`、`remaining_quantity` 和 `unit` 的原语义；
3. 让 v0.7.2 数据库直接启动并完成迁移；
4. 备份和恢复包含新增字段；
5. 导出数据包含包装事实，导入旧格式时字段为 `NULL`；
6. 撤销、重做恢复包装字段和跨批次 movements；
7. 不以解析历史 `source_text` 的方式猜测并回填旧包装。

## 12. 测试策略

所有实现遵循 RED-GREEN-REFACTOR。

### 12.1 P0 回归

```text
入库：2盒豆花，一盒180g，明天到期
入库：3盒同款，一盒180g，下周五到期
新会话查询：显示5盒、180g/盒、共900g和两个批次
丢弃：3盒鼓包，快到期先算
结果：旧批次-360g，新批次-180g，单事务提交
```

需要先在 v0.7.2 基线上看见失败，再实现最小修复。

### 12.2 日期回归

上海时区提交 `expiry_date=2026-08-05`，入库、查询、换会话和报告均显示 8 月 5 日；数据库可使用 UTC，但不得改变日历语义。

### 12.3 熟食回归

做饭生成 180g 熟猫耳朵面后，用新会话输入“刚把冰箱那盒猫耳朵吃了”：

- 一次业务写调用；
- 复用 266.25 kcal 快照；
- 熟食 180g → 0；
- 干面保持 450g；
- 撤销后熟食恢复 180g。

### 12.4 契约和成本回归

- 同一失败指纹第二次不进入 Python；
- 修改明确错误字段后允许继续；
- `no_op` 不产生数据库事务；
- 每类 response 都有正确 `outcome`；
- 单商品查询不读取全库名称；
- 主 Skill 和 references 不重复同一参数表；
- 构建包包含生成契约、迁移和 Skill 文件；
- v0.7.2 数据库升级、备份、恢复和回滚夹具全部通过。

## 13. 预计文件范围

实现计划可涉及：

```text
skills/personal-diet-pantry/SKILL.md
skills/personal-diet-pantry/references/pantry-and-expiry.md
skills/personal-diet-pantry/references/meal-and-nutrition.md
skills/personal-diet-pantry/references/cooking-and-leftovers.md
skills/personal-diet-pantry/references/reply-style-and-error-boundaries.md
contracts/tools.yaml
src/schemas.ts
src/index.ts
src/reliability.ts
python/personal_diet_pantry/pantry.py
python/personal_diet_pantry/inventory_matching.py
python/personal_diet_pantry/meals.py
python/personal_diet_pantry/service.py
python/personal_diet_pantry/timezones.py
migrations/021_package_semantics_and_product_operations.sql
对应 TypeScript、Python、Skill eval、迁移、构建与发布测试
版本号、更新说明、工具文档和发布清单
```

当前工作树已有的 `package.json`、构建脚本和安装测试修改属于用户资产。实施时只在确有版本或发布需要时做兼容追加，不覆盖或回退现有内容。

## 14. 发布和回滚

交付顺序：

```text
失败回归测试
→ 数据模型与领域逻辑
→ 工具 Schema 和服务接口
→ Skill 与 references
→ 全量测试
→ 构建 v0.7.3 安装包
→ 从 v0.7.2 备份恢复夹具升级
→ 离线 OpenClaw 新会话 UAT
→ 单独决定是否部署生产
```

发布包必须可与 v0.7.2 备份并存。生产部署前创建冷备份；若回归失败，回退程序和数据库备份，不能尝试用旧程序直接打开已经迁移且继续写入过的新数据库。

## 15. 验收标准

v0.7.3 只有同时满足以下条件才可交付：

1. 包装数量、单件规格、基础总量跨会话一致；
2. 同商品多批次不询问批次，按配置顺序原子分配；
3. “三盒”绝不会被换算为全部 `900g`；
4. 日期按用户时区保持同一日历日；
5. 熟食食用一次业务调用完成且不重复扣原料；
6. 明确已完成动作不因营养估算单独要求确认；
7. 结构化错误能指导一次定向修复；
8. 相同失败指纹不重复访问后端；
9. 旧数据不伪造包装，旧能力和撤销不退化；
10. Skill 在新安装、新会话和离线测试库中均通过相同回归。

## 16. 已确认决策

- 版本号为 `0.7.3`；
- 项目目标是完善完整 Skill，不建设独立应用；
- 采用兼容式契约升级；
- 以工具托管业务真值，Skill 负责自然语言和路线选择；
- 不对软路由、某个商品或某个会话写死；
- 不继续用增加提示词代替数据模型和工具契约修复。
