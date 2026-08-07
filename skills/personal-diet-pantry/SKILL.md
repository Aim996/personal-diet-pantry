---
name: personal-diet-pantry
description: Use when a user speaks naturally about their own meals, nutritious drinks, plain water, cooking or leftovers, pantry stock or expiry, nutrition plans or reports, goals or preferences, undo/redo, explicit body weight, a diet event that ultimately did not happen, events explicitly not done, or any diet_* task—even without naming the Skill; exclude writing/translation, generic knowledge, image requests, and code/examples; MUST read this SKILL.md before any reply or diet_* tool.
---

# Personal Diet Pantry

This is the complete runtime contract; read it once when the Skill activates.
Runtime reference reads are exactly 0: do not open `references/`, source, tests,
schemas, databases, logs, reports, or packages. The seven typed tools and their
results are the only business authority.

## Natural-language activation

Interpret the user's complete intent, including Chinese colloquial wording, omitted subjects, spoken quantities, typos, and incomplete grammar; examples are tests, not a closed runtime keyword list.

When an event is clearly completed, use the applicable route below. Plans, hypotheticals, denials, and non-occurrence make zero write calls. A negative, planned, hypothetical, or cancelled event short-circuits before time or quantity resolution. Non-occurrence always wins over weight, water, food, and a pending
confirmation.

After activation, the rest of this Skill remains mandatory in Telegram, WebUI, and every other
channel. Never bypass it to call a `diet_*` tool.

## Readiness

Readiness is per required capability. Inspect the visible tool catalog without a
tool call. A missing unrelated capability does not block the task. If the one
required capability is unavailable, stop with one short setup instruction.

Do not run a warm-up, self-check, source scan, memory search, or exploratory query.
Use `diet_system self_check` only when the user asks for a health check or after a
database-integrity failure.

For just-completed `diet_meal record`, `preview_record`, and `record_cooking`, or
for `diet_water record`, use omitted `occurred_at` when the user gives no earlier
time so the trusted system clock is used. Send an explicit time only from a
user-supplied resolvable time. If the user gives only a coarse past segment such as
“早上” and the write requires a point in time, ask one short question for the
approximate time; combine it with any other essential missing fact. Never replace
that segment with the current time. Never invent `context.now`.

## Preferred capability routes

This is the preferred capability route table. Use exactly one primary route unless the table explicitly shows a two-call route.

| Intent | Route |
| --- | --- |
| completed food or nutritious drink | `diet_meal record` |
| uncertain but estimable food amount | `diet_meal preview_record`; later `commit_record` after pure confirmation |
| correct a recorded meal | `diet_meal update`; later `commit_record` after preview confirmation |
| cooking with eaten and stored portions | `diet_meal record_cooking` |
| prepared leftover eaten | `diet_pantry search` → `prepared_food_handle` → `diet_meal record_prepared` |
| plain water | `diet_water record` |
| explicit body weight | `diet_weight record` |
| complete pantry intake or packaged pantry intake | `diet_pantry add` |
| pantry add completed by a supplemental fact | `diet_pantry preview_add`; later `commit_add` after pure confirmation |
| completed pantry food or nutritious drink consumption | `diet_pantry search` → `inventory_match_handle` → `diet_meal record` |
| non-intake product use or discard | `diet_pantry search` → `diet_pantry deduct` or `discard` |
| inventory, expiry, or product nutrition lookup | one `diet_pantry` read |
| explicit whole current meal-record deletion | same-session meal handle → `diet_meal delete`; otherwise one meal query → delete |
| recent diet operation status | exactly one `diet_transaction get_recent` |
| undo or redo of an operation | `diet_transaction get_recent`; act only when the operation is unambiguous |
| progress or summary | one `diet_report` read |
| goals or preferences | one `diet_system` action |
| maintenance explicitly requested | one `diet_system` action |

Use `search` with the user's wording. Use `query` with `normalized_name` only when the canonical name is already known.
To locate the user's original wording, use `search` with `search_text`.
Prefer `expiry_date` for a calendar expiry date. Let the tool convert package display units.

## One-pass decision workflow

Use this control flow:

`event status -> requested scope -> sufficient facts -> one route -> one terminal result -> one reply`

Rules:

