# 安装、初始化与升级手册

适用产品版本：Personal Diet Pantry（食序管家）v0.7.4.28。

包管理器、Python 包和 OpenClaw 插件元数据使用合法 SemVer 技术版本 `0.8.28`；发布文件名、导出 manifest 和用户文档使用产品版本 `0.7.4.28`。

本手册只说明当前源码和发布构件已经支持的本地安装流程；它不会部署、重启或配置任何既有 OpenClaw 实例。食序管家的正式事实来源是 SQLite，Markdown 报告、导出和健康报告均可由 SQLite 重新生成。

## 1. 安装前确认

运行环境需要满足以下条件：

| 项目 | 最低版本或要求 |
| --- | --- |
| OpenClaw | 2026.5.17+ |
| Node.js | `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`（含 npm） |
| Python | `>=3.11,<4` |
| 数据目录 | 为此插件单独准备、可写、非源码目录的 `dataDir` |

Python 由 OpenClaw 进程中的 `PYTHON` 选择；未设置时使用 `python`。该解释器必须具备安装包内 [`requirements.lock`](../requirements.lock) 锁定的运行时依赖：

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
| `personal-diet-pantry-0.7.4.28-installable.tgz` | 含编译后的插件、Python 运行包、锁定依赖、配置、迁移（含 021）、模板和 Skill 的 npm-compatible 安装包 | 可以，且只能使用它安装 |
| `personal-diet-pantry-0.7.4.28-source.tar.gz` | 与发布输入对应的源码快照，用于审阅、留档和源码复现 | 不可以 |

`source.tar.gz` 不含编译后的 `dist/`，不能替代安装包。两种归档均不应包含 SQLite、报告、缓存、`.env`、密钥或个人数据；若发现这些内容，应停止使用该归档并重新从受控源码构建或取得发布包。

正式发布目录顶层恰好包含 `personal-diet-pantry-0.7.4.28-source.tar.gz`、`personal-diet-pantry-0.7.4.28-installable.tgz`、`release-manifest.json`、`TEST-SUMMARY-v0.7.4.28.zh-CN.md`、覆盖这四个文件的 `SHA256SUMS`，以及 `GitHub文档/` 文档树。该目录必须位于 Git 工作树和项目根目录之外；不要使用旧名校验文件，也不要把其他文件混入顶层。

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

1. 取得 `personal-diet-pantry-0.7.4.28-installable.tgz`，并将命令中的绝对路径替换为该文件的真实位置。

   ```bash
   openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.7.4.28-installable.tgz
   openclaw plugins enable personal-diet-pantry
   ```

   该安装路径会在所选 OpenClaw 状态中注册并启用插件；它不会替你选择个人 `dataDir`，也不表示某个生产实例已经完成部署。

2. 使用第 3 节命令或目标实例的受支持管理界面设置专用 `dataDir`，并确保运行进程使用的 Python 已具备前述锁定依赖。普通本机网关可执行 `openclaw gateway restart`；Docker、远程网关或自定义服务必须使用各自既有重启方式。

3. 先执行 `openclaw plugins inspect personal-diet-pantry --runtime --json`，从真实运行时结果确认七类工具都可用：`diet_meal`、`diet_water`、`diet_weight`、`diet_pantry`、`diet_transaction`、`diet_report`、`diet_system`。文件存在、元数据存在或 shell 命令成功不能代替七类工具证据。

4. 全新账本只有在用户明确授权初始化后，才调用 `diet_system` 的 `initialize`。未授权时保持零写入并报告等待用户决定。初始化后调用 `diet_system` 的 `self_check`，确认必需检查没有 `FAIL`，至少包括迁移、SQLite 完整性、外键和 schema 检查。若未通过，不要尝试日常写入，按[故障排除](TROUBLESHOOTING.zh-CN.md)处理。

5. 用只读工具做业务层验证：调用 `diet_pantry` 的 `search` 并提供具体 `search_text`，确认返回不超过 5 个紧凑候选；再调用 `diet_report` 的 `progress` 读取当前进度。初始空库、无匹配候选或未设置目标是正常状态；关键是它们读取的是同一 `dataDir` 中的 SQLite，且没有错误或意外写入。只有明确要浏览完整库存时才使用分页 `query`。

