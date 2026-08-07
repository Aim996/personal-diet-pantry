# Maintainer Release Guide

本页只供维护者发布食序管家 v0.7.4.27。版本目录和制品一经创建即不可变；发现已有同名目录、Tag 或 Release 时必须停止，不得覆盖。

## 固定发布顺序

1. Confirm main is clean and CI is green.
2. Confirm productVersion 0.7.4.27 and package version 0.8.27.
3. Run `git ls-remote --tags origin refs/tags/v0.7.4.27`; stop if output is non-empty.
4. Build and verify the local immutable candidate.
5. Create the annotated tag only after approval.
6. Push only that tag.
7. Wait for the Release workflow.
8. Verify the Release page and all five assets can be downloaded and hashed.
9. Report any failed or unverified step instead of claiming success.

## 发布前门禁

- 当前提交必须位于 `main`，工作树干净，CI 通过，产品/技术版本与 `RELEASE.zh-CN.md` 一致。
- 完整读取跨版本产品行为约束；确认七类工具、40 个动作、受保护回执和 migrations 001–021 没有漂移。
- 敏感信息扫描、Python、TypeScript、构建、Skill 校验、release audit、可安装包隔离与可复现构建全部通过。
- 候选目录必须是新目录，五个远程资产和本地 `GitHub文档/` 审阅树的哈希与 manifest 一致。

正式 Tag 必须是已批准提交上的 annotated tag `v0.7.4.27`。Release 工作流的手动触发只能做 dry-run 构建；只有推送精确匹配产品版本、且提交可追溯到 `origin/main` 的 Tag 才能进入 `GitHub Release` 发布 job。发布前先确认 `gh release view v0.7.4.27` 不存在；存在即停止，不得覆盖、替换或补传同名制品。

本次“GitHub 公开就绪”实现不会执行上述第 5–8 步：它不创建 Tag、不推送 Tag、不创建 GitHub Release，也不改变仓库 Private 状态。