1. Resolve occurred versus planned/denied first.
2. Keep separate events separate; do not merge two meals because their contents match.
3. Ask at most one short question only when no defensible bounded estimate or target exists.
4. A clear single-domain read or write gets one tool call.
5. A targeted lookup followed by an action gets at most two calls.
6. A preview and its commit occur across user turns; each turn gets one call. Reuse its handle only in the same turn or live pending workflow.
7. Stop immediately on `write_committed`, `preview_ready`, `no_op`, or `failed`.
8. Supplemental facts are not confirmation: merge them and reassess write readiness.
   For pantry intake, the exact safe route is `pantry supplement -> preview_add -> pure confirmation -> commit_add`.
   If a required expiry fact was missing, ask once; after the user supplies it, send one complete
   `preview_add` with the original package facts plus the new expiry. A confirmation such as ‘确认记上’ must call commit_add with that live handle, never a fresh `add`.
9. Only a pure affirmation of the unchanged live preview may call `commit_record`.
10. A pure query or status check authorizes reads only. “刚才记上了吗” never
    auto-fills a missing record unless the same message explicitly says to补记.
    Use the status route above once. Do not scan meal, pantry, report, files, or another business tool; never replay the write.
11. Until a cross-domain atomic transaction exists, one message that asks two or
    more of meal, water, weight, pantry, transaction, or profile to write must be
    rejected before the first write. State that nothing was changed and ask the
    user to split it; never commit the easy parts first.
12. Permission is scoped to the current host run. A stopped, cancelled, failed, or
    previous turn cannot authorize a later write. A context-only correction must
    bind an opaque selected-record handle; a bare confirmation must bind the live
    commit handle. A single-domain prompt authorizes only the specific action the
    user expressed, never every mutation in that domain.

Never call `diet_report progress` to rebuild a successful write receipt. Never use Exec, Shell, SQL, Memory Search, file traversal, or another tool family as fallback.

Use tool-returned local fields for user-visible times. Never display the UTC companion when a `*_at_local` field is present. Use UTC only without a local projection.

## Completed, vague, and corrected intake

Clear completed food with a clear natural amount records directly. A countable common
item such as one corn or one sausage does not become ambiguous merely because its gram
weight and nutrition require a standard estimate; record directly in the same turn,
keep both the natural count and the estimated gram weight, label the estimate in the
receipt, and let a later correction replace it. Unknown nutrition stays unknown;
missing nutrients never become zero.

For an open-ended vague quantity such as “一点、一些、几口、几粒、一小把”:

- If a credible bounded estimate exists, show the proposed amount, unit, practical
  range, and estimated nutrition in one combined preview. The preview performs zero
  business writes and asks once for confirmation. This is a zero-write preview.
- If no defensible estimate exists, ask one concrete question and make zero calls.
- Never hide an estimate. “一个玉米” is recorded as a natural count plus an explicit
  estimated edible weight, for example “1个 90克（估算）”, not only calories.
- Preserve the user's natural classifier: “吃了个玉米” stays `1个`; do not silently
  rewrite it as `1根`. For food with an inedible core, peel, shell, bone, or pit,
  distinguish gross whole-item weight from edible portion. Nutrition always uses
  the edible portion. With no explicit weight, show a bounded edible estimate such
  as `1个｜可食部（玉米粒）约90克（估算）`. If the user gives a gross whole-item
  weight such as “带芯/带皮/带骨/连壳总重”, store that as gross evidence and mark
  any derived edible ratio and edible weight as estimates. An explicit edible weight
  replaces the estimate without an estimate label.
- Before the Meal call, put the same count, measurement object, edible weight, and
  estimate marker in one complete `portion_expression`; do not send only `1个` while
  leaving the edible evidence elsewhere. The plugin preserves submitted evidence but
  does not infer an edible-part label from the food name. For corn, the direct-call
  expression is `1个｜可食部（玉米粒）约90克（估算）`, the consumed weight is 90 g,
  and the bounded estimate uses that same value.
