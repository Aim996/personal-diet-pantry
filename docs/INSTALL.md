# 安装食序管家 v0.7.4.27

这是给普通 OpenClaw 用户的简明入口。当前 v0.7.4.27 仍处于发布准备中；只有 GitHub Release 正式出现且文件哈希可验证后，才按本文安装。完整的隔离、初始化、冷备份和故障处理命令见[详细安装手册](INSTALLATION.zh-CN.md)。

## 1. 运行要求

- OpenClaw `>=2026.5.17`
- Node.js `>=22.22.3`
- Python `>=3.11,<4`
- 一个位于源码和 OpenClaw 程序目录之外、可持久化且权限受控的专用 `dataDir`

## 2. 只下载固定制品

从 `Aim996/personal-diet-pantry` 的正式 v0.7.4.27 GitHub Release 下载：

- `personal-diet-pantry-0.7.4.27-installable.tgz`
- `SHA256SUMS`

不要安装 `personal-diet-pantry-0.7.4.27-source.tar.gz`；源码包只用于审阅和复现。不要从聊天附件、网盘或不明镜像获取安装包。

## 3. 校验 SHA-256

PowerShell：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\personal-diet-pantry-0.7.4.27-installable.tgz'
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

把下面路径换成经过哈希校验的真实本地文件路径：

```text
openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.7.4.27-installable.tgz
```

确认插件已启用，并按该 OpenClaw 实例既有方式重新加载或重启。不要让安装脚本猜测宿主的停止、启动或配置命令。

## 6. 初始化与只读验收

1. 在 OpenClaw 工具列表中独立确认七类工具均已注册：`diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system`。
2. 新用户明确同意后，运行 `diet_system(initialize)` 创建本地账本结构。
3. 运行 `diet_system(self_check)`，确认迁移、SQLite、外键、schema 和配置没有失败项。
4. 用 `query_goals`、`progress` 和定向库存查询做只读验收；不要为了验收写入真实餐食、饮水、体重或库存。
5. 记录产品版本 `0.7.4.27`、技术包版本 `0.8.27`、七类工具状态、自检结果和 `dataDir` 是否独立，不记录数据库内容。

`self_check` 不能证明其他六类工具已经注册，因此第 1 步不可省略。任何一步失败都应明确报告“未完成”，再查阅[故障排除](TROUBLESHOOTING.zh-CN.md)。