初始化、自检和只读验证不能用编辑数据库、手工创建 `schema_migrations` 或伪造报告来替代。首次验收是零业务写入：不得新增、修改、删除、撤销或重做真实餐食、饮水、体重、库存、目标或偏好。

## 5. 备份用途与降级冷备份

v0.7.4.28 没有新增 migration，与 v0.7.4.19 共用 migration 001–021，因此直接代码回退只需保留 v0.7.4.19 可安装包并在实例停止时替换版本。0.7.4.19 是技术回退包，不是产品 UAT 通过基线。仍应保留**实例已停止时取得的一致 SQLite 冷备份**，用于安装损坏、磁盘故障或数据库异常，而不是为了逆转 schema。

在线 `diet_system backup` 仅用于同版本恢复，不能替代升级前冷备份。在线快照仍适合 v0.7.4.28 日常恢复；调用 restore 后应运行 `self_check`。v0.7.4.28 schema 与 v0.7.4.19 相同，但冷备份仍是数据受损时的独立恢复证据。

### 5.1 创建并校验升级前冷备份

先按目标实例已有的运维流程**停止目标实例**，并由操作者确认插件进程已经退出；本项目不虚构宿主专属的停止命令，也不尝试检测进程状态。使用受信 v0.7.4.28 源码包中的 `scripts/cold_backup.py`，通过 Python 标准库 `sqlite3` backup API 把 `diet.sqlite` 备份到 `dataDir` 外、权限受控且尚不存在的路径。该 API 会把尚未 checkpoint 的已提交 WAL 数据纳入一致快照，而不是直接复制单个主数据库文件。

Windows PowerShell 示例（把解释器、helper、源库和目标路径替换为本机实际绝对路径；目标目录必须存在，目标文件必须尚不存在）：

```powershell
& 'C:\actual\python.exe' 'C:\trusted-v0.7.4.28-source\scripts\cold_backup.py' backup --source 'C:\actual\personal-diet-pantry-data\diet.sqlite' --destination 'D:\controlled-backups\diet.sqlite.pre-v0.7.4.28'
```

POSIX 示例（同样替换为实际绝对路径，并使用实例真实的 Python 3 解释器）：

```bash
/actual/python3 /trusted-v0.7.4.28-source/scripts/cold_backup.py backup --source /actual/personal-diet-pantry-data/diet.sqlite --destination /controlled-backups/diet.sqlite.pre-v0.7.4.28
```

helper 以独占创建方式拒绝覆盖已有目标及其 SQLite sidecar，并在成功前对生成的数据库执行 `PRAGMA quick_check`；任一前置条件、SQLite 复制或完整性检查失败都会以非零状态停止。helper 会清理本次创建的未完成文件；若 Windows 文件锁等操作系统错误阻止清理，错误会明确指出**未完成目标可能保留**，此时不得覆盖或直接重试该路径。只有命令输出已验证的 SHA-256 时，才算完成校验冷备份。记录备份路径与 SHA-256，但不要把路径、哈希或数据内容公开到 issue、日志或文档中。

## 6. 升级到目标版本

升级必须把软件包和数据视为一套状态。v0.7.4.28 不会自动部署、停止或重启实例。升级到产品版本 v0.7.4.28 时按以下顺序执行：

1. 确认直接回退所需的 `personal-diet-pantry-0.7.4.19-installable.tgz` 可读且来自受信发布位置。
2. 按第 5 节停止实例，创建并校验升级前冷备份；在线 `diet_system backup` 不能代替这一步。
3. 保持实例停止，使用目标版本的 `personal-diet-pantry-0.7.4.28-installable.tgz` 通过 npm-pack 安装路径安装，不要把 `source.tar.gz` 交给安装器。
4. 保留原有 `dataDir` 映射。不要为了“清理安装”切换到新数据目录，否则会看到空数据库而非原有记录。
5. 按实例既有流程启动或重新加载插件；普通本机网关可使用 `openclaw gateway restart`。本版没有新增 migration，服务只会确认既有 migration 001–021。
6. 执行 `openclaw plugins inspect personal-diet-pantry --runtime --json` 核验七类工具，再调用 `diet_system` 的 `self_check`，确认所有必需检查通过。
7. 仅做只读验证：调用定向 `diet_pantry search` 检查候选上限、`nutrition_mode` 和 `inventory_match_handle`，调用 `diet_system query_goals` 检查 `goal_source` 与 `confirmed_at`，再调用 `diet_report progress` 检查进度。需要更深的数据库验证时，调用 `diet_system` 的 `validate_database`。