- A user correction replaces the previous fact through `diet_meal update`. When the
  successful write returned a unique handle and the correction is complete, use that
  handle-bound correction to update directly and atomically; never ask for a second confirmation.
  Send the complete replacement `items` collection, but do not repeat unchanged
  meal-level history merely to satisfy the tool: `occurred_at`, `meal_type`,
  and `location_type` may be omitted and are preserved from the handle-bound
  record. Always send the user's new correction sentence as `source_text`; it is
  the new portion evidence and audit reason. For an exact correction such as
  “其实是80克”, send `80克` in `portion_expression` with no `约` or `估算` marker.
  For “大概80克” keep the approximate marker. Explicitly supplied new meal-level
  values still replace the old values.
  Exact evidence replaces conflicting estimates; retain only compatible whole-item
  counts or edible-part labels.
  Do not try multiple parameter shapes, delete-and-recreate manually,
  or switch domains. If no unique live handle exists, query once and confirm the target.

An explicit whole current meal-record deletion is not historical operation undo.
With a verified same-session meal handle, call `diet_meal delete` once and do not call `diet_transaction undo`, query, or ask for confirmation. Without that live
handle, query only meal candidates once and delete only a selected `meal_handle`;
never guess the latest record. If an updated record is deleted, delete its current
meal handle rather than traversing or retrying older transaction operations.

When a zero-write meal preview is waiting for confirmation, keep its workflow scoped
to the visible current conversation:

- A pure affirmation such as “确认”, “就按这个记吧”, or “记上吧” consumes the
  unchanged live handle with exactly one `commit_record`; never turn it into a new
  direct `record` call and never ask for a second equivalent confirmation.
- A supplemental fact without final write authorization is not confirmation. Merge it with the
  visible event: if still incomplete, make one replacement `preview_record` and ask once; if it
  supplies a usable final value and says “就按…记” or “直接记录”, call direct `record` once.
  Never commit a stale preview, repeat the question, or ask for the original sentence again.
- A cancellation, denial, contradiction, expired handle, missing visible preview, or
  session change cannot commit the old proposal. Keep zero writes and either stop or
  ask the single fact needed to form a new proposal.

Every item with nutrition uses exactly one `nutrition_basis`: `per_100g`,
`per_100ml`, `per_serving`, or `consumed_total`. Scale exactly once. Do not put a
whole serving total in a per-100 basis.

Send exactly one of `nutrition_facts` or `nutrition_estimate`.
A/B evidence uses `nutrition_facts`; C/D evidence uses `nutrition_estimate`.
Never duplicate the same object into both fields. Missing label fields stay unknown:
omit unknown nutrition properties, never invent zero. Use only `hydration_ml`; never send `hydration`.

Meal type and location are analytical labels, not intake facts, and must not block a
clear completed intake. Normalize explicit wording deterministically: use
`breakfast/lunch/dinner/snack/other` for meal type and
`home/restaurant/takeout/unknown` for location (for example 午餐→`lunch`,
加餐→`snack`, 家里→`home`, 外卖→`takeout`). If the user did not supply either
label, omit them; the plugin fills the honest defaults `other` and `unknown`. Never
ask a question only to obtain these labels, never try multiple Chinese enum variants,
and never inspect implementation files.

## Time and query scope

Send relative or dated-segment wording (e.g. “8月3号晚上到4号天亮前”) verbatim
once in `natural_window.text`. The plugin resolves anchors, inherited dates, and
explicit endpoint clocks in profile timezone; never hand-build a retry range.

One cross-day `diet_meal query` includes every meal, snack, nutritious drink, and
supplement in the resolved window. Never filter only by meal label or use memory/chat.

For an explicit interval, send exact start/end. When `scope.complete` is true, use
one numbered row per returned meal; identical-looking records remain separate.
Never re-query or call them duplicates unless asked.

## Pantry evidence and food safety

An exact inventory selection is quantity evidence. Preserve the user's raw product
wording while using the returned opaque handle. Multiple physical batches of one product are not product ambiguity; distinct products are. If distinct products
match, show candidates and allow the user to choose or to record without inventory
association.

For a completed pantry food or nutritious drink consumption, the first and only
lookup is `diet_pantry search` with the user's product wording and
`nutrition_mode: "summary"`; do not call `diet_pantry query` after the successful search or search again. For one product candidate, copy its unchanged `raw_name`,
`normalized_name`, and `inventory_match_handle` into `diet_meal record`; keep the user's package amount and unit unchanged, for example `amount: 1`, `unit: 盒`.
Do not convert it to 250/ml or send nutrition fields while the handle owns registered conversion
and nutrition. If search says nutrition is unknown, send the user's label with that same handle
in one meal `record`; never call Pantry, re-preview, or write zero nutrition. The meal
transaction alone deducts inventory; never call pantry deduct separately for completed consumption. `diet_pantry deduct` is only for explicit non-intake use.

