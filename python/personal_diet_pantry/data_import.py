"""Strict validation and atomic import for supported portability bundles."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Mapping
import zipfile

from . import privacy
from .data_export import (
    CONTRACT_VERSION,
    EXPORT_SCHEMA_VERSION,
    PORTABLE_TABLES,
    _EXCLUDED_COLUMNS,
    _JSON_COLUMNS,
    _RELATIONS,
)
from .models import DataPaths
from .paths import validate_owned_path


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_FILES = 32
MAX_ARCHIVE_FILE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100_000
SUPPORTED_PRODUCT_VERSIONS = frozenset(
    {
        "0.6.7",
        "0.7.0",
        "0.7.1",
        "0.7.2",
        "0.7.3",
        "0.7.3.1",
        "0.7.3.2",
        "0.7.3.3",
        "0.7.3.4",
        "0.7.3.5",
        "0.7.3.6",
        "0.7.4.0",
        "0.7.4.1",
        "0.7.4.2",
        "0.7.4.3",
        "0.7.4.4",
        "0.7.4.5",
        "0.7.4.6",
        "0.7.4.7",
        "0.7.4.8",
        "0.7.4.9",
        "0.7.4.10",
        "0.7.4.11",
        "0.7.4.12",
        "0.7.4.13",
        "0.7.4.14",
        "0.7.4.15",
        "0.7.4.16",
        "0.7.4.17",
        "0.7.4.18",
        "0.7.4.19",
        "0.7.4.20",
        "0.7.4.21",
        "0.7.4.22",
        "0.7.4.23",
        "0.7.4.24",
        "0.7.4.25",
        "0.7.4.26",
        "0.7.4.27",
        "0.7.4.28",
        "0.7.5",
    }
)
_IMPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.(?:json|zip)$")
_EXTERNAL_HANDLE = re.compile(r"^pdp_[a-z_]+_[A-Za-z0-9_-]{16,64}$")


class DataImportError(ValueError):
    """Raised when a portability artifact is unsafe or incompatible."""


@dataclass(frozen=True)
class ImportPlan:
    import_name: str
    artifact_sha256: str
    manifest: Mapping[str, Any]
    records: Mapping[str, list[Mapping[str, Any]]]

    @property
    def record_counts(self) -> Mapping[str, int]:
        return {
            table: len(self.records[table]) for table in PORTABLE_TABLES
        }


def load_and_validate(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    import_name: str,
) -> ImportPlan:
    path = _safe_import_path(data_paths, import_name)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise DataImportError("Import artifact is unavailable") from error
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise DataImportError("Import artifact size is outside the safe limit")
    artifact_sha256 = _file_sha256(path)
    if path.suffix.lower() == ".json":
        bundle = _load_json(path)
    else:
        bundle = _load_csv_archive(path)
    manifest, records = _validate_bundle(bundle)
    _validate_against_schema(connection, records)
    _ensure_empty_target(connection)
    _ensure_no_handle_conflicts(connection, records)
    _dry_run_validate(connection, records)
    return ImportPlan(
        import_name=import_name,
        artifact_sha256=artifact_sha256,
        manifest=manifest,
        records=records,
    )


def commit_import(
    connection: sqlite3.Connection,
    plan: ImportPlan,
    *,
    now: datetime,
    preview_token_hash: str,
) -> dict[str, Any]:
    _ensure_empty_target(connection)
    effect_count = sum(plan.record_counts.values())
    transaction_id = f"txn_import_{secrets.token_urlsafe(18)}"
    timestamp = _timestamp(now)
    ids = _allocate_ids(connection, plan.records)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys = ON")
        _ensure_empty_target(connection)
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, source_text
            ) VALUES (?, 'record_correction', 'pending', ?, ?)
            """,
            (transaction_id, timestamp, "portable data import"),
        )
        for table in PORTABLE_TABLES:
            if table == "nutrition_goal_profiles":
                _import_goal(
                    connection,
                    plan.records[table],
                    ids,
                    transaction_id,
                )
                continue
            for record in plan.records[table]:
                _insert_record(
                    connection,
                    table,
                    record,
                    ids,
                    transaction_id,
                )
        connection.execute(
            """
            UPDATE transactions
            SET status = 'committed',
                committed_at = ?,
                before_snapshot = '[]',
                after_snapshot = '[]',
                undo_policy = 'none',
                effect_count = ?
            WHERE id = ?
            """,
            (timestamp, effect_count, transaction_id),
        )
        public_result = {
            "import": {
                "record_counts": dict(plan.record_counts),
                "export_schema_version": EXPORT_SCHEMA_VERSION,
                "artifact_sha256": plan.artifact_sha256,
            }
        }
        changed = connection.execute(
            """
            UPDATE operation_previews
            SET result_json = ?,
                consumed_at = ?,
                transaction_id = ?
            WHERE token_hash = ? AND consumed_at IS NULL
            """,
            (
                _canonical_json(public_result),
                timestamp,
                transaction_id,
                preview_token_hash,
            ),
        ).rowcount
        if changed != 1:
            raise DataImportError("Import preview is stale")
        failures = connection.execute("PRAGMA foreign_key_check").fetchall()
        if failures:
            raise DataImportError("Imported relationships are invalid")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return public_result["import"]


