# 食序管家 v0.7.0 可信闭环设计

> 设计日期：2026-07-30
> 目标版本：`0.7.0`
> 当前基线：`0.6.7` / `f1a39d97edf657a3230c72dd8d9aa1e179a64857`
> 审计依据：`docs/reviews/2026-07-30-v0.6.7-DEEP-AUDIT.zh-CN.md`
> 状态：已确认方案 A，等待书面规格复核

## 1. 版本定位

v0.7.0 定位为：

> **可信业务闭环版：修复 v0.6.7 的数据可信度、维护恢复、Skill 行为和食品决策缺口，并把已经公开的菜谱—购物—入库主路径补成真实闭环。**

这不是一次全盘重写，也不是只改几个补丁。版本号升级到 `0.7.0` 的理由是用户可见语义会发生系统性变化：

- “删除全部业务数据”必须真的清除正式业务数据及其预览、事务快照和派生缓存；
- 导入与隐私删除不再伪装成普通可撤销事务；
- 中断维护不得永久锁死；
- 旧备份可以重新列出、预览和恢复；
- 趋势按用户本地时区计算；
- 已过期食材不得进入推荐；
- 购买、入库、用餐地点和库存来源成为不同事实；
- Skill 评测从静态文档 lint 升级为真实工具轨迹与数据库结果验证；
- 购物条目能够从计划进入已购买和已入库，并链接真实库存批次。

## 2. 设计原则

### 2.1 保留稳定核心

以下模块不推倒重写：

- 餐食和营养记录；
- 饮水记录；
- 精简体重记录；
- 库存批次与扣减；
- 普通业务事务的撤销和重做；
- 七个现有顶层工具。

### 2.2 一次规划，分波交付

v0.7.0 使用同一份设计、同一条开发分支和同一个最终版本号，但内部必须通过独立门禁：

1. 可信操作；
2. 领域正确性；
3. Skill 真实行为；
4. 菜谱—购物—入库闭环；
5. 分析与发布。

前一波不通过，后一波不能用“最终一起修”作为理由继续累积风险。

### 2.3 深化接缝，不增加浅包装

本次只建立三个值得长期存在的 module：

1. `TrustedWorkflowModule`；
2. `FoodDecisionModule`；
3. `SkillBehaviorContract`。

每个 module 都必须隐藏一组原本散落的复杂规则。禁止给每个旧文件再包一层只转发参数的浅 module。

### 2.4 事实优先

- SQLite 正式业务表仍是事实源；
- metric snapshot 是可重建派生数据，不得反过来成为事实源；
- Skill 不得根据口语补写用户没有表达的事实；
- 推荐理由只能引用真实库存、目标、偏好、营养和时间证据；
- 没有可计算分母的指标不对用户展示伪精确数值。

### 2.5 兼容优先

- 七个顶层工具名称不变；
- 现有 action 默认保持；
- 新 action 只用于补齐无法通过旧入口安全完成的恢复和购物状态；
- v0.6.7 数据必须支持原地迁移；
- 回滚必须恢复旧软件和升级前完整数据备份，不能只降代码。

## 3. 范围

### 3.1 必须完成

#### 数据与隐私

- `all_business` 删除覆盖业务表、预览 JSON、事务快照、源文本、派生缓存和可携带句柄；
- 删除 plan digest 在取得写锁后重建和比较；
- 删除完成前运行 residue verifier；
- import/privacy deletion 使用不可撤销语义；
- 空快照、零实际效果和已脱敏事务不得进入普通 undo；
- 凭据字段识别 snake_case、kebab-case、camelCase 和 PascalCase；
- 敏感扫描检查实际源码归档成员，不整体排除测试目录。

#### 维护与恢复

- 服务启动及首次维护请求前核对 interrupted/running 操作；
- 证据不足时保守标为失败或需要验证，不猜测成功；
- 解除已经失去有效执行者的互斥锁；
- 列出历史备份；
- 对选定备份生成恢复预览；
- 校验、恢复并返回可核验摘要；
- 维护阶段写入 artifact/check/event 证据。

#### 领域正确性

- 趋势事件先转换到 profile timezone，再生成本地日/月 bucket；
- 过期与临期严格分离；
- 已过期或过期语义不确定的批次不参与菜谱推荐；
- self-check 覆盖迁移 016–021 的正式表、索引、外键和关键守恒；
- 成本继续按币种分别守恒，不进行隐式汇率换算。

