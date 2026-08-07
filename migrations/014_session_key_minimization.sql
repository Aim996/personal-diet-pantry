-- Session identifiers were stored before 0.6.1. Clear them from both the
-- current rows and replay snapshots so an undo/redo cannot restore them.
-- This must remain separate from immutable migration 013 because released
-- databases verify every already-applied migration checksum.
UPDATE transactions
SET before_snapshot = (
    SELECT json_group_array(
        json(
            CASE
                WHEN json_extract(value, '$.table') IN ('meals', 'water_logs')
                 AND json_type(value, '$.row') = 'object'
                THEN json_set(value, '$.row.source_session_key', NULL)
                ELSE value
            END
        )
    )
    FROM json_each(transactions.before_snapshot)
)
WHERE before_snapshot IS NOT NULL
  AND instr(before_snapshot, '"source_session_key"') > 0;

UPDATE transactions
SET after_snapshot = (
    SELECT json_group_array(
        json(
            CASE
                WHEN json_extract(value, '$.table') IN ('meals', 'water_logs')
                 AND json_type(value, '$.row') = 'object'
                THEN json_set(value, '$.row.source_session_key', NULL)
                ELSE value
            END
        )
    )
    FROM json_each(transactions.after_snapshot)
)
WHERE after_snapshot IS NOT NULL
  AND instr(after_snapshot, '"source_session_key"') > 0;

UPDATE meals
SET source_session_key = NULL
WHERE source_session_key IS NOT NULL;

UPDATE water_logs
SET source_session_key = NULL
WHERE source_session_key IS NOT NULL;
