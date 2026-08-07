# 食序管家 v0.4.3 稳定性修复设计

## 目标

消除库存入库和饮水撤销过程中因模型遗漏字段而产生的不可恢复工具错误，
保证明确包装规格得到精确保存，并在现有 SQLite `REAL` 无法无损承载精确
十进制数量时拒绝写入。

本版本只修复稳定性和数据准确性，不加入自动保质期功能，不清理或重建
现有 SQLite 数据。

## 已确认的问题

1. `diet_pantry add` 和 `preview_add` 的公开参数强制要求
   `added_at` 与 `source_text`。模型经常遗漏 `added_at`，导致合法的商品、
   数量、单位和重量在进入插件逻辑前就被拒绝。
2. 服务端会删除内部校验消息，只返回统一的
   `INVALID_INPUT: The request is invalid`。模型无法知道缺失字段，进而连续
   更换单位、数量和动作盲试。
3. 豆浆输入已经被模型正确计算为 `6 × 330ml = 1980ml`，但正确调用因
   缺少 `added_at` 失败。后续模型沿用估算值 `2000ml` 并成功写入。
4. `diet_water delete` 强制要求 `source_text`，导致通过记录句柄撤销时先
   产生一次无意义红错。
5. 当前 Skill 文档中有部分中文乱码，会削弱模型理解稳定性。
6. 入库成功时可能把完整商品名称缩减成通用品名，增加未来同类产品并存
   时的匹配歧义。
7. 公开 schema 强制要求 `food_name`、`quantity`、`unit`，使服务已有的
   安全字段级诊断在正常工具路径不可达。
8. 系统生成的 `added_at` 会读取公开 schema 明确禁止的 `context.now`，
   允许绕过 schema 的调用方影响系统时间。
9. 包装计算字段在换算后继续进入 Python；`preview_add` 会把它们复制到
   公开预览和工作流 JSON。
10. `Decimal` 转为 SQLite `REAL` 时只检查溢出、下溢和符号，超大整数与
    高精度小数可能被静默舍入。
11. JSON `number` 在进入 Python 前已经由 JavaScript 转成双精度数。若原始
    十进制超出安全精度，Python 只能看到舍入后的值；该值本身可能通过
    SQLite 往返校验，无法恢复或识别原始数字。

## 方案

### 公开接口宽容、内部数据严格

公开工具参数中，将可由系统安全生成的字段改为可选：

- `diet_pantry add/preview_add.added_at`
- `diet_pantry add/preview_add.source_text`
- `diet_water delete.source_text`

此外，`diet_pantry add/preview_add` 的 `food_name`、`quantity`、`unit`
在公开 schema 中可省略，使请求能够进入插件执行边界；插件立即按固定顺序
做本地必填校验并返回安全、可恢复的字段诊断。Python 服务继续严格要求这
三个字段，任何诊断分支都不调用 Python、不写数据。

插件在调用 Python 服务前补齐缺失值：

- `added_at`：只采用插件可信当前时间；忽略绕过 schema 传入的
  `context.now`。
- 库存 `source_text`：根据商品名、数量和单位生成简短、稳定的说明。
- 饮水删除 `source_text`：生成“删除饮水记录”的稳定说明。

Python 服务仍保持这些字段为内部必填，保证正式事务和撤销记录完整。

### 可恢复的字段级错误

服务端保留对意外异常和敏感信息的统一隐藏，但对已知输入错误返回安全、
结构化的诊断信息：

- `field`：出错字段。
- `reason`：`required`、`unsupported`、`invalid_format` 或
  `inconsistent_quantity`、`not_representable`。
- `expected`：简短的合法格式或预期值。
- `retryable`：是否允许修正后重试一次。

不得返回数据库路径、SQL、堆栈、原始内部标识或配置内容。Skill 继续要求
最多自动修正一次；没有字段级提示时停止重试。

### 包装规格精确计算

库存入库支持可选结构化包装信息：

- `package_count`
- `quantity_per_package`
- `package_unit`

当三项完整时，插件计算：

`exact_total = package_count × quantity_per_package`

液体包装把正式库存数量保存为精确总毫升；计重包装保存为精确总克数。
如果模型同时提交的 `quantity` 与精确总量不同，插件以结构化包装规格为准，
并返回修正警告，不允许估算值覆盖明确规格。

