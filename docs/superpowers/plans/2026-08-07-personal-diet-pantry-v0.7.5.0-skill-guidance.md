# 食序管家 v0.7.5.0 Skill 指路边界实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布一个产品版本为 `0.7.5.0` 的候选，使 Skill 提供领域路标、智能体选择普通正向工具路线、插件只执行负向安全护栏。

**Architecture:** 将普通正向对话分类为不绑定域和动作的 `agent_directed`，由智能体依据 Skill 与工具描述自行决策；保留只读、否定、计划、他人、复合写入和破坏性目标护栏。SQLite、事务、回执、库存默认值和 migration 022 不变。

**Tech Stack:** TypeScript 5.9、TypeBox、Vitest、Python 3.12、pytest、SQLite、OpenClaw plugin hooks。

## Global Constraints

- 产品版本必须为 `0.7.5.0`，技术包版本必须为 `0.9.1`。
- 不修改 `E:\codx\食序管家\0.7.5`、`0.7.5.1`、v0.7.4.28 或既有候选制品。
- 不新增数据库迁移；migration 022 继续是最新迁移。
- CORE-01～CORE-10、SAFE-01～SAFE-07、GOV-01～GOV-02、UI-01 和 GF-001～GF-005 不得弱化。
- 所有行为变化先看到对应测试在基线失败，再做最小实现。
- 不部署 OpenClaw、不更新 GitHub `main`、不创建 Release，除非用户单独授权。

---

### Task 1: 锁定“路标而非路线”的结构合同

**Files:**
- Create: `src-tests/agent-guidance-boundary.test.ts`
- Modify: `src-tests/plugin-turn-guard.test.ts`
- Modify: `src-tests/turn-guard.test.ts`
- Create: `tests/contracts/test_v0750_skill_guidance_contract.py`

**Interfaces:**
- Consumes: `classifyTurnIntent(text): TurnIntent`、OpenClaw `before_prompt_build` hook。
- Produces: `TurnIntent.mode === "agent_directed"` 的测试合同；普通正向 prompt 不包含具体工具路线的合同。

- [ ] **Step 1: 写普通正向自治红灯**

```ts
it.each(["吃了个玉米", "刚喝了137毫升水", "刚称了下106.8", "买了两盒酸奶"])(
  "leaves an ordinary positive fact agent-directed: %s",
  (text) => expect(classifyTurnIntent(text)).toEqual({ mode: "agent_directed", domains: [] }),
);
```

- [ ] **Step 2: 写 prompt 不编排路线红灯**

```ts
const result = await beforePrompt({ prompt: "吃了个玉米" }, context);
expect(result).toBeUndefined();
```

- [ ] **Step 3: 写源码与 Skill 结构红灯**

```python
def test_skill_points_without_prescribing_positive_route():
    assert not (ROOT / "src/direct-write-policy.ts").exists()
    skill = (ROOT / "skills/personal-diet-pantry/SKILL.md").read_text("utf-8")
    assert "能力地图" in skill
    assert "普通正向输入" in skill
```

- [ ] **Step 4: 运行红灯并确认失败原因**

Run: `node node_modules/vitest/vitest.mjs run src-tests/agent-guidance-boundary.test.ts src-tests/plugin-turn-guard.test.ts src-tests/turn-guard.test.ts`

Expected: FAIL，因为 `agent_directed` 尚不存在且普通正向 prompt 仍注入具体路线。

Run: `python -m pytest tests/contracts/test_v0750_skill_guidance_contract.py -q`

Expected: FAIL，因为正向路由文件仍存在且 Skill 尚未改成能力地图。

### Task 2: 移除普通正向口语路由

**Files:**
- Delete: `src/direct-write-policy.ts`
- Delete: `src-tests/direct-write-policy.test.ts`
- Modify: `src/turn-guard.ts`
- Modify: `src/index.ts`
- Test: `src-tests/agent-guidance-boundary.test.ts`

