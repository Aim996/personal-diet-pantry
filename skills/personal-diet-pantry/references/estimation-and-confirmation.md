# Estimation and confirmation

Treat vague quantity language as evidence that may be resolved, not as permission to write an
invented exact amount. Examples such as “一点”, “一些”, “几口”, “半碗”, or “一小把” are tests,
not a closed runtime keyword list; reason from the food, container, preparation, user wording,
and any confirmed learned portion.

First preserve the original `portion_expression`. If a confirmed learned portion applies,
the tool may resolve it directly. Otherwise provide a bounded estimate with a suggested value
and range, unit, policy, and short evidence basis. Bounds must be plausible and ordered; avoid
false precision. If no defensible bounded range exists, ask one short quantity question with
zero business writes.

When one completed event contains one or more unresolved portions, send every resolvable item
in one `diet_meal preview_record`: one combined preview, not one preview per item. The preview
may resolve pantry identity internally. It must produce zero business writes and must not
deduct inventory. Show each suggested value and range, mark them as estimates, and ask
one confirmation covering the whole proposal.

A pure confirmation calls `commit_record` exactly once with the unchanged live handle and
`confirmed:true`. Build the receipt only from the committed result and its
`confirmed_estimate` or `confirmed_estimates`. New quantities, sources, targets, corrections,
or denials invalidate the old preview; merge the facts and reassess instead of committing it.
Supplemental facts are not confirmation. When a user supplies a missing time, quantity,
product identity, or inventory choice without final write authorization, issue one replacement
`preview_record` containing the complete merged proposal. The replacement remains
zero-business-write, supersedes the old handle, and needs only one new confirmation. Never
commit the old handle, demand that the user repeat the full original sentence, or accept two
equivalent confirmations for one proposal.

An exact replacement removes conflicting vague language and stale estimated measurements from
`portion_expression`. Preserve compatible natural classifiers and edible-part structure, but do
not retain an old vague classifier, granular count estimate, or prior gram value beside the
user's exact correction.

Supplemental facts with explicit final write authorization take a narrower route. If the same
message supplies a usable final value and explicitly says “就按…记” or “直接记录”, merge it with
the still-visible completed event and call `diet_meal record` exactly once. Do not commit the stale
handle, do not create a replacement preview, and do not ask again. If a prior pantry match reported
nutrition unavailable and the user now supplies the label, reuse the visible inventory_match_handle with the supplied nutrition facts in that one meal call; do not write Pantry metadata, deduct
separately, or turn unknown nutrition into zero. Without the visible completed event or product
identity, keep zero writes and ask only for the missing fact.
If the live preview is no longer visible in the same conversation, do not reconstruct or
guess its handle; ask for the minimum fact needed to form a new proposal.

Example: “晚上吃了根火腿肠” can preview one estimated sausage while asking for the missing
time. “刚刚吃的” then creates one replacement preview with trusted current time by omitting
`occurred_at`; “记上吧” commits that replacement handle exactly once. “吃了个玉米” creates
one preview that includes both `1个` and the estimated edible weight; “就按这个记吧” commits
it without another confirmation loop.

Non-occurrence wins before estimation. “Almost ate”, “planned to eat”, and “ended up not
eating” create no estimate, no preview, no meal, and no inventory movement. Never perform
nutrition estimation merely to satisfy validation, and never turn unknown nutrients into zero.