三项只提供一部分时，`add` 与 `preview_add` 都返回同一安全形状：
`code: INVALID_INPUT`、`field: package_specification`、
`reason: required`、完整 `expected` 和 `retryable: true`。Skill 在同一
动作中最多补齐并重试一次。

计算完成后，插件删除 `package_count`、`quantity_per_package`、
`package_unit`，再把负载传给 Python。Python 的预览边界也会防御性删除
这些字段，因此公开预览与 `operation_previews.request_json` 均不保存它们。

没有明确包装规格时，继续允许模型按上下文估算，但必须在用户回复中标记
“估算”。

### SQLite REAL 精确性边界

插件先在 provider 执行边界检查 `quantity`、`package_count` 和
`quantity_per_package`。非安全整数或超过 15 位有效十进制数字的
JavaScript `number` 在调用 bridge/Python 前返回
`INVALID_INPUT(<field>, not_representable)`；调用方必须直接提供有依据的
原始十进制字符串，不能把已经舍入的 `number` 再字符串化。常见短小数
（例如 `0.1`、`0.3`）和安全整数继续正常使用。

精确字符串和安全 `number` 进入 Python 后，再执行存储层校验：
正式数量进入 SQLite `REAL` 前执行 `Decimal → float → Decimal(str(float))`
十进制往返校验。只有往返后与原始 `Decimal` 数值相等才允许写入；因此
`1980` 和 `0.3` 可用，`90071992547409930` 或超出双精度有效位数的高精度
小数会被拒绝。

拒绝结果为安全、可恢复的 `INVALID_INPUT(quantity)`，原因是
`not_representable`。校验发生在直接写入、预览工作流签发和事务创建之前，
失败时批次、库存移动、事务与工作流均保持零写入。该保护复用现有列，不
改变 SQLite 表结构，也不做数据迁移。

### 商品名称

`food_name` 保留用户提供的完整商品名称；`normalized_name` 用于模糊匹配。
二者不得互相覆盖。包装营养表继续链接到该库存批次。

### Skill 文档

修复乱码并加入以下硬规则：

- 系统字段由插件自动补齐，模型不为这些字段重复试错。
- 明确乘法规格必须精确计算，禁止取整或近似。
- `INVALID_INPUT` 只按返回字段修正一次。
- 成功后不查询完整库存验证，不在回复中显示内部诊断。

## 数据兼容性

- 不修改现有 SQLite 表结构。
- 不重写已有库存、营养表、饮食记录或事务。
- 新字段只存在于工具调用层，最终仍写入现有数量、单位和重量字段。
- 安装新版本时沿用原数据目录。

## 测试

1. 鸡蛋入库省略 `added_at` 和 `source_text`，一次调用成功。
2. 苹果入库省略系统字段，一次调用成功。
3. 饮水删除只传记录句柄，一次调用成功。
4. `6 × 330ml` 保存为 `1980ml`，不能保存为 `2000ml`。
5. `3 × 0.1ml` 保存为 `0.3ml`。
6. 包装总量冲突时，以结构化规格为准并返回警告。
7. 包装字段不完整时，`add/preview_add` 统一返回一次可执行诊断。
8. 包装字段不传给 Python，也不出现在预览或工作流 JSON。
9. 缺失 `food_name`、`quantity`、`unit` 可穿透 provider schema 并在执行
   边界返回同一安全诊断；Python 内部仍严格。
10. 调用方 `context.now` 不能控制系统生成的 `added_at`。
11. 已丢精度的 JavaScript `number` 在 bridge 前被拒绝；无法由 SQLite
    `REAL` 往返的精确字符串在 Python 层被拒绝。真实
    plugin/provider → bridge/Python → SQLite 测试确认两者均零写入，同时
    `1980` 与 `0.3` 准确保存并可查询。
12. 完整商品名与规范化名称分别保存。
13. 现有测试全部通过，打包和插件校验通过。

## 版本与封存

- 版本号：`0.4.3`
- 封存目录：`C:\path\to\personal-diet-pantry\0.4.3\`
- 封存内容：
  - 完整源码目录
  - 可安装发布包
  - `食序管家-v0.4.3-更新说明.md`
  - 校验摘要和版本信息