#### Skill 行为

- 否定只作用于对应分句或事件；
- 否定式纠错进入 update/correction；
- `per_unit` 统一为 schema 支持的 `per_serving`；
- 做饭全吃完仍使用 cooking；
- 全部留存和明确制作损耗由正式 schema 表达，未表达时不得伪造；
- purchase、possession、stocked 分离；
- consumption location 与 inventory provenance 分离；
- 多意图句逐事件规划、按依赖执行、逐项返回结果；
- confirmation 只能消费当前会话仍有效的 handle；
- 保留当前用户要求的裸数字体重，但变为显式个人偏好。

#### 菜谱与购物闭环

- 菜谱保存份数、每份营养、准备时间、来源、替代项和版本；
- 菜谱评分使用库存可用性、临期、营养目标、偏好、时间、份数和计划剩菜；
- 推荐理由返回结构化证据；
- 购物条目状态为 `planned → purchased → stocked`，并允许 `cancelled`；
- `stocked` 与一次真实库存批次写入原子完成；
- 重复请求不能重复入库；
- shopping item 能查询其关联 pantry batch。

#### 分析

- 保留每币种 purchase/consume/waste/remaining；
- 增加有可靠分母的浪费率、采购频率、临期处理率、库存周转、使用波动和营养覆盖趋势；
- metric snapshot 可删除、可重建、可校验；
- 暂不展示无法从正式事实证明的“缺货频率”。

### 3.2 明确不做

- 多用户、角色权限和共享家庭；
- 云同步或远程托管；
- 自动图片识别；
- 医疗诊断或治疗建议；
- 自动汇率换算；
- 复杂生成式菜谱创作；
- 一次性清空或重写整个 `service.py`；
- 重写已经稳定的餐食、饮水、体重和库存核心；
- 将软路由部署纳入源码开发过程；
- 在没有正式 stockout fact 前展示缺货频率；
- 把静态 lint 继续称为真实 Skill 行为评测。

## 4. 总体结构

```text
用户口语
  │
  ▼
SkillBehaviorContract
  │  TurnPlan / action catalog / live handle rules
  ▼
现有七个顶层工具
  ├──────────────► TrustedWorkflowModule
  │                 删除、导入、撤销、备份、恢复、维护、自检、隐私
  │
  └──────────────► FoodDecisionModule
                    菜谱、购物、库存安全、成本、浪费、趋势、指标
                         │
                         ▼
                 SQLite 正式事实与可重建快照
```

`service.py` 保留兼容编排职责，但新增领域逻辑不得继续直接堆入。旧 action handler 只负责：

1. 输入适配；
2. 调用对应 module interface；
3. 通过公开响应过滤器返回结果。

## 5. TrustedWorkflowModule

### 5.1 职责

所有高风险、不可逆、跨业务数据库、控制数据库或文件系统的操作都经过同一个 seam。

### 5.2 Interface

```text
preview(command, request_context) -> WorkflowPreview
commit(workflow_handle, confirmation) -> OperationReceipt
recover_startup() -> RecoveryReport
inspect(query) -> TrustReport
```

Interface 包含以下不变量：

- preview 必须绑定精确目标、范围、版本、digest 和过期时间；
- commit 必须在写锁内重建计划；
- digest 不同必须返回 stale preview，不执行部分删除；
- commit 成功必须有可核验 effect count；
- effect count 为 0 的 undo 不能返回成功；
- import/privacy deletion 的 undo policy 为 `none`；
- recover 不自动重放不可逆操作；
- restore 必须消费针对具体备份生成的 restore preview；
- 所有路径只能位于受管 dataDir；
- 公开响应不得包含绝对路径、凭据或内部快照。

### 5.3 Implementation 隐藏的复杂性

- 删除目标图和 residue 扫描；
- 事务、预览、业务实体和可携带句柄的 lineage；
- 写锁、digest 和回滚；
- undo/redo 可用性判断；
- maintenance operation 状态机；
- 维护 artifact、check 和 event 证据；
- 备份发现、校验、兼容性与恢复；
- 凭据递归脱敏；
- schema/index/foreign-key/conservation 自检；
- 生产时钟和测试假时钟；
- 业务 SQLite、控制 SQLite 和受管文件系统 adapter。

### 5.4 新增公开动作

在 `diet_system` 增加：

