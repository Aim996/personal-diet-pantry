# 食序管家 AI 操作提示词

以下三段可直接交给能够访问目标 OpenClaw 环境的执行型 AI。它们要求先核验再行动、使用正式可安装包和外部 `dataDir`，并把真实业务写入排除在安装验收之外。

## A. 全新安装提示词

```text
请为一套全新的隔离 OpenClaw 状态安装 GitHub 仓库 Aim996/personal-diet-pantry 的产品版本 0.7.4.27，技术包版本 0.8.27。

只允许使用正式 GitHub Release 中的 personal-diet-pantry-0.7.4.27-installable.tgz，并下载 SHA256SUMS 独立核对同名文件哈希；不要安装 source.tar.gz，不要从聊天附件或镜像下载。确认运行环境满足 OpenClaw >=2026.5.17、Node.js >=22.22.3、Python >=3.11,<4。

先定位目标 OpenClaw 的真实配置方式，创建一个位于源码、下载目录和其他实例之外的专用持久 dataDir。不得扫描、读取、复制、修改或删除任何已有个人饮食、体重、库存、数据库、备份、导出、报告或凭据。通过 openclaw plugins install npm-pack: 加已校验安装包绝对路径执行安装，确认插件启用，再按该实例既有运维方式重新加载；不要猜测宿主命令。

安装后独立确认 diet_meal、diet_water、diet_weight、diet_pantry、diet_transaction、diet_report、diet_system 七类工具均已注册。得到用户对新账本初始化的明确同意后再运行 initialize，然后运行 self_check。验收只做版本、工具注册、自检、目标和空账本状态等只读检查，不写入测试餐食、饮水、体重或库存。

最终逐项报告：制品来源、SHA-256、OpenClaw/Node/Python 版本、dataDir 是否独立、七类工具状态、initialize 结果、self_check 结果、所有未完成项。任何一步失败都明确写“未完成”，不要宣称安装成功。
```

## B. 安全更新提示词

```text
请把现有 OpenClaw 中的 Aim996/personal-diet-pantry 从产品版本 0.7.4.19 安全更新到 0.7.4.27；技术包目标版本为 0.8.27。本次没有新增 migration，schema 继续使用 migrations 001–021。0.7.4.19 只作为技术回退包，不代表产品 UAT 已通过。

先以只读方式确认目标实例、当前版本、插件路径和真实 dataDir，并记录餐食、饮水、体重、库存批次、目标等关键记录数量。不要输出个人明细。按实例既有运维流程停止写入，然后严格依照仓库 docs/INSTALLATION.zh-CN.md，用受信源码中的 scripts/cold_backup.py 在 dataDir 外的新路径创建并验证升级前冷备份；不得覆盖已有备份，在线 diet_system backup 不能替代该冷备份。

只从正式 GitHub Release 下载 personal-diet-pantry-0.7.4.27-installable.tgz 与 SHA256SUMS，独立校验哈希。保持实例停止和原 dataDir 不变，通过 openclaw plugins install npm-pack: 加已校验安装包绝对路径只替换插件包。不得手改 diet.sqlite、schema_migrations 或 migrations 001–021，也不得创建新 migration。按实例既有方式启动或重新加载。

更新后独立确认七类工具，运行 self_check，并以只读方式核对产品 0.7.4.27、技术包 0.8.27、目标、进度、定向库存和更新前后记录数量。验收期间零真实数据写入。若插件或自检失败且数据未变，停止实例并重新安装已校验的 0.7.4.19 包恢复技术运行状态；若数据库或数量异常，保持停止并按详细手册从升级前冷备份恢复后再回退。

最终逐项报告：更新前后版本、安装包 SHA-256、升级前冷备份路径与验证结果、migration 结果、七类工具、自检、更新前后记录数量对比、是否执行回滚、所有未完成项。没有证据时不得写“更新成功”。
```

## C. 安装验收提示词

```text
请对已安装的 Aim996/personal-diet-pantry 产品版本 0.7.4.27、技术包版本 0.8.27 做一次零业务写入验收。

先确认正在检查的 OpenClaw 实例和外部专用 dataDir，不得切换到其他实例，不得读取或展示数据库明细、个人饮食、体重、库存、备份、导出、报告、地址、令牌或主机凭据。若可以取得正式 GitHub Release 的 personal-diet-pantry-0.7.4.27-installable.tgz 与 SHA256SUMS，则复核当前安装来源和哈希；无法证明时标记为未验证。

独立检查 diet_meal、diet_water、diet_weight、diet_pantry、diet_transaction、diet_report、diet_system 七类工具是否注册。运行 diet_system self_check，核对 migrations 001–021、SQLite 完整性、外键、schema 和配置。随后只执行 query_goals、progress、定向库存查询或等价只读动作，确认工具调用走真实应用层而不是读源码、Exec、SQL 或文件兜底。不要新增、修改、删除、撤销或重做任何餐食、饮水、体重、库存、偏好和目标。

检查普通写入路径的格式只能通过现有自动测试或脱敏固定样例确认，不要向真实账本写测试玉米或饮水。确认受保护回执仍是热量、蛋白、脂肪、碳水、纤维、饮水六项固定顺序、每项两行、10 格进度条；确认模糊份量在用户确认前零写入、过期食品不进入计划或推荐候选，只有用户明确报告已经食用且取得宿主本轮授权时才按事实记为 `consume`。

最终逐项报告：实例、产品/技术包版本、安装来源与哈希状态、dataDir 隔离、七类工具、自检、migration、只读查询、受保护行为证据、是否发生任何写入、所有失败与未验证项。只有全部必需项有证据且零真实数据变化时，才能写“验收通过”。
```
