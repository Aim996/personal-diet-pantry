# 食序管家 v0.7.3.1 液体饮食工具兼容设计

日期：2026-08-03

目标版本：`0.7.3.1`

基线版本：`0.7.3`

状态：设计已确认，等待书面复核

## 1. 目标

修复 DeepSeek/OpenClaw 真实会话中液体饮食无法直接使用 `consumed_volume_ml + per_100ml` 的问题，使一条明确的饮用记录能够一次调用完成餐食写入、营养缩放、饮水贡献和库存扣减，并可由一次撤销完整恢复。

本版是小范围兼容修复，不增加用户功能域，不改变现有七个 typed tools，不改变数据库结构，也不通过增加提示词掩盖工具接口问题。

## 2. 已确认根因

线上 UAT 输入“刚才那盒豆奶我喝了一盒”后，第一次工具调用提交：

```text
consumed_volume_ml = 250
nutrition_basis = per_100ml
```

工具入口却要求 `consumed_weight_g`，并把 `nutrition_basis` 限制为 `per_100g`。模型经过多次失败后只能用“250g + per_100g”模拟“250ml + per_100ml”，最终虽然扣减成功，但饮水贡献丢失，数据口径也不真实。

源码调查确认：

- TypeScript 原始 Schema、Python 服务和既有单元测试均支持 `per_100ml`；
- 原始 `MealParametersSchema` 约 48 KB；
- OpenClaw 为模型内联 `$ref` 后约 813 KB，增长约 16.9 倍；
- 单个餐项的模型侧 Schema 约 200 KB；
- 餐项使用深层 `allOf + anyOf`，直接营养证据有九个联合分支，并递归展开最多八层 ingredients；
- 当前测试只约束原始 Schema 小于 125 KB，没有验证 OpenClaw 规范化后真正发送给模型的 Schema。

因此根因是模型侧公共 Schema 过深、过大且使用兼容性较差的组合结构，不是营养计算器或自然语言提示词不足。

## 3. 方案比较

### 方案 A：扁平化模型侧餐项 Schema，运行时校验组合关系

采用。

餐项对模型呈现为一个普通对象：重量、体积、份数、营养事实和营养口径均是可选字段；`nutrition_basis` 使用普通枚举。TypeScript/Python 运行时继续验证跨字段不变量，并返回字段级错误。

优点：保持现有工具名称、action、数据库和业务能力；减少 Schema 体积；兼容不同 OpenAI 协议模型；错误仍可被确定性拦截。

### 方案 B：按固体、液体和做饭拆分更多工具

不采用。Schema 会更简单，但会增加模型选工具的负担，破坏当前能力分类和公共接口稳定性。

### 方案 C：保持 Schema，只增加 Skill 提示词

不采用。模型实际接收到的接口已经错误或过度膨胀，提示词无法改变工具验证器。

## 4. 公共 Schema 设计

### 4.1 餐项使用单一对象

移除 `MealItemBaseSchema` 与 `DirectNutritionEvidenceSchema` 的 `Type.Intersect`/九分支联合结构。餐项保留现有字段名：

```text
raw_name
normalized_name
inventory_match_handle
amount
unit
portion_expression
consumed_weight_g
consumed_volume_ml
consumed_servings
nutrition_basis
nutrition_facts
nutrition_estimate
preparation_losses
ingredients
```

`nutrition_basis` 的模型侧取值仍为：

```text
per_100g
per_100ml
per_serving
consumed_total
```

不再用 Schema 分支表达字段组合；组合关系由运行时统一检查。

### 4.2 ingredients 保留能力但停止递归内联

普通餐食保留 ingredients 输入能力，但模型侧不再递归复制完整餐项八次。外层只表达它是有界数组，子项由现有输入深度/数量限制和 Python 餐项解析器验证。

`record_cooking` 继续使用专用做饭结构，不改变完整菜品、原料扣减、已吃比例和剩菜保存流程。

### 4.3 运行时不变量

TypeScript 边界在调用 Python 前检查：

- 提供 `nutrition_facts` 或 `nutrition_estimate` 时必须提供 `nutrition_basis`；
- `per_100g` 必须有正数 `consumed_weight_g`；
- `per_100ml` 必须有正数 `consumed_volume_ml`；
- `per_serving` 必须有正数 `consumed_servings`；
- `consumed_total` 不要求缩放量；
- 没有直接营养事实时不得单独提供 `nutrition_basis`；
- `nutrition_facts` 与 `nutrition_estimate` 不得同时出现；
- 校验递归覆盖 ingredients，并保持现有最大深度和子项数量限制。

Python 服务保留同一组权威校验，形成边界快速反馈和服务端最终防线。

错误必须返回：

```text
code = INVALID_INPUT
field = items[n].具体字段
reason = required | incompatible
expected = 可执行修正方式
retryable = true
```

不得只返回笼统“expected a valid public action payload”。

## 5. 液体营养与饮水

液体按自身 `consumed_volume_ml` 独立缩放一次：

