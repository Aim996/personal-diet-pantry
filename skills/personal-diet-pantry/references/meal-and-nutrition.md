# Meal and nutrition

Use `diet_meal` for completed food and nutritious drinks, including milk, soy milk, soup,
protein drinks, and food eaten away from home. External meals never deduct home inventory
unless the user explicitly names home batches that were used.

Each item declares exactly one `nutrition_basis`: `per_100g`, `per_100ml`, `per_serving`, or
`consumed_total`. Pair `per_serving` with `consumed_servings` and scale exactly once. A
`consumed_total` already describes the whole consumed portion and must never be multiplied by
grams, millilitres, or servings again.

Prefer reliable label or database evidence. Missing label nutrients remain unknown.
Never add a model estimate only to make a write pass. Never replace missing label fields with zero.
For a user-supplied partial package label, send every known field exactly once and omit every
field the label did not state. A per-100g or per-100ml label may therefore contain only calories
and some macros; do not ask the user to declare fiber, sodium, or hydration as zero. The tool
scales known fields and keeps the others unknown.
When a genuine estimate is the available evidence route, preserve the original quantity
wording, attach an estimate confidence, and never invent precision. Liquids may contribute
estimated food hydration, but they are not plain-water records.

A clearly completed meal writes directly without asking “是否记录”. When its facts are complete
and it is external or otherwise not linked to home stock, call `diet_meal` once; do not preflight
with pantry, report, self-check, or preview calls. Plans, questions, hypotheticals, denials,
corrections without a resolvable target, and non-occurrence use zero tool calls. Query before
correcting only when the target is ambiguous.

When the quantity is colloquial but can be bounded, follow
`estimation-and-confirmation.md`: preserve the wording, return a suggested amount and range,
and use one combined preview for the whole event. Do not write the suggested value before
confirmation. A negative or cancelled event exits before portion, nutrition, or time work.

For an exact home-inventory meal, use one targeted `diet_pantry search` with
`nutrition_mode: "summary"`, then let `diet_meal record` perform internal targeted inventory resolution and the inventory mutation; no separate pantry preflight is needed after that search.
Never call a separate `diet_pantry query` or broad inventory browse after that search. Pass its
unchanged `inventory_match_handle` into the meal item.

Minimum pantry-linked meal item template:
`raw_name + normalized_name + amount + unit + inventory_match_handle`.
The validated handle and exact amount bind the meal write and pantry deduction into one atomic
operation, even when the user did not separately state a home location. An explicitly external
meal still never deducts household stock. Report a deduction only when the committed result
returns it in `inventory_effects`; do not imitate linkage with a second pantry mutation.
Preserve the user's package display amount and unit. Do not add `nutrition_facts`, a nutrition
basis, consumed dimensions, or package conversions on this normal path; the trusted handle
binds the selected inventory facts and the tool resolves the stored base quantity.

If that handle reports nutrition unavailable and the user then supplies a label, reuse the same
visible handle in one `diet_meal record`. Keep the completed package amount/unit, add the label's
basis and known `nutrition_facts`, and omit unstated nutrients. The handle supplies its registered
base conversion before the tool validates per-100g/per-100ml scaling; do not deduct Pantry
separately and do not replace unknown fields with zero.

When the user ate a previously stored prepared-food batch, search its pantry wording and use
the returned `prepared_food_handle` with `diet_meal record_prepared`. Do not rebuild the raw
recipe, deduct its original ingredients again, or route through a new nutrition estimate.

Never default an unknown bone-in item to a precise weight. First use confirmed package,
unit-weight, or edible-portion evidence; when none exists and the difference matters, ask one
short weight question and make zero writes.

In a mixed meal request, each liquid keeps its own volume and nutrition basis. Validate and
scale milk, coffee, soup, or other liquids independently, then send them in one meal action;
never copy one item's millilitres or nutrition facts to another.

For a successful write, report the meal, one compact nutrition summary, and only pantry effects
returned in committed `inventory_effects`. Never mention internal scaling, handler names,
confidence implementation, raw IDs, or diagnostic recovery.