**Interfaces:**
- Produces: `TurnMode` 新增 `agent_directed`；`classifyTurnIntent` 未命中负向或破坏性分支时返回 `{ mode: "agent_directed", domains: [] }`。
- Preserves: `read_only`、`workflow_confirmation`、目标绑定、失败熔断和跨域原子安全分支。

- [ ] **Step 1: 删除正向分类器导入和 prompt 注入**

从 `src/index.ts` 删除 `classifyDirectWrite`、`directWriteInstruction` 以及普通正向 `appendContext` 分支；删除对应源码和测试文件。

- [ ] **Step 2: 添加 `agent_directed` 模式**

```ts
export type TurnMode =
  | "read_only"
  | "workflow_confirmation"
  | "single_domain_write"
  | "multi_domain_write"
  | "agent_directed"
  | "ambiguous";
```

- [ ] **Step 3: 让普通正向消息不绑定域和动作**

在处理操作状态、确认、他人、查询、否定、复合写入和目标型动作之后，普通非空消息返回：

```ts
return { mode: "agent_directed", domains: [] };
```

不得调用食物、量词或摄入动词分类器来生成正向域和 `allowedActions`。

- [ ] **Step 4: 运行结构测试**

Run: `node node_modules/vitest/vitest.mjs run src-tests/agent-guidance-boundary.test.ts src-tests/plugin-turn-guard.test.ts src-tests/turn-guard.test.ts`

Expected: 新增结构测试 PASS；旧测试中只允许因正向固定路线合同过期而失败。

### Task 3: 把授权器收口为负向护栏

**Files:**
- Modify: `src/turn-guard.ts`
- Modify: `src-tests/turn-guard.test.ts`
- Modify: `src-tests/plugin-turn-guard.test.ts`

**Interfaces:**
- Consumes: `TurnIntent.mode === "agent_directed"`、`domain`、`action`、工具参数。
- Produces: `isAgentDirectedCreate(domain, action): boolean`；仅允许普通非破坏性新增和预览。

- [ ] **Step 1: 写授权范围红灯**

```ts
expect(authorizeToolCall(agentDirected, "meal", { action: "record" })).toEqual({ allowed: true });
expect(authorizeToolCall(agentDirected, "pantry", { action: "add" })).toEqual({ allowed: true });
expect(authorizeToolCall(agentDirected, "meal", { action: "delete" })).toMatchObject({ allowed: false });
expect(authorizeToolCall(agentDirected, "system", { action: "update_goals" })).toMatchObject({ allowed: false });
```

- [ ] **Step 2: 实现非破坏性动作集合**

```ts
function isAgentDirectedCreate(domain: DietDomain, action: string): boolean {
  return (domain === "meal" && ["record", "preview_record", "record_cooking", "record_prepared"].includes(action)) ||
    (domain === "water" && action === "record") ||
    (domain === "weight" && action === "record") ||
    (domain === "pantry" && ["add", "preview_add"].includes(action));
}
```

- [ ] **Step 3: 在授权器中加入 `agent_directed` 分支**

读动作仍始终可用；负向 `read_only` 仍阻止写；`agent_directed` 只允许上述集合；目标型、系统型和维护型写入继续走原有明确授权与句柄合同。

- [ ] **Step 4: 运行全部 turn guard 测试**

Run: `node node_modules/vitest/vitest.mjs run src-tests/turn-guard.test.ts src-tests/plugin-turn-guard.test.ts`

Expected: PASS。

### Task 4: 将 Skill 改成能力地图

**Files:**
- Modify: `skills/personal-diet-pantry/SKILL.md`
- Modify: `tests/contracts/test_v0750_skill_guidance_contract.py`
- Modify: `tests/contracts/test_v075_core_write_skill_contract.py`
- Modify: `tests/test_skill_progressive_disclosure.py`

**Interfaces:**
- Produces: 小于 20,000 UTF-8 字节的 `SKILL.md`；正文主干为目标、观察点、能力地图、硬边界和回执合同。
- Preserves: `references/*.md` 作为按需领域知识，GF-001～GF-004 的用户可见行为。

- [ ] **Step 1: 重写主干**

