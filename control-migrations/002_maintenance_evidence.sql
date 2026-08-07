ALTER TABLE maintenance_operations
ADD COLUMN reconciliation_decision TEXT CHECK (
    reconciliation_decision IS NULL
    OR reconciliation_decision IN (
        'committed',
        'failed',
        'verification_required'
    )
);

ALTER TABLE maintenance_operations
ADD COLUMN exclusive_released_at TEXT;

ALTER TABLE maintenance_artifacts
RENAME TO maintenance_artifacts_before_evidence;

CREATE TABLE maintenance_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    stage_code TEXT NOT NULL CHECK (length(trim(stage_code)) > 0),
    artifact_kind TEXT NOT NULL,
    relative_name TEXT NOT NULL,
    sha256 TEXT,
    verified_at TEXT,
    expected_sha256 TEXT CHECK (
        expected_sha256 IS NULL
        OR (
            length(expected_sha256) = 64
            AND expected_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    observed_sha256 TEXT CHECK (
        observed_sha256 IS NULL
        OR (
            length(observed_sha256) = 64
            AND observed_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE
);

INSERT INTO maintenance_artifacts (
    id,
    operation_id,
    stage_code,
    artifact_kind,
    relative_name,
    sha256,
    verified_at,
    expected_sha256,
    observed_sha256
)
SELECT
    id,
    operation_id,
    'legacy:' || CAST(id AS TEXT),
    artifact_kind,
    relative_name,
    sha256,
    verified_at,
    NULL,
    NULL
FROM maintenance_artifacts_before_evidence;

DROP TABLE maintenance_artifacts_before_evidence;

ALTER TABLE maintenance_checks
RENAME TO maintenance_checks_before_evidence;

CREATE TABLE maintenance_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    stage_code TEXT NOT NULL CHECK (length(trim(stage_code)) > 0),
    check_code TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'warn', 'fail')),
    checked_at TEXT NOT NULL,
    expected_json TEXT CHECK (
        expected_json IS NULL OR json_valid(expected_json) = 1
    ),
    observed_json TEXT CHECK (
        observed_json IS NULL OR json_valid(observed_json) = 1
    ),
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE
);

INSERT INTO maintenance_checks (
    id,
    operation_id,
    stage_code,
    check_code,
    outcome,
    checked_at,
    expected_json,
    observed_json
)
SELECT
    id,
    operation_id,
    'legacy:' || CAST(id AS TEXT),
    check_code,
    outcome,
    checked_at,
    NULL,
    NULL
FROM maintenance_checks_before_evidence;

DROP TABLE maintenance_checks_before_evidence;

CREATE UNIQUE INDEX idx_maintenance_artifact_stage_unique
ON maintenance_artifacts(
    operation_id,
    stage_code,
    artifact_kind,
    relative_name
);

CREATE UNIQUE INDEX idx_maintenance_check_stage_unique
ON maintenance_checks(operation_id, stage_code, check_code);

CREATE TABLE maintenance_quarantine_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    item_key TEXT NOT NULL CHECK (
        length(item_key) = 64
        AND item_key NOT GLOB '*[^0-9a-f]*'
    ),
    original_relative_name TEXT NOT NULL,
    staged_relative_name TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64
        AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK (
        state IN (
            'planned',
            'staged',
            'purge_pending',
            'restored',
            'purged'
        )
    ),
    recorded_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE,
    UNIQUE (operation_id, item_key),
    UNIQUE (operation_id, original_relative_name)
);

CREATE INDEX idx_maintenance_quarantine_state
ON maintenance_quarantine_items(operation_id, state, id);
