ALTER TABLE pantry_batches ADD COLUMN storage_location_source TEXT NOT NULL
    DEFAULT 'legacy_unknown'
    CHECK (storage_location_source IN ('user', 'inferred', 'legacy_unknown'));

ALTER TABLE pantry_batches ADD COLUMN expiry_source TEXT NOT NULL
    DEFAULT 'legacy_unknown'
    CHECK (expiry_source IN ('user', 'estimated', 'legacy_unknown'));

CREATE INDEX idx_pantry_batches_default_provenance
ON pantry_batches (
    storage_location_source,
    expiry_source,
    status,
    expires_at
);
