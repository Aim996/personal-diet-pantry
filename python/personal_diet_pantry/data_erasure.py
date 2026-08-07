"""Exact preview/commit privacy erasure with durable non-content tombstones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from . import privacy
from .data_export import PORTABLE_TABLES
from .models import DataPaths
from .workflow_lineage import workflow_keys_for_entities


ERASURE_SCOPES = frozenset(
    {
        "raw_source_text",
        "preferences",
        "intake_range",
        "business_facts_keep_config",
        "all_business",
    }
)

_SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "meals": ("source_text",),
    "water_logs": ("source_text",),
    "nutrition_profiles": ("source_text",),
    "recipe_profiles": ("source_text",),
    "shopping_lists": ("source_text",),
    "pantry_movements": ("reason",),
    "transactions": ("source_text",),
}

_DELETE_ORDER = (
    "pantry_cost_allocations",
    "pantry_nutrition_links",
    "prepared_food_profiles",
    "pending_inventory_links",
    "meal_item_nutrition_evidence",
    "pantry_movements",
    "shopping_list_items",
    "learning_events",
    "meal_items",
    "pantry_batches",
    "meals",
    "water_logs",
    "body_weight_logs",
    "nutrition_profiles",
    "nutrition_cache",
    "recipe_profiles",
    "shopping_lists",
    "personal_rules",
)


class DataErasureError(ValueError):
    """Raised when a deletion preview is stale or unsafe."""


class StaleErasurePreviewError(DataErasureError):
    """Raised when targets changed after preview."""


@dataclass(frozen=True)
class ResidueReport:
    count: int
    locations: tuple[str, ...]


@dataclass(frozen=True)
class ErasurePlan:
    scope: str
    date_start: str | None
    date_end: str | None
    start_utc: str | None
    end_utc: str | None
    affected_counts: Mapping[str, int]
    target_digest: str
    target_ids: Mapping[str, tuple[int, ...]]
    transaction_ids: tuple[str, ...]
    preview_token_hashes: tuple[str, ...]
    workflow_keys: tuple[tuple[str, str], ...]


def build_plan(
    connection: sqlite3.Connection,
    *,
    scope: str,
    date_start: str | None = None,
    date_end: str | None = None,
    start_utc: str | None = None,
    end_utc: str | None = None,
    excluded_preview_token_hash: str | None = None,
) -> ErasurePlan:
    if scope not in ERASURE_SCOPES:
        raise DataErasureError("Deletion scope is unsupported")
    if scope == "intake_range":
        if not all((date_start, date_end, start_utc, end_utc)):
            raise DataErasureError(
                "intake_range requires date_start and date_end"
            )
    elif any((date_start, date_end, start_utc, end_utc)):
        raise DataErasureError(
            "Date range is only accepted for intake_range deletion"
        )

    if scope == "raw_source_text":
        target_ids = _source_targets(connection)
    elif scope == "preferences":
        target_ids = {
            "learning_events": _ids(connection, "learning_events"),
            "personal_rules": _ids(connection, "personal_rules"),
        }
    elif scope == "intake_range":
        assert start_utc is not None and end_utc is not None
        target_ids = _intake_targets(connection, start_utc, end_utc)
    else:
        excluded = (
            {"personal_rules", "learning_events"}
            if scope == "business_facts_keep_config"
            else set()
        )
        target_ids = {
            table: _ids(connection, table)
            for table in _DELETE_ORDER
            if table not in excluded
        }
        if scope == "all_business":
            target_ids["nutrition_goal_profiles_reset"] = (1,)
    target_ids = {
        table: tuple(sorted(values))
        for table, values in target_ids.items()
        if values
    }
    counts = {table: len(values) for table, values in target_ids.items()}
    entities = tuple(
        (table, str(row_id))
        for table, row_ids in target_ids.items()
        if not table.endswith("_reset") and table != "operation_previews"
        for row_id in row_ids
    )
    workflow_keys = tuple(
        item
        for item in workflow_keys_for_entities(connection, entities)
        if not (
            item[0] == "preview"
            and item[1] == excluded_preview_token_hash
        )
    )
    transaction_ids = tuple(
        sorted(
            {
                *_transaction_ids_for_targets(connection, target_ids),
                *(
                    workflow_key
                    for workflow_kind, workflow_key in workflow_keys
                    if workflow_kind == "transaction"
                ),
            }
        )
    )
    preview_token_hashes: set[str] = {
        workflow_key
        for workflow_kind, workflow_key in workflow_keys
        if workflow_kind == "preview"
    }
    if scope == "all_business":
        preview_token_hashes.update(
            str(row[0])
            for row in connection.execute(
                "SELECT token_hash FROM operation_previews ORDER BY token_hash"
            )
            if str(row[0]) != excluded_preview_token_hash
        )
    elif "operation_previews" in target_ids:
        rowids = target_ids["operation_previews"]
        if rowids:
            marks = ", ".join("?" for _ in rowids)
            preview_token_hashes.update(
                str(row[0])
                for row in connection.execute(
                    f"SELECT token_hash FROM operation_previews WHERE rowid IN ({marks})",
                    rowids,
                )
                if str(row[0]) != excluded_preview_token_hash
            )
    preview_hashes = tuple(sorted(preview_token_hashes))
    digest_input = {
        "scope": scope,
        "date_start": date_start,
        "date_end": date_end,
        "targets": target_ids,
        "transactions": transaction_ids,
        "previews": preview_hashes,
        "workflow_keys": workflow_keys,
        "source_digest": (
            _source_content_digest(connection) if scope == "raw_source_text" else None
        ),
    }
    digest = hashlib.sha256(
        _canonical_json(digest_input).encode("utf-8")
    ).hexdigest()
    return ErasurePlan(
        scope=scope,
        date_start=date_start,
        date_end=date_end,
        start_utc=start_utc,
        end_utc=end_utc,
        affected_counts=counts,
        target_digest=digest,
        target_ids=target_ids,
        transaction_ids=transaction_ids,
        preview_token_hashes=preview_hashes,
        workflow_keys=workflow_keys,
    )


def commit_plan(
    connection: sqlite3.Connection,
    plan: ErasurePlan,
    *,
    expected_digest: str,
    preview_token_hash: str,
    control_operation_handle: str,
    now: datetime,
    goal_defaults: Mapping[str, Any],
    data_paths: DataPaths,
) -> dict[str, Any]:
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = build_plan(
            connection,
            scope=plan.scope,
            date_start=plan.date_start,
            date_end=plan.date_end,
            start_utc=plan.start_utc,
            end_utc=plan.end_utc,
            excluded_preview_token_hash=preview_token_hash,
        )
        if current.target_digest != expected_digest:
            raise StaleErasurePreviewError("Deletion preview is stale")
        summary = _apply_locked_plan(
            connection,
            current,
            preview_token_hash=preview_token_hash,
            control_operation_handle=control_operation_handle,
            now=now,
            goal_defaults=goal_defaults,
        )
        residue = verify_no_residue(
            connection,
            current,
            data_paths=data_paths,
            retained_preview_token_hash=preview_token_hash,
        )
        if residue.count:
            raise DataErasureError("Deletion residue verification failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {**summary, "residue_count": residue.count}


def _apply_locked_plan(
    connection: sqlite3.Connection,
    plan: ErasurePlan,
    *,
    preview_token_hash: str,
    control_operation_handle: str,
    now: datetime,
    goal_defaults: Mapping[str, Any],
) -> dict[str, Any]:
    timestamp = _timestamp(now)
    erasure_handle = f"erase_{secrets.token_urlsafe(24)}"
    if plan.scope == "raw_source_text":
        _redact_source_text(connection)
    elif plan.scope == "intake_range":
        _delete_intake_rows(connection, plan.target_ids)
    else:
        _delete_target_rows(connection, plan.target_ids)
        if plan.scope == "all_business":
            _reset_goal(connection, goal_defaults, timestamp)

    if plan.scope != "raw_source_text":
        _delete_handles(connection, plan.target_ids)
        _delete_lineage(connection, plan)
        _cleanup_transactions(connection, plan.transaction_ids)
    if plan.preview_token_hashes:
        marks = ", ".join("?" for _ in plan.preview_token_hashes)
        connection.execute(
            f"DELETE FROM operation_previews WHERE token_hash IN ({marks})",
            plan.preview_token_hashes,
        )
    if plan.scope == "all_business":
        connection.execute("DELETE FROM operation_receipts")
        connection.execute("DELETE FROM semantic_operation_receipts")
        connection.execute("DELETE FROM workflow_entity_links")
        connection.execute("DELETE FROM transactions")

    summary = {
        "scope": plan.scope,
        "date_start": plan.date_start,
        "date_end": plan.date_end,
        "affected_counts": dict(plan.affected_counts),
        "backups_deleted": False,
        "irreversible": True,
        "erasure_handle": erasure_handle,
        "effect_count": sum(plan.affected_counts.values()),
    }
    summary_sha256 = hashlib.sha256(
        _canonical_json(summary).encode("utf-8")
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO privacy_erasure_tombstones (
            erasure_handle, preview_token_hash, scope,
            affected_counts_json, summary_sha256, committed_at,
            control_operation_handle
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            erasure_handle,
            preview_token_hash,
            plan.scope,
            _canonical_json(dict(plan.affected_counts)),
            summary_sha256,
            timestamp,
            control_operation_handle,
        ),
    )
    stored = _canonical_json({"deletion": summary})
    changed = connection.execute(
        """
        UPDATE operation_previews
        SET request_json = ?, result_json = ?, resource_versions_json = '{}',
            consumed_at = ?
        WHERE token_hash = ? AND consumed_at IS NULL
        """,
        (stored, stored, timestamp, preview_token_hash),
    ).rowcount
    if changed != 1:
        raise StaleErasurePreviewError("Deletion preview is stale")
    return summary


def verify_no_residue(
    connection: sqlite3.Connection,
    plan: ErasurePlan,
    *,
    data_paths: DataPaths,
    retained_preview_token_hash: str,
) -> ResidueReport:
    locations: set[str] = set()
    if plan.scope != "raw_source_text":
        for table, ids in plan.target_ids.items():
            if table.endswith("_reset") or table == "operation_previews" or not ids:
                continue
            marks = ", ".join("?" for _ in ids)
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE id IN ({marks}) LIMIT 1", ids
            ).fetchone():
                locations.add("formal_rows")
            for row_id in ids:
                if connection.execute(
                    """
                    SELECT 1 FROM workflow_entity_links
                    WHERE entity_kind = ? AND entity_key = ? LIMIT 1
                    """,
                    (table, str(row_id)),
                ).fetchone():
                    locations.add("workflow_lineage")
    for transaction_id in plan.transaction_ids:
        row = connection.execute(
            """
            SELECT source_text, before_snapshot, after_snapshot
            FROM transactions WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if row is not None and (
            row["source_text"] != "[removed]"
            or row["before_snapshot"] not in (None, "[]")
            or row["after_snapshot"] not in (None, "[]")
        ):
            locations.add("transaction_snapshot")
    if plan.preview_token_hashes:
        marks = ", ".join("?" for _ in plan.preview_token_hashes)
        if connection.execute(
            f"SELECT 1 FROM operation_previews WHERE token_hash IN ({marks}) LIMIT 1",
            plan.preview_token_hashes,
        ).fetchone():
            locations.add("operation_preview")
    if plan.scope != "raw_source_text":
        for table, ids in plan.target_ids.items():
            if table not in PORTABLE_TABLES or not ids:
                continue
            marks = ", ".join("?" for _ in ids)
            if connection.execute(
                f"""
                SELECT 1 FROM portable_entity_handles
                WHERE entity_kind = ? AND entity_key IN ({marks}) LIMIT 1
                """,
                (table, *(str(value) for value in ids)),
            ).fetchone():
                locations.add("portable_handle")
    if plan.scope == "all_business":
        if any(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _DELETE_ORDER
        ):
            locations.add("formal_rows")
        if connection.execute("SELECT count(*) FROM transactions").fetchone()[0]:
            locations.add("derived_snapshot")
        if connection.execute(
            "SELECT count(*) FROM operation_previews WHERE token_hash <> ?",
            (retained_preview_token_hash,),
        ).fetchone()[0]:
            locations.add("operation_preview")
        if _derived_regular_file_count(data_paths) != 0:
            locations.add("derived_files")
    stable = tuple(sorted(locations))
    return ResidueReport(count=len(stable), locations=stable)