本版保留并验证迁移 001–021，没有新增 migration。迁移 020 增加定向库存搜索索引和 `pantry_product_reference`；沿用 v0.7.3 已有的迁移 021，为库存批次加入包装展示数量、展示单位、单包装基础数量和包装层级，并加入熟食精确引用所需的 `prepared_food_reference`。营养档案与批次快照仍规范化保存在同一个 `diet.sqlite`，不会把营养复制进库存批次。已有餐食、饮水、体重、库存、目标和预览保持原值；升级不会自动创建库存、导入或删除数据、补录历史营养、调用模型、擅自改写历史估算或替用户确认目标。

## 7. 成套回滚

若升级后的自检或只读验证失败，先停止实例。直接代码回滚需要：

1. 安装 `personal-diet-pantry-0.7.4.19-installable.tgz`；
2. 保留现有 `dataDir`，因为 v0.7.4.28 没有新增 migration，schema 与 v0.7.4.19 相同；
3. 启动后运行 `self_check` 和只读查询确认状态。

只有数据库本身异常、安装过程破坏文件或需要恢复升级前数据状态时，才使用第 5 节 helper 创建并校验的升级前一致 SQLite 冷备份。

保持实例停止，使用同一 helper 恢复。恢复命令不会检测进程状态，必须由操作者先确认实例已经停止。它会先验证冷备份，并在活动目录中创建、校验临时恢复候选；随后以拒绝覆盖的方式把当前 `diet.sqlite`、`diet.sqlite-wal`、`diet.sqlite-shm` 和 `diet.sqlite-journal`（若存在）分别移到指定隔离基名及其 `-wal`/`-shm`/`-journal` 文件，最后才替换活动数据库。任一前置、完整性或目标已存在检查失败都会在替换前停止；隔离中途失败会尝试把已经移动的文件原路恢复，若操作系统也阻止回滚则会明确报错并保持实例停止。不要删除隔离副本或覆盖唯一备份。

PowerShell 数据库恢复示例：

```powershell
& 'C:\actual\python.exe' 'C:\trusted-v0.7.4.28-source\scripts\cold_backup.py' restore --backup 'D:\controlled-backups\diet.sqlite.pre-v0.7.4.28' --active 'C:\actual\personal-diet-pantry-data\diet.sqlite' --quarantine 'C:\actual\personal-diet-pantry-data\diet.sqlite.v0.7.4.28-quarantine'
```

POSIX 数据库恢复示例：

```bash
/actual/python3 /trusted-v0.7.4.28-source/scripts/cold_backup.py restore --backup /controlled-backups/diet.sqlite.pre-v0.7.4.28 --active /actual/personal-diet-pantry-data/diet.sqlite --quarantine /actual/personal-diet-pantry-data/diet.sqlite.v0.7.4.28-quarantine
```

恢复数据并校验一致后，再安装 v0.7.4.19 可安装包恢复技术运行状态：

```bash
openclaw plugins install npm-pack:/absolute/path/personal-diet-pantry-0.7.4.19-installable.tgz
```

随后按实例既有运维流程启动 v0.7.4.19，调用 `diet_system self_check`，并用 `diet_pantry query` 和 `diet_report progress` 做只读确认。不得在实例仍运行时覆盖插件或数据库。该回退只用于恢复运行和保护数据，不能覆盖 v0.7.4.19 已有的真实 UAT 失败结论。

绝不能通过删除迁移文件、修改迁移 SQL、直接编辑 SQLite 或手工更改 `schema_migrations` 来“降级”。发生 migration checksum 不匹配时，请停止写入并按[故障排除](TROUBLESHOOTING.zh-CN.md#迁移校验和不匹配)恢复成套的已验证版本与数据。

## 8. 安全边界

- 图片识别是独立 Skill 的职责；食序管家只接收结构化事实并通过业务工具写入。
- 计划、尚未发生的事件和失败操作不应成为正式记录；只有工具成功结果可证明已经写入。
- 不要在示例、命令历史、截图或支持请求中暴露真实令牌、主机地址、SQLite 内容、完整库存或个人饮食记录。
- 本仓库与发布包不承诺 Docker 部署、自动化任务或某个真实实例的运行状态。
