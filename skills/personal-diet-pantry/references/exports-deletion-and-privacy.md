# Export, import, deletion, and privacy

Use `diet_system export_data`, `validate_import`, `import_data`, `preview_delete_data`, and
`commit_delete_data`. Never simulate them by editing SQLite, JSON, CSV, YAML, backups, or
reports directly.

Exports have a manifest with schema version, created time, included domains, row counts, and
checksums. User-facing output returns a safe external handle or filename, never an absolute
internal path.

Import is validate-then-commit. Validation accepts only a safe filename already placed in
`imports/`, checks schema compatibility, checksums, units, currency, required relationships,
duplicates, limits, and conflicts, and performs a rolled-back SQLite dry-run. A commit must use
the exact validated proposal and must not partially import.

Deletion is preview-then-commit. The only scopes are `raw_source_text`, `preferences`,
`intake_range`, `business_facts_keep_config`, and `all_business`; dates are permitted only for
`intake_range`. Preview identifies exact scope, counts, digest, consequences, and exclusions.
Commit requires confirmation bound to that exact preview; a vague “删掉” cannot expand scope.

Backups are governed separately from ordinary business deletion. Never silently delete backups
that may contain the same historical facts. Explain that distinction before irreversible work.

Privacy output omits credentials, API keys, gateway tokens, internal IDs, database/control
paths, stack traces, and raw source diagnostics. Keep only the minimum facts required by the
requested operation and documented retention policy.

For bulk import, restore, purge, or deletion uncertainty, query maintenance status before any
retry. Report committed counts and partial failures honestly.
