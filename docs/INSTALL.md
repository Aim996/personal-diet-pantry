# 安装食序管家 v0.7.5

这是给普通 OpenClaw 用户和代为执行安装的智能体使用的简明入口。v0.7.5 正式发布后，固定下载页为 [GitHub Release v0.7.5](https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.5)；页面不存在时说明尚未发布，不得从不明来源替代。完整步骤见[详细安装手册](INSTALLATION.zh-CN.md)。

## 1. 运行要求

- OpenClaw `>=2026.5.17`
- Node.js `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`
- Python `>=3.11,<4`
- 一个位于源码和 OpenClaw 程序目录之外、可持久化且权限受控的专用 `dataDir`

## 2. 只下载固定制品

只从 `Aim996/personal-diet-pantry` 的正式 v0.7.5 GitHub Release 下载：

- `personal-diet-pantry-0.7.5-installable.tgz`
- `SHA256SUMS`

不要安装 `personal-diet-pantry-0.7.5-source.tar.gz`；源码包只用于审阅和复现。不要从聊天附件、网盘或不明镜像获取安装包。

## 3. 校验 SHA-256

PowerShell：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\personal-diet-pantry-0.7.5-installable.tgz'
Get-Content -LiteralPath '.\SHA256SUMS' -Encoding UTF8
```

Linux/macOS：

```bash
sha256sum -c SHA256SUMS --ignore-missing
```

安装包的实际哈希必须与 `SHA256SUMS` 中同名文件完全一致；不一致时立即停止，不要尝试安装。

## 4. 准备独立 dataDir

在 OpenClaw 插件配置中把 `dataDir` 设置为一个专用于食序管家的持久目录。它不能是源码目录、下载目录、临时目录，也不能与另一套实例共用。首次安装不要把验收指向现有个人数据库。

## 5. 通过 npm-pack 安装

先确认 OpenClaw 进程使用的 Python `>=3.11,<4`，并让该解释器安装已安装插件根目录中的 `requirements.lock`；不要把依赖装到不相干的 Python。然后把下面路径和 `dataDir` 换成真实绝对路径：

```text
openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.7.5-installable.tgz
openclaw plugins enable personal-diet-pantry
openclaw config set plugins.entries.personal-diet-pantry.config.dataDir "/absolute/persistent/personal-diet-pantry-data"
openclaw gateway restart
openclaw plugins inspect personal-diet-pantry --runtime --json
```

如果 OpenClaw 位于 Docker、远程网关或自定义服务管理器中，使用该实例既有的配置和重启方式，不要在无法确认目标实例时照抄本机命令。Python 由 OpenClaw 进程的 `PYTHON` 环境变量选择；插件没有 `pythonExecutable` 配置字段。

## 6. 初始化与只读验收

1. 从 `openclaw plugins inspect personal-diet-pantry --runtime --json` 的真实运行时结果中，独立确认七类工具均已注册：`diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system`。
2. 全新账本只有在用户明确授权初始化后，才运行 `diet_system(initialize)` 创建本地账本结构；未授权时保持未初始化并报告等待用户决定。
3. 运行 `diet_system(self_check)`，确认迁移、SQLite、外键、schema 和配置没有失败项。
4. 用 `query_goals`、`progress` 和定向库存查询做零业务写入验收；不要为了验收写入、修改、删除、撤销或重做真实餐食、饮水、体重、库存、目标或偏好。
5. 记录产品版本 `0.7.5`、技术包版本 `0.9.0`、七类工具状态、自检结果和 `dataDir` 是否独立，不记录数据库内容。

`self_check` 不能证明其他六类工具已经注册，因此第 1 步不可省略。任何一步失败都应明确报告“未完成”，再查阅[故障排除](TROUBLESHOOTING.zh-CN.md)。

## 7. 安全失败与回滚

全新安装失败时保留 `dataDir`，不要反复初始化或删除数据库。更新现有实例前必须停止实例并建立经校验的升级前冷备份；本版新增 migration 022，回退时必须先恢复该冷备份再安装 v0.7.4.28。任何哈希、工具注册、自检或记录数量证据缺失时，都应报告“未完成”。