def _derived_regular_file_count(data_paths: DataPaths) -> int:
    count = 0
    for root in (data_paths.cache, data_paths.exports, data_paths.reports):
        directory = Path(root)
        if directory.is_symlink() or not directory.is_dir():
            return count + 1
        try:
            entries = tuple(directory.rglob("*"))
        except OSError:
            return count + 1
        for entry in entries:
            try:
                details = entry.lstat()
            except OSError:
                return count + 1
            if entry.is_symlink() or bool(
                getattr(details, "st_file_attributes", 0) & 0x400
            ):
                count += 1
            elif entry.is_file():
                count += 1
    return count


def _delete_lineage(connection: sqlite3.Connection, plan: ErasurePlan) -> None:
    for workflow_kind, workflow_key in plan.workflow_keys:
        connection.execute(
            "DELETE FROM workflow_entity_links WHERE workflow_kind = ? AND workflow_key = ?",
            (workflow_kind, workflow_key),
        )
    for table, ids in plan.target_ids.items():
        for row_id in ids:
            connection.execute(
                "DELETE FROM workflow_entity_links WHERE entity_kind = ? AND entity_key = ?",
                (table, str(row_id)),
            )


def replayed_result(
    connection: sqlite3.Connection,
    preview_token_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT erasure_handle, scope, affected_counts_json, committed_at
        FROM privacy_erasure_tombstones
        WHERE preview_token_hash = ?
        """,
        (preview_token_hash,),
    ).fetchone()
    if row is None:
        return None
    return {
        "scope": str(row["scope"]),
        "date_start": None,
        "date_end": None,
        "affected_counts": json.loads(str(row["affected_counts_json"])),
        "backups_deleted": False,
        "irreversible": True,
        "erasure_handle": str(row["erasure_handle"]),
    }


def _source_targets(
    connection: sqlite3.Connection,
) -> dict[str, tuple[int, ...]]:
    targets: dict[str, tuple[int, ...]] = {}
    for table, columns in _SOURCE_COLUMNS.items():
        if table == "transactions":
            rows = connection.execute(
                """
                SELECT rowid
                FROM transactions
                WHERE source_text <> '[removed]'
                   OR before_snapshot LIKE '%source_text%'
                   OR after_snapshot LIKE '%source_text%'
                """
            ).fetchall()
            targets[table] = tuple(int(row[0]) for row in rows)
            continue
        predicates = " OR ".join(
            f"({column} IS NOT NULL AND {column} <> '[removed]')"
            for column in columns
        )
        targets[table] = _ids(connection, table, where=predicates)
    preview_ids: list[int] = []
    preview_rows = connection.execute(
        """
        SELECT rowid, request_json, result_json, resource_versions_json
        FROM operation_previews
        ORDER BY rowid
        """
    ).fetchall()
    for row in preview_rows:
        changed = False
        for column in (
            "request_json",
            "result_json",
            "resource_versions_json",
        ):
            try:
                parsed = json.loads(str(row[column]))
            except json.JSONDecodeError:
                changed = True
                break
            if _redact_json(parsed) != parsed:
                changed = True
                break
        if changed:
            preview_ids.append(int(row["rowid"]))
    targets["operation_previews"] = tuple(preview_ids)
    return targets


def _intake_targets(
    connection: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
) -> dict[str, tuple[int, ...]]:
    meals = _ids(
        connection,
        "meals",
        where="occurred_at >= ? AND occurred_at < ?",
        values=(start_utc, end_utc),
    )
    water = _ids(
        connection,
        "water_logs",
        where="occurred_at >= ? AND occurred_at < ?",
        values=(start_utc, end_utc),
    )
    weights = _ids(
        connection,
        "body_weight_logs",
        where="measured_at >= ? AND measured_at < ?",
        values=(start_utc, end_utc),
    )
    if meals:
        marks = ", ".join("?" for _ in meals)
        items = _ids(
            connection,
            "meal_items",
            where=f"meal_id IN ({marks})",
            values=meals,
        )
    else:
        items = ()
    if items:
        marks = ", ".join("?" for _ in items)
        evidence = _ids(
            connection,
            "meal_item_nutrition_evidence",
            where=f"meal_item_id IN ({marks})",
            values=items,
        )
        pending = _ids(
            connection,
            "pending_inventory_links",
            where=f"meal_item_id IN ({marks})",
            values=items,
        )
    else:
        evidence = ()
        pending = ()
    prepared = ()
    if meals:
        marks = ", ".join("?" for _ in meals)
        prepared = _ids(
            connection,
            "prepared_food_profiles",
            where=f"source_meal_id IN ({marks})",
            values=meals,
        )
    return {
        "meal_item_nutrition_evidence": evidence,
        "pending_inventory_links": pending,
        "prepared_food_profiles": prepared,
        "meal_items": items,
        "meals": meals,
        "water_logs": water,
        "body_weight_logs": weights,
    }


def _delete_intake_rows(
    connection: sqlite3.Connection,
    targets: Mapping[str, tuple[int, ...]],
) -> None:
    meal_ids = targets.get("meals", ())
    item_ids = targets.get("meal_items", ())
    if meal_ids:
        marks = ", ".join("?" for _ in meal_ids)
        connection.execute(
            f"""
            UPDATE pantry_batches
            SET source_meal_id = NULL
            WHERE source_meal_id IN ({marks})
            """,
            meal_ids,
        )
        connection.execute(
            f"""
            UPDATE pantry_movements
            SET linked_meal_id = NULL
            WHERE linked_meal_id IN ({marks})
            """,
            meal_ids,
        )
    if item_ids:
        marks = ", ".join("?" for _ in item_ids)
        connection.execute(
            f"""
            UPDATE pantry_movements
            SET linked_meal_item_id = NULL
            WHERE linked_meal_item_id IN ({marks})
            """,
            item_ids,
        )
    _delete_target_rows(connection, targets)


def _delete_target_rows(
    connection: sqlite3.Connection,
    targets: Mapping[str, tuple[int, ...]],
) -> None:
    for table in _DELETE_ORDER:
        ids = targets.get(table, ())
        if not ids:
            continue
        marks = ", ".join("?" for _ in ids)
        if table == "meal_items":
            connection.execute(
                f"""
                DELETE FROM meal_items
                WHERE id IN ({marks}) AND parent_item_id IS NOT NULL
                """,
                ids,
            )
        connection.execute(
            f"DELETE FROM {table} WHERE id IN ({marks})",
            ids,
        )


def _redact_source_text(connection: sqlite3.Connection) -> None:
    for table, columns in _SOURCE_COLUMNS.items():
        if table == "transactions":
            continue
        for column in columns:
            connection.execute(
                f"""
                UPDATE {table}
                SET {column} = '[removed]'
                WHERE {column} IS NOT NULL AND {column} <> '[removed]'
                """
            )
    transaction_rows = connection.execute(
        """
        SELECT id, before_snapshot, after_snapshot
        FROM transactions
        """
    ).fetchall()
    for row in transaction_rows:
        connection.execute(
            """
            UPDATE transactions
            SET source_text = '[removed]',
                before_snapshot = '[]',
                after_snapshot = '[]',
                undo_policy = 'none',
                effect_count = 0
            WHERE id = ?
            """,
            (row["id"],),
        )
    preview_rows = connection.execute(
        """
        SELECT token_hash, request_json, result_json, resource_versions_json
        FROM operation_previews
        """
    ).fetchall()
    for row in preview_rows:
        connection.execute(
            """
            UPDATE operation_previews
            SET request_json = ?, result_json = ?, resource_versions_json = ?
            WHERE token_hash = ?
            """,
            (
                _redacted_json_text(row["request_json"]) or "{}",
                _redacted_json_text(row["result_json"]) or "{}",
                _redacted_json_text(row["resource_versions_json"]) or "{}",
                row["token_hash"],
            ),
        )


def _redacted_json_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return "[]"
    return _canonical_json(_redact_json(parsed))


def _redact_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            compact = key.lower().replace("-", "_")
            if compact in {
                "source_text",
                "source_session_key",
                "source_session_hash",
                "source_model",
                "test_run_id",
            }:
                output[key] = "[removed]"
            else:
                output[key] = _redact_json(child)
        return output
    if isinstance(value, list):
        return [_redact_json(child) for child in value]
    scrubbed, _count = privacy.scrub_export_value(value)
    return scrubbed


def _reset_goal(
    connection: sqlite3.Connection,
    defaults: Mapping[str, Any],
    timestamp: str,
) -> None:
    allowed = {
        "calories_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "fiber_g",
        "sodium_mg",
        "water_ml",
        "timezone_name",
    }
    if set(defaults) != allowed:
        raise DataErasureError("Goal reset defaults are incomplete")
    assignments = ", ".join(f"{name} = ?" for name in defaults)
    connection.execute(
        f"""
        UPDATE nutrition_goal_profiles
        SET {assignments},
            updated_at = ?,
            transaction_id = NULL,
            goal_source = 'configuration_default',
            confirmed_at = NULL
        WHERE id = 1
        """,
        (*defaults.values(), timestamp),
    )


def _delete_handles(
    connection: sqlite3.Connection,
    targets: Mapping[str, tuple[int, ...]],
) -> None:
    for table, ids in targets.items():
        if table not in PORTABLE_TABLES or not ids:
            continue
        marks = ", ".join("?" for _ in ids)
        connection.execute(
            f"""
            DELETE FROM portable_entity_handles
            WHERE entity_kind = ?
              AND entity_key IN ({marks})
            """,
            (table, *(str(value) for value in ids)),
        )


def _cleanup_transactions(
    connection: sqlite3.Connection,
    transaction_ids: tuple[str, ...],
) -> None:
    if not transaction_ids:
        return
    referenced = _referenced_transactions(connection)
    for transaction_id in transaction_ids:
        if transaction_id in referenced:
            connection.execute(
                """
                UPDATE transactions
                SET source_text = '[removed]',
                    before_snapshot = '[]',
                    after_snapshot = '[]',
                    undo_policy = 'none',
                    effect_count = 0,
                    error_message = NULL
                WHERE id = ?
                """,
                (transaction_id,),
            )
            continue
        connection.execute(
            "DELETE FROM operation_receipts WHERE transaction_id = ?",
            (transaction_id,),
        )
        connection.execute(
            "DELETE FROM semantic_operation_receipts WHERE transaction_id = ?",
            (transaction_id,),
        )
        connection.execute(
            "DELETE FROM transactions WHERE id = ?",
            (transaction_id,),
        )


def _referenced_transactions(connection: sqlite3.Connection) -> set[str]:
    referenced: set[str] = set()
    tables = set(PORTABLE_TABLES) | {"nutrition_cache"}
    for table in tables:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "transaction_id" not in columns:
            continue
        referenced.update(
            str(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT transaction_id
                FROM {table}
                WHERE transaction_id IS NOT NULL
                """
            )
        )
    return referenced