```text
factor = consumed_volume_ml / 100
```

`nutrition_facts.hydration_ml` 与其他营养字段使用同一 factor。最终液体饮水贡献必须满足：

```text
0 <= total_hydration_ml <= consumed_volume_ml
```

若可信营养资料没有 hydration 值，本版不凭空制造精确含水率；返回数据质量提示，但不得把重量当作体积，也不得产生超过实际饮用体积的饮水量。

同一餐有多种液体时分别缩放，不能先合并体积，也不能额外生成白水记录。

## 6. 记录时间

明确提供时间的用户表达继续提交经时区解析的 `occurred_at`。

“刚才、刚喝、今天吃了”等未提供具体时间的表达不再要求模型构造时间戳：

- 模型可省略 `occurred_at`；
- 工具使用可信系统时钟生成记录时刻；
- 日历归属和报告展示使用用户配置时区，当前为 `Asia/Shanghai`；
- 不允许模型为了满足必填字段猜测 `-05:00` 等偏移。

已有显式 `occurred_at` 请求保持兼容。

## 7. 幂等与撤销

一次明确液体饮食只能产生一个有效 meal 事务。语义指纹继续覆盖会话、规范化来源文本、发生分钟和业务目标；预览、正式记录和修正重试属于同一动作族。

一次撤销必须共同恢复：

- 餐食记录；
- 营养进度；
- 饮水贡献；
- 对应库存批次与包装数量。

修复不得降低 v0.7.3 已通过的 FEFO、多批次恢复和新商品包装展示能力。

## 8. 测试设计

### 8.1 模型侧 Schema 回归

测试必须调用 OpenClaw 实际使用的 `normalizeToolParameterSchema`，使用 DeepSeek provider/model 兼容参数，并断言：

- 规范化 Schema 接受 `consumed_volume_ml + per_100ml`；
- 同时仍接受 `consumed_weight_g + per_100g`；
- 模型侧 Schema 不含餐项 `allOf`；
- `per_100ml` 和 `consumed_volume_ml` 在模型侧可见；
- 规范化后的餐食 Schema UTF-8 字节数必须小于 `160000`，防止再次膨胀到 813 KB；该上限写入自动测试，不能只记录日志。

### 8.2 运行时字段级错误

分别验证：

- `per_100ml` 缺少体积时返回 `items[0].consumed_volume_ml`；
- `per_100g` 缺少重量时返回 `items[0].consumed_weight_g`；
- 营养事实缺少 basis 时返回 `items[0].nutrition_basis`；
- basis 没有营养事实时返回 `items[0].nutrition_basis` 的 incompatible 错误；
- nutrition_facts 与 nutrition_estimate 同时出现时返回冲突字段。

### 8.3 端到端豆奶场景

使用隔离数据库完成：

1. 入库 2 盒豆奶，每盒 250ml；
2. 搜索取得库存句柄；
3. 一次 `diet_meal record` 喝 1 盒；
4. 断言只生成 1 条餐食、库存剩 1 盒/250ml；
5. 断言营养按 `per_100ml` 缩放 2.5 倍；
6. 断言 hydration 大于等于 0 且不超过 250ml；
7. 一次撤销恢复为 2 盒/500ml，并恢复营养和饮水进度。

### 8.4 时间回归

使用固定系统时钟和 `Asia/Shanghai`，省略 `occurred_at` 记录“刚才喝了豆奶”，断言记录归属上海当前日；显式时间请求仍保留原时刻。

## 9. 兼容与发布

- 用户可见产品版本提升为 `0.7.3.1`；npm/OpenClaw/Python 包使用合法且高于 v0.7.3 的三段技术版本 `0.7.4`，发布文件名、更新说明和数据导出仍标识产品版本 `0.7.3.1`；
- 不新增数据库迁移；
- 不改变现有七个 typed tools 及其公共名称；
- 不删除公共 action；
- 不修改真实生产数据库；
- 构建、TypeScript、Python、安装包和升级测试全部通过后才生成发布包；
- 线上安装前先保留当前插件包和数据冷备份；
- 安装后必须在新会话复测盒装豆奶场景，旧会话不作为是否加载新 Schema 的判断依据。

## 10. 完成标准

满足以下全部条件才可交付：

1. DeepSeek 模型侧 Schema 直接接受液体口径，不再要求伪造重量；
2. 一条明确饮用记录最多一次正常业务调用即可提交；
3. 不出现 Schema 错误风暴或笼统 `INVALID_INPUT`；
4. `consumed_volume_ml`、`per_100ml` 和 hydration 证据真实落库；
5. 饮水贡献不超过实际饮用体积；
6. 未提供时间时使用系统时间和用户时区；
7. 饮食、库存、营养和饮水可由一次撤销共同恢复；
8. v0.7.3 既有自动测试、安装升级测试和 FEFO 回归全部通过；
9. 发布包、版本说明、实施记录和线上 UAT 文档一致。
