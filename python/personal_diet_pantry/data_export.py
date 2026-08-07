"""Versioned, privacy-scanned JSON and CSV portability exports."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any
import zipfile

from .file_io import (
    atomic_write_bytes,
    atomic_write_text,
    durable_unlink,
    sha256_regular_file,
)
from .models import DataPaths
from .derived_file_leases import (
    DerivedFileLeaseManager,
    LeaseOwnerToken,
    manager_for,
)
from .paths import validate_owned_path
from . import privacy


EXPORT_SCHEMA_VERSION = 1
CONTRACT_VERSION = 1

PORTABLE_TABLES = (
    "nutrition_profiles",
    "meals",
    "meal_items",
    "meal_item_nutrition_evidence",
    "water_logs",
    "body_weight_logs",
    "personal_rules",
    "learning_events",
    "pantry_batches",
    "pantry_movements",
    "pantry_cost_allocations",
    "pantry_nutrition_links",
    "prepared_food_profiles",
    "pending_inventory_links",
    "recipe_profiles",
    "shopping_lists",
    "shopping_list_items",
    "nutrition_goal_profiles",
)

_JSON_COLUMNS = frozenset(
    {
        ("meals", "nutrition_missing_fields_json"),
        ("meal_item_nutrition_evidence", "input_facts_json"),
        ("meal_item_nutrition_evidence", "portion_evidence_json"),
        ("meal_item_nutrition_evidence", "warnings_json"),
        ("personal_rules", "rule_json"),
        ("learning_events", "evidence_json"),
        ("pending_inventory_links", "candidate_json"),
        ("nutrition_profiles", "nutrition_json"),
        ("pantry_nutrition_links", "nutrition_snapshot_json"),
        ("prepared_food_profiles", "nutrition_json"),
        ("recipe_profiles", "ingredients_json"),
    }
)

_RELATIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("meal_items", "meal_id"): ("meals", "meal_handle"),
    ("meal_items", "parent_item_id"): ("meal_items", "parent_item_handle"),
    ("meal_item_nutrition_evidence", "meal_item_id"): (
        "meal_items",
        "meal_item_handle",
    ),
    ("learning_events", "rule_id"): ("personal_rules", "rule_handle"),
    ("pantry_batches", "source_meal_id"): ("meals", "source_meal_handle"),
    ("pantry_movements", "pantry_batch_id"): (
        "pantry_batches",
        "pantry_batch_handle",
    ),
    ("pantry_movements", "linked_meal_id"): (
        "meals",
        "linked_meal_handle",
    ),
    ("pantry_movements", "linked_meal_item_id"): (
        "meal_items",
        "linked_meal_item_handle",
    ),
    ("pantry_cost_allocations", "pantry_batch_id"): (
        "pantry_batches",
        "pantry_batch_handle",
    ),
    ("pantry_cost_allocations", "pantry_movement_id"): (
        "pantry_movements",
        "pantry_movement_handle",
    ),
    ("pantry_nutrition_links", "pantry_batch_id"): (
        "pantry_batches",
        "pantry_batch_handle",
    ),
    ("pantry_nutrition_links", "nutrition_profile_id"): (
        "nutrition_profiles",
        "nutrition_profile_handle",
    ),
    ("prepared_food_profiles", "pantry_batch_id"): (
        "pantry_batches",
        "pantry_batch_handle",
    ),
    ("prepared_food_profiles", "source_meal_id"): (
        "meals",
        "source_meal_handle",
    ),
    ("pending_inventory_links", "meal_item_id"): (
        "meal_items",
        "meal_item_handle",
    ),
    ("shopping_list_items", "shopping_list_id"): (
        "shopping_lists",
        "shopping_list_handle",
    ),
}

_EXCLUDED_COLUMNS = frozenset(
    {
        "id",
        "transaction_id",
        "source_session_key",
        "source_session_hash",
        "source_model",
        "test_run_id",
        "intake_fingerprint",
        "batch_code",
    }
)


class DataExportError(RuntimeError):
    """Raised when an export cannot be safely produced."""


def export_data(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    *,
    export_format: str,
    product_version: str,
    timezone_name: str,
    now: datetime,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
) -> dict[str, Any]:
    manager = lease_manager or manager_for(data_paths)
    with manager.shared_publisher(owner=lease_owner):
        return _export_data_owned(
            connection,
            data_paths,
            export_format=export_format,
            product_version=product_version,
            timezone_name=timezone_name,
            now=now,
        )


def _export_data_owned(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    *,
    export_format: str,
    product_version: str,
    timezone_name: str,
    now: datetime,
) -> dict[str, Any]:
    if export_format not in {"json", "csv"}:
        raise ValueError("format must be json or csv")
    timestamp = _timestamp(now)
    suffix = ".json" if export_format == "json" else ".zip"
    name = (
        f"personal-diet-pantry-export-"
        f"{timestamp.replace('-', '').replace(':', '')}-"
        f"{secrets.token_hex(4)}{suffix}"
    )
    destination = data_paths.exports / name
    validate_owned_path(data_paths, destination)
    try:
        connection.execute("BEGIN IMMEDIATE")
        records, redactions = _portable_records(connection, now=now)
        manifest = _manifest(
            records,
            product_version=product_version,
            timezone_name=timezone_name,
            exported_at=timestamp,
            redactions=redactions,
        )
        bundle = {"manifest": manifest, "records": records}
        privacy.assert_portable_payload(bundle)
        if export_format == "json":
            atomic_write_text(
                destination,
                _canonical_json(bundle) + "\n",
                data_paths=data_paths,
            )
        else:
            _write_csv_archive(destination, manifest, records, data_paths)
        artifact_sha256 = sha256_regular_file(
            destination,
            data_paths=data_paths,
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        try:
            durable_unlink(destination, data_paths=data_paths)
        except FileNotFoundError:
            pass
        raise
    return {
        "name": name,
        "format": export_format,
        "sha256": artifact_sha256,
        "record_counts": manifest["record_counts"],
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "redacted_value_count": redactions,
    }


def _portable_records(
    connection: sqlite3.Connection,
    *,
    now: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    rows_by_table = {
        table: connection.execute(
            f"SELECT * FROM {table} ORDER BY id"
        ).fetchall()
        for table in PORTABLE_TABLES
    }
    handles: dict[tuple[str, int], str] = {}
    for table, rows in rows_by_table.items():
        for row in rows:
            key = int(row["id"])
            handles[(table, key)] = _entity_handle(
                connection,
                table,
                key,
                now=now,
            )

    redactions = 0
    output: dict[str, list[dict[str, Any]]] = {}
    for table, rows in rows_by_table.items():
        exported_rows: list[dict[str, Any]] = []
        for row in rows:
            values: dict[str, Any] = {}
            for column in row.keys():
                if column in _EXCLUDED_COLUMNS:
                    continue
                relation = _RELATIONS.get((table, column))
                if relation is not None:
                    target_table, output_name = relation
                    related_id = row[column]
                    values[output_name] = (
                        handles.get((target_table, int(related_id)))
                        if related_id is not None
                        else None
                    )
                    continue
                value = row[column]
                if (table, column) in _JSON_COLUMNS and value is not None:
                    try:
                        value = json.loads(str(value))
                    except json.JSONDecodeError as error:
                        raise DataExportError(
                            "Stored structured data is invalid"
                        ) from error
                value, count = privacy.scrub_export_value(value)
                redactions += count
                values[column] = value
            exported_rows.append(
                {
                    "external_handle": handles[(table, int(row["id"]))],
                    "values": values,
                }
            )
        output[table] = exported_rows
    return output, redactions


def _entity_handle(
    connection: sqlite3.Connection,
    table: str,
    key: int,
    *,
    now: datetime,
) -> str:
    entity_key = str(key)
    row = connection.execute(
        """
        SELECT external_handle, erased_at
        FROM portable_entity_handles
        WHERE entity_kind = ? AND entity_key = ?
        """,
        (table, entity_key),
    ).fetchone()
    if row is not None:
        if row["erased_at"] is not None:
            raise DataExportError("An erased external identity was reused")
        return str(row["external_handle"])
    handle = f"pdp_{table}_{secrets.token_urlsafe(18)}"
    connection.execute(
        """
        INSERT INTO portable_entity_handles (
            entity_kind, entity_key, external_handle, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (table, entity_key, handle, _timestamp(now)),
    )
    return handle