- `list_backups`
- `preview_restore`

现有 `restore` 改为只提交 `preview_restore` 生成的精确 handle。

`list_backups` 只返回：

- opaque backup id；
- 创建时间；
- 产品版本；
- 数据库 schema 版本；
- 文件大小；
- 完整性状态；
- 是否与当前版本兼容。

不得返回绝对路径。

### 5.5 删除语义

`all_business` 删除范围包括：

- meal logs/items；
- water logs；
- body weight logs；
- pantry items/batches/movements；
- prepared foods；
- shopping lists/items/links；
- recipes；
- goals/preferences/learning outcomes；
- cost allocations/waste classifications/metric snapshots；
- operation previews 中的业务事实；
- transaction snapshots/source text 中的业务事实；
- portable handles；
- 派生缓存和全文索引。

允许保留：

- 不包含原始事实的删除 tombstone；
- 产品版本和 schema 版本；
- 不可逆摘要计数；
- 备份仍存在的明确提示。

### 5.6 撤销语义

事务增加：

```text
undo_policy: snapshot | none
effect_count: integer >= 0
```

普通 undo 候选必须同时满足：

- status 为 committed；
- `undo_policy = snapshot`；
- before/after snapshot 可解析；
- 存在至少一个可应用变化；
- 未被 privacy redaction 破坏；
- 未超过产品定义的事务历史边界。

## 6. FoodDecisionModule

### 6.1 职责

统一菜谱、购物、库存安全、成本、浪费和趋势的判断规则，使所有建议使用同一份事实、时区和口径。

### 6.2 Interface

```text
answer(decision_query, profile_context) -> EvidenceBackedDecision
transition(decision_handle, transition_command) -> DecisionReceipt
rebuild_metrics(scope) -> MetricRebuildReport
```

`decision_query` 是受限联合类型：

- recipe suggestion；
- meal plan preview；
- shopping draft；
- cost summary；
- waste summary；
- trend summary。

### 6.3 食品安全不变量

- `expires_at <= local_now` 的普通冷藏/常温批次属于 expired；
- expired 批次不进入 available pantry；
- expired 不得显示为“临期优先使用”；
- 无法证明冻结延长规则时，采取保守排除；
- thawed 批次按正式 thaw/expiry 字段判断；
- 推荐结果必须把“临期可优先”和“已过期需检查/处置”分开。

### 6.4 菜谱评分

每个候选菜谱返回分项证据：

```text
pantry_coverage
expiry_priority
nutrition_fit
preference_fit
time_fit
serving_fit
leftover_fit
safety_gate
```

规则：

- `safety_gate = fail` 时不可推荐；
- 评分只使用数据库事实；
- 缺少某维度数据时标为 unknown，不自动给满分；
- meal_type 必须参与候选过滤或评分；
- 推荐理由只能引用非 unknown 的证据；
- 同分时使用确定性排序，保证相同事实返回相同结果。

### 6.5 购物条目状态机

状态：

```text
planned ──► purchased ──► stocked
   │             │
   └─────────────┴──────► cancelled
```

禁止：

- `planned → stocked` 时没有明确实际到货/入库事实；
- `cancelled → purchased/stocked`；
- `stocked` 再次入库；
- purchased 自动等价为家中库存；
- 没有 pantry batch 的 stocked。

新增公开动作建议放在 `diet_pantry`：

- `mark_shopping_item_purchased`
- `stock_shopping_item`
- `cancel_shopping_item`

`stock_shopping_item` 必须在同一 SQLite 事务中：

1. 验证 item 当前为 purchased；
2. 创建或定位真实 pantry batch；
3. 写 shopping item ↔ pantry batch link；
4. 把 item 改为 stocked；
5. 写入可撤销业务事务；
6. 返回一个 idempotent receipt。

### 6.6 本地时间和指标

- 查询范围用 profile timezone 生成 UTC 边界；
- 每个 UTC 事件先转 profile timezone，再生成本地 bucket；
- 日、月、年标签与查询边界使用同一时区；
- 成本按 currency 分组，禁止自动合并币种；
- metric snapshot 是派生加速层，必须能从正式事实全量重建；
- snapshot 损坏时报告降级为直接查询，不影响业务写入。

v0.7.0 支持的派生指标：