def _validate_bundle(
    value: Any,
) -> tuple[Mapping[str, Any], Mapping[str, list[Mapping[str, Any]]]]:
    if not isinstance(value, Mapping) or set(value) != {"manifest", "records"}:
        raise DataImportError("Import bundle structure is invalid")
    manifest = value["manifest"]
    records = value["records"]
    if not isinstance(manifest, Mapping) or not isinstance(records, Mapping):
        raise DataImportError("Import bundle structure is invalid")
    if manifest.get("export_schema_version") != EXPORT_SCHEMA_VERSION:
        raise DataImportError("Export schema version is unsupported")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise DataImportError("Export contract version is unsupported")
    product_version = manifest.get("product_version")
    if product_version not in SUPPORTED_PRODUCT_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_PRODUCT_VERSIONS))
        raise DataImportError(
            f"Only portability exports from {supported} are accepted"
        )
    if set(records) != set(PORTABLE_TABLES):
        raise DataImportError("Import record domains are incomplete")

    normalized: dict[str, list[Mapping[str, Any]]] = {}
    handles: dict[str, str] = {}
    count = 0
    for table in PORTABLE_TABLES:
        rows = records[table]
        if not isinstance(rows, list):
            raise DataImportError("Import domain records must be arrays")
        normalized_rows: list[Mapping[str, Any]] = []
        for record in rows:
            if not isinstance(record, Mapping) or set(record) != {
                "external_handle",
                "values",
            }:
                raise DataImportError("Import record structure is invalid")
            handle = record["external_handle"]
            values = record["values"]
            if (
                not isinstance(handle, str)
                or _EXTERNAL_HANDLE.fullmatch(handle) is None
                or handle in handles
                or not isinstance(values, Mapping)
            ):
                raise DataImportError("Import external identity is invalid")
            handles[handle] = table
            _validate_record_fields(table, values)
            normalized_rows.append(
                {"external_handle": handle, "values": dict(values)}
            )
            count += 1
        normalized[table] = normalized_rows
    if count > MAX_RECORDS:
        raise DataImportError("Import record count exceeds the safe limit")
    expected_counts = {
        table: len(normalized[table]) for table in PORTABLE_TABLES
    }
    if manifest.get("record_counts") != expected_counts:
        raise DataImportError("Import record counts do not match the manifest")
    digest = hashlib.sha256(
        _canonical_json(normalized).encode("utf-8")
    ).hexdigest()
    if manifest.get("records_sha256") != digest:
        raise DataImportError("Import records checksum does not match")
    privacy.assert_portable_payload(
        {"manifest": dict(manifest), "records": normalized}
    )
    if len(normalized["nutrition_goal_profiles"]) > 1:
        raise DataImportError("Import contains multiple goal profiles")
    _validate_relationships(normalized, handles)
    return dict(manifest), normalized


def _validate_record_fields(table: str, values: Mapping[str, Any]) -> None:
    forbidden = set(values) & _EXCLUDED_COLUMNS
    if forbidden or any(key.endswith("_id") for key in values):
        raise DataImportError("Import record contains internal fields")
    allowed_relation_names = {
        output_name
        for (relation_table, _column), (_target, output_name) in _RELATIONS.items()
        if relation_table == table
    }
    relation_columns = {
        column
        for relation_table, column in _RELATIONS
        if relation_table == table
    }
    # Scalar column validity is checked against the live schema at commit.
    if relation_columns & set(values):
        raise DataImportError("Import record exposes relationship identifiers")
    for key in values:
        if key.endswith("_handle") and key not in allowed_relation_names:
            raise DataImportError("Import record relationship is unsupported")


def _validate_relationships(
    records: Mapping[str, list[Mapping[str, Any]]],
    handles: Mapping[str, str],
) -> None:
    for table, rows in records.items():
        relation_names = {
            output_name: target_table
            for (relation_table, _column), (target_table, output_name) in _RELATIONS.items()
            if relation_table == table
        }
        for record in rows:
            values = record["values"]
            for name, expected_table in relation_names.items():
                target = values.get(name)
                if target is not None and handles.get(str(target)) != expected_table:
                    raise DataImportError(
                        "Import relationship target is missing or incompatible"
                    )


