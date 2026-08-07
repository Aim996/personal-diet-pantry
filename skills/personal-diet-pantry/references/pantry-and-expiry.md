# Pantry and expiry

Use `diet_pantry` for physical food entering, changing, leaving, expiring, freezing, opening,
or being discarded. Keep separate batches when purchase time, expiry, price, source, storage,
or package identity differs.

For a clear completed purchase or intake, add the batch directly. Preserve the package count
exactly; a guessed mass may supplement but never replace the user's original unit. Shopping-list
purchase state is not pantry stock: only food reported as physically received may be added.
For packaged intake, pass the original `display_quantity`, `display_unit`, and
`base_quantity_per_display_unit`; retain a supplied `package_hierarchy`. These package facts
are durable inventory data, not reply-only decoration.

Minimum packaged-add template:
`food_name + unit + display_quantity + display_unit + base_quantity_per_display_unit`.
The tool derives base quantity only from this complete package specification. Do not invent a
package factor or add redundant base quantity when the display facts are sufficient.

## Targeted lookup

Use search before browse. For the first lookup, preserve the user's raw product wording.
Use `nutrition_mode: "summary"` when the lookup will feed a completed meal or nutritious-drink
record; use `nutrition_mode: "none"` for ordinary identity/quantity lookup. Search performs
exact, alias, and keyword expansion and returns at most five candidates. Do not page through
inventory to identify one item.

Multiple physical batches of the same product are one identity match: aggregate their available
quantity while preserving batch-level deduction order. Distinct products require one choice
before mutation; reuse the returned handle as `inventory_match_handle` for the chosen
downstream action while it is valid; do not repeat the lookup. For non-intake product identity use or
discard, search once, then call `deduct` or `discard` with that handle, quantity, and unit. For
completed intake, pass the handle with the unchanged natural package amount/unit to
`diet_meal record`; never query again or deduct separately. Never choose a physical batch or
calculate FEFO manually: the tool reduces eligible batches atomically in configured order.

The tool converts a package display unit to the stored base unit only when persisted package
facts prove one deterministic conversion. Pass the user's unit unchanged. If conversion is
unsupported or package sizes conflict, repair the named field or ask one short question; do
not guess a conversion.

Allowed `nutrition_mode` values are exactly `none | summary | full`.
The `none` mode is the default and does not read or return nutrition evidence. For a
main-nutrition question about a named pantry item, use `diet_pantry search` with
`nutrition_mode: "summary"`; this is the stable `nutrition_mode summary` route.

`full` is allowed only when the user explicitly asks for the complete/full nutrition label.
A single-field nutrition question must not silently promote `summary` to `full`.
If `summary` omits that field, say it is unavailable and ask whether to load the full label.

Browse and paginate full inventory only when the user explicitly asks for complete stock or a
full inventory/expiry report. Follow returned pagination until complete; never substitute a
first page for the full inventory.

## Raw, prepared, and related stock

Do not infer that a prepared batch is included in, or separate from, a raw ingredient total.
Answer an inclusion or deduction question only from the tool's returned relation: for example,
`derived_from`, `includes`, `separate_from`, or an equivalent explicit relation. A matching
name, recipe assumption, or arithmetic coincidence is not evidence. If the relation is absent,
say the stock relationship is not established and leave both quantities unchanged.

A prepared item may be backed by a committed cooking transaction while the raw ingredient has
already been deducted, or it may have been stocked independently. Use returned `relations` and
inventory kind to distinguish prepared stock from its raw ingredient. For a new mutation, only
committed `inventory_effects` prove that either total changed.

## Batches, waste, and expiry

Deduction uses the confirmed identity or handle, then lets the tool apply opened-first and
earliest-expiry batch strategy while respecting storage state and unit compatibility. Do not
turn multiple batches of one product into a product-choice question.

When the user gives a calendar date for pantry or leftover expiry, submit `expiry_date` as
`YYYY-MM-DD`. Do not invent a timezone offset or convert it to `expires_at`; the tool resolves
the local calendar end of day. Use `expires_at` only for an explicitly known instant.

“坏了扔了” or “过期丢掉” is waste, not consumption. Record the discard reason and quantity,
then report remaining stock only when the tool returns it. Expiry reporting is read-only:
separate expired, urgent, and upcoming items, and never imply they were discarded, frozen, or
consumed merely because they appeared in a report.

Keep recommendation eligibility separate from retrospective truth. Never recommend an expired
batch. When the user explicitly says they already consumed an expired item, use its exact
expired-only inventory selection in the meal transaction so the audit movement is `consume`;
never rewrite that event as discard or loss.
