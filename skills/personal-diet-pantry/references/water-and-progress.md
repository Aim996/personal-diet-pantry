# Water and progress

Use `diet_water` only for plain water or an explicit plain-water equivalent. Milk, soy milk,
soup, coffee, tea with meaningful ingredients, and other nutritious drinks belong to
`diet_meal`, even when they contribute estimated hydration.

A clearly completed plain-water event writes directly with the stated volume. Use a cautious
conversion only for ordinary cups or bottles when no exact volume is given, and label it as
estimated. Never let food hydration exceed the consumed liquid volume.

Queries, corrections, and deletion must resolve the intended record. If multiple records fit,
show human-readable time and volume choices without exposing record IDs.

Daily progress uses committed totals and configured goals. Do not add plain water and food
hydration twice. If a goal is absent, show the amount without inventing a percentage.

For a successful meal or water write, build the receipt only from `daily_progress` in the final
successful tool result. That snapshot already contains the committed totals and the current-turn
increments. Never reuse totals or a progress snapshot from an earlier turn.
Do not call `diet_report progress` merely to rebuild a successful write receipt. A direct progress
query uses the aggregate report result and omits current-turn increments when none are returned.

Use one aggregate report call for a requested daily or weekly progress view. Do not fan out
into separate meal, water, weight, and pantry reads unless the aggregate result explicitly
lacks a required field.

When goals are confirmed, render all six metrics in fixed order: calories, protein, fat,
carbohydrate, fiber, and water. Every metric uses exactly two lines.
The first line contains only Emoji, name, bar, and percentage.
The second line contains the current amount, target, and an optional current-turn increment:

```text
🔥 热量 ██████████ 103%
🔥1954.3 / 1900 kcal +111.3kcal +6%
🥩 蛋白 ████████░░ 80%
🥩135.7 / 170 g +26.1g +15%
🧈 脂肪 ██████████ 156%
🧈85.59 / 55 g +0.09g +<1%
🌾 碳水 ██████████ 109%
🌾163.81 / 150 g +1.11g +<1%
🥬 纤维 ████░░░░░░ 40%
🥬12 / 30 g
💧 饮水 ░░░░░░░░░░ 0%
💧0ml / 3L
```

Compute `percentage = round(current / target * 100)` and
`filled = clamp(round(current / target * 10), 0, 10)`. The bar is exactly 10 cells using only
`█` and `░`. Over-target percentages remain real even though the bar is capped at 10 filled cells.

For a committed write, append the current-turn increment returned for that metric as
`+amount unit +percent`, where `increment_percentage = round(increment / target * 100)`.
Omit a zero or absent increment;
positive increments below 1% use `+<1%`. Do not wrap increments in parentheses. Do not derive an
increment from old context or from the difference between separately fetched reports.

Fiber with an explicitly unknown current remains `未知`, never `0`.
Water always uses current milliliters and target liters, such as `0ml / 3L`; water increments use
milliliters. If goals are not confirmed, state that progress bars are unavailable instead of
inventing targets.

After this fixed block, do not generate unsolicited judgment, praise, warnings, remaining-budget
commentary, or meal recommendations. Add interpretation only when the user explicitly asks for
analysis or advice. This presentation rule does not change the stored nutrition or inventory
transaction.

Do not add a progress-summary heading such as `📊 今日进度：` to a successful write receipt.
