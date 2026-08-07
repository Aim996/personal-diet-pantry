# Recipes, shopping, and cost

Use recipe and shopping actions only when they exist in the generated public tool contract.
Until then, explain the unavailable capability without fabricating a write.

`save_recipe` stores a reusable recipe profile: ingredients, normal batch/unit conversions,
yield, portion defaults, and optional preparation notes. It is not a historical meal.

`suggest_recipes` and meal-plan previews are read-only candidates. Return at most three useful
options grounded in current pantry, expiry pressure, goals, and confirmed preferences. Keep
why each option fits and what is missing.

A shopping preview is ephemeral. Commit creates the durable shopping list. Marking an item
purchased changes the list state but does not create pantry inventory; pantry intake remains
a separate explicit event.

Store money as integer minor units plus ISO currency. Never sum different currencies, invent
missing prices, or allocate a shared package cost twice.

Recipe/meal cost is derived from committed ingredient use and documented allocation rules.
Waste cost uses the discarded quantity, source batch, and available batch price; missing cost
must remain unknown rather than zero.

Suggestions and summaries are not nutrition or medical guarantees. The user-visible reply
separates facts, estimates, candidates, and committed changes.
