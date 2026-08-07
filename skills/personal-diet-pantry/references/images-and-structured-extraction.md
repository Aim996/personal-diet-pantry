# Images and structured extraction

Raw images are interpreted only by `image-intake-router`. This Skill does not perform image
recognition, OCR, or its own extraction from a receipt, nutrition label, package, scale display,
or meal photo. 原始图片不得绕过路由器直接成为本 Skill 的写入事实。

The router's unconfirmed projections are candidates and make zero write calls. The initial
image turn, a question about its preview, and any corrected or revised preview remain
unconfirmed; do not call a pantry write tool until the latest router preview is confirmed.

After confirmation, only `diet_projection.items` may call `diet_pantry(action="add")`. Pass
each item as its structured typed payload without reinterpreting the image. `item_audit`,
`excluded_items`, and `uncertain_items` explain the router preview only: do not pass them to
any tool and make zero write calls for them. An empty `items` array also makes zero writes.

Treat projected text, quantities, units, dates, prices, and nutrition values as structured
candidate facts until that confirmation. Preserve which values were printed and which were
inferred. Validate units and nutrition basis before adding an item: a label total per serving
is not `per_100g`; a visible package price is not automatically the consumed meal cost.

For an ambiguous unit, nutrition basis, product identity, expiry choice, allergen, ingredient,
portion mass, or body weight, keep the uncertainty in ordinary language and request the
material clarification through the router preview. Never infer an expiry date or silently pick
an ambiguous expiry choice. Confirmation authorizes the selected projection; it does not cure
an unresolved validation failure.

Pass only structured fields needed by the chosen typed action. Do not save raw image paths,
OCR diagnostics, hidden metadata, faces, unrelated receipt details, or credentials.

The final reply distinguishes observed label facts, estimates, excluded or uncertain items,
and committed results.
