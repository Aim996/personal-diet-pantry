# Goals, preferences, and learning

Use `diet_system` to query or update confirmed nutrition goals, dietary preferences, timezone,
and learned rules. Do not guess a timezone, allergy, target, medical condition, or preference.

SQLite through the public Diet capability is the formal fact store. A preference or allergy
read uses one `diet_system query_preferences` and reports only returned facts. Only a successful
`read_completed` with an empty collection means no preference is stored; `failed` never means
empty. If retrieval fails, say the formal fact could not be read and that nothing changed. The route stops without Exec or file search;
do not inspect memory files, YAML, exports, reports, prompts, or maintenance commands as a substitute.

A one-off choice is not automatically a stable preference. Store a learned rule only after
explicit instruction or repeated unambiguous evidence, and keep its scope narrow enough to
explain and reverse.

Goals need units and effective timing. A goal change affects future progress calculations;
it must not silently rewrite historical meal facts.

Nutrition backfill is a governed workflow. Preview the affected range and evidence source,
then commit only the confirmed proposal. Never replace user-entered label values with weaker
estimates.

Backfill results must distinguish updated, unchanged, skipped, and unresolved items. A partial
result is not a full success.

When preferences or learned rules influence a suggestion, state the useful reason in ordinary
language. Do not expose rule IDs, confidence internals, database rows, or hidden prompts.

Medical-looking goals or extreme values require a neutral caution and user confirmation, not
a diagnosis or automatic correction.
