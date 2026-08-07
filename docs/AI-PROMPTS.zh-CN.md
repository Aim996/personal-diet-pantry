# 食序管家 AI 操作提示词

以下三段可直接交给能够访问目标 OpenClaw 环境的执行型 AI。它们要求先核验再行动、使用正式可安装包和外部 `dataDir`，并把真实业务写入排除在安装验收之外。

## A. 全新安装提示词

```text
请为一套全新的隔离 OpenClaw 状态安装 GitHub 仓库 Aim996/personal-diet-pantry 的产品版本 0.7.5，技术包版本 0.9.0。

只允许使用 https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.5 中的 personal-diet-pantry-0.7.5-installable.tgz，并下载 SHA256SUMS 独立核对同名文件哈希；source.tar.gz 只用于审阅，绝不能交给插件安装器。若该 Release 不存在则停止，不得从聊天附件或镜像替代。确认运行环境满足 OpenClaw >=2026.5.17、Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`、Python >=3.11,<4。

先定位目标 OpenClaw 的真实配置方式，创建一个位于源码、下载目录和其他实例之外的专用持久 dataDir。不得扫描、读取、复制、修改或删除任何已有个人饮食、体重、库存、数据库、备份、导出、报告或凭据。通过 `openclaw plugins install npm-pack:/绝对路径/personal-diet-pantry-0.7.5-installable.tgz` 安装，执行 `openclaw plugins enable personal-diet-pantry`，把 dataDir 写入 `plugins.entries.personal-diet-pantry.config.dataDir`。确认 OpenClaw 进程选择的 Python 已安装 requirements.lock 中依赖；Python 由进程环境变量 PYTHON 选择，不要伪造 pythonExecutable 插件配置。然后按目标实例既有方式重启；普通本机网关可用 `openclaw gateway restart`，容器或远程实例不能盲目照抄。

安装后先执行 `openclaw plugins inspect personal-diet-pantry --runtime --json`，独立确认 diet_meal、diet_water、diet_weight、diet_pantry、diet_transaction、diet_report、diet_system 七类工具均已注册。得到用户明确授权新账本初始化后再运行 initialize；没有授权就暂停并报告，不得自动初始化。初始化后运行 self_check。验收是零业务写入，只做版本、工具注册、自检、目标和空账本状态等只读检查，不写入测试餐食、饮水、体重、库存、目标或偏好。

最终逐项报告：制品来源、SHA-256、OpenClaw/Node/Python 版本、dataDir 是否独立、七类工具状态、initialize 结果、self_check 结果、所有未完成项。任何一步失败都明确写“未完成”，不要宣称安装成功。
```

## B. 安全更新提示词

```text
请把现有 OpenClaw 中的 Aim996/personal-diet-pantry 从产品版本 0.7.4.28 安全更新到 0.7.5；技术包目标版本为 0.9.0。本次新增 migration 022，回退必须恢复升级前冷备份。

先以只读方式确认目标实例、当前版本、插件路径和真实 dataDir，并记录餐食、饮水、体重、库存批次、目标等关键记录数量。不要输出个人明细。按实例既有运维流程停止写入，然后严格依照仓库 docs/INSTALLATION.zh-CN.md，用受信源码中的 scripts/cold_backup.py 在 dataDir 外的新路径创建并验证升级前冷备份；不得覆盖已有备份，在线 diet_system backup 不能替代该冷备份。

只从正式 GitHub Release 下载 personal-diet-pantry-0.7.5-installable.tgz 与 SHA256SUMS，独立校验哈希。保持实例停止和原 dataDir 不变，通过 openclaw plugins install npm-pack: 加已校验安装包绝对路径替换插件包。不得手改 diet.sqlite、schema_migrations 或 migrations 001–022。按实例既有方式启动或重新加载。

更新后执行 `openclaw plugins inspect personal-diet-pantry --runtime --json` 独立确认七类工具，运行 self_check，并以只读方式核对产品 0.7.5、技术包 0.9.0、migration 022、目标、进度、定向库存和更新前后记录数量。若失败，停止实例、恢复升级前冷备份，再安装已校验的 0.7.4.28 包；禁止只换旧代码并复用已迁移数据库。

最终逐项报告：更新前后版本、安装包 SHA-256、升级前冷备份路径与验证结果、migration 结果、七类工具、自检、更新前后记录数量对比、是否执行回滚、所有未完成项。没有证据时不得写“更新成功”。
```

## C. 安装验收提示词

```text
请对已安装的 Aim996/personal-diet-pantry 产品版本 0.7.5、技术包版本 0.9.0 做一次零业务写入验收。

先确认正在检查的 OpenClaw 实例和外部专用 dataDir，不得切换到其他实例，不得读取或展示数据库明细、个人饮食、体重、库存、备份、导出、报告、地址、令牌或主机凭据。若可以取得正式 GitHub Release 的 personal-diet-pantry-0.7.5-installable.tgz 与 SHA256SUMS，则复核当前安装来源和哈希；无法证明时标记为未验证。

先执行 `openclaw plugins inspect personal-diet-pantry --runtime --json`，独立检查 diet_meal、diet_water、diet_weight、diet_pantry、diet_transaction、diet_report、diet_system 七类工具是否注册。运行 diet_system self_check，核对 migrations 001–022、SQLite 完整性、外键、schema 和配置。随后只执行 query_goals、progress、定向库存查询或等价只读动作，确认工具调用走真实应用层而不是读源码、Exec、SQL 或文件兜底。整个过程保持零业务写入，不要新增、修改、删除、撤销或重做任何餐食、饮水、体重、库存、偏好和目标。

检查普通写入路径的格式只能通过现有自动测试或脱敏固定样例确认，不要向真实账本写测试玉米或饮水。确认受保护回执仍是热量、蛋白、脂肪、碳水、纤维、饮水六项固定顺序、每项两行、10 格进度条；确认模糊份量在用户确认前零写入、过期食品不进入计划或推荐候选，只有用户明确报告已经食用且取得宿主本轮授权时才按事实记为 `consume`。

最终逐项报告：实例、产品/技术包版本、安装来源与哈希状态、dataDir 隔离、七类工具、自检、migration、只读查询、受保护行为证据、是否发生任何写入、所有失败与未验证项。只有全部必需项有证据且零真实数据变化时，才能写“验收通过”。
```