def _validate_against_schema(
    connection: sqlite3.Connection,
    records: Mapping[str, list[Mapping[str, Any]]],
) -> None:
    for table in PORTABLE_TABLES:
        columns = _table_columns(connection, table)
        relationship_columns = {
            column
            for relation_table, column in _RELATIONS
            if relation_table == table
        }
        relationship_outputs = {
            output_name
            for (relation_table, _column), (_target, output_name) in _RELATIONS.items()
            if relation_table == table
        }
        allowed = (
            columns
            - set(_EXCLUDED_COLUMNS)
            - relationship_columns
        ) | relationship_outputs
        for record in records[table]:
            if not set(record["values"]) <= allowed:
                raise DataImportError(
                    "Import record contains unsupported fields"
                )


def _ensure_no_handle_conflicts(
    connection: sqlite3.Connection,
    records: Mapping[str, list[Mapping[str, Any]]],
) -> None:
    handles = [
        str(record["external_handle"])
        for table in PORTABLE_TABLES
        for record in records[table]
    ]
    for offset in range(0, len(handles), 500):
        chunk = handles[offset : offset + 500]
        marks = ", ".join("?" for _ in chunk)
        if connection.execute(
            f"""
            SELECT 1
            FROM portable_entity_handles
            WHERE external_handle IN ({marks})
            LIMIT 1
            """,
            chunk,
        ).fetchone() is not None:
            raise DataImportError(
                "Import external identities conflict with retained history"
            )


def _dry_run_validate(
    connection: sqlite3.Connection,
    records: Mapping[str, list[Mapping[str, Any]]],
) -> None:
    """Exercise live SQLite constraints and relationships, then roll back."""

    transaction_id = f"txn_validate_{secrets.token_urlsafe(18)}"
    timestamp = "1970-01-01T00:00:00Z"
    allocated = _allocate_ids(connection, records)
    connection.execute("SAVEPOINT portable_import_validation")
    try:
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO transactions (
                id, transaction_type, status, created_at, source_text
            ) VALUES (?, 'record_correction', 'pending', ?, ?)
            """,
            (transaction_id, timestamp, "portable import validation"),
        )
        for table in PORTABLE_TABLES:
            if table == "nutrition_goal_profiles":
                _import_goal(
                    connection,
                    records[table],
                    allocated,
                    transaction_id,
                )
                continue
            for record in records[table]:
                _insert_record(
                    connection,
                    table,
                    record,
                    allocated,
                    transaction_id,
                )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise DataImportError("Imported relationships are invalid")
    except BaseException as error:
        connection.execute("ROLLBACK TO portable_import_validation")
        connection.execute("RELEASE portable_import_validation")
        if isinstance(error, DataImportError):
            raise
        if isinstance(error, sqlite3.DatabaseError):
            raise DataImportError(
                "Import records violate the target data contract"
            ) from error
        raise
    else:
        connection.execute("ROLLBACK TO portable_import_validation")
        connection.execute("RELEASE portable_import_validation")


def _allocate_ids(
    connection: sqlite3.Connection,
    records: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, tuple[str, int]]:
    allocated: dict[str, tuple[str, int]] = {}
    for table in PORTABLE_TABLES:
        if table == "nutrition_goal_profiles":
            for record in records[table]:
                allocated[str(record["external_handle"])] = (table, 1)
            continue
        maximum = connection.execute(
            f"SELECT COALESCE(max(id), 0) FROM {table}"
        ).fetchone()[0]
        for offset, record in enumerate(records[table], start=1):
            allocated[str(record["external_handle"])] = (
                table,
                int(maximum) + offset,
            )
    return allocated


def _insert_record(
    connection: sqlite3.Connection,
    table: str,
    record: Mapping[str, Any],
    allocated: Mapping[str, tuple[str, int]],
    transaction_id: str,
) -> None:
    handle = str(record["external_handle"])
    new_id = allocated[handle][1]
    values = _database_values(table, record["values"], allocated)
    values["id"] = new_id
    values["transaction_id"] = transaction_id
    if table == "pantry_batches":
        values["batch_code"] = f"IMP-{secrets.token_hex(8).upper()}"
    columns = _table_columns(connection, table)
    if not set(values) <= columns:
        raise DataImportError("Import record contains unsupported fields")
    names = list(values)
    connection.execute(
        f"""
        INSERT INTO {table} ({", ".join(names)})
        VALUES ({", ".join("?" for _ in names)})
        """,
        tuple(values[name] for name in names),
    )
    connection.execute(
        """
        INSERT INTO portable_entity_handles (
            entity_kind, entity_key, external_handle, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            table,
            str(new_id),
            handle,
            _portable_created_at(record["values"]),
        ),
    )


