# 故障排除与安全恢复

适用版本：Personal Diet Pantry（食序管家）v0.7.4.28。

先记住两条规则：SQLite（`dataDir/diet.sqlite`）是正式事实来源；任何工具没有返回成功前，都不能把本次操作说成“已记录”。数据库或迁移出现异常时，优先保护数据并只读诊断，而不是反复重试或手工编辑文件。

## 通用处置顺序

1. 记录错误代码、发生时间、使用的动作和是否已有成功结果；不要复制真实个人数据、密钥、地址或内部句柄。
2. 区分“明确的业务失败”与“结果无法确认”。收到 `TIMEOUT`、`INVALID_RESPONSE`、进程/连接中断等桥接层错误时，写入结果不确定：必须先通过只读状态或事实查询确认；确认前既不能重试，也不能宣称已提交或未提交。
3. 先停止本问题要求停止的动作，再做该问题列出的只读诊断。
4. 只有只读证据确认没有写入，或恢复步骤完成并通过验证后，才重新提交用户仍然需要的操作。

`diet_system validate_database`、`diet_pantry query`、`diet_meal query`、`diet_transaction get_recent` 与 `diet_report progress` 是相应的只读诊断动作。`diet_system self_check` 还会生成可重建的健康报告，但不修改 SQLite 中的正式业务事实；需要全面检查时可使用它。不要用直接打开或编辑 SQLite、报告、YAML 或迁移文件来代替这些工具。

## TIMEOUT / INVALID_RESPONSE（结果无法确认）

| 项目 | 处置 |
| --- | --- |
| 现象 | 工具调用返回 `TIMEOUT`、`INVALID_RESPONSE`、进程错误或连接中断，没有取得可信的最终业务响应。请求可能在回执丢失前已经提交。 |
| 是否写入 | 状态不确定；不得把“没有收到成功回执”解释为未提交，也不得据此宣称已经提交。 |
| 停止什么 | 停止同一事实的重试、补写、撤销及任何依赖其结果的后续写操作，避免重复或错误抵消。 |
| 只读诊断 | 按业务域使用事实查询：饮食用 `diet_meal query`，库存用 `diet_pantry query`，已完成操作用 `diet_transaction get_recent`，聚合影响用 `diet_report progress`；必要时运行 `diet_system self_check`。 |
| 恢复后验证 | 若只读证据确认已有一笔预期记录，不再重试；若确认没有记录且数据库检查通过，才重新提交一次；若证据冲突，继续停止写入并按数据库完整性流程处理。 |

## INVALID_INPUT（参数无效）

| 项目 | 处置 |
| --- | --- |
| 现象 | 工具返回 `INVALID_INPUT`，可能指出某个字段不符合单位、日期、精度或动作约束。 |
| 是否写入 | 收到结构化 `INVALID_INPUT` 业务响应表示该请求未通过校验，不应产生该次正式写入。若实际收到的是 `TIMEOUT`、`INVALID_RESPONSE` 或连接中断，则不适用此结论，应按上一节视为状态不确定。 |
| 停止什么 | 停止同一动作的连续试错；最多只在错误同时给出 `field`、`reason`、`expected` 且标记为可重试时修正**该字段**一次。 |
| 只读诊断 | 重新核对原始输入、单位和日期；必要时用对应的 `query` 或 `diet_report progress` 读取当前状态。不要读取后再猜测字段值。 |
| 恢复后验证 | 以原始事实修正一个字段后重做同一动作一次；只有成功结果才确认写入。随后可用相应只读查询确认结果。 |

## DATABASE_INTEGRITY_ERROR（数据库完整性错误）

| 项目 | 处置 |
| --- | --- |
| 现象 | 工具返回 `DATABASE_INTEGRITY_ERROR`，启动失败，或数据库完整性、外键、schema 检查出现 `FAIL`。 |
| 是否写入 | 当前失败操作不得视为写入；服务会阻断写入并可能以只读方式打开数据库，既有正式记录仍需保留。 |
| 停止什么 | 停止当前会话的全部写操作：入库、饮食、饮水、更新、删除、撤销、重做、预览/提交与偏好更新。不要重试原写入。 |
| 只读诊断 | 先调用一次 `diet_system self_check`；如工具仍可用，再调用 `diet_system validate_database`，记录检查项的 PASS/FAIL，不要暴露实际路径或数据。 |
| 恢复后验证 | 由数据负责人从已验证的完整备份恢复，或按迁移问题的成套回滚流程处理。用户明确表示已修复后再运行一次 `self_check`；只有所有必需检查均为 PASS 时才恢复写入。 |

## 迁移校验和不匹配

