# 食序管家 v0.6.7 更新说明

发布日期：2026-07-30  
升级基线：v0.6.1.5.1  
发布方式：一次规划、连续开发、分段验证、最终交付

## 版本结论

v0.6.7 是一次完整的能力升级，不是把中间版本分别打包后简单叠加。v0.6.2–v0.6.6 仅作为同一开发分支内的里程碑；最终对外只发布 v0.6.7。

公共入口仍是七个工具：

- `diet_meal`
- `diet_water`
- `diet_weight`
- `diet_pantry`
- `diet_transaction`
- `diet_report`
- `diet_system`

公共动作由 55 个增加到 72 个。原有饮食、饮水、体重、库存、事务、报告、目标和偏好动作保持兼容；新增能力在原有领域内扩展，没有增加第八个顶层工具。

## 本次新增

### 1. 可恢复的维护控制面

备份、恢复、迁移、导出、导入和删除等维护操作拥有独立状态、操作键、参数指纹、产物校验和、安全摘要与历史查询。调用结果不确定时先使用 `maintenance_status` 或 `maintenance_history` 核对，不盲目重试破坏性操作。

维护控制面位于 `dataDir/control/maintenance.sqlite`，只保存运维元数据，不复制餐食、饮水、体重、库存、目标或偏好事实。

### 2. 单一动作契约与渐进式 Skill

`contracts/tools.yaml` 成为 72 个动作的单一机读事实源，用于生成和核对 TypeScript/Python 动作清单、正式变更集合、公共行为契约与动作索引。

主 Skill 保留口语路由、安全边界和回复原则；饮食饮水、体重、库存采购、事务、报告、系统维护与数据隐私细节拆入参考文件。确定性评测覆盖普通口语、上下文续接、否定事件、编号、代码、图片和无关请求。

### 3. 库存感知菜谱与购物清单

新增：

- `diet_meal save_recipe`
- `diet_meal suggest_recipes`
- `diet_meal preview_meal_plan`
- `diet_pantry preview_shopping_list`
- `diet_pantry commit_shopping_list`
- `diet_pantry query_shopping_list`
- `diet_pantry cancel_shopping_list`

菜谱是可复用配置，不是已经做饭的事实。推荐最多返回三个候选，并结合库存覆盖、临期利用、已确认偏好和目标信息。购物清单必须先预览再提交；标记已购买也不会自动创建库存批次。

### 4. 成本、浪费与趋势

批次价格使用整数最小货币单位和三位大写币种保存，例如 `1299 CNY`。消费、丢弃、过期和减少调整按当时剩余数量分摊成本，最后一次分摊吸收舍入余数，满足：

```text
原始成本 = 剩余成本 + 已分摊成本
```

新增：

- `diet_report cost_summary`
- `diet_report waste_summary`
- `diet_report trend_summary`

不同币种始终分开统计；未知价格保持未知。只有丢弃和过期属于浪费，普通盘点修正不会伪装成浪费。

### 5. 版本化导出与校验导入

`diet_system export_data` 支持：

- 单文件版本化 JSON；
- CSV ZIP，每张表以安全 CSV 表示；
- manifest、记录数、时区/时间范围和 SHA-256；
- 与数据库主键无关的稳定外部引用；
- API Key、令牌、内部 ID、绝对路径、会话键和诊断信息排除/脱敏；
- CSV 公式注入防护。

导入分两步：

1. 将本版本生成的 JSON/CSV ZIP 以安全文件名放入 `dataDir/imports/`，调用 `validate_import`；
2. 校验版本、校验和、计数、关系、限制、冲突并完成回滚式 SQLite dry-run 后，使用原样返回的 `import_handle` 和 `confirmed: true` 调用 `import_data`。

提交为单次原子操作；失败不保留部分业务事实。同一已提交请求重试返回原结果，不重复导入。

### 6. 精确隐私删除

删除只允许五种范围：

- `raw_source_text`
- `preferences`
- `intake_range`
- `business_facts_keep_config`
- `all_business`

`intake_range` 必须提供本地日期起止，其余范围不能夹带日期。所有删除先调用 `preview_delete_data`，再使用精确 `delete_handle` 和 `confirmed: true` 调用 `commit_delete_data`。预览绑定范围、目标计数和摘要；目标变化、预览过期或句柄不匹配都会拒绝提交。

删除完成后仅保留不含原业务内容的范围、数量、摘要、时间和被擦除外部引用作为最小凭证。已有备份不随普通业务删除被静默移除。

## 数据模型升级

v0.6.7 保留迁移 001–015，并新增：

| 迁移 | 内容 |
| --- | --- |
| 016 | 菜谱档案、购物清单与条目 |
| 017 | 结构化批次价格、成本分摊与浪费分类 |
| 018 | 可移植实体引用、隐私删除凭证和导入/删除预览类型 |

升级迁移不会自动创建菜谱、购物清单或库存，不会自动导入、导出或删除数据，不会猜测旧价格币种，不会重算历史营养，也不会把默认目标改成用户已确认目标。

## 升级步骤

1. 停止目标实例。
2. 完整备份旧可安装包和整个 `dataDir`。
3. 安装 `personal-diet-pantry-0.6.7-installable.tgz`，保留原 `dataDir`。
4. 启动或重新加载插件，等待迁移 016–018 完成。
5. 调用 `diet_system self_check`。
6. 用 `diet_pantry query`、`diet_system query_goals`、`diet_weight query`、`diet_report progress` 和 `diet_report cost_summary` 做只读核对。

回滚必须同时恢复旧软件包和升级前完整 `dataDir`。禁止编辑、删除或伪造 `schema_migrations`。

## 不包含

v0.6.7 不负责：

- Docker、软路由或远程实例部署；
- 多用户共享和权限系统；
- 云同步、自动外网暴露或凭据托管；
- 自动图片识别；
- 汇率换算；
- 医疗诊断、治疗或个体化医疗建议；
- 自动删除备份。

## 验证口径

正式交付只接受当前干净 Git 提交重新生成的证据：

- 72 动作生成契约一致；
- Python 与 TypeScript 全量测试通过；
- Skill 路由评测通过；
- 迁移 001–018、升级、回滚、桥接和实际可安装包验证通过；
- 生产依赖审计和开发依赖风险接受校验通过；
- 源码包和可安装包各独立生成两次且 SHA-256 一致；
- 发布包不包含 SQLite、备份、报告、缓存、凭据、环境文件或个人数据。

详细动作字段见 `docs/TOOLS-REFERENCE.zh-CN.md`，数据关系见 `docs/DATA-MODEL.zh-CN.md`，安装和回滚见 `docs/INSTALLATION.zh-CN.md`。