def _transaction_ids_for_targets(
    connection: sqlite3.Connection,
    targets: Mapping[str, tuple[int, ...]],
) -> tuple[str, ...]:
    values: set[str] = set()
    for table, ids in targets.items():
        if table == "nutrition_goal_profiles_reset" and ids:
            row = connection.execute(
                """
                SELECT transaction_id
                FROM nutrition_goal_profiles
                WHERE id = 1 AND transaction_id IS NOT NULL
                """
            ).fetchone()
            if row is not None:
                values.add(str(row[0]))
            continue
        if table.endswith("_reset") or not ids:
            continue
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "transaction_id" not in columns or "id" not in columns:
            continue
        marks = ", ".join("?" for _ in ids)
        values.update(
            str(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT transaction_id
                FROM {table}
                WHERE id IN ({marks}) AND transaction_id IS NOT NULL
                """,
                ids,
            )
        )
    return tuple(sorted(values))


def _source_content_digest(connection: sqlite3.Connection) -> str:
    values: list[Any] = []
    for table, columns in _SOURCE_COLUMNS.items():
        select = ", ".join(columns)
        values.extend(
            [table, *tuple(row) ]
            for row in connection.execute(
                f"SELECT {select} FROM {table} ORDER BY rowid"
            )
        )
    values.extend(
        [
            "operation_previews",
            row["token_hash"],
            row["request_json"],
            row["result_json"],
            row["resource_versions_json"],
        ]
        for row in connection.execute(
            """
            SELECT token_hash, request_json, result_json, resource_versions_json
            FROM operation_previews
            ORDER BY token_hash
            """
        )
        if any(
            _redact_json(json.loads(str(row[column])))
            != json.loads(str(row[column]))
            for column in (
                "request_json",
                "result_json",
                "resource_versions_json",
            )
        )
    )
    return hashlib.sha256(
        _canonical_json(values).encode("utf-8")
    ).hexdigest()


def _ids(
    connection: sqlite3.Connection,
    table: str,
    *,
    where: str | None = None,
    values: tuple[Any, ...] = (),
) -> tuple[int, ...]:
    suffix = f" WHERE {where}" if where else ""
    return tuple(
        int(row[0])
        for row in connection.execute(
            f"SELECT id FROM {table}{suffix} ORDER BY id",
            values,
        )
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
