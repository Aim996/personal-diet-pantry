# Transactions and maintenance

Use `diet_transaction` for recent operations, undo, and redo. Locate a target through readable
time, domain, and summary. Never ask the user to copy an internal operation or record ID.

Undo and redo are writes. Respect the database circuit breaker, use the tool result as truth,
and report the restored user-visible state rather than implementation details.

Use `diet_system` for initialize, self-check, backup, restore, migrate, repair, maintenance
status, and maintenance history. Destructive or exclusive operations use an `operation_key`
so a retry cannot create a second independent operation.

Do not use `self_check` as a session warm-up. Run it after installation, genuine capability
uncertainty, an explicit user request, or a database integrity failure; visible tools already
establish ordinary session availability.

If a maintenance response is uncertain, timed out, interrupted, or reports an active operation,
query status/history before retrying. Never blindly repeat backup, restore, migrate, repair,
or delete work.

Restore requires an explicit preview and confirmation tied to the exact candidate. A successful
backup must be verified before claiming it is usable.

On `DATABASE_INTEGRITY_ERROR`, stop business writes and make `diet_system(action="self_check")`
the only next tool call. Explain one safe user action without raw paths, SQL, stack traces,
control rows, tokens, or credentials.
