# GitHub 发布与容器首次部署验收设计

## 目标

把 Personal Diet Pantry v0.7.3 的正式源码作为一个新的私有 GitHub 仓库发布，并在 GitHub Actions 的 Docker 环境中模拟一位没有历史配置和数据的普通用户完成首次安装。

## 方案选择

采用“私有仓库 + GitHub Actions Docker 验收”。不在当前 Windows 主机安装 Docker Desktop，也不把产品改造成常驻 Docker 服务。相比本机安装 Docker，这一方案不会改变宿主系统；相比只运行 `npm install`，它会经过 OpenClaw 的真实插件安装、配置和运行时检查路径。

## 发布边界

- 以 v0.7.3 正式源码归档为唯一源码基线。
- 仓库默认私有，因为项目当前没有许可证，且 `package.json` 标记为 `private`。
- 不上传 SQLite、报告、备份、导出、缓存、凭据、`.env`、虚拟环境或 `node_modules`。
- 运行时安装包由 CI 从当前提交执行 `npm run build` 和 `npm pack` 生成；Docker 阶段只接收生成后的 `.tgz`。

## 容器模型

- 基础环境：Node.js 24、Python 3、OpenClaw 2026.7.1-2。
- 安装锁定依赖：`PyYAML==6.0.3`、`tzdata==2026.3`。
- 创建固定但非 root 的陌生用户 `newcomer`，UID/GID 为 12001。
- 使用全新的 OpenClaw 状态目录和独立数据目录。
- 通过 `openclaw plugins install npm-pack:<archive> --force` 安装；禁止使用 `--dangerously-force-unsafe-install`。
- 配置 `plugins.entries.personal-diet-pantry.config.dataDir`，启用插件并验证配置。

## 验收标准

容器只有在以下条件全部成立时才以零状态退出：

1. 当前进程不是 root，初始状态目录和数据目录为空。
2. OpenClaw 接受可安装 `.tgz`，并能在运行时加载插件。
3. `diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system` 七个工具全部注册。
4. `diet_system initialize` 成功。
5. `diet_system self_check` 没有 `FAIL`。
6. 独立数据目录中生成 `diet.sqlite`，数据库不位于源码或安装包目录。
7. 安装包不包含 `src`、`tests`、`src-tests` 或 `contracts`。

## 失败处理

任何版本不兼容、依赖缺失、安装失败、配置无效、工具缺失、自检失败或数据目录越界都直接使 GitHub Actions 失败。工作流保存文本验收日志，但不保存或上传个人数据库。

