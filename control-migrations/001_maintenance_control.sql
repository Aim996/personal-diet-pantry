CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
);

CREATE TABLE maintenance_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_handle TEXT NOT NULL UNIQUE,
    operation_key TEXT UNIQUE,
    action TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'accepted',
            'running',
            'committed',
            'failed',
            'interrupted',
            'reconciling'
        )
    ),
    exclusive_operation INTEGER NOT NULL DEFAULT 0 CHECK (
        exclusive_operation IN (0, 1)
    ),
    exclusive_slot INTEGER NOT NULL DEFAULT 1 CHECK (exclusive_slot = 1),
    accepted_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT CHECK (
        result_json IS NULL OR json_valid(result_json) = 1
    ),
    error_code TEXT
);

CREATE UNIQUE INDEX idx_maintenance_one_active_exclusive
ON maintenance_operations(exclusive_slot)
WHERE exclusive_operation = 1
  AND status IN ('accepted', 'running', 'interrupted', 'reconciling');

CREATE INDEX idx_maintenance_operations_accepted
ON maintenance_operations(accepted_at DESC, id DESC);

CREATE TABLE maintenance_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    artifact_kind TEXT NOT NULL,
    relative_name TEXT NOT NULL,
    sha256 TEXT,
    verified_at TEXT,
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE
);

CREATE TABLE maintenance_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    check_code TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'warn', 'fail')),
    checked_at TEXT NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE
);

CREATE TABLE maintenance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reason_code TEXT,
    FOREIGN KEY (operation_id) REFERENCES maintenance_operations(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_maintenance_events_operation
ON maintenance_events(operation_id, id);