- waste rate：`waste_cost / (consumed_cost + waste_cost)`；
- purchase frequency：窗口内正式 purchase 事务数；
- expiry handling rate：进入临期窗口后，在到期前 consume/freeze/discard 的批次数占比；
- inventory turnover：窗口 consumed cost / 平均 inventory cost；
- usage volatility：按本地日的消耗量变异；
- nutrition coverage：有可信营养值的已记录摄入占比及目标覆盖趋势。

分母为 0 时返回 `not_available`，不返回 0%。

## 7. SkillBehaviorContract

### 7.1 职责

把口语路由从散落自然语言规则变为可生成、可重放、可验证的行为契约。

它不是新增顶层工具，也不在 Python 中猜测用户原句。它位于：

```text
Skill 主说明 / references / action catalog / 行为案例 / OpenClaw 工具轨迹
```

之间的 seam。

### 7.2 TurnPlan

每个用户回合在调用工具前形成内部计划：

```text
events[]
negated_events[]
corrections[]
ordered_actions[]
handle_requirements[]
partial_failure_policy
receipt_plan
```

规则：

- 否定绑定到事件，不绑定整句；
- correction 优先定位上一条可修改事实；
- 一个回合可包含多个 domain；
- 跨域操作不伪装为原子事务；
- 有依赖时按依赖顺序执行；
- 一个事件失败不能静默吞掉其他独立事件；
- 最终回复逐项报告成功、失败和未执行；
- 不暴露 action 名、内部字段、置信度调试语或工具错误栈。

### 7.3 统一的领域语义

#### 体重

- 当前用户默认允许合理范围内的裸数字；
- 该行为保存为显式个人偏好；
- “不是 A，是 B”优先更新上一条；
- 新会话仍使用个人偏好，但共享/匿名渠道不得继承私人偏好。

#### 购买与入库

- purchase：已经购买；
- possession：已经拿到手；
- stocked：已经进入家庭库存；
- 只有 stocked 创建或链接 pantry batch。

#### 外食与库存来源

- consumption location：在哪里吃；
- inventory provenance：食物来自哪里；
- “在公司吃了家里带的便当”可以是外部地点 + 家庭库存来源。

#### 做饭

- 做了且吃完：cooking，无 leftover；
- 做了、吃了、剩余：cooking + leftover；
- 做了、未吃、全部保存：schema 允许 consumed=0；
- 明确制作损耗：结构化 preparation loss；
- 数量无法守恒时不得自动补齐。

### 7.4 契约生成

`contracts/tools.yaml` 升级为 contract v2，至少生成或验证：

- TypeScript input schema 映射；
- Python action map；
- mutation/confirmation/retry policy；
- public response allowlist；
- Skill action catalog；
- reference 字段与枚举；
- 行为测试 action 索引；
- 文档动作表。

主 Skill 和 reference 不再手工复制 `per_serving` 等枚举。

### 7.5 两层评测

#### 第一层：确定性发布门禁

输入：

- 用户口语和多轮上下文；
- channel；
- profile preferences；
- 初始数据库 fixture；
- 预期 reference；
- 预期工具轨迹；
- 预期 payload；
- 预期数据库差异；
- 预期公开回复片段。

断言：

- 无效 action 为 0；
- 无效 payload 为 0；
- 禁止调用为 0；
- 重复写入为 0；
- 漏事件为 0；
- 跨会话 handle 消费为 0；
- 数据库差异与预期完全一致。

现有 `scripts/evaluate_skill.py` 更名为 Skill case lint，只负责静态质量。

#### 第二层：真实模型与渠道验收

- 使用正式 OpenClaw 和目标主模型；
- WebUI 连续会话；
- WebUI 全新会话；
- Telegram 连续会话；
- Telegram 全新会话；
- 每个安全案例必须全部通过；
- 普通案例失败必须有重试和人工审阅证据，不能用静态 lint 覆盖。

## 8. 数据模型

### 8.1 Migration 019：可信事务与工作流

建议文件：

```text
migrations/019_trusted_workflows.sql
control-migrations/002_maintenance_evidence.sql
```

业务数据库：

- transactions 增加 `undo_policy`、`effect_count`；
- operation preview 支持 `restore_preview`；
- 新增 `workflow_entity_links`，只保存 opaque id 和关系，不保存原始事实；
- 新增必要索引；
- 历史普通事务回填 `snapshot`；
- import/privacy workflow 回填 `none`；
- 无法证明可撤销性的事务保守回填 `none`。

