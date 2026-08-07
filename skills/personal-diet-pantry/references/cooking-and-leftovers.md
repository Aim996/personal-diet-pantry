# Cooking and leftovers

Use one `diet_meal(action="record_cooking")` when a single event prepares ingredients and
may also consume a portion or create leftovers. Never split the event into independent
pantry deductions, meal writes, and leftover additions.

One complete cooking event uses one atomic diet_meal action. Apply this rule: fried food with unknown oil is not a complete cooked result.
Ask once for the oil amount unless the user supplied a reliable
recipe default, and make zero writes while that fact is missing.

Describe every prepared ingredient with its pantry match, prepared quantity, and nutrition
basis. Describe the consumed quantity and stored leftover quantity independently; do not
infer that everything prepared was eaten.

The invariant is:

`prepared = consumed + stored leftovers + explicitly discarded preparation loss`.

Explicit fat loss changes fat and calories. Preserve the raw ingredient deduction, subtract
the confirmed removed-fat nutrition from the consumed result, and store the loss evidence. A
range such as 3–5g must ask for one usable value before the final nutrition write.

All ingredient deductions, the consumed meal, and newly stored leftover batches commit
atomically. If any required part fails, none of them may be claimed as changed.

When that prepared leftover is later eaten, locate it with targeted pantry search, then pass
its exact `prepared_food_handle` to `diet_meal(action="record_prepared")`. Reuse the immutable
prepared nutrition snapshot and deduct only that prepared batch; never recalculate the recipe
or deduct the raw ingredients again. If several distinct prepared foods match, ask one target
question before writing.

Use recipe profiles only as reusable ingredient/portion defaults. User wording in the current
event overrides a saved recipe. Save a recipe only when the user asks or gives a stable
repeatable recipe, never merely because one meal resembles an earlier meal.

The success reply shows the whole recipe, consumed fraction, stored leftovers, and committed
`inventory_effects`. Do not expose transaction IDs or narrate internal calls.