用如下结构替换 “Preferred capability routes” 和 “One-pass decision workflow” 等固定路线章节：

```markdown
## 目标
## 先看清事实
## 能力地图
## 证据与估算
## 不可越过的边界
## 正式结果
## 按需参考
```

- [ ] **Step 2: 删除封闭正向路线措辞**

普通新增部分不得出现“遇到某句话调用某工具恰好一次”的短语表。保留成功即停止、相同失败不重试等结果约束；破坏性操作和工具真实依赖可以保持严格。

- [ ] **Step 3: 运行 Skill 合同和大小校验**

Run: `python -m pytest tests/contracts/test_v0750_skill_guidance_contract.py tests/contracts/test_v075_core_write_skill_contract.py tests/test_skill_progressive_disclosure.py -q`

Expected: PASS，且 `SKILL.md` UTF-8 大小小于 20,000 字节。

### Task 5: 统一版本为 0.7.5.0 / 0.9.1

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `openclaw.plugin.json`
- Modify: `pyproject.toml`
- Modify: `python/personal_diet_pantry/__init__.py`
- Modify: `python/personal_diet_pantry/data_import.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `RELEASE.zh-CN.md`
- Rename: `UPDATE-v0.7.5.zh-CN.md` to `UPDATE-v0.7.5.0.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `scripts/build_release.py`
- Modify: version, packaging, workflow and public-surface tests that currently expect `0.7.5` / `0.9.0`.

**Interfaces:**
- Produces: 产品版本 `0.7.5.0`、技术包版本 `0.9.1`、制品前缀 `personal-diet-pantry-0.7.5.0`。
- Preserves: migration count 22 and migration 022 rollback contract。

- [ ] **Step 1: 写版本红灯**

先把版本合同测试期望改为 `0.7.5.0` / `0.9.1` 并运行，确认因旧值失败。

- [ ] **Step 2: 更新代码与发布文档版本**

所有当前候选入口统一使用 `0.7.5.0`；历史段落中的 v0.7.4.28 保持不变。`0.7.5` 只能出现在解释被纠正的未发布候选历史时。

- [ ] **Step 3: 运行版本和制品测试**

Run: `python -m pytest tests/test_version_contract.py tests/test_build_release.py tests/test_github_workflows.py tests/test_public_repository_surface.py tests/integration/test_installable_e2e.py -q`

Run: `node node_modules/vitest/vitest.mjs run src-tests/version-contract.test.ts src-tests/package-contents.test.ts`

Expected: PASS。

### Task 6: 回望、全量门禁和候选制品

**Files:**
- Create: `docs/版本回望档案/0.7.5.0.md`
- Modify: `docs/版本回望档案/README.md`
- Modify: `E:\codx\食序管家\食序管家项目总约束.md`（只追加本轮经用户明确确认的职责边界）

**Interfaces:**
- Produces: 干净提交、完整门禁证据和项目外候选目录。
- Preserves: GitHub `main`、OpenClaw 实例和真实 `dataDir` 零改动。

- [ ] **Step 1: 更新档案和总约束**

记录“Skill 指路、智能体选择正向路线、插件只守负向边界”；列出 GF-001～GF-005 的实际测试状态，不把自动化冒充实机 UAT。

- [ ] **Step 2: 运行完整门禁**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File ci/verify.ps1`

Expected: Python、TypeScript、Skill、敏感信息、依赖、迁移、安装和升级全部通过。

- [ ] **Step 3: 提交干净源码**

```text
git add -A
git diff --cached --check
git commit -m "refactor: let agents choose positive diet routes"
```

- [ ] **Step 4: 从干净提交生成候选**

Run: `python scripts/build_release.py --project-root . --release-root E:\codx\食序管家\0.7.5.0\release-candidate`

Expected: 可重复源码包和安装包、manifest、测试摘要和 SHA-256 均指向同一提交。

- [ ] **Step 5: 保持发布边界**

不安装、不更新 `main`、不创建 Release。向用户报告候选路径、测试证据、剩余实机 UAT 和错误远端分支的清理选项。