控制数据库：

- maintenance artifacts/checks 增加阶段唯一性；
- 每个 exclusive operation 保存 expected/observed evidence；
- reconciliation 保存最终判定和释放互斥时间。

### 8.2 Migration 020：菜谱与购物闭环

建议文件：

```text
migrations/020_recipe_shopping_lifecycle.sql
```

- recipe profile 增加 servings、per-serving nutrition、prep minutes、source 和 substitutions；
- shopping item 状态统一为 planned/purchased/stocked/cancelled；
- 增加 purchased_at、stocked_at；
- 新增 `shopping_item_links`；
- `(shopping_item_id, pantry_batch_id)` 唯一；
- 一个 item 默认只能完成一次 stocked transition；
- 旧 pending 映射为 planned；
- 旧 purchased 保持 purchased；
- cancelled 保持 cancelled。

### 8.3 Migration 021：可重建分析

建议文件：

```text
migrations/021_metric_snapshots.sql
```

- 新增 metric snapshots；
- 新增结构化 waste classification；
- snapshot 带 metric definition version；
- snapshot 可安全清空后重建；
- privacy deletion 清除与目标范围相关的 snapshot。

## 9. 错误处理

新增或统一错误语义：

| 错误 | 含义 | 用户行为 |
|---|---|---|
| `STALE_PREVIEW` | 锁内重建结果与预览不同 | 重新预览 |
| `NOT_UNDOABLE` | 不可撤销、空效果或已脱敏 | 说明原因，不改状态 |
| `MAINTENANCE_RECONCILIATION_REQUIRED` | 中断操作尚未核对 | 先运行核对/自检 |
| `BACKUP_INCOMPATIBLE` | 备份版本或 schema 不兼容 | 拒绝恢复 |
| `BACKUP_INTEGRITY_FAILED` | 备份校验失败 | 拒绝恢复 |
| `EXPIRED_INGREDIENT_EXCLUDED` | 已过期批次被安全过滤 | 展示处置提示 |
| `INVALID_SHOPPING_TRANSITION` | 购物状态跳转非法 | 保持原状态 |
| `PARTIAL_TURN_RESULT` | 多意图部分完成 | 逐项说明 |

所有错误必须通过公开响应过滤，不包含：

- SQL；
- 表名和内部文件路径；
- stack trace；
- action/debug 字段；
- 凭据；
- 内部快照。

## 10. 执行波次

### Wave 0：冻结基线与 RED 证据

- 固定 v0.6.7 数据副本、canary、行为语料和包哈希；
- 为审计 F-01 至 F-12 建立失败测试；
- 固定 contract v2 最小字段；
- 固定 v0.6.7 → v0.7.0 升级与回滚矩阵。

### Wave 1：可信操作

并行子波：

- 1A：删除、lineage、residue、锁内 digest；
- 1B：undo policy、effect count、import/privacy 不可撤销；
- 1C：maintenance reconciliation、旧备份列表与恢复预览；
- 1D：脱敏、实际归档扫描和 self-check。

Wave 1 未通过时，禁止开始新的业务闭环实现。

### Wave 2：领域正确性

- profile timezone 趋势；
- 过期库存过滤；
- 冷冻/解冻保守规则；
- 成本守恒和指标定义；
- metric snapshot 重建与校验。

### Wave 3：Skill 真实行为

- contract v2 和生成 action catalog；
- 否定、纠错、做饭、购买、外食来源和多意图规则；
- 裸数字体重偏好；
- 确定性行为门禁；
- WebUI/Telegram 真实渠道验收框架。

### Wave 4：菜谱—购物—入库

- recipe metadata；
- 可解释评分；
- shopping item 状态机；
- purchased/stocked/cancelled 动作；
- shopping item 与 pantry batch 原子链接；
- 幂等、撤销和成本继承。

### Wave 5：集成与发布

- 故障注入；
- 并发；
- 跨时区；
- 001–021 全新迁移；
- 0.6.7 原地升级；
- 备份回滚；
- 离线安装；
- 重复构建；
- 更新说明、数据模型、用户指南、故障恢复和根路线图。

### 10.1 实施计划拆分

v0.7.0 只有一个版本目标和一份总进度，但三个 module 可以独立审查。书面实施计划采用“一份总计划 + 四份可执行子计划”：

