# 食序管家 v0.7.3.4 更新说明

## 本版目标

v0.7.3.4 在 v0.7.3.3 恢复六项两行进度回执的基础上，补齐强制版本迭代规则。
以后只要版本内容发生实质修改，就必须创建下一版本，不能原地修改旧目录、沿用旧版本号
或覆盖同名安装包。技术包版本为 `0.7.7`，产品版本为 `0.7.3.4`。

## 强制版本迭代

- 版本目录创建后保持不可变，无论它是否已经上传、安装或正式发布。
- 文档、Skill、reference、规则、配置、源码、测试和构建脚本的变化都属于版本变化。
- 一次连续实现任务中的多次编辑作为一个变更批次，对应一个新版本；任务完成或形成候选后，
  后续新增修改必须再创建下一版本。
- 产品版本与 npm/OpenClaw/Python 技术版本同时递增，并同步发布说明、锁文件、导入兼容、
  制品文件名和自动化版本合同。
- 不得用相同版本号或相同制品文件名覆盖已有内容。

长期规则记录在 `docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md`，并由
`tests/test_version_contract.py::test_every_material_change_requires_a_new_immutable_version`
持续检查。

## 继承的进度回执修复

本版完整继承 v0.7.3.3 的回复层修复：写入成功后从最终工具结果读取 `daily_progress`，
固定显示六项、每项两行、10 格进度条、真实超标百分比和本次增量，不生成进度摘要标题
或未经请求的评价。

## 数据迁移与兼容

本版没有新增 migration，继续使用 migration 001–021，schema 与 v0.7.3.3 相同。
导入继续接受 0.6.7、0.7.0、0.7.1、0.7.2、0.7.3、0.7.3.1、0.7.3.2、0.7.3.3，
并接受 v0.7.3.4 可移植导出。升级时继续使用原专用 `dataDir`。

## 发布制品

正式发布目录顶层恰好包含：

- `personal-diet-pantry-0.7.3.4-source.tar.gz`
- `personal-diet-pantry-0.7.3.4-installable.tgz`
- `release-manifest.json`
- `TEST-SUMMARY-v0.7.3.4.zh-CN.md`
- `SHA256SUMS`
- `GitHub文档`

## 升级与回退

本项目不会自动部署、停止或重启 OpenClaw。升级前停止目标实例，创建并校验包含已提交
WAL 数据的一致 SQLite 升级前冷备份，同时保留 v0.7.3.3 可安装包。安装后运行
`diet_system self_check`，再验证一次餐食或饮水记录的六项进度回执。

本版没有 schema 变化；需要回退时，可以停止实例后安装 v0.7.3.3 并继续使用同一
`dataDir`。在线 `diet_system backup` 仍只用于同版本恢复。
