# 安装、初始化与升级手册

适用产品版本：Personal Diet Pantry（食序管家）v0.7.5.3。

包管理器、Python 包和 OpenClaw 插件元数据使用合法 SemVer 技术版本 `0.9.3`；发布文件名、导出 manifest 和用户文档使用产品版本 `0.7.5.3`。

本手册只说明当前源码和发布构件已经支持的本地安装流程；它不会部署、重启或配置任何既有 OpenClaw 实例。食序管家的正式事实来源是 SQLite，Markdown 报告、导出和健康报告均可由 SQLite 重新生成。

## 1. 安装前确认

运行环境需要满足以下条件：

| 项目 | 最低版本或要求 |
| --- | --- |
| OpenClaw | 2026.5.17+ |
| Node.js | `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`（含 npm） |
| Python | `>=3.11,<4` |
| 数据目录 | 为此插件单独准备、可写、非源码目录的 `dataDir` |

Python 优先由 OpenClaw 进程中的 `PYTHON` 选择；未设置时，Windows 使用 `python`，Linux、macOS 和其他非 Windows 平台使用 `python3`。该解释器必须具备安装包内 [`requirements.lock`](../requirements.lock) 锁定的运行时依赖：

```text
PyYAML==6.0.3
tzdata==2026.3
```

先确认将由 OpenClaw 使用的解释器，再安装或核对依赖。下面的 `python` 必须替换为该解释器；`/absolute/path/to/installed-plugin` 必须替换为已安装插件根目录，而不是源码目录或数据目录。

```bash
python --version
python -m pip install -r /absolute/path/to/installed-plugin/requirements.lock
python -c "import yaml, tzdata; print('Python runtime dependencies available')"
```

以上 `pip` 命令会改变该 Python 环境；请按运行环境的依赖管理规范执行。若组织策略不允许直接安装，请由运维人员在同一解释器环境中预置这两个锁定版本。不要把依赖安装到与 OpenClaw 实际使用的 Python 不同的虚拟环境中。

## 2. 选择正确的发布包

构建会在 `dist-package/` 生成两个用途不同的归档：

| 归档 | 用途 | 能否交给插件安装器 |
| --- | --- | --- |
| `personal-diet-pantry-0.7.5.3-installable.tgz` | 含编译后的插件、Python 运行包、锁定依赖、配置、迁移（含 023）、模板和 Skill 的 npm-compatible 安装包 | 可以，且只能使用它安装 |
| `personal-diet-pantry-0.7.5.3-source.tar.gz` | 与发布输入对应的源码快照，用于审阅、留档和源码复现 | 不可以 |

`source.tar.gz` 不含编译后的 `dist/`，不能替代安装包。两种归档均不应包含 SQLite、报告、缓存、`.env`、密钥或个人数据；若发现这些内容，应停止使用该归档并重新从受控源码构建或取得发布包。

正式发布目录顶层恰好包含 `personal-diet-pantry-0.7.5.3-source.tar.gz`、`personal-diet-pantry-0.7.5.3-installable.tgz`、`release-manifest.json`、`TEST-SUMMARY-v0.7.5.3.zh-CN.md`、覆盖这四个文件的 `SHA256SUMS`，以及 `GitHub文档/` 文档树。该目录必须位于 Git 工作树和项目根目录之外；不要使用旧名校验文件，也不要把其他文件混入顶层。

从干净 Git 提交生成正式制品时，发布脚本先执行完整门禁，再各生成两次源码包和可安装包并比较哈希与成员清单。它不会安装、启用、配置或重启生产 OpenClaw：

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py `
  --project-root C:\absolute\path\to\personal-diet-pantry `
  --release-root C:\absolute\path\to\release-directory