```text
2026-07-30-v0.7.0-master.md
2026-07-30-v0.7.0-trusted-workflows.md
2026-07-30-v0.7.0-skill-behavior.md
2026-07-30-v0.7.0-food-decision-loop.md
2026-07-30-v0.7.0-integration-release.md
```

总计划只维护依赖、波次、审查点和最终门禁；子计划包含文件级 TDD 步骤。拆分计划不等于拆分版本，也不允许跳过总门禁单独发布。

## 11. 验收标准

### 11.1 数据与隐私

- 删除 canary 后，在业务表、预览 JSON、事务快照、缓存、全文索引和导出中出现次数为 0；
- digest 必须在写锁内重建；
- `affected_rows = 0` 永远不能返回 undo 成功；
- import/privacy workflow 不进入普通 undo/redo；
- camel/Pascal/snake/kebab credential aliases 全部脱敏；
- 实际源码归档敏感扫描 findings 为 0。

### 11.2 维护与恢复

- restore/import/delete/migrate 每个阶段被强制终止后，重启均不会永久锁死；
- 未验证操作不得标为 committed；
- 至少一天前的备份可列出、预览、校验和恢复；
- 恢复后的数据库摘要与备份摘要一致；
- backup list 不泄露绝对路径。

### 11.3 食品安全与分析

- expired active 批次进入推荐的次数为 0；
- 上海 00:00、00:30、23:59、月初和年初全部正确；
- 至少一个 DST 时区春季和秋季边界正确；
- 每币种成本分别守恒；
- snapshot 全部删除后可重建出相同指标；
- 分母为 0 的指标返回 not_available。

### 11.4 Skill

- 至少 50 个确定性行为案例；
- 安全案例 100%；
- 无效 action 0；
- 无效 payload 0；
- 重复写入 0；
- 漏事件 0；
- 跨会话 handle 消费 0；
- WebUI 与 Telegram 各有连续和全新会话证据；
- 静态 lint 与真实行为成绩分开报告。

### 11.5 购物闭环

- planned/purchased/stocked/cancelled 所有合法和非法跳转均有测试；
- purchased 不创建库存；
- stocked 必须创建/链接真实 batch；
- 重复 stock 请求不重复入库；
- shopping item 与 batch 可双向查询；
- stocked 操作可通过普通事务正确撤销，链接与库存同时恢复。

### 11.6 发布

- Python、TypeScript、迁移、行为和安装测试全绿；
- self-check、SQLite integrity 和 foreign key check 全绿；
- 001–021 空库安装通过；
- v0.6.7 数据副本原地升级通过；
- 完整备份回滚通过；
- 外部子进程全部有 timeout 和阶段诊断；
- source archive 与 installable package 分别重复构建两次，内容清单一致；
- 包内没有运行数据库、备份、凭据、绝对路径或测试临时数据；
- 没有遗留 P1；
- 更新说明不夸大未完成能力。

## 12. 文档与发布口径

开发完成时同步：

- `README.md`
- `README.en.md`
- `RELEASE.zh-CN.md`
- `UPDATE-v0.7.0.zh-CN.md`
- `docs/ARCHITECTURE.zh-CN.md`
- `docs/DATA-MODEL.zh-CN.md`
- `docs/TOOLS-REFERENCE.zh-CN.md`
- `docs/USER-GUIDE.zh-CN.md`
- `docs/TROUBLESHOOTING.zh-CN.md`
- `docs/GENERATED-ACTIONS.zh-CN.md`
- 根目录总路线图

发布口径：

> v0.7.0 完成的是可信操作、真实 Skill 行为和菜谱—购物—入库闭环，不包含多用户、云同步、自动图片识别或医疗建议。

## 13. 最终决策

v0.7.0 采用以下组合：

- 不采用“只修高风险 Bug”的最小方案，因为它不足以让 `0.7.0` 名副其实；
- 不采用全量 application/domain/adapter 重写，因为风险和周期过大；
- 采用三个深 module 收口复杂度；
- 完成全部 P1；
- 完成最必要的菜谱—购物—入库和可信指标；
- 保留七个顶层工具与稳定旧核心；
- 以真实用户任务和数据库结果验收，而不是动作数量、文件数量或静态 Skill 分数。

一句话执行原则：

> **先用 RED 反例锁定每个问题，再在三个稳定 seam 后修复；每一波独立验收，最终只发布一个完整的 v0.7.0。**
