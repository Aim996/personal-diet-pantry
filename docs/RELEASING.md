# Maintainer Release Guide

本页只供维护者发布食序管家 v0.7.5.2。版本目录和制品一经创建即不可变；发现已有同名目录、Tag 或 Release 时必须停止，不得覆盖。

## 固定发布顺序

1. Confirm main is clean and the complete local release gate is green. If GitHub Actions cannot start because of an account/infrastructure restriction, record that fact; never report it as a passing CI run.
2. Confirm productVersion 0.7.5.2 and package version 0.9.2.
3. Run `git ls-remote --tags origin refs/tags/v0.7.5.2`; stop if output is non-empty.
4. Build and verify the local immutable candidate.
5. Create the annotated tag only after approval.
6. Push only that tag.
7. Prefer the verified Release workflow. When GitHub Actions is unavailable for a documented infrastructure reason, the maintainer may manually create the Release only from the exact locally verified immutable candidate and the tagged main commit.
8. Verify the Release page and all five assets can be downloaded and hashed.
9. Report any failed or unverified step, including the Actions state, instead of claiming success.

## 发布前门禁

- 当前提交必须位于 `main`，工作树干净，CI 通过，产品/技术版本与 `RELEASE.zh-CN.md` 一致。
- 完整读取跨版本产品行为约束；确认七类工具、75 个 TypeScript/Python 契约动作（其中 42 个是日常默认动作）、受保护回执和 migrations 001–022 没有漂移。
- 敏感信息扫描、Python、TypeScript、构建、Skill 校验、release audit、可安装包隔离与可复现构建全部通过。
- 候选目录必须是新目录，五个远程资产和本地 `GitHub文档/` 审阅树的哈希与 manifest 一致。

正式 Tag 必须是已批准提交上的 annotated tag `v0.7.5.2`。Release 工作流的手动触发只能做 dry-run 构建；只有推送精确匹配产品版本、且提交可追溯到 `origin/main` 的 Tag 才能进入 `GitHub Release` 发布 job。发布前先确认 `gh release view v0.7.5.2` 不存在；存在即停止，不得覆盖、替换或补传同名制品。

本版已经获得创建正式 Release 的明确授权。人工发布时必须用 `RELEASE.zh-CN.md` 作为正文，并且只上传构建器生成且经 `SHA256SUMS` 验证的五个资产；不得临时重打包、补传或覆盖同名 Release。

## v0.7.4.28 已完成发布证据

- 正式页面：[Personal Diet Pantry v0.7.4.28](https://github.com/Aim996/personal-diet-pantry/releases/tag/v0.7.4.28)。
- annotated tag `v0.7.4.28` 解引用提交、发布时的 `origin/main` 和 manifest 提交均为 `b649fd1b38f1b7a6f6eef31117ad855ddb35a641`。
- Release 为公开、非草稿、非预发布，恰好包含构建器生成的五个资产；没有上传本地 `GitHub文档/` 审阅树。
- 最终本地门禁：Python `606 passed / 610`、`4 skipped`；TypeScript `238 / 238`；Skill 路由 `43 / 43`；安装 E2E `4 / 4`；21 个 migration；生产依赖漏洞和敏感信息命中均为 0；两个归档均通过可复现构建。
- Tag 推送触发的 [Verified Release run 31161584233](https://github.com/Aim996/personal-diet-pantry/actions/runs/31161584233) 因 GitHub 账户账单锁在执行任何 step 前停止，不能记作 GitHub CI 通过。用户已经明确授权创建正式 Release，因此维护者使用最终不可变候选按本页人工回退规则发布。
- 发布后重新从 GitHub 下载五个资产，逐文件 SHA-256 与本地候选一致；tag 解引用提交再次与 manifest 一致。

详细资产哈希、环境和数据边界见 [v0.7.4.28 公开发布实录](releases/v0.7.4.28.zh-CN.md)。这段记录只描述已经完成的版本；未来发布必须重新执行门禁，不能沿用本次结果代替新证据。