| 项目 | 处置 |
| --- | --- |
| 现象 | 启动、迁移或 `self_check` 报 migration checksum 不匹配、已应用迁移缺失，或 migration 检查为 FAIL。换行符兼容性已被处理；内容变更仍会被拒绝。 |
| 是否写入 | 当前迁移/启动未成功时，不得把本次操作视为写入。 |
| 停止什么 | 停止实例上的所有写入、迁移重试和“修补”动作；不要删除迁移文件、修改迁移 SQL 或直接编辑 `schema_migrations`。 |
| 只读诊断 | 调用 `diet_system self_check` 和 `diet_system validate_database`，确认失败属于迁移检查还是 SQLite 本体完整性；核对正在使用的软件包版本与升级前备份记录。 |
| 恢复后验证 | 停止实例后，按[成套回滚流程](INSTALLATION.zh-CN.md#7-成套回滚)处理。v0.7.4.28 没有新增 migration，可直接安装 v0.7.4.19 可安装包并复用同一 `dataDir`；若数据库本身异常，则先校验并恢复升级前冷备份。0.7.4.19 只恢复技术运行状态，不代表产品 UAT 通过。在线 `diet_system backup` 仍仅用于同版本恢复。启动后运行 `self_check`，再以 `diet_pantry query`、`diet_report progress` 做只读验证。 |

## 缺少 Python 依赖或解释器不正确

| 项目 | 处置 |
| --- | --- |
| 现象 | 插件启动或调用 Python 核心失败，提示模块缺失、解释器版本不符，或运行环境与预期依赖不一致。 |
| 是否写入 | 在 Python 核心未启动或请求未成功时，本次没有可确认写入。 |
| 停止什么 | 停止日常写入和重复安装尝试；不要把依赖安装到与 OpenClaw 实际调用不同的 Python 后继续假定已修复。 |
| 只读诊断 | 使用 OpenClaw 实际选择的解释器执行 `python --version`、`python -m pip show PyYAML tzdata`；核对其为 Python 3.11+，且版本分别为 `6.0.3`、`2026.3`。这些命令中的 `python` 必须替换为实际解释器。 |
| 恢复后验证 | 在同一解释器环境中按安装包的 `requirements.lock` 预置或安装锁定依赖，重新加载插件后调用 `diet_system self_check`。确认七类工具可用后，再做 `diet_report progress` 的只读读取。 |

## 插件未注册，或七类工具没有出现

| 项目 | 处置 |
| --- | --- |
| 现象 | 在目标 OpenClaw 状态中看不到 `personal-diet-pantry`，或缺少 `diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system` 之一。 |
| 是否写入 | 工具未可用时没有本插件可确认的写入。 |
| 停止什么 | 停止任何业务写入；不要用未公开的命令、替代工具名或直接 SQLite 编辑绕过注册问题。 |
| 只读诊断 | 运行 `openclaw plugins inspect personal-diet-pantry --runtime --json`，并在受支持的 OpenClaw 管理界面或日志中核对所选状态、安装包名称和 Python 启动错误。 |
| 恢复后验证 | 注册故障应使用 `personal-diet-pantry-0.7.4.28-installable.tgz` 的 npm-pack 安装路径重装同版本，并保留当前专用 `dataDir` 映射后重新加载。七类工具出现后调用 `diet_system self_check`。若需要回退，按[成套回滚流程](INSTALLATION.zh-CN.md#7-成套回滚)安装 v0.7.4.19；本版没有新增 migration，可继续复用同一 `dataDir`。 |

## 营养估算失败

| 项目 | 处置 |
| --- | --- |
| 现象 | 饮食记录的部分营养字段为 `null`，并以 `nutrition_data_state` 或 `nutrition_status` 表示不完整。v0.7.4.28 继续允许可靠标签缺失的字段保持未知，不应为通过写入而补造零值或模型估算。 |
| 是否写入 | 营养估算未满足且动作未成功时，不写入饮食，也不扣减库存。 |
| 停止什么 | 停止第二次以上的同一估算重试；不要改用 `diet_water`、`diet_pantry` 或手工数据库写入来伪造该餐。 |
| 只读诊断 | 用 `diet_pantry query` 查看是否存在可安全使用的标签快照，或用 `diet_report progress` 查看当前已确认进度；不要把查询结果当作新估算。 |
| 恢复后验证 | 准备完整、与原食物和数量对应的结构化营养估算，并仅重试同一饮食动作一次。成功后以工具返回的进度和库存影响为准；失败则保留未提交提案。 |

## 库存匹配有歧义

| 项目 | 处置 |
| --- | --- |
| 现象 | 多个不同商品都可能匹配用户说法，或返回需要确认对象的结果。例如“番茄”不能自动扣减“番茄罐头”。同一商品的多个物理批次不属于此类歧义。 |
| 是否写入 | 自动库存扣减不会在歧义未解除前发生；若没有成功结果，不得宣称饮食或库存已写入。 |
| 停止什么 | 停止猜测商品、批次或内部 handle；不要连续换名称碰撞成功，也不要手动扣库存。 |
| 只读诊断 | 调用 `diet_pantry query` 取得紧凑候选；仅在已确定一个 `normalized_name` 且确需细节时请求该名称的详细查询。 |
| 恢复后验证 | 让用户选择明确商品，或在没有安全库存目标时按工具结果记录为不扣库存的饮食。收到成功结果后确认其中的 `inventory_effects`；如需复查，再用紧凑 `query` 只读读取。 |

## 重复确认或待确认操作续接异常

| 项目 | 处置 |
| --- | --- |
| 现象 | 用户重复发送“确认”“可以”等短语，或确认短语并非紧接着一个仍有效的待确认饮食。 |
| 是否写入 | 没有当前会话中未过期的待确认 handle 时，短确认不会发起写入。若已调用 `commit_record` 却收到 `TIMEOUT`、`INVALID_RESPONSE` 或连接中断，则写入状态不确定，必须先只读确认。 |
| 停止什么 | 停止重新预览、创建第二条记录或把普通“是”绑定到猜测的历史操作。 |
| 只读诊断 | 只检查当前会话中最近一次待确认摘要；如需核对已完成的历史操作，使用 `diet_transaction get_recent`，但不要把它当作未提交操作的替身。 |
| 恢复后验证 | 仅在存在原始、未过期 handle 时提交一次 `commit_record`；否则请用户重新给出完整的已发生事实。成功回执或只读交易查询应只显示一笔正式操作。 |

## 进度跨会话不连续

| 项目 | 处置 |
| --- | --- |
| 现象 | 新会话的进度、库存或偏好像是空的，或与上一会话不一致。常见原因是切换了 `dataDir`、OpenClaw 状态或时区配置。 |
| 是否写入 | 诊断本身不应写入；不要为了让数字“连续”而补写餐食、饮水、目标或偏好。 |
| 停止什么 | 停止任何补录、清库、目标重置和手工报告修改。 |
| 只读诊断 | 调用 `diet_system self_check`、`diet_report progress`、`diet_pantry query` 和必要时 `diet_transaction get_recent`；在受支持的配置入口核对该实例是否仍指向原专用 `dataDir`，且未泄露实际路径。 |
| 恢复后验证 | 恢复原 `dataDir` 映射并重新加载实例；再次运行 `self_check`，然后以 `progress`、紧凑库存查询和最近交易的只读结果确认连续性。 |

## 目标百分比为空或目标来源不是用户确认

| 项目 | 处置 |
| --- | --- |
| 现象 | `progress` 或 `insights` 返回 `goal_source=configuration_default`，目标、百分比、进度条或目标差距为 `null`。 |
| 是否写入 | 这是只读结果，不会写入。它表示现有数值只是配置默认值，不是用户已经确认的目标。 |
| 停止什么 | 不要在回复层自行计算百分比，不要改 SQLite，也不要把默认目标描述成用户承诺。 |
| 只读诊断 | 调用 `diet_system query_goals`，核对 `goals_confirmed`、`goal_source` 与 `confirmed_at`。 |
| 恢复后验证 | 只有用户明确给出并确认完整七项目标后，才调用一次 `diet_system update_goals`。成功结果应为 `goal_source=user_confirmed` 且 `confirmed_at` 为 UTC 时间；再调用 `progress` 验证百分比恢复。 |

## 回执声称成功，但 SQLite 中没有记录

| 项目 | 处置 |
| --- | --- |
| 现象 | 用户看到了成功回执，但后续只读查询、进度或交易记录中找不到对应的正式记录。 |
| 是否写入 | 状态不确定；在证据一致前不要假定写入成功，也不要假定一定未写入。 |
| 停止什么 | 立即停止对同一事实的重试、补写、撤销或手工修库，避免形成重复记录或误撤销。 |
| 只读诊断 | 用原始发生时间、食物摘要等人可识别事实调用 `diet_meal query`、`diet_transaction get_recent`、`diet_report progress`，并运行 `diet_system self_check`。不要依赖回执文本、内部 ID 或报告文件。 |
| 恢复后验证 | 若只读证据确认没有该操作，且数据库检查全部通过，再由用户重新提交完整原始事实一次；若证据冲突或自检失败，保持停止写入并按数据库完整性流程由数据负责人恢复。成功后再次用只读查询确认恰有预期的一笔记录。 |

## 恢复完成的最低标准

在重新开放写入前，至少满足以下条件：

- `diet_system self_check` 的全部必需检查没有 `FAIL`；
- 目标实例指向预期的独立 `dataDir`，其中的 `diet.sqlite` 是要恢复的那一套数据；
- 用 `diet_pantry query`、`diet_report progress` 或 `diet_transaction get_recent` 完成与故障相关的只读验证；
- 如发生升级或回滚，软件包与数据目录来自同一套受控版本/备份。

任何迁移、完整性或恢复问题都不要通过编辑 `schema_migrations` 解决。需要人工处理时，提供不含真实用户数据的错误代码、版本和检查级别即可。