def _import_goal(
    connection: sqlite3.Connection,
    records: list[Mapping[str, Any]],
    allocated: Mapping[str, tuple[str, int]],
    transaction_id: str,
) -> None:
    if len(records) > 1:
        raise DataImportError("Import contains multiple goal profiles")
    if not records:
        return
    record = records[0]
    values = _database_values(
        "nutrition_goal_profiles",
        record["values"],
        allocated,
    )
    values["transaction_id"] = transaction_id
    columns = _table_columns(connection, "nutrition_goal_profiles") - {"id"}
    if not set(values) <= columns:
        raise DataImportError("Import goal contains unsupported fields")
    assignments = ", ".join(f"{name} = ?" for name in values)
    connection.execute(
        f"UPDATE nutrition_goal_profiles SET {assignments} WHERE id = 1",
        tuple(values.values()),
    )
    handle = str(record["external_handle"])
    connection.execute(
        """
        INSERT INTO portable_entity_handles (
            entity_kind, entity_key, external_handle, created_at
        ) VALUES ('nutrition_goal_profiles', '1', ?, ?)
        """,
        (handle, _portable_created_at(record["values"])),
    )


def _database_values(
    table: str,
    raw_values: Mapping[str, Any],
    allocated: Mapping[str, tuple[str, int]],
) -> dict[str, Any]:
    relation_outputs = {
        output_name: (column, target_table)
        for (relation_table, column), (target_table, output_name) in _RELATIONS.items()
        if relation_table == table
    }
    values: dict[str, Any] = {}
    for name, value in raw_values.items():
        relation = relation_outputs.get(name)
        if relation is not None:
            column, expected_table = relation
            if value is None:
                values[column] = None
                continue
            target = allocated.get(str(value))
            if target is None or target[0] != expected_table:
                raise DataImportError("Import relationship type is invalid")
            values[column] = target[1]
            continue
        if (table, name) in _JSON_COLUMNS and value is not None:
            value = _canonical_json(value)
        values[name] = value
    return values


def _ensure_empty_target(connection: sqlite3.Connection) -> None:
    occupied = {
        table: int(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        for table in PORTABLE_TABLES
        if table != "nutrition_goal_profiles"
    }
    if any(occupied.values()):
        raise DataImportError("Import target already contains business data")
    goal = connection.execute(
        """
        SELECT goal_source, confirmed_at
        FROM nutrition_goal_profiles
        WHERE id = 1
        """
    ).fetchone()
    if (
        goal is None
        or goal["goal_source"] != "configuration_default"
        or goal["confirmed_at"] is not None
    ):
        raise DataImportError("Import target contains a confirmed goal profile")


def _safe_import_path(data_paths: DataPaths, name: str) -> Path:
    if not isinstance(name, str) or _IMPORT_NAME.fullmatch(name) is None:
        raise DataImportError("Import name is invalid")
    path = data_paths.imports / name
    validate_owned_path(data_paths, path)
    if path.parent.resolve() != data_paths.imports.resolve():
        raise DataImportError("Import name is invalid")
    return path


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DataImportError("Import JSON is invalid") from error


def _load_csv_archive(path: Path) -> Mapping[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_ARCHIVE_FILES:
                raise DataImportError("Import archive file count is unsafe")
            expanded = 0
            for info in infos:
                member = Path(info.filename)
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or info.file_size > MAX_ARCHIVE_FILE_BYTES
                ):
                    raise DataImportError("Import archive member is unsafe")
                expanded += info.file_size
            if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                raise DataImportError("Import archive expands beyond the safe limit")
            names = {info.filename for info in infos}
            expected = {"manifest.json"} | {
                f"{table}.csv" for table in PORTABLE_TABLES
            }
            if names != expected:
                raise DataImportError("Import archive members are incomplete")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, Mapping):
                raise DataImportError("Import manifest is invalid")
            checksums = manifest.get("files")
            if not isinstance(checksums, Mapping):
                raise DataImportError("Import file checksums are missing")
            records: dict[str, list[dict[str, Any]]] = {}
            for table in PORTABLE_TABLES:
                name = f"{table}.csv"
                contents = archive.read(name)
                if checksums.get(name) != hashlib.sha256(contents).hexdigest():
                    raise DataImportError("Import file checksum does not match")
                rows = csv.DictReader(
                    io.StringIO(contents.decode("utf-8-sig"))
                )
                if rows.fieldnames != ["external_handle", "values_json"]:
                    raise DataImportError("Import CSV header is invalid")
                records[table] = [
                    {
                        "external_handle": row["external_handle"],
                        "values": json.loads(row["values_json"]),
                    }
                    for row in rows
                ]
            manifest_without_files = dict(manifest)
            manifest_without_files.pop("files", None)
            return {
                "manifest": manifest_without_files,
                "records": records,
            }
    except DataImportError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as error:
        raise DataImportError("Import archive is invalid") from error


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _portable_created_at(values: Mapping[str, Any]) -> str:
    for name in ("created_at", "updated_at", "occurred_at", "measured_at"):
        value = values.get(name)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return _timestamp(parsed)
    return "1970-01-01T00:00:00Z"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
