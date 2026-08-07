# 食序管家 v0.7.5.2 更新说明

## 本版目标

v0.7.5.2 是一个只处理运行时启动兼容性的补丁版本。产品版本为 `0.7.5.2`，npm/OpenClaw/Python 技术包版本为 `0.9.2`。

真实 OpenClaw 安装验证发现：部分 Linux 或容器环境只提供 `python3`，没有名为 `python` 的可执行文件。v0.7.5.0 的 Node bridge 在未设置 `PYTHON` 时固定调用 `python`，因此七类工具虽然能够注册，第一次实际调用仍会以 `Unable to start the Python service` 失败。

## 修复内容

Python 解释器按以下顺序选择：

1. 调用方显式传入的解释器；
2. OpenClaw 进程环境变量 `PYTHON`；
3. 未配置时，Windows 使用 `python`，Linux、macOS 和其他非 Windows 平台使用 `python3`。

显式配置仍拥有最高优先级。本版没有增加插件配置字段，不要求普通用户伪造 `pythonExecutable`，也不会创建系统级软链接。

## 保留不变

- 继续保留 v0.7.5.0 的 Skill 指路边界、直接记录、直接纠错、六项两行进度回执和纯饮水简洁回执；
- 七类工具名称、请求契约、外部 `dataDir` 和安全边界不变；
- 沿用 migration 022，不新增数据库迁移；
- 不修改任何旧版本目录或已发布制品。

## 安装与更新

本版不新增 migration。

从 v0.7.5.0 更新到 v0.7.5.2 时保留原外部 `dataDir`，替换可安装包并重启 Gateway 即可；本次不会新增 migration。仍建议更新前创建并校验冷备份。

从 v0.7.4.28 或更早版本直接更新时，首次连接仍会应用既有 migration 022，必须遵循升级前冷备份与成套回滚要求。

在线 `diet_system backup` 仅用于同版本恢复，不能替代升级前冷备份。

OpenClaw 插件安装器只能接收 `personal-diet-pantry-0.7.5.2-installable.tgz`；`source.tar.gz` 只用于审阅。

正式发布候选必须包含并校验以下内容：

- `personal-diet-pantry-0.7.5.2-source.tar.gz`
- `personal-diet-pantry-0.7.5.2-installable.tgz`
- `release-manifest.json`
- `TEST-SUMMARY-v0.7.5.2.zh-CN.md`
- `SHA256SUMS`
- `GitHub文档/`

## 验证重点

- 在未设置 `PYTHON`、系统只有 `python3` 的 Linux 环境中，第一次 `diet_meal` 调用能够启动服务；
- 显式 `PYTHON` 覆盖仍然生效；
- 七类工具、自检、migration 022 和业务记录数量保持一致；
- 真实口语记录、纠错、饮水、库存、模糊输入与只读查询继续通过回归。
