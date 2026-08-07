"""Trusted workflow seam for safety-sensitive maintenance operations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import secrets
import sqlite3
import stat
from typing import Any, Callable, Iterator, Literal, Mapping

from . import data_erasure, data_import, maintenance_control, self_check
from .clock import Clock
from .derived_file_leases import manager_for
from .file_io import (
    durable_mkdir,
    durable_replace,
    durable_rmdir,
    durable_unlink,
    sha256_regular_file,
)
from .maintenance_control import MaintenanceController
from .models import DataPaths
from .paths import validate_owned_path
from .workflow_lineage import EntityLink, index_preview_links


_HANDLE_PREFIX = "wfh_"


def _crash_probe(checkpoint: str) -> None:
    """Process-crash injection seam; production deliberately does nothing."""

    del checkpoint


@dataclass(frozen=True)
class _QuarantinedArtifact:
    domain: Literal["cache", "exports", "reports"]
    item_key: str
    normalized_relative_path: str
    content_sha256: str
    original: Path
    staged: Path


def _quarantine_item_key(
    domain: Literal["cache", "exports", "reports"],
    normalized_relative_path: str,
) -> str:
    return hashlib.sha256(
        f"{domain}\0{normalized_relative_path}".encode("utf-8")
    ).hexdigest()


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class WorkflowStaleError(ValueError):
    """Raised when a trusted workflow handle is missing, expired, or consumed."""


class DerivedFilesChangedError(data_erasure.DataErasureError):
    """Raised when generated artifacts change during locked erasure."""


class ErasureVerificationRequired(data_erasure.DataErasureError):
    """Raised when durable evidence cannot prove a safe terminal result."""


@dataclass(frozen=True)
class DeleteDataCommand:
    scope: str
    date_start: date | None = None
    date_end: date | None = None
    start_utc: str | None = None
    end_utc: str | None = None


@dataclass(frozen=True)
class ImportDataCommand:
    import_name: str


@dataclass(frozen=True)
class RestoreCommand:
    backup_id: str


WorkflowCommand = DeleteDataCommand | ImportDataCommand | RestoreCommand


@dataclass(frozen=True)
class RequestContext:
    now: datetime
    operation_key: str | None = None


@dataclass(frozen=True)
class Confirmation:
    confirmed: bool
    operation_key: str | None = None


@dataclass(frozen=True)
class WorkflowPreview:
    workflow_handle: str
    command: str
    summary: Mapping[str, Any]
    expires_at: str


@dataclass(frozen=True)
class OperationReceipt:
    command: str
    effect_count: int
    undo_policy: Literal["snapshot", "none"]
    result: Mapping[str, Any]


@dataclass(frozen=True)
class RecoveryReport:
    inspected: int
    committed: int
    failed: int
    verification_required: int


@dataclass(frozen=True)
class ListBackupsQuery:
    limit: int = 50


@dataclass(frozen=True)
class SelfCheckQuery:
    now: datetime | None = None


TrustQuery = ListBackupsQuery | SelfCheckQuery


@dataclass(frozen=True)
class TrustReport:
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class TrustedWorkflowDependencies:
    connection: Callable[[], sqlite3.Connection]
    replace_connection: Callable[[sqlite3.Connection], None]
    data_paths: DataPaths
    migrations_dir: Path
    source_root: Path
    controller: MaintenanceController
    clock: Clock
    preview_ttl: timedelta


class TrustedWorkflowModule:
    def __init__(self, dependencies: TrustedWorkflowDependencies) -> None:
        self._dependencies = dependencies

    def _crash_probe(self, checkpoint: str) -> None:
        _crash_probe(checkpoint)

    def preview(
        self,
        command: WorkflowCommand,
        request_context: RequestContext,
    ) -> WorkflowPreview:
        if isinstance(command, ImportDataCommand):
            return self._preview_import(command, request_context)
        if isinstance(command, DeleteDataCommand):
            return self._preview_delete(command, request_context)
        raise ValueError("trusted workflow preview is unsupported")

    def commit(
        self,
        workflow_handle: str,
        confirmation: Confirmation,
    ) -> OperationReceipt:
        now = self._dependencies.clock()
        row = self._workflow_row(
            workflow_handle,
            "delete_data_preview",
            now=now,
            allow_consumed=True,
            required=False,
        )
        if row is not None:
            return self._commit_delete(workflow_handle, confirmation)
        return self._commit_import(workflow_handle, confirmation)

    def recover_startup(self) -> RecoveryReport:
        decisions = self._reconcile_erasure_quarantines()
        return RecoveryReport(
            inspected=len(decisions),
            committed=sum(value == "committed" for value in decisions.values()),
            failed=sum(value == "failed" for value in decisions.values()),
            verification_required=sum(
                value == "verification_required"
                for value in decisions.values()
            ),
        )

    def _reconcile_erasure_quarantines(self) -> Mapping[int, str]:
        control = self._dependencies.controller.connection
        operations = control.execute(
            """
            SELECT id, operation_handle, status
            FROM maintenance_operations AS operation
            WHERE action = 'commit_delete_data'
              AND (
                    status IN ('accepted', 'running', 'interrupted', 'reconciling')
                    OR EXISTS (
                        SELECT 1 FROM maintenance_quarantine_items AS item
                        WHERE item.operation_id = operation.id
                          AND item.state IN ('planned', 'staged', 'purge_pending')
                    )
                    OR operation.reconciliation_decision = 'verification_required'
              )
            ORDER BY id
            """
        ).fetchall()
        if not operations:
            return {}
        decisions: dict[int, str] = {}
        manager = manager_for(self._dependencies.data_paths)
        with manager.exclusive_erasure():
            for operation in operations:
                operation_id = int(operation["id"])
                handle = str(operation["operation_handle"])
                try:
                    decision = self._reconcile_one_erasure(operation_id, handle)
                except Exception as error:
                    self._record_check(
                        operation_id,
                        stage_code="recovery",
                        check_code="erasure_recovery_failure",
                        expected={"outcome": "deterministic_terminal"},
                        observed={"error_type": type(error).__name__},
                        outcome="fail",
                    )
                    self._terminal_reconciliation(
                        operation_id,
                        handle,
                        "verification_required",
                    )
                    decision = "verification_required"
                decisions[operation_id] = decision
        return decisions

    def _reconcile_one_erasure(
        self,
        operation_id: int,
        control_operation_handle: str,
    ) -> str:
        control = self._dependencies.controller.connection
        manifest = control.execute(
            """
            SELECT outcome, expected_json, observed_json
            FROM maintenance_checks
            WHERE operation_id = ?
              AND stage_code = 'manifest'
              AND check_code = 'erasure_manifest'
            """,
            (operation_id,),
        ).fetchone()
        if manifest is None:
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"
        try:
            binding = json.loads(str(manifest["expected_json"]))
            observed = json.loads(str(manifest["observed_json"]))
            preview_hash = binding["preview_token_hash"]
            target_digest = binding["target_digest"]
            file_count = observed["file_count"]
            if (
                manifest["outcome"] != "pass"
                or not _is_lower_sha256(preview_hash)
                or not _is_lower_sha256(target_digest)
                or not isinstance(file_count, int)
                or isinstance(file_count, bool)
                or file_count < 0
            ):
                raise ValueError("invalid erasure manifest binding")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"
        tombstone = self._exact_erasure_tombstone(
            control_operation_handle,
            preview_hash,
        )
        if tombstone == "ambiguous":
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"

        rows = control.execute(
            """
            SELECT item_key, original_relative_name, staged_relative_name,
                   sha256, state
            FROM maintenance_quarantine_items
            WHERE operation_id = ?
            ORDER BY item_key
            """,
            (operation_id,),
        ).fetchall()
        if len(rows) != file_count:
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"
        actions: list[tuple[str, sqlite3.Row, Path, Path, str]] = []
        ambiguous = False
        for row in rows:
            try:
                original, staged = self._recovery_paths(row, preview_hash)
                expected_sha256 = str(row["sha256"])
                if not _is_lower_sha256(expected_sha256):
                    raise ValueError("invalid manifest content hash")
                action = self._recovery_action(
                    tombstone=tombstone,
                    state=str(row["state"]),
                    original=original,
                    staged=staged,
                    expected_sha256=expected_sha256,
                )
            except (OSError, ValueError, data_erasure.DataErasureError):
                ambiguous = True
                break
            if action == "ambiguous":
                ambiguous = True
                break
            actions.append(
                (action, row, original, staged, expected_sha256)
            )
        if ambiguous:
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"

        for action, row, original, staged, expected_sha256 in actions:
            item_key = str(row["item_key"])
            if action == "restore":
                durable_replace(
                    staged,
                    original,
                    data_paths=self._dependencies.data_paths,
                    expected_sha256=expected_sha256,
                )
                self._set_item_state(
                    operation_id, item_key, "restored", resolved=True
                )
            elif action == "mark_restored":
                self._set_item_state(
                    operation_id, item_key, "restored", resolved=True
                )
            elif action == "purge":
                self._set_item_state(operation_id, item_key, "purge_pending")
                durable_unlink(staged, data_paths=self._dependencies.data_paths)
                self._set_item_state(
                    operation_id, item_key, "purged", resolved=True
                )
            elif action == "mark_purged":
                self._set_item_state(
                    operation_id, item_key, "purged", resolved=True
                )

        operation_dir = (
            self._dependencies.data_paths.control
            / "erasure-quarantine"
            / preview_hash
        )
        decision = "committed" if tombstone == "committed" else "failed"
        terminal_state = "purged" if tombstone == "committed" else "restored"
        terminal_ok, expected, observed = self._verify_quarantine_terminal(
            operation_id,
            preview_hash=preview_hash,
            terminal_state=terminal_state,
        )
        if not terminal_ok:
            self._record_check(
                operation_id,
                stage_code="terminal",
                check_code="erasure_quarantine_terminal",
                expected=expected,
                observed=observed,
                outcome="fail",
            )
            self._terminal_reconciliation(
                operation_id,
                control_operation_handle,
                "verification_required",
            )
            return "verification_required"
        try:
            durable_rmdir(
                operation_dir,
                data_paths=self._dependencies.data_paths,
            )
        except FileNotFoundError:
            pass
        self._record_check(
            operation_id,
            stage_code="terminal",
            check_code="erasure_quarantine_terminal",
            expected=expected,
            observed=observed,
            outcome="pass",
        )
        self._terminal_reconciliation(
            operation_id,
            control_operation_handle,
            decision,
        )
        return decision

    def _exact_erasure_tombstone(
        self,
        control_operation_handle: str,
        preview_token_hash: str,
    ) -> str:
        rows = self._dependencies.connection().execute(
            """
            SELECT erasure_handle, scope, preview_token_hash,
                   summary_sha256, committed_at
            FROM privacy_erasure_tombstones
            WHERE control_operation_handle = ?
            """,
            (control_operation_handle,),
        ).fetchall()
        if not rows:
            return "absent"
        if len(rows) != 1:
            return "ambiguous"
        row = rows[0]
        if (
            row["scope"] != "all_business"
            or row["preview_token_hash"] != preview_token_hash
        ):
            return "ambiguous"
        preview = self._dependencies.connection().execute(
            "SELECT result_json FROM operation_previews WHERE token_hash = ?",
            (preview_token_hash,),
        ).fetchone()
        if preview is None:
            return "ambiguous"
        try:
            stored = json.loads(str(preview["result_json"]))
            summary = stored["deletion"]
            observed = hashlib.sha256(
                _canonical_json(summary).encode("utf-8")
            ).hexdigest()
        except (json.JSONDecodeError, KeyError, TypeError):
            return "ambiguous"
        return (
            "committed"
            if observed == str(row["summary_sha256"])
            else "ambiguous"
        )

    def _recovery_paths(
        self,
        row: sqlite3.Row,
        preview_hash: str,
    ) -> tuple[Path, Path]:
        data_paths = self._dependencies.data_paths
        original_value = str(row["original_relative_name"])
        staged_value = str(row["staged_relative_name"])
        original_name = PurePosixPath(original_value)
        staged_name = PurePosixPath(staged_value)
        item_key = str(row["item_key"])
        if (
            original_name.is_absolute()
            or staged_name.is_absolute()
            or ".." in original_name.parts
            or ".." in staged_name.parts
            or len(original_name.parts) < 2
            or original_name.parts[0] not in {"cache", "exports", "reports"}
            or original_name.as_posix() != original_value
            or staged_name.as_posix() != staged_value
        ):
            raise data_erasure.DataErasureError(
                "Quarantine manifest path is invalid"
            )
        domain = original_name.parts[0]
        normalized = PurePosixPath(*original_name.parts[1:]).as_posix()
        expected_key = _quarantine_item_key(domain, normalized)
        expected_staged = (
            "control",
            "erasure-quarantine",
            preview_hash,
            f"{expected_key}.bin",
        )
        if item_key != expected_key or staged_name.parts != expected_staged:
            raise data_erasure.DataErasureError(
                "Quarantine manifest binding is invalid"
            )
        original = data_paths.root.joinpath(*original_name.parts)
        staged = data_paths.root.joinpath(*staged_name.parts)
        validate_owned_path(data_paths, original)
        validate_owned_path(data_paths, staged)
        return original, staged

    def _recovery_action(
        self,
        *,
        tombstone: str,
        state: str,
        original: Path,
        staged: Path,
        expected_sha256: str,
    ) -> str:
        original_hash = _optional_file_sha256(
            original, self._dependencies.data_paths
        )
        staged_hash = _optional_file_sha256(
            staged, self._dependencies.data_paths
        )
        original_exists = original_hash is not None
        staged_exists = staged_hash is not None
        if original_exists and staged_exists:
            return "ambiguous"
        original_ok = original_hash == expected_sha256
        staged_ok = staged_hash == expected_sha256
        if tombstone == "absent":
            if state in {"planned", "staged"}:
                if original_ok and not staged_exists:
                    return "mark_restored"
                if staged_ok and not original_exists:
                    return "restore"
            if state == "restored" and original_ok and not staged_exists:
                return "keep"
            return "ambiguous"
        if tombstone == "committed":
            if state in {"staged", "purge_pending"} and staged_ok and not original_exists:
                return "purge"
            if state == "purge_pending" and not original_exists and not staged_exists:
                return "mark_purged"
            if state == "purged" and not original_exists and not staged_exists:
                return "keep"
        return "ambiguous"

    def _terminal_reconciliation(
        self,
        operation_id: int,
        handle: str,
        decision: str,
    ) -> None:
        control = self._dependencies.controller.connection
        status = "committed" if decision == "committed" else "failed"
        timestamp = _workflow_timestamp(self._dependencies.clock())
        with _control_transaction(control):
            current = control.execute(
                "SELECT status FROM maintenance_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
            if current is None:
                return
            control.execute(
                """
                UPDATE maintenance_operations
                SET status = ?, finished_at = COALESCE(finished_at, ?),
                    reconciliation_decision = ?,
                    exclusive_released_at = COALESCE(exclusive_released_at, ?),
                    result_json = CASE
                        WHEN ? = 'committed' THEN '{"reconciled":true}'
                        ELSE result_json
                    END,
                    error_code = CASE
                        WHEN ? = 'committed' THEN error_code
                        WHEN ? = 'verification_required' THEN 'VERIFICATION_REQUIRED'
                        ELSE 'INTERRUPTED'
                    END
                WHERE id = ?
                """,
                (
                    status,
                    timestamp,
                    decision,
                    timestamp,
                    decision,
                    decision,
                    decision,
                    operation_id,
                ),
            )
            control.execute(
                """
                INSERT INTO maintenance_events (
                    operation_id, from_status, to_status, occurred_at, reason_code
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    str(current["status"]),
                    status,
                    timestamp,
                    f"ERASURE_{decision.upper()}",
                ),
            )

    def inspect(self, query: TrustQuery) -> TrustReport:
        if not isinstance(query, SelfCheckQuery):
            raise ValueError("trusted workflow inspection is unsupported")
        checks = self_check.run_self_check(
            self._dependencies.connection(),
            self._dependencies.data_paths,
            self._dependencies.migrations_dir,
            source_root=self._dependencies.source_root,
            now=query.now,
        )
        control_checks = self._dependencies.controller.validate()
        return TrustReport(
            kind="self_check",
            payload={"checks": (*checks, *control_checks)},
        )

    def _preview_delete(
        self,
        command: DeleteDataCommand,
        request_context: RequestContext,
    ) -> WorkflowPreview:
        connection = self._dependencies.connection()
        plan = data_erasure.build_plan(
            connection,
            scope=command.scope,
            date_start=(
                command.date_start.isoformat()
                if command.date_start is not None
                else None
            ),
            date_end=(
                command.date_end.isoformat()
                if command.date_end is not None
                else None
            ),
            start_utc=command.start_utc,
            end_utc=command.end_utc,
        )
        preview = {
            "scope": plan.scope,
            "date_start": plan.date_start,
            "date_end": plan.date_end,
            "affected_counts": dict(plan.affected_counts),
            "backups_deleted": False,
            "irreversible": True,
        }
        handle, expires_at = self._issue_workflow(
            "delete_data_preview",
            request={
                "scope": plan.scope,
                "date_start": plan.date_start,
                "date_end": plan.date_end,
                "start_utc": plan.start_utc,
                "end_utc": plan.end_utc,
                "target_digest": plan.target_digest,
            },
            result={"preview": preview},
            resource_versions={"target_digest": plan.target_digest},
            now=request_context.now,
            entity_links=tuple(
                EntityLink(table, str(row_id), "request")
                for table, ids in plan.target_ids.items()
                if not table.endswith("_reset")
                for row_id in ids
            ),
        )
        return WorkflowPreview(
            workflow_handle=handle,
            command="delete_data",
            summary={
                "preview": preview,
                "requires_confirmation": True,
            },
            expires_at=expires_at,
        )

    def _commit_delete(
        self,
        workflow_handle: str,
        confirmation: Confirmation,
    ) -> OperationReceipt:
        if confirmation.confirmed is not True:
            raise data_erasure.DataErasureError(
                "Deletion requires explicit confirmation"
            )
        parameters = {
            "commit_handle": workflow_handle,
            "confirmed": confirmation.confirmed,
        }
        result = self._run_exclusive_maintenance(
            "commit_delete_data",
            parameters,
            operation_key=confirmation.operation_key,
            work=lambda record: self._commit_delete_preview(
                workflow_handle,
                record.handle,
            ),
        )
        deletion = result.get("deletion")
        effect_count = (
            int(deletion.get("effect_count", 0))
            if isinstance(deletion, Mapping)
            else 0
        )
        return OperationReceipt(
            command="delete_data",
            effect_count=effect_count,
            undo_policy="none",
            result=result,
        )

    def _commit_delete_preview(
        self,
        workflow_handle: str,
        control_operation_handle: str,
    ) -> Mapping[str, Any]:
        now = self._dependencies.clock()
        connection = self._dependencies.connection()
        preview = self._workflow_row(
            workflow_handle,
            "delete_data_preview",
            now=now,
            allow_consumed=True,
        )
        if preview["consumed_at"] is not None:
            return _stored_object(
                preview["result_json"],
                "stored deletion result",
            )
        request = _stored_object(
            preview["request_json"],
            "stored deletion request",
        )
        plan = data_erasure.build_plan(
            connection,
            scope=_required_text(request, "scope"),
            date_start=_optional_text(request, "date_start"),
            date_end=_optional_text(request, "date_end"),
            start_utc=_optional_text(request, "start_utc"),
            end_utc=_optional_text(request, "end_utc"),
            excluded_preview_token_hash=str(preview["token_hash"]),
        )
        goals = self._goal_defaults()
        commit_arguments = {
            "expected_digest": _required_text(request, "target_digest"),
            "preview_token_hash": str(preview["token_hash"]),
            "control_operation_handle": control_operation_handle,
            "now": now,
            "goal_defaults": goals,
        }
        if plan.scope == "all_business":
            manager = manager_for(self._dependencies.data_paths)
            with manager.exclusive_erasure():
                self._crash_probe("derived_exclusive_acquired")
                deletion = self._commit_delete_with_quarantine(
                    plan,
                    **commit_arguments,
                )
        else:
            deletion = data_erasure.commit_plan(
                connection,
                plan,
                data_paths=self._dependencies.data_paths,
                **commit_arguments,
            )
        return {"deletion": deletion}

    def _goal_defaults(self) -> Mapping[str, Any]:
        from .config import load_settings

        settings = load_settings(
            self._dependencies.source_root,
            self._dependencies.data_paths,
        )
        goals = settings.nutrition_goals
        return {
            "calories_kcal": goals.calories_kcal,
            "protein_g": goals.protein_g,
            "fat_g": goals.fat_g,
            "carbohydrate_g": goals.carbohydrate_g,
            "fiber_g": goals.fiber_g,
            "sodium_mg": goals.sodium_mg,
            "water_ml": goals.water_ml,
            "timezone_name": settings.profile.timezone,
        }

    def _commit_delete_with_quarantine(
        self,
        plan: data_erasure.ErasurePlan,
        *,
        expected_digest: str,
        preview_token_hash: str,
        control_operation_handle: str,
        now: datetime,
        goal_defaults: Mapping[str, Any],
    ) -> dict[str, Any]:
        data_paths = self._dependencies.data_paths
        operation_id = self._operation_id(control_operation_handle)
        quarantine = (
            data_paths.control
            / "erasure-quarantine"
            / preview_token_hash
        )
        business_committed = False
        artifacts: tuple[_QuarantinedArtifact, ...] = ()
        try:
            durable_mkdir(quarantine, data_paths=data_paths)
            artifacts = self._scan_derived_files(quarantine)
            self._preflight_initial_artifacts(artifacts)
            self._persist_manifest(
                operation_id,
                artifacts,
                preview_token_hash=preview_token_hash,
                target_digest=expected_digest,
                recorded_at=_workflow_timestamp(now),
            )
            self._crash_probe("manifest_persisted")
            self._preflight_initial_artifacts(artifacts)
            for artifact in artifacts:
                durable_replace(
                    artifact.original,
                    artifact.staged,
                    data_paths=data_paths,
                    expected_sha256=artifact.content_sha256,
                )
                self._crash_probe("stage_replace_complete")
                self._set_item_state(operation_id, artifact.item_key, "staged")

            self._crash_probe("before_second_derived_scan")
            if self._scan_derived_files(quarantine):
                raise DerivedFilesChangedError(
                    "Generated files changed during deletion"
                )
            deletion = data_erasure.commit_plan(
                self._dependencies.connection(),
                plan,
                expected_digest=expected_digest,
                preview_token_hash=preview_token_hash,
                control_operation_handle=control_operation_handle,
                now=now,
                goal_defaults=goal_defaults,
                data_paths=data_paths,
            )
            business_committed = True
            self._crash_probe("database_commit_complete")
            self._crash_probe("after_database_commit_before_residue_evidence")
            observed_count = len(self._scan_derived_files(quarantine))
            self._record_check(
                operation_id,
                stage_code="post_commit",
                check_code="derived_roots_zero_residue",
                expected={"file_count": 0},
                observed={"file_count": observed_count},
                outcome="pass" if observed_count == 0 else "fail",
            )
            if observed_count:
                self._mark_verification_required(control_operation_handle)
                raise ErasureVerificationRequired(
                    "Generated-file residue requires verification"
                )
            for index, artifact in enumerate(artifacts):
                self._set_item_state(
                    operation_id,
                    artifact.item_key,
                    "purge_pending",
                )
                self._crash_probe("purge_pending_persisted")
                durable_unlink(artifact.staged, data_paths=data_paths)
                self._crash_probe("purge_unlink_fsynced")
                self._set_item_state(
                    operation_id,
                    artifact.item_key,
                    "purged",
                    resolved=True,
                )
                if index == 0:
                    self._crash_probe("first_item_purged")
            terminal_ok, expected, observed = self._verify_quarantine_terminal(
                operation_id,
                preview_hash=preview_token_hash,
                terminal_state="purged",
            )
            if not terminal_ok:
                self._record_check(
                    operation_id,
                    stage_code="terminal",
                    check_code="erasure_quarantine_terminal",
                    expected=expected,
                    observed=observed,
                    outcome="fail",
                )
                self._mark_verification_required(control_operation_handle)
                raise ErasureVerificationRequired(
                    "Quarantine terminal state requires verification"
                )
            durable_rmdir(quarantine, data_paths=data_paths)
            self._record_check(
                operation_id,
                stage_code="terminal",
                check_code="erasure_quarantine_terminal",
                expected=expected,
                observed=observed,
                outcome="pass",
            )
            return {
                **deletion,
                "derived_files_removed": len(artifacts),
                "effect_count": int(deletion.get("effect_count", 0))
                + len(artifacts),
            }
        except BaseException:
            if not business_committed:
                try:
                    self._restore_artifacts(operation_id, artifacts)
                    if artifacts:
                        terminal_ok, expected, observed = (
                            self._verify_quarantine_terminal(
                                operation_id,
                                preview_hash=preview_token_hash,
                                terminal_state="restored",
                            )
                        )
                        if not terminal_ok:
                            self._record_check(
                                operation_id,
                                stage_code="terminal",
                                check_code="erasure_quarantine_terminal",
                                expected=expected,
                                observed=observed,
                                outcome="fail",
                            )
                            raise ErasureVerificationRequired(
                                "Quarantine restoration requires verification"
                            )
                    try:
                        durable_rmdir(quarantine, data_paths=data_paths)
                    except FileNotFoundError:
                        pass
                    if artifacts:
                        self._record_check(
                            operation_id,
                            stage_code="terminal",
                            check_code="erasure_quarantine_terminal",
                            expected=expected,
                            observed=observed,
                            outcome="pass",
                        )
                except BaseException:
                    self._mark_verification_required(control_operation_handle)
            else:
                self._mark_verification_required(control_operation_handle)
            raise

    def _scan_derived_files(
        self,
        quarantine: Path,
    ) -> tuple[_QuarantinedArtifact, ...]:
        data_paths = self._dependencies.data_paths
        found: list[_QuarantinedArtifact] = []
        roots: tuple[
            tuple[Literal["cache", "exports", "reports"], Path], ...
        ] = (
            ("cache", data_paths.cache),
            ("exports", data_paths.exports),
            ("reports", data_paths.reports),
        )
        for domain, root in roots:
            validate_owned_path(data_paths, root)
            if root.is_symlink() or not root.is_dir():
                raise data_erasure.DataErasureError(
                    "Generated-file root is unsafe"
                )
            try:
                entries = sorted(root.rglob("*"))
            except OSError as error:
                raise data_erasure.DataErasureError(
                    "Generated-file root is unreadable"
                ) from error
            for path in entries:
                validate_owned_path(data_paths, path)
                details = path.lstat()
                if path.is_symlink() or bool(
                    getattr(details, "st_file_attributes", 0) & 0x400
                ):
                    raise data_erasure.DataErasureError(
                        "Generated-file path is unsafe"
                    )
                if stat.S_ISDIR(details.st_mode):
                    continue
                if not stat.S_ISREG(details.st_mode):
                    raise data_erasure.DataErasureError(
                        "Generated-file entry is not regular"
                    )
                raw_relative = path.relative_to(root)
                normalized = PurePosixPath(*raw_relative.parts).as_posix()
                pure = PurePosixPath(normalized)
                if (
                    not normalized
                    or pure.is_absolute()
                    or ".." in pure.parts
                ):
                    raise data_erasure.DataErasureError(
                        "Generated-file path is invalid"
                    )
                item_key = _quarantine_item_key(domain, normalized)
                found.append(
                    _QuarantinedArtifact(
                        domain=domain,
                        item_key=item_key,
                        normalized_relative_path=normalized,
                        content_sha256=_file_sha256(path, data_paths),
                        original=path,
                        staged=quarantine / f"{item_key}.bin",
                    )
                )
        keys = [artifact.item_key for artifact in found]
        if len(keys) != len(set(keys)):
            raise data_erasure.DataErasureError(
                "Generated-file manifest contains duplicate paths"
            )
        return tuple(sorted(found, key=lambda artifact: artifact.item_key))

    def _preflight_initial_artifacts(
        self,
        artifacts: tuple[_QuarantinedArtifact, ...],
    ) -> None:
        data_paths = self._dependencies.data_paths
        for artifact in artifacts:
            validate_owned_path(data_paths, artifact.original)
            validate_owned_path(data_paths, artifact.staged)
            if not artifact.original.is_file() or artifact.original.is_symlink():
                raise data_erasure.DataErasureError(
                    "Generated-file manifest changed before staging"
                )
            if _optional_file_sha256(artifact.staged, data_paths) is not None:
                raise data_erasure.DataErasureError(
                    "Generated-file quarantine path already exists"
                )
            if (
                _file_sha256(artifact.original, data_paths)
                != artifact.content_sha256
            ):
                raise data_erasure.DataErasureError(
                    "Generated-file manifest changed before staging"
                )

    def _persist_manifest(
        self,
        operation_id: int,
        artifacts: tuple[_QuarantinedArtifact, ...],
        *,
        preview_token_hash: str,
        target_digest: str,
        recorded_at: str,
    ) -> None:
        control = self._dependencies.controller.connection
        with _control_transaction(control):
            control.executemany(
                """
                INSERT INTO maintenance_quarantine_items (
                    operation_id, item_key, original_relative_name,
                    staged_relative_name, sha256, state, recorded_at
                ) VALUES (?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    (
                        operation_id,
                        artifact.item_key,
                        f"{artifact.domain}/{artifact.normalized_relative_path}",
                        artifact.staged.relative_to(
                            self._dependencies.data_paths.root
                        ).as_posix(),
                        artifact.content_sha256,
                        recorded_at,
                    )
                    for artifact in artifacts
                ),
            )
            control.execute(
                """
                INSERT INTO maintenance_checks (
                    operation_id, stage_code, check_code, outcome,
                    checked_at, expected_json, observed_json
                ) VALUES (?, 'manifest', 'erasure_manifest', 'pass', ?, ?, ?)
                """,
                (
                    operation_id,
                    recorded_at,
                    _canonical_json(
                        {
                            "preview_token_hash": preview_token_hash,
                            "target_digest": target_digest,
                        }
                    ),
                    _canonical_json({"file_count": len(artifacts)}),
                ),
            )

    def _verify_quarantine_terminal(
        self,
        operation_id: int,
        *,
        preview_hash: str,
        terminal_state: Literal["purged", "restored"],
    ) -> tuple[bool, Mapping[str, Any], Mapping[str, Any]]:
        control = self._dependencies.controller.connection
        rows = control.execute(
            """
            SELECT item_key, original_relative_name, staged_relative_name,
                   sha256, state
            FROM maintenance_quarantine_items
            WHERE operation_id = ?
            ORDER BY item_key
            """,
            (operation_id,),
        ).fetchall()
        bindings_valid = True
        locations_valid = True
        hashes_valid = True
        expected_files: dict[str, str] = {}
        for row in rows:
            try:
                expected_hash = str(row["sha256"])
                if not _is_lower_sha256(expected_hash):
                    raise ValueError("invalid manifest content hash")
                original, staged = self._recovery_paths(row, preview_hash)
                original_hash = _optional_file_sha256(
                    original, self._dependencies.data_paths
                )
                staged_hash = _optional_file_sha256(
                    staged, self._dependencies.data_paths
                )
                original_present = original_hash is not None
                staged_present = staged_hash is not None
                if terminal_state == "purged":
                    if original_present or staged_present:
                        locations_valid = False
                else:
                    expected_files[str(row["item_key"])] = expected_hash
                    if not original_present or staged_present:
                        locations_valid = False
                    elif original_hash != expected_hash:
                        hashes_valid = False
            except (OSError, ValueError, data_erasure.DataErasureError):
                bindings_valid = False
                locations_valid = False
                hashes_valid = False

        quarantine = (
            self._dependencies.data_paths.control
            / "erasure-quarantine"
            / preview_hash
        )
        quarantine_entry_count, quarantine_entries_valid = (
            self._inspect_terminal_quarantine(quarantine)
        )
        try:
            root_files = self._scan_derived_files(quarantine)
            root_file_count = len(root_files)
            if terminal_state == "restored":
                observed_files = {
                    artifact.item_key: artifact.content_sha256
                    for artifact in root_files
                }
                if set(observed_files) != set(expected_files):
                    locations_valid = False
                if any(
                    observed_files.get(key) != expected_hash
                    for key, expected_hash in expected_files.items()
                ):
                    hashes_valid = False
        except (OSError, ValueError, data_erasure.DataErasureError):
            root_file_count = -1
            locations_valid = False
            hashes_valid = False

        states = sorted({str(row["state"]) for row in rows})
        states_valid = all(str(row["state"]) == terminal_state for row in rows)
        expected_root_count = 0 if terminal_state == "purged" else len(rows)
        expected = {
            "terminal_state": terminal_state,
            "item_count": len(rows),
            "root_file_count": expected_root_count,
            "quarantine_entry_count": 0,
            "quarantine_entries_valid": True,
            "bindings_valid": True,
            "locations_valid": True,
            "hashes_valid": True,
        }
        observed = {
            "item_states": states,
            "item_count": len(rows),
            "root_file_count": root_file_count,
            "quarantine_entry_count": quarantine_entry_count,
            "quarantine_entries_valid": quarantine_entries_valid,
            "bindings_valid": bindings_valid,
            "locations_valid": locations_valid,
            "hashes_valid": hashes_valid,
        }
        verified = (
            states_valid
            and root_file_count == expected_root_count
            and quarantine_entry_count == 0
            and quarantine_entries_valid
            and bindings_valid
            and locations_valid
            and hashes_valid
        )
        return verified, expected, observed

    def _inspect_terminal_quarantine(
        self,
        quarantine: Path,
    ) -> tuple[int, bool]:
        data_paths = self._dependencies.data_paths
        try:
            validate_owned_path(data_paths, quarantine)
            details = quarantine.lstat()
        except FileNotFoundError:
            return 0, True
        except (OSError, ValueError, data_erasure.DataErasureError):
            return -1, False
        if (
            quarantine.is_symlink()
            or bool(getattr(details, "st_file_attributes", 0) & 0x400)
            or not stat.S_ISDIR(details.st_mode)
        ):
            return -1, False
        try:
            entries = tuple(quarantine.iterdir())
        except OSError:
            return -1, False
        entries_valid = not entries
        for entry in entries:
            try:
                validate_owned_path(data_paths, entry)
                entry_details = entry.lstat()
                if (
                    entry.is_symlink()
                    or bool(
                        getattr(entry_details, "st_file_attributes", 0)
                        & 0x400
                    )
                    or not stat.S_ISREG(entry_details.st_mode)
                ):
                    entries_valid = False
                    continue
                _file_sha256(entry, data_paths)
            except (OSError, ValueError, data_erasure.DataErasureError):
                entries_valid = False
        return len(entries), entries_valid

    def _restore_artifacts(
        self,
        operation_id: int,
        artifacts: tuple[_QuarantinedArtifact, ...],
    ) -> None:
        actions: list[tuple[str, _QuarantinedArtifact]] = []
        for artifact in artifacts:
            original_hash = _optional_file_sha256(
                artifact.original, self._dependencies.data_paths
            )
            staged_hash = _optional_file_sha256(
                artifact.staged, self._dependencies.data_paths
            )
            original_exists = original_hash is not None
            staged_exists = staged_hash is not None
            if original_exists and not staged_exists:
                if original_hash != artifact.content_sha256:
                    raise ErasureVerificationRequired(
                        "Quarantine restoration is ambiguous"
                    )
                actions.append(("mark", artifact))
            elif staged_exists and not original_exists:
                if staged_hash != artifact.content_sha256:
                    raise ErasureVerificationRequired(
                        "Quarantine restoration is ambiguous"
                    )
                actions.append(("restore", artifact))
            else:
                raise ErasureVerificationRequired(
                    "Quarantine restoration is ambiguous"
                )
        for action, artifact in actions:
            if action == "restore":
                durable_replace(
                    artifact.staged,
                    artifact.original,
                    data_paths=self._dependencies.data_paths,
                    expected_sha256=artifact.content_sha256,
                )
            self._set_item_state(
                operation_id,
                artifact.item_key,
                "restored",
                resolved=True,
            )

    def _set_item_state(
        self,
        operation_id: int,
        item_key: str,
        state: str,
        *,
        resolved: bool = False,
    ) -> None:
        control = self._dependencies.controller.connection
        with _control_transaction(control):
            changed = control.execute(
                """
                UPDATE maintenance_quarantine_items
                SET state = ?, resolved_at = CASE WHEN ? THEN ? ELSE resolved_at END
                WHERE operation_id = ? AND item_key = ?
                """,
                (
                    state,
                    int(resolved),
                    _workflow_timestamp(self._dependencies.clock()),
                    operation_id,
                    item_key,
                ),
            ).rowcount
            if changed != 1:
                raise ErasureVerificationRequired(
                    "Quarantine item transition did not match exactly one row"
                )

    def _record_check(
        self,
        operation_id: int,
        *,
        stage_code: str,
        check_code: str,
        expected: Mapping[str, Any],
        observed: Mapping[str, Any],
        outcome: Literal["pass", "warn", "fail"],
    ) -> None:
        control = self._dependencies.controller.connection
        with _control_transaction(control):
            control.execute(
                """
                INSERT OR REPLACE INTO maintenance_checks (
                    operation_id, stage_code, check_code, outcome,
                    checked_at, expected_json, observed_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    stage_code,
                    check_code,
                    outcome,
                    _workflow_timestamp(self._dependencies.clock()),
                    _canonical_json(expected),
                    _canonical_json(observed),
                ),
            )

    def _operation_id(self, handle: str) -> int:
        row = self._dependencies.controller.connection.execute(
            "SELECT id FROM maintenance_operations WHERE operation_handle = ?",
            (handle,),
        ).fetchone()
        if row is None:
            raise maintenance_control.MaintenanceNotFound(
                "Maintenance operation was not found"
            )
        return int(row[0])

    def _mark_verification_required(self, handle: str) -> None:
        control = self._dependencies.controller.connection
        control.execute(
            """
            UPDATE maintenance_operations
            SET reconciliation_decision = 'verification_required'
            WHERE operation_handle = ?
            """,
            (handle,),
        )
        control.commit()

    def _preview_import(
        self,
        command: ImportDataCommand,
        request_context: RequestContext,
    ) -> WorkflowPreview:
        connection = self._dependencies.connection()
        plan = data_import.load_and_validate(
            connection,
            self._dependencies.data_paths,
            command.import_name,
        )
        validation = {
            "valid": True,
            "export_schema_version": plan.manifest["export_schema_version"],
            "product_version": plan.manifest["product_version"],
            "record_counts": dict(plan.record_counts),
            "artifact_sha256": plan.artifact_sha256,
            "conflict_count": 0,
        }
        handle, expires_at = self._issue_workflow(
            "import_preview",
            request={
                "import_name": command.import_name,
                "artifact_sha256": plan.artifact_sha256,
            },
            result={"validation": validation},
            resource_versions={
                "records_sha256": plan.manifest["records_sha256"],
            },
            now=request_context.now,
        )
        return WorkflowPreview(
            workflow_handle=handle,
            command="import_data",
            summary={"validation": validation},
            expires_at=expires_at,
        )

    def _commit_import(
        self,
        workflow_handle: str,
        confirmation: Confirmation,
    ) -> OperationReceipt:
        if confirmation.confirmed is not True:
            raise data_import.DataImportError(
                "Import requires explicit confirmation"
            )

        parameters = {
            "commit_handle": workflow_handle,
            "confirmed": confirmation.confirmed,
        }
        result = self._run_exclusive_maintenance(
            "import_data",
            parameters,
            operation_key=confirmation.operation_key,
            work=lambda _record: self._commit_import_preview(workflow_handle),
        )
        effect_count = _import_effect_count(result)
        return OperationReceipt(
            command="import_data",
            effect_count=effect_count,
            undo_policy="none",
            result=result,
        )

    def _commit_import_preview(
        self,
        workflow_handle: str,
    ) -> Mapping[str, Any]:
        now = self._dependencies.clock()
        connection = self._dependencies.connection()
        preview = self._workflow_row(
            workflow_handle,
            "import_preview",
            now=now,
            allow_consumed=True,
        )
        if preview["consumed_at"] is not None:
            return _stored_object(
                preview["result_json"],
                "stored import result",
            )
        request = _stored_object(
            preview["request_json"],
            "stored import request",
        )
        plan = data_import.load_and_validate(
            connection,
            self._dependencies.data_paths,
            _required_text(request, "import_name"),
        )
        if plan.artifact_sha256 != _required_text(
            request,
            "artifact_sha256",
        ):
            raise data_import.DataImportError(
                "Import artifact changed after validation"
            )
        return {
            "import": data_import.commit_import(
                connection,
                plan,
                now=now,
                preview_token_hash=str(preview["token_hash"]),
            )
        }

    def _run_exclusive_maintenance(
        self,
        action: str,
        parameters: Mapping[str, Any],
        *,
        operation_key: str | None,
        work: Callable[[maintenance_control.MaintenanceRecord], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        controller = self._dependencies.controller
        record, replayed = controller.accept(
            action,
            parameters,
            operation_key=operation_key,
            exclusive=True,
        )
        if replayed:
            if record.status == "committed":
                stored = controller.result(record.handle) or {}
                return {
                    **stored,
                    "maintenance": maintenance_control.public_record(record),
                }
            if record.status == "failed":
                raise maintenance_control.MaintenanceStateError(
                    "The maintenance operation failed"
                )
            return {
                "maintenance": maintenance_control.public_record(record),
            }

        controller.mark_running(record.handle)
        try:
            result = dict(work(record))
        except Exception as error:
            controller.mark_failed(record.handle, type(error).__name__)
            self._mark_exclusive_released(record.handle)
            raise
        committed = controller.mark_committed(record.handle, result)
        self._mark_exclusive_released(record.handle)
        return {
            **result,
            "maintenance": maintenance_control.public_record(committed),
        }

    def _issue_workflow(
        self,
        operation_type: str,
        *,
        request: Any,
        result: Any,
        resource_versions: Any,
        now: datetime,
        entity_links: tuple[EntityLink, ...] = (),
    ) -> tuple[str, str]:
        handle = _HANDLE_PREFIX + secrets.token_urlsafe(32)
        created_at = now.astimezone(timezone.utc).replace(microsecond=0)
        expires_at = created_at + self._dependencies.preview_ttl
        connection = self._dependencies.connection()
        try:
            token_hash = _workflow_hash(handle)
            connection.execute(
                """
                INSERT INTO operation_previews (
                    token_hash, operation_type, request_json, result_json,
                    resource_versions_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    operation_type,
                    _canonical_json(request),
                    _canonical_json(result),
                    _canonical_json(resource_versions),
                    _workflow_timestamp(created_at),
                    _workflow_timestamp(expires_at),
                ),
            )
            index_preview_links(
                connection,
                token_hash=token_hash,
                links=entity_links,
                created_at=_workflow_timestamp(created_at),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return handle, _workflow_timestamp(expires_at)

    def _workflow_row(
        self,
        handle: str,
        operation_type: str,
        *,
        now: datetime,
        allow_consumed: bool = False,
        required: bool = True,
    ) -> sqlite3.Row | None:
        if not isinstance(handle, str) or not handle.startswith(_HANDLE_PREFIX):
            raise WorkflowStaleError("Workflow reference is stale")
        row = self._dependencies.connection().execute(
            """
            SELECT *
            FROM operation_previews
            WHERE token_hash = ? AND operation_type = ?
            """,
            (_workflow_hash(handle), operation_type),
        ).fetchone()
        if row is None:
            if not required:
                return None
            raise WorkflowStaleError("Workflow reference is stale")
        expires_at = _datetime_value(row["expires_at"], "workflow expiry")
        if now.astimezone(timezone.utc) >= expires_at:
            raise WorkflowStaleError("Workflow reference is stale")
        if row["consumed_at"] is not None and not allow_consumed:
            raise WorkflowStaleError("Workflow reference is stale")
        return row

    def _mark_exclusive_released(self, handle: str) -> None:
        controller = self._dependencies.controller
        controller.connection.execute(
            """
            UPDATE maintenance_operations
            SET exclusive_released_at = COALESCE(exclusive_released_at, ?)
            WHERE operation_handle = ?
            """,
            (_workflow_timestamp(self._dependencies.clock()), handle),
        )
        controller.connection.commit()


def _import_effect_count(result: Mapping[str, Any]) -> int:
    imported = result.get("import")
    if not isinstance(imported, Mapping):
        return 0
    counts = imported.get("record_counts")
    if not isinstance(counts, Mapping):
        return 0
    return sum(
        value
        for value in counts.values()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def _workflow_hash(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _workflow_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _datetime_value(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise WorkflowStaleError(f"{field} is unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkflowStaleError(f"{field} is unavailable") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkflowStaleError(f"{field} is unavailable")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stored_object(value: str, label: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise WorkflowStaleError(f"{label} is unavailable") from error
    if not isinstance(decoded, Mapping):
        raise WorkflowStaleError(f"{label} is unavailable")
    return decoded


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} is required")
    return item.strip()


def _optional_text(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be non-empty when provided")
    return item.strip()


def _file_sha256(path: Path, data_paths: DataPaths) -> str:
    try:
        return sha256_regular_file(path, data_paths=data_paths)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise data_erasure.DataErasureError(
            "Generated-file entry is not a safe regular file"
        ) from error


def _optional_file_sha256(path: Path, data_paths: DataPaths) -> str | None:
    try:
        return _file_sha256(path, data_paths)
    except FileNotFoundError:
        return None


@contextmanager
def _control_transaction(
    connection: sqlite3.Connection,
) -> Iterator[None]:
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
