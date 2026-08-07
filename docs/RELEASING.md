# Maintainer Release Guide

本页只供维护者发布食序管家 v0.7.5.0。版本目录和制品一经创建即不可变；发现已有同名目录、Tag 或 Release 时必须停止，不得覆盖。

## 固定发布顺序

1. Confirm main is clean and the complete local release gate is green. If GitHub Actions cannot start because of an account/infrastructure restriction, record that fact; never report it as a passing CI run.
2. Confirm productVersion 0.7.5.0 and package version 0.9.1.
3. Run `git ls-remote --tags origin refs/tags/v0.7.5.0`; stop if output is non-empty.
4. Build and verify the local immutable candidate.
5. Create the annotated tag only after approval.
6. Push only that tag.
7. Prefer the verified Release workflow. When GitHub Actions is unavailable for a documented infrastructure reason, the maintainer may manually create the Release only from the exact locally verified immutable candidate and the tagged main commit.
8. Verify the Release page and all five assets can be downloaded and hashed.
9. Report any failed or unverified step, including the Actions state, instead of claiming success.

## 发布前门禁

- 当前提交必须位于 `main`，工作树干净，CI 通过，产品/技术版本与 `RELEASE.zh-CN.md` 一致。
- 完整读取跨版本产品行为约束；确认七类工具、42 个动作、受保护回执和 migrations 001–022 没有漂移。
- 敏感信息扫描、Python、TypeScript、构建、Skill 校验、release audit、可安装包隔离与可复现构建全部通过。
- 候选目录必须是新目录，五个远程资产和本地 `GitHub文档/` 审阅树的哈希与 manifest 一致。

正式 Tag 必须是已批准提交上的 annotated tag `v0.7.5.0`。Release 工作流的手动触发只能做 dry-run 构建；只有推送精确匹配产品版本、且提交可追溯到 `origin/main` 的 Tag 才能进入 `GitHub Release` 发布 job。发布前先确认 `gh release view v0.7.5.0` 不存在；存在即停止，不得覆盖、替换或补传同名制品。

本地开发、测试和候选构建不构成创建正式 Release 的授权。收到单独明确发布指令后，人工发布必须用 `RELEASE.zh-CN.md` 作为正文，并且只上传构建器生成且经 `SHA256SUMS` 验证的五个资产；不得临时重打包、补传或覆盖同名 Release。
