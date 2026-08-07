ALTER TABLE transactions
ADD COLUMN undo_policy TEXT NOT NULL DEFAULT 'snapshot'
CHECK (undo_policy IN ('snapshot', 'none'));

ALTER TABLE transactions
ADD COLUMN effect_count INTEGER NOT NULL DEFAULT 0
CHECK (typeof(effect_count) = 'integer' AND effect_count >= 0);

UPDATE transactions
SET effect_count = MAX(
    CASE
        WHEN json_valid(before_snapshot) = 1 THEN
            CASE
                WHEN json_type(before_snapshot) = 'array'
                THEN json_array_length(before_snapshot)
                ELSE 0
            END
        ELSE 0
    END,
    CASE
        WHEN json_valid(after_snapshot) = 1 THEN
            CASE
                WHEN json_type(after_snapshot) = 'array'
                THEN json_array_length(after_snapshot)
                ELSE 0
            END
        ELSE 0
    END
);

UPDATE transactions
SET undo_policy = 'none',
    effect_count = 0
WHERE effect_count = 0;

UPDATE transactions
SET undo_policy = 'none'
WHERE id IN (
    SELECT transaction_id
    FROM operation_previews
    WHERE operation_type = 'import_preview'
      AND transaction_id IS NOT NULL
);

UPDATE transactions
SET undo_policy = 'none'
WHERE status IN ('committed', 'reverted')
  AND EXISTS (
      SELECT 1
      FROM operation_previews AS preview
      JOIN privacy_erasure_tombstones AS tombstone
        ON tombstone.preview_token_hash = preview.token_hash
      WHERE preview.operation_type = 'delete_data_preview'
        AND preview.consumed_at IS NOT NULL
  );

ALTER TABLE privacy_erasure_tombstones
ADD COLUMN control_operation_handle TEXT
CHECK (
    control_operation_handle IS NULL
    OR (
        length(control_operation_handle) = 36
        AND substr(control_operation_handle, 1, 4) = 'mop_'
        AND substr(control_operation_handle, 5)
            NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE UNIQUE INDEX
idx_privacy_erasure_tombstones_control_operation
ON privacy_erasure_tombstones(control_operation_handle)
WHERE control_operation_handle IS NOT NULL;

ALTER TABLE operation_previews
RENAME TO operation_previews_before_trusted_workflows;

CREATE TABLE operation_previews (
    token_hash TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'meal_preview',
        'water_preview',
        'pantry_add_preview',
        'pantry_deduct_preview',
        'pantry_adjust_preview',
        'water_reference',
        'weight_reference',
        'pantry_batch_reference',
        'meal_reference',
        'transaction_undo_reference',
        'transaction_redo_reference',
        'backup_reference',
        'shopping_list_preview',
        'shopping_list_reference',
        'import_preview',
        'delete_data_preview',
        'export_reference',
        'restore_preview'
    )),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    resource_versions_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    expires_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', expires_at, '+0 seconds') = expires_at,
        0
    )),
    consumed_at TEXT CHECK (
        consumed_at IS NULL OR COALESCE(
            strftime('%Y-%m-%dT%H:%M:%SZ', consumed_at, '+0 seconds') = consumed_at,
            0
        )
    ),
    transaction_id TEXT REFERENCES transactions(id) ON DELETE SET NULL,
    CHECK (julianday(expires_at) > julianday(created_at))
);

INSERT INTO operation_previews (
    token_hash,
    operation_type,
    request_json,
    result_json,
    resource_versions_json,
    created_at,
    expires_at,
    consumed_at,
    transaction_id
)
SELECT
    token_hash,
    operation_type,
    request_json,
    result_json,
    resource_versions_json,
    created_at,
    expires_at,
    consumed_at,
    transaction_id
FROM operation_previews_before_trusted_workflows;

DROP TABLE operation_previews_before_trusted_workflows;

CREATE INDEX idx_operation_previews_expiry
ON operation_previews(expires_at, consumed_at);

CREATE INDEX idx_operation_previews_type_expiry
ON operation_previews(operation_type, expires_at, consumed_at);

CREATE TABLE workflow_entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_kind TEXT NOT NULL CHECK (
        workflow_kind IN ('transaction', 'preview')
    ),
    workflow_key TEXT NOT NULL,
    entity_kind TEXT NOT NULL CHECK (
        length(trim(entity_kind)) BETWEEN 1 AND 64
    ),
    entity_key TEXT NOT NULL CHECK (
        length(trim(entity_key)) BETWEEN 1 AND 128
    ),
    relation TEXT NOT NULL CHECK (
        relation IN ('before', 'after', 'request', 'result')
    ),
    created_at TEXT NOT NULL CHECK (COALESCE(
        strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+0 seconds') = created_at,
        0
    )),
    UNIQUE (
        workflow_kind,
        workflow_key,
        entity_kind,
        entity_key,
        relation
    )
);

CREATE INDEX idx_workflow_entity_links_entity
ON workflow_entity_links(entity_kind, entity_key);

CREATE INDEX idx_workflow_entity_links_workflow
ON workflow_entity_links(workflow_kind, workflow_key);

INSERT OR IGNORE INTO workflow_entity_links (
    workflow_kind,
    workflow_key,
    entity_kind,
    entity_key,
    relation,
    created_at
)
SELECT
    'transaction',
    transactions.id,
    json_extract(
        CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
        '$.table'
    ),
    CAST(
        json_extract(
            CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
            '$.row_id'
        )
        AS TEXT
    ),
    'before',
    transactions.created_at
FROM transactions
JOIN json_each(
    CASE
        WHEN json_valid(transactions.before_snapshot) = 1 THEN
            CASE
                WHEN json_type(transactions.before_snapshot) = 'array'
                THEN transactions.before_snapshot
                ELSE '[]'
            END
        ELSE '[]'
    END
) AS entry
WHERE entry.type = 'object'
  AND json_extract(
      CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
      '$.table'
  ) IS NOT NULL
  AND json_extract(
      CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
      '$.row_id'
  ) IS NOT NULL;

INSERT OR IGNORE INTO workflow_entity_links (
    workflow_kind,
    workflow_key,
    entity_kind,
    entity_key,
    relation,
    created_at
)
SELECT
    'transaction',
    transactions.id,
    json_extract(
        CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
        '$.table'
    ),
    CAST(
        json_extract(
            CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
            '$.row_id'
        )
        AS TEXT
    ),
    'after',
    transactions.created_at
FROM transactions
JOIN json_each(
    CASE
        WHEN json_valid(transactions.after_snapshot) = 1 THEN
            CASE
                WHEN json_type(transactions.after_snapshot) = 'array'
                THEN transactions.after_snapshot
                ELSE '[]'
            END
        ELSE '[]'
    END
) AS entry
WHERE entry.type = 'object'
  AND json_extract(
      CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
      '$.table'
  ) IS NOT NULL
  AND json_extract(
      CASE WHEN entry.type = 'object' THEN entry.value ELSE '{}' END,
      '$.row_id'
  ) IS NOT NULL;