Treat registered or same-message exact package conversion as evidence. With a partial
label, keep amount, classifier, and full `source_text` in one Meal call; the host
derives the measure. Conflict/mismatch: zero writes, ask once. Missing nutrition
stays unknown; never add zeroes, split deduction, or retry.

Expired inventory is never a recommendation candidate. This is unconditional even
when the user says “优先把快坏的吃掉”. Keep expired items in a separate discard list;
do not suggest smelling, tasting, or reheating them as permission to eat. Recommend
only usable inventory returned by the tool.

A completed fact is not a recommendation. If the user explicitly reports already
eating expired stock, record the meal truthfully and use the exact expired-only
inventory selection in that same meal record so the movement remains `consume`.
Never refuse the fact, relabel it as discard, or use `record_prepared` without a real
`prepared_food_handle`. This exception never makes expired stock eligible for plans,
recipes, suggestions, or future-intent writes.

External meals never deduct home inventory. Inventory changes may be stated only
when the successful terminal result contains `inventory_effects`. For corrections,
describe the returned net change, not merely the new deduction.

## Progress and receipt

For a successful meal or water write, copy `rendered_receipt` from the final successful tool result exactly once and verbatim. Never reuse any field from an earlier receipt. Do not reconstruct, paraphrase,
compress, prepend a second “已记录”, or append a conflicting total.

The renderer owns the six metrics in fixed order: calories, protein, fat,
carbohydrate, fiber, water. Every metric uses exactly two lines. Percentages above
100 remain real. Known partial totals are marked as incomplete; unknown values are
not rendered as zero.

Pure water remains compact: acknowledge the water amount and show only water
progress. Never expand a plain-water event into the six nutrition metrics.

The stable visual form is:

```text
🔥 热量 ██████████ 103%
🔥1954.3 / 1900 kcal +111.3kcal +6%
```

After the verbatim receipt, add nothing unless the user explicitly asked another
question in the same message.

## Failure and retry boundary

One terminal error ends the route. Never repeat an unchanged failure fingerprint.
Do not change tools merely because validation failed. Use the returned structured
field/reason/expected information to ask for one corrected fact; retry only after
the user supplies changed input.

For a timeout or invalid bridge response after a write, the plugin may perform one
read-only operation-status lookup. Never replay the write. Report that the outcome
is uncertain when status cannot be proven.

On `DATABASE_INTEGRITY_ERROR`, make the only next call `diet_system self_check`.
Do not retry or queue the write. If integrity remains failed, block further writes
until the user says it was repaired and a new explicit self-check passes.

After two terminal Diet failures in the same host run, stop all further Diet
calls for that run. Do not insert a read call merely to reset the breaker.

## Body weight

Body-weight writes require explicit body-weight wording or a clear weight unit in
measurement context. A bare number without explicit body-weight wording must not create a body-weight record; make zero tool calls and ask for the unit or intent.

Example: `diet_weight(action="record", weight=104.6, unit="kg", status_note="空腹")`.
测量时间由工具读取系统当前时间；`measured_at` 不是公共参数，不要根据用户文字补传时间。

When present, replies may include `7日均值：104.2 kg` and
`趋势：7日均下降 ⬇️0.5 kg`; 没有 `trend` 时省略趋势行。

Deleting a weight is always two turns: first call `delete` with the selected
`record_handle` and show the exact weight plus local measurement time; this is a
zero-business-write preview. Only a pure confirmation may call `delete` again
with the returned `commit_handle`. Never delete from a guessed, stale, or merely
“recent” handle.

## Public reply boundary

Use concise Chinese. Never show tool names. Never expose those diagnostics. A public reply contains no internal identifier, path, credential, stack trace, source filename, database or transaction ID, or workflow handle. 不要显示工具名、数据库 ID、事务 ID 或工作流句柄。

The only public business tools are `diet_meal`, `diet_water`, `diet_weight`, `diet_pantry`, `diet_transaction`, `diet_report`, and `diet_system`.

`references/` is development-only; runtime agents must not read or route through it.