```

工作树存在已暂存、未暂存或未跟踪文件时，正式发布构建会拒绝运行。`--release-root` 等于或位于 Git 工作树/项目根目录内，或者目标路径已经存在时，脚本都会在创建目录或运行完整门禁前拒绝执行；必须传入尚不存在的外部路径。

构建入口使用启动 `scripts/build_release.py` 的解释器，并由脚本把该解释器传给后续门禁。在 Windows 的 Git Bash 中也应直接调用目标解释器，例如 `/c/absolute/path/to/.venv/Scripts/python.exe scripts/build_release.py ...`。不要把上述临时构建目录设为个人运行数据目录。

## 3. 规划独立数据目录

在 OpenClaw 支持的插件配置入口中，为 `personal-diet-pantry` 设置 `dataDir`。配置字段已由插件声明为字符串；CLI 可使用：

```bash
openclaw config set plugins.entries.personal-diet-pantry.config.dataDir "/absolute/path/to/personal-diet-pantry-data"
```

如果插件运行在 Docker、远程网关或受管理服务中，必须确认该命令写入的是目标实例的配置，并保证宿主内部能访问该绝对路径；无法确认时停止，不要猜测映射。

建议使用新建的专用目录，例如 `/absolute/path/to/personal-diet-pantry-data`。不要指向源码检出目录、发布包解压目录、临时构建目录、共享插件目录或另一位用户的数据目录。路径下也不应通过符号链接或重解析点逃逸到目录外。

数据目录的解析优先级固定如下：

1. 插件配置 `dataDir`；
2. 环境变量 `PERSONAL_DIET_PANTRY_DATA_DIR`；
3. `<OpenClaw data root>/personal-diet-pantry`。

所选根目录下的数据库文件固定为 `diet.sqlite`，并由插件维护 `backups/`、`exports/`、`imports/`、`reports/`、`cache/` 与 `health-report.md`。其中只有 `diet.sqlite` 是正式事实来源；不要直接编辑它、生成报告或迁移记录。待导入 JSON/CSV ZIP 只能以安全文件名放入 `imports/`，再通过 `validate_import` 校验，不接受绝对路径或跨目录引用。

## 4. 安装、初始化与首次验证

1. 取得 `personal-diet-pantry-0.7.5.3-installable.tgz`，并将命令中的绝对路径替换为该文件的真实位置。

   ```bash
   openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.7.5.3-installable.tgz
   openclaw plugins enable personal-diet-pantry
   ```

   该安装路径会在所选 OpenClaw 状态中注册并启用插件；它不会替你选择个人 `dataDir`，也不表示某个生产实例已经完成部署。

2. 使用第 3 节命令或目标实例的受支持管理界面设置专用 `dataDir`，并确保运行进程使用的 Python 已具备前述锁定依赖。普通本机网关可执行 `openclaw gateway restart`；Docker、远程网关或自定义服务必须使用各自既有重启方式。

3. 先执行 `openclaw plugins inspect personal-diet-pantry --runtime --json`，从真实运行时结果确认七类工具都可用：`diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system`。文件存在、元数据存在或 shell 命令成功不能代替七类工具证据。

4. 全新账本只有在用户明确授权初始化后，才调用 `diet_system` 的 `initialize`。未授权时保持零写入并报告等待用户决定。初始化后调用 `diet_system` 的 `self_check`，确认必需检查没有 `FAIL`，至少包括迁移、SQLite 完整性、外键和 schema 检查。若未通过，不要尝试日常写入，按[故障排除](TROUBLESHOOTING.zh-CN.md)处理。

5. 用只读工具做业务层验证：调用 `diet_pantry` 的 `search` 并提供具体 `search_text`，确认返回不超过 5 个紧凑候选；再调用 `diet_report` 的 `progress` 读取当前进度。初始空库、无匹配候选或未设置目标是正常状态；关键是它们读取的是同一 `dataDir` 中的 SQLite，且没有错误或意外写入。只有明确要浏览完整库存时才使用分页 `query`。

初始化、自检和只读验证不能用编辑数据库、手工创建 `schema_migrations` 或伪造报告来替代。首次验收是零业务写入：不得新增、修改、删除、撤销或重做真实餐食、饮水、体重、库存、目标或偏好。

## 5. 备份用途与降级冷备份

v0.7.5.3 新增 migration 023，只扩展目标更新预览句柄，不改写既有业务事实。从 v0.7.5.2 更新时保留原外部 `dataDir`，并在实例停止时取得一致 SQLite 冷备份；回退到 v0.7.5.2 时必须先恢复该备份。在线 `diet_system backup` 仅用于同版本恢复，不能替代升级前冷备份。

先停止目标实例并确认没有进程继续写入。使用受信 v0.7.5.3 源码中的 `scripts/cold_backup.py`，把 `diet.sqlite` 备份到 `dataDir` 外、权限受控且尚不存在的路径。helper 使用 Python 标准库 `sqlite3` backup API，会包含尚未 checkpoint 的已提交 WAL 数据，拒绝覆盖目标，并在成功前执行完整性检查。若操作系统阻止清理，错误会明确提示“未完成目标可能保留”，此时必须换新路径，不得覆盖重试。

```powershell
& 'C:\actual\python.exe' 'C:\trusted-v0.7.5.3-source\scripts\cold_backup.py' backup --source 'C:\actual\personal-diet-pantry-data\diet.sqlite' --destination 'D:\controlled-backups\diet.sqlite.pre-v0.7.5.3'
```

```bash
/actual/python3 /trusted-v0.7.5.3-source/scripts/cold_backup.py backup --source /actual/personal-diet-pantry-data/diet.sqlite --destination /controlled-backups/diet.sqlite.pre-v0.7.5.3
```

只有 helper 输出已验证 SHA-256 才算完成校验冷备份。记录路径和哈希，但不要把它们或数据内容公开到 issue、日志或文档中。

## 6. 升级到目标版本

1. 停止实例，创建并校验升级前冷备份。
2. 保留原外部 `dataDir`，通过 npm-pack 安装 `personal-diet-pantry-0.7.5.3-installable.tgz`。
3. 启动或重新加载插件；服务应在既有 migration 022 之后应用并记录 migration 023。
4. 独立核验七类工具，运行 `self_check`，再做目标、进度和定向库存的只读核对。
5. 确认旧记录数量不变，旧库存已有位置和到期时间不变，新增来源字段为 `legacy_unknown`。

Migration 023 只给内部预览表增加 `goal_update_preview` 操作类型。升级不会自动修改目标，也不会创建库存、重算到期日、补录营养或改写旧值；migration 022 的来源留痕规则继续保留。

## 7. 成套回滚

v0.7.5.2 不认识 migration 023，禁止仅替换程序包后继续使用已升级数据库。

回退到 v0.7.5.2 时必须先恢复该备份。

1. 停止实例并隔离当前已迁移的 `dataDir`。
2. 使用同一 helper 执行 `scripts/cold_backup.py restore`，验证并恢复升级前冷备份；恢复命令不会检测进程状态，必须由操作者确认实例已停止。它会成套隔离 `diet.sqlite`、`diet.sqlite-wal`、`diet.sqlite-shm` 和 `diet.sqlite-journal`，并拒绝覆盖隔离目标。
3. 安装已校验的 `personal-diet-pantry-0.7.5.2-installable.tgz`。
4. 启动后核对七类工具、`self_check`、migration 最大版本 022 和关键记录数量。

```powershell
& 'C:\actual\python.exe' 'C:\trusted-v0.7.5.3-source\scripts\cold_backup.py' restore --backup 'D:\controlled-backups\diet.sqlite.pre-v0.7.5.3' --active 'C:\actual\personal-diet-pantry-data\diet.sqlite' --quarantine 'C:\actual\personal-diet-pantry-data\diet.sqlite.v0.7.5.3-quarantine'
```

没有可验证的升级前冷备份时，不得宣称已安全回退。绝不能通过删除 migration、修改 SQL、直接编辑 SQLite 或手改 `schema_migrations` 来“降级”。

## 8. 安全边界

- 图片识别是独立 Skill 的职责；食序管家只接收结构化事实并通过业务工具写入。
- 计划、尚未发生的事件和失败操作不应成为正式记录；只有工具成功结果可证明已经写入。
- 不要在示例、命令历史、截图或支持请求中暴露真实令牌、主机地址、SQLite 内容、完整库存或个人饮食记录。
- 本仓库与发布包不承诺 Docker 部署、自动化任务或某个真实实例的运行状态。
