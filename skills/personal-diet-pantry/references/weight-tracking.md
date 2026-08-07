# Weight tracking

Use `diet_weight` for body-weight facts only when body-weight wording or a clear weight unit
makes the measurement explicit. A bare number is unresolved: make zero tool calls and ask for
its unit or intent. Non-occurrence always wins over weight.

Record the system time automatically. `measured_at` 不是公共参数 and the user does not need
to state a time. Preserve a free-form status such as 空腹、餐前、餐后、睡前 when supplied;
do not force statuses into a fixed vocabulary.

Use kilograms as the stored/displayed canonical unit. Convert a clearly stated alternate unit
once and show the resulting kilograms. Never guess a unit when the number is implausible as
kilograms and no usable context exists.

The compact success reply is:

`已记录：104.6 kg（空腹）`

`7日均值：104.6 kg｜趋势：⬇️0.5 kg`

Show the second line only when enough history exists for a meaningful 7-day comparison.
Trend compares compatible rolling windows, not the current point against an arbitrary old
measurement. Query/update/delete choices use readable time, value, and status, never IDs.