def _manifest(
    records: dict[str, list[dict[str, Any]]],
    *,
    product_version: str,
    timezone_name: str,
    exported_at: str,
    redactions: int,
) -> dict[str, Any]:
    records_text = _canonical_json(records)
    observed = _observed_time_range(records)
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "product_version": product_version,
        "exported_at": exported_at,
        "timezone": timezone_name,
        "time_range": observed,
        "record_counts": {
            table: len(rows) for table, rows in records.items()
        },
        "quality_summary": {
            "redacted_value_count": redactions,
            "unknown_price_batches": sum(
                1
                for record in records.get("pantry_batches", [])
                if record["values"].get("price_minor") is None
            ),
        },
        "records_sha256": hashlib.sha256(
            records_text.encode("utf-8")
        ).hexdigest(),
    }


def _observed_time_range(
    records: dict[str, list[dict[str, Any]]],
) -> dict[str, str | None]:
    values: list[str] = []
    for table, field in (
        ("meals", "occurred_at"),
        ("water_logs", "occurred_at"),
        ("body_weight_logs", "measured_at"),
    ):
        values.extend(
            str(record["values"][field])
            for record in records.get(table, [])
            if record["values"].get(field)
        )
    return {
        "earliest_utc": min(values) if values else None,
        "latest_utc": max(values) if values else None,
    }


def _write_csv_archive(
    destination: Path,
    manifest: dict[str, Any],
    records: dict[str, list[dict[str, Any]]],
    data_paths: DataPaths,
) -> None:
    files: dict[str, bytes] = {}
    for table, table_records in records.items():
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("external_handle", "values_json"))
        for record in table_records:
            writer.writerow(
                (
                    privacy.csv_safe(str(record["external_handle"])),
                    privacy.csv_safe(_canonical_json(record["values"])),
                )
            )
        files[f"{table}.csv"] = ("\ufeff" + stream.getvalue()).encode("utf-8")
    csv_manifest = dict(manifest)
    csv_manifest["files"] = {
        name: hashlib.sha256(contents).hexdigest()
        for name, contents in files.items()
    }
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(
        archive_bytes,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr("manifest.json", _canonical_json(csv_manifest) + "\n")
        for name, contents in files.items():
            archive.writestr(name, contents)
    atomic_write_bytes(
        destination,
        archive_bytes.getvalue(),
        data_paths=data_paths,
    )


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
