# 食序管家 v0.7.5 核心写入链路重构执行文档

## 任务 1：锁定真实口语路由

修改 `src-tests/turn-guard.test.ts` 与 `src-tests/plugin-turn-guard.test.ts`，先新增失败用例：

- `刚啃了根玉米` 只能授权 `diet_meal record`，禁止 `preview_record`；
- `吃了点花生` 不能获得正式写入授权；
- `刚称了下106.8` 注入 kg 默认和一次 `diet_weight record` 指令；
- `刚买了俩苹果，放冰箱了` 注入一次 `diet_pantry add` 指令且不得追问日期；
- 纯水回执路由与六项餐食回执保持原测试通过。

运行定向 Vitest，保存红灯；随后新增 `src/direct-write-policy.ts`，让 `src/turn-guard.ts` 与 `src/index.ts` 共用同一个决策结果，移除明确餐食/入库路径上的 preview 选择权。

## 任务 2：实现 Pantry 默认值与来源

新增：

- `python/personal_diet_pantry/pantry_defaults.py`
- `migrations/022_pantry_default_provenance.sql`
- `tests/test_pantry_defaults.py`

修改：

- `src/schemas.ts`
- `src/index.ts`
- `python/personal_diet_pantry/pantry.py`
- `python/personal_diet_pantry/service.py`
- 导出/导入及迁移合同所需文件。

先写失败测试，覆盖显式位置优先、冷藏/冷冻/常温推断、缺失到期日自动估算、显式到期日不被覆盖、旧记录来源为 `legacy_unknown`。然后删除 TypeScript 与 Python 的“必须恰好提供一个到期字段”要求，在服务端一次补齐默认值并持久化来源。

## 任务 3：收口用户回执和 Skill

修改 `skills/personal-diet-pantry/SKILL.md` 及对应合同测试：

- 明确事实直接写，不预览；
- 明确称重语境中的裸数默认 kg；
- 普通入库不要求生产日期/到期日；
- 自动位置和到期日必须标估算，可自然纠正；
- 普通餐食继续六项双行；
- 纯水继续只显示饮水双行。

不得增加运行时 reference 读取、源码扫描或新的确认话术。

## 任务 4：版本、迁移和档案

把产品版本更新为 `0.7.5`，技术包版本更新为 `0.9.0`；同步 package、Python、插件、生成合同、更新说明、迁移数量、发布脚本和版本测试。更新 `docs/版本回望档案/0.7.5.md` 与总索引，但不修改任何旧版本目录或旧档案。

## 任务 5：验证与候选构建

按以下顺序执行：

1. 定向 Vitest/Pytest；
2. TypeScript 构建和完整 Vitest；
3. Python 完整 Pytest；
4. Skill 校验、敏感信息扫描、迁移/升级/安装 E2E；
5. 发布审计；
6. 在项目外的一次性目录生成 v0.7.5 候选制品和 SHA-256。

任一 CORE、SAFE、GOV 或 Good Flag 失败即停止，不安装、不发布。真实 OpenClaw UAT 和生产安装需在本地候选验证通过后另行执行。
