# Time and query scopes

Interpret every temporal expression as a composable time constraint, not as a closed list of
special phrases. Examples are tests, not a closed runtime keyword list. Apply the user's
configured IANA timezone; for this installation it is `Asia/Shanghai`.

Resolve an explicit interval exactly. Resolve a natural expression into a local half-open
interval `[start, end)`, then use the UTC bounds returned or accepted by the plugin. A range may
cross midnight, a week, a month, or a year. Calendar scopes, rolling durations, meal-period
scopes, relative-day scopes, and user-supplied start/end bounds all use the same mechanism.

Use exactly one public scope descriptor per query: `natural_window` for verbatim natural
calendar phrases (relative or explicit calendar dates plus registered segments),
`calendar_window` for structured calendar-relative periods and configured segments,
`rolling_window` for a duration ending now, or `local_range` for already-resolved explicit
local start/end bounds. `occurred_on` is the legacy full-day form. Do not send a timezone
argument that the action Schema does not expose; the plugin applies the profile zone.

A meal-history question covers all intake domains carried by `diet_meal` in that interval:
meals, snacks, nutritious drinks, supplements, and other meal-domain intake. Plain-water-only
history remains a `diet_water` route. “Last night” is an example, not a special-case definition.
Do not narrow an explicit interval to the `dinner` meal type, and do not omit late snacks
merely because their stored meal label differs.

For one meal-history question, use one `diet_meal query` with the resolved scope. Do not
preflight with pantry, report, or system tools. Use the returned `scope` and `complete` fields
to describe the time range and completeness; never claim a complete answer from a truncated
page. Only a returned cursor authorizes the next page.

When `scope.complete` is true and the user asked what they ate, the final list is mechanical:
its count equals the length of the returned `meals` array, and it contains one numbered row for
each array entry in returned order. Two entries with the same time, foods, or calories are still
two rows. Do not group, summarize away, or deduplicate either one, and stop after this successful
query. This rule does not take over deletion candidates, nutrition analysis, suggestions, or an
incomplete current window.

Treat membership in the returned half-open scope as authoritative. A historical record may
carry a legacy meal label or `source_text` whose wording disagrees with its projected local
timestamp; report the record and, if useful, flag the inconsistency separately. Never omit it,
reclassify it as outside the range, or query again merely because that text looks surprising.

Keep storage and display distinct: facts are stored as UTC instants, while replies show local
time and `timezone_name`. Local projections are display facts, not new stored events.

If a local wall time is nonexistent during a clock transition, ask for a valid time. If it is
ambiguous, ask which occurrence was intended. Do not guess either case. A negative, cancelled,
planned, or hypothetical event is resolved before this file is loaded and never becomes a
historical query or write merely because it contains a time phrase.
