"""Comprehensive read-only health checks and narrowly scoped safe repairs."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

from .backup import (
    BackupNameKind,
    BackupVerificationError,
    parse_backup_name,
    verify_backup,
)
from . import learning, nutrition_audit
from .config import load_settings, validate_automation, validate_static_rules
from .database import CheckResult, migration_checksums
from .file_io import atomic_write_text
from .derived_file_leases import (
    DerivedFileLeaseManager,
    LeaseOwnerToken,
    manager_for,
)
from .models import ConfigurationError, DataPaths, Settings
from .paths import PathSafetyError, validate_owned_path
from .reports import (
    build_daily_report,
    build_monthly_report,
    build_weekly_report,
    resolve_timezone,
)


_MIGRATION_NAME = re.compile(r"^(?P<version>\d+)_.+\.sql$")
_CHECK_ORDER = (
    "configuration_check",
    "static_rules_check",
    "automation_check",
    "schema_check",
    "integrity_check",
    "migration_check",
    "foreign_key_check",
    "negative_stock",
    "orphan_rows",
    "pending_transactions",
    "personal_rules",
    "nutrition_history_audit",
    "backup_check",
    "backup_age",
    "directory_check",
    "expired_previews",
)
_EXPECTED_TABLES = frozenset(
    {
        "meals",
        "meal_items",
        "water_logs",
        "body_weight_logs",
        "pantry_batches",
        "pantry_movements",
        "transactions",
        "nutrition_cache",
        "nutrition_profiles",
        "pantry_nutrition_links",
        "prepared_food_profiles",
        "personal_rules",
        "learning_events",
        "pending_inventory_links",
        "operation_previews",
        "operation_receipts",
        "meal_item_nutrition_evidence",
        "schema_migrations",
    }
)
_EXPECTED_INDEXES = frozenset(
    {
        "idx_meals_occurred_at",
        "idx_meal_items_meal_id",
        "idx_water_logs_occurred_at",
        "idx_body_weight_logs_active_measured_at",
        "idx_pantry_batches_selection",
        "idx_pantry_movements_batch_id",
        "idx_pantry_movements_meal_id",
        "idx_transactions_created_at",
        "idx_nutrition_cache_expiry",
        "idx_nutrition_profiles_lookup",
        "idx_personal_rules_subject",
        "idx_learning_events_rule_id",
        "idx_pending_inventory_links_status",
        "idx_operation_previews_expiry",
        "idx_operation_receipts_transaction",
        "idx_meals_active_intake_fingerprint",
        "idx_prepared_food_profiles_source_meal",
    }
)


class SafeRepairTransactionError(RuntimeError):
    """Raised when safe repair would interfere with a caller-owned transaction."""


def run_self_check(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    migrations_dir: Path,
    *,
    source_root: Path | None = None,
    now: datetime | None = None,
    write_report: bool = True,
    lease_owner: LeaseOwnerToken | None = None,
    lease_manager: DerivedFileLeaseManager | None = None,
    _publish_period_reports: bool = False,
) -> tuple[CheckResult, ...]:
    """Run database, domain, backup, and directory checks in stable order."""

    if write_report:
        manager = lease_manager or manager_for(data_paths)
        with manager.shared_publisher(owner=lease_owner) as owner:
            return run_self_check(
                connection,
                data_paths,
                migrations_dir,
                source_root=source_root,
                now=now,
                write_report=False,
                lease_owner=owner,
                lease_manager=manager,
                _publish_period_reports=True,
            )

    checked_at = _aware_utc(now)
    effective_source_root = (
        Path(source_root) if source_root is not None else Path(migrations_dir).parent
    )
    configuration, settings = _configuration_check(
        effective_source_root, data_paths
    )
    checks = {
        "configuration_check": configuration,
        "static_rules_check": _static_rules_check(effective_source_root),
        "automation_check": _automation_check(settings),
        "schema_check": _schema_check(connection),
        "integrity_check": _integrity_check(connection),
        "migration_check": _migration_check(connection, Path(migrations_dir)),
        "foreign_key_check": _foreign_key_check(connection),
        "negative_stock": _negative_stock_check(connection),
        "orphan_rows": _orphan_check(connection),
        "pending_transactions": _pending_transaction_check(connection),
        "personal_rules": _personal_rule_check(connection),
        "nutrition_history_audit": _nutrition_history_audit_check(
            connection
        ),
        "backup_check": _backup_check(data_paths),
        "backup_age": _backup_age_check(data_paths, checked_at),
        "directory_check": _directory_check(data_paths),
        "expired_previews": _expired_preview_check(connection, checked_at),
    }
    results = tuple(checks[code] for code in _CHECK_ORDER)
    if (
        _publish_period_reports
        and lease_owner is not None
        and settings is not None
    ):
        manager = lease_manager or manager_for(data_paths)
        report_date = checked_at.astimezone(
            resolve_timezone(settings.profile.timezone)
        ).date()
        for builder in (
            build_daily_report,
            build_weekly_report,
            build_monthly_report,
        ):
            builder(
                connection,
                data_paths,
                settings,
                report_date,
                templates_dir=effective_source_root / "templates",
                lease_owner=lease_owner,
                lease_manager=manager,
            )
    if lease_owner is not None and data_paths.root.is_dir():
        try:
            validate_owned_path(data_paths, data_paths.health_report)
            _write_health_report(data_paths, results)
        except (OSError, PathSafetyError):
            pass
    return results


def _configuration_check(
    source_root: Path,
    data_paths: DataPaths,
) -> tuple[CheckResult, Settings | None]:
    try:
        settings = load_settings(source_root, data_paths)
    except ConfigurationError as error:
        return _failure("configuration_check", str(error)), None
    return _pass("configuration_check", "Configuration is valid"), settings


def _static_rules_check(source_root: Path) -> CheckResult:
    try:
        validate_static_rules(source_root)
    except ConfigurationError as error:
        return _failure("static_rules_check", str(error))
    return _pass("static_rules_check", "Static rule files are valid")


def _automation_check(settings: Settings | None) -> CheckResult:
    if settings is None:
        return _failure(
            "automation_check",
            "Automation definitions cannot be validated while configuration is invalid",
        )
    try:
        validate_automation(settings)
    except ConfigurationError as error:
        return _failure("automation_check", str(error))
    return _pass("automation_check", "Automation definitions are valid")


def _schema_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    except sqlite3.DatabaseError as error:
        return _failure("schema_check", f"Schema inspection failed: {error}")
    missing_tables = sorted(_EXPECTED_TABLES - tables)
    missing_indexes = sorted(_EXPECTED_INDEXES - indexes)
    if missing_tables or missing_indexes:
        details = []
        if missing_tables:
            details.append("missing tables: " + ", ".join(missing_tables))
        if missing_indexes:
            details.append("missing indexes: " + ", ".join(missing_indexes))
        return _failure("schema_check", "; ".join(details))
    return _pass("schema_check", "All expected tables and indexes are present")


def repair_safe_issues(
    connection: sqlite3.Connection,
    data_paths: DataPaths,
    migrations_dir: Path,
    *,
    settings: Settings | None = None,
    templates_dir: Path | None = None,
    report_date: date | None = None,
    now: datetime | None = None,
) -> tuple[CheckResult, ...]:
    """Repair only directories, expired previews, and generated reports."""

    if connection.in_transaction:
        raise SafeRepairTransactionError(
            "Safe repair cannot run while the connection has an active transaction"
        )
    checked_at = _aware_utc(now)
    validate_owned_path(data_paths, data_paths.reports)
    validate_owned_path(data_paths, data_paths.cache)
    data_paths.reports.mkdir(parents=True, exist_ok=True)
    data_paths.cache.mkdir(parents=True, exist_ok=True)
    repair_started = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        repair_started = True
        retention_cutoff = checked_at - timedelta(hours=24)
        connection.execute(
            """
            DELETE FROM operation_previews
            WHERE consumed_at IS NULL
              AND expires_at <= ?
            """,
            (_utc_text(retention_cutoff),),
        )
        connection.commit()
    except BaseException:
        if repair_started and connection.in_transaction:
            connection.rollback()
        raise

    manager = manager_for(data_paths)
    with manager.shared_publisher() as owner:
        if settings is not None:
            effective_date = report_date
            if effective_date is None:
                effective_date = checked_at.astimezone(
                    resolve_timezone(settings.profile.timezone)
                ).date()
            build_daily_report(
                connection,
                data_paths,
                settings,
                effective_date,
                templates_dir=templates_dir,
                lease_owner=owner,
                lease_manager=manager,
            )
            build_weekly_report(
                connection,
                data_paths,
                settings,
                effective_date,
                templates_dir=templates_dir,
                lease_owner=owner,
                lease_manager=manager,
            )
            build_monthly_report(
                connection,
                data_paths,
                settings,
                effective_date,
                templates_dir=templates_dir,
                lease_owner=owner,
                lease_manager=manager,
            )
        return run_self_check(
            connection,
            data_paths,
            migrations_dir,
            now=checked_at,
            write_report=False,
            lease_owner=owner,
            lease_manager=manager,
        )


def _integrity_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        messages = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as error:
        return _failure("integrity_check", f"SQLite integrity check failed: {error}")
    if messages == ["ok"]:
        return _pass("integrity_check", "SQLite integrity check passed")
    return _failure("integrity_check", "; ".join(str(item) for item in messages))


def _migration_check(
    connection: sqlite3.Connection,
    migrations_dir: Path,
) -> CheckResult:
    if not migrations_dir.is_dir():
        return _failure(
            "migration_check",
            f"Migration directory is missing: {migrations_dir}",
        )
    expected: dict[int, tuple[str, set[str]]] = {}
    try:
        for path in migrations_dir.iterdir():
            match = _MIGRATION_NAME.fullmatch(path.name)
            if not path.is_file() or match is None:
                continue
            version = int(match["version"])
            if version in expected:
                return _failure(
                    "migration_check",
                    f"Duplicate migration version {version}",
                )
            expected[version] = (
                path.name,
                migration_checksums(path.read_bytes()),
            )
        applied = {
            int(row["version"]): (str(row["name"]), str(row["checksum"]))
            for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            )
        }
    except (OSError, sqlite3.DatabaseError) as error:
        return _failure("migration_check", f"Migration check failed: {error}")
    if not expected:
        return _failure("migration_check", "No migration files were found")
    if set(applied) != set(expected):
        missing = sorted(set(expected) - set(applied))
        unknown = sorted(set(applied) - set(expected))
        details = []
        if missing:
            details.append(f"unapplied versions {missing}")
        if unknown:
            details.append(f"unknown applied versions {unknown}")
        return _failure("migration_check", "Migration mismatch: " + "; ".join(details))
    for version in sorted(expected):
        if applied[version][0] != expected[version][0]:
            return _failure(
                "migration_check",
                f"Migration {version} filename does not match",
            )
        if applied[version][1] not in expected[version][1]:
            return _failure(
                "migration_check",
                f"Migration {version} checksum does not match",
            )
    return _pass(
        "migration_check",
        f"All {len(expected)} migrations are applied with matching checksums",
    )


def _foreign_key_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        violations = list(connection.execute("PRAGMA foreign_key_check"))
    except sqlite3.DatabaseError as error:
        return _failure("foreign_key_check", f"Foreign-key check failed: {error}")
    if violations:
        return _failure(
            "foreign_key_check",
            f"Found {len(violations)} foreign-key violation(s)",
        )
    return _pass("foreign_key_check", "Foreign-key check passed")


def _negative_stock_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        count = connection.execute(
            """
            SELECT count(*) FROM pantry_batches
            WHERE typeof(remaining_quantity) NOT IN ('integer', 'real')
               OR remaining_quantity < 0
            """
        ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        return _failure("negative_stock", f"Negative-stock check failed: {error}")
    if count:
        return _failure(
            "negative_stock",
            f"Found {count} batch(es) with invalid or negative remaining stock",
        )
    return _pass("negative_stock", "No negative pantry stock found")


def _orphan_check(connection: sqlite3.Connection) -> CheckResult:
    relationships = (
        ("meals", "transactions", "m.transaction_id = p.id", "m", "p"),
        ("meal_items", "meals", "m.meal_id = p.id", "m", "p"),
        ("meal_items", "transactions", "m.transaction_id = p.id", "m", "p"),
        ("water_logs", "transactions", "m.transaction_id = p.id", "m", "p"),
        (
            "body_weight_logs",
            "transactions",
            "m.transaction_id = p.id",
            "m",
            "p",
        ),
        ("pantry_batches", "transactions", "m.transaction_id = p.id", "m", "p"),
        (
            "pantry_movements",
            "pantry_batches",
            "m.pantry_batch_id = p.id",
            "m",
            "p",
        ),
        ("pantry_movements", "transactions", "m.transaction_id = p.id", "m", "p"),
        ("nutrition_cache", "transactions", "m.transaction_id = p.id", "m", "p"),
        ("personal_rules", "transactions", "m.transaction_id = p.id", "m", "p"),
        ("learning_events", "personal_rules", "m.rule_id = p.id", "m", "p"),
        ("learning_events", "transactions", "m.transaction_id = p.id", "m", "p"),
        (
            "pending_inventory_links",
            "meal_items",
            "m.meal_item_id = p.id",
            "m",
            "p",
        ),
        (
            "pending_inventory_links",
            "transactions",
            "m.transaction_id = p.id",
            "m",
            "p",
        ),
    )
    try:
        count = 0
        for child, parent, join, child_alias, parent_alias in relationships:
            count += int(
                connection.execute(
                    f"""
                    SELECT count(*) FROM {child} AS {child_alias}
                    LEFT JOIN {parent} AS {parent_alias} ON {join}
                    WHERE {parent_alias}.id IS NULL
                    """
                ).fetchone()[0]
            )
    except sqlite3.DatabaseError as error:
        return _failure("orphan_rows", f"Orphan-row check failed: {error}")
    if count:
        return _failure("orphan_rows", f"Found {count} orphaned formal row(s)")
    return _pass("orphan_rows", "No orphaned formal rows found")


def _pending_transaction_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        count = connection.execute(
            "SELECT count(*) FROM transactions WHERE status = 'pending'"
        ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        return _failure(
            "pending_transactions",
            f"Pending-transaction check failed: {error}",
        )
    if count:
        return CheckResult(
            code="pending_transactions",
            level="WARN",
            message=f"Found {count} pending transaction(s)",
            repairable=False,
        )
    return _pass("pending_transactions", "No pending transactions found")


def _personal_rule_check(connection: sqlite3.Connection) -> CheckResult:
    try:
        invalid = connection.execute(
            """
            SELECT count(*) FROM personal_rules
            WHERE typeof(confidence) NOT IN ('integer', 'real')
               OR confidence < 0 OR confidence > 1
               OR typeof(evidence_count) <> 'integer'
               OR evidence_count < 0
               OR json_valid(rule_json) = 0
            """
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT rule_type, subject, rule_json, active,
                   json_valid(rule_json) AS rule_json_valid
            FROM personal_rules
            ORDER BY id
            """
        ).fetchall()
        invalid_events = connection.execute(
            """
            SELECT count(*) FROM learning_events
            WHERE json_valid(evidence_json) = 0
            """
        ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        return _failure("personal_rules", f"Personal-rule check failed: {error}")
    invalid_canonical = 0
    active_keys: dict[tuple[str, str], int] = {}
    for row in rows:
        if row["rule_json_valid"] != 1:
            continue
        try:
            payload = json.loads(
                row["rule_json"],
                parse_constant=_reject_json_constant,
            )
            if not isinstance(payload, dict):
                raise ValueError("rule_json must be an object")
            canonical_type = learning.RuleType(payload.get("rule_type"))
            canonical_subject = learning._subject(row["subject"])
            if row["rule_type"] != learning._storage_rule_type(
                canonical_type
            ):
                raise ValueError("storage rule type does not match canonical type")
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            learning.LearningValidationError,
        ):
            invalid_canonical += 1
            continue
        if row["active"]:
            key = (canonical_type.value, canonical_subject)
            active_keys[key] = active_keys.get(key, 0) + 1
    duplicates = sum(1 for count in active_keys.values() if count > 1)
    total = (
        int(invalid)
        + invalid_canonical
        + duplicates
        + int(invalid_events)
    )
    if total:
        return _failure(
            "personal_rules",
            f"Found {total} invalid or conflicting personal-rule record(s)",
        )
    return _pass("personal_rules", "Personal rules and learning evidence are valid")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant is not allowed: {value}")


def _backup_check(data_paths: DataPaths) -> CheckResult:
    backup_dir = Path(data_paths.backups)
    try:
        validate_owned_path(data_paths, backup_dir)
    except PathSafetyError:
        return _failure(
            "backup_check",
            "Backup path safety validation failed",
        )
    if not backup_dir.is_dir():
        return CheckResult(
            code="backup_check",
            level="WARN",
            message="Backup directory is missing",
            repairable=False,
        )
    backups = sorted(backup_dir.glob("*.sqlite"))
    if not backups:
        return CheckResult(
            code="backup_check",
            level="WARN",
            message="No database backup is available",
            repairable=False,
        )
    candidates: list[tuple[datetime, Path]] = []
    malformed: list[Path] = []
    for path in backups:
        parsed = parse_backup_name(path)
        if parsed.kind is BackupNameKind.MALFORMED:
            malformed.append(path)
        elif parsed.kind in {
            BackupNameKind.RETENTION_MANAGED,
            BackupNameKind.PLUGIN_BACKUP,
        }:
            if parsed.timestamp is None:
                malformed.append(path)
            else:
                candidates.append((parsed.timestamp, path))
    if not candidates:
        malformed_message = (
            "; malformed backup filename(s): "
            + ", ".join(path.name for path in malformed)
            if malformed
            else ""
        )
        return CheckResult(
            code="backup_check",
            level="WARN",
            message=(
                "No valid plugin database backup is available"
                + malformed_message
            ),
            repairable=False,
        )
    _, latest = max(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1].name),
    )
    try:
        verify_backup(latest, data_paths=data_paths)
    except PathSafetyError:
        return _failure(
            "backup_check",
            "Backup path safety validation failed",
        )
    except BackupVerificationError as error:
        return _failure(
            "backup_check",
            f"Latest backup {latest.name} is invalid: {error}",
        )
    if malformed:
        return CheckResult(
            code="backup_check",
            level="WARN",
            message=(
                f"Latest backup {latest.name} is verified; ignored malformed "
                "backup filename(s): "
                + ", ".join(path.name for path in malformed)
            ),
            repairable=False,
        )
    return _pass("backup_check", f"Latest backup {latest.name} is verified")


def _backup_age_check(
    data_paths: DataPaths,
    checked_at: datetime,
) -> CheckResult:
    candidates = []
    try:
        validate_owned_path(data_paths, data_paths.backups)
        for path in data_paths.backups.glob("*.sqlite"):
            parsed = parse_backup_name(path)
            if (
                parsed.kind
                in {
                    BackupNameKind.RETENTION_MANAGED,
                    BackupNameKind.PLUGIN_BACKUP,
                }
                and parsed.timestamp is not None
            ):
                candidates.append(parsed.timestamp)
    except (OSError, PathSafetyError):
        return _failure("backup_age", "Backup age cannot be inspected")
    if not candidates:
        return CheckResult(
            code="backup_age",
            level="WARN",
            message="No verified backup timestamp is available",
            repairable=False,
        )
    latest = max(candidates)
    age = checked_at - latest
    if age > timedelta(days=7):
        return CheckResult(
            code="backup_age",
            level="WARN",
            message=f"Latest backup is {age.days} day(s) old",
            repairable=False,
        )
    return _pass("backup_age", "Latest backup is recent")


def _nutrition_history_audit_check(
    connection: sqlite3.Connection,
) -> CheckResult:
    try:
        findings = nutrition_audit.audit_nutrition(connection)
    except sqlite3.DatabaseError as error:
        return _failure(
            "nutrition_history_audit",
            f"Nutrition history audit failed: {error}",
        )
    if findings:
        return CheckResult(
            code="nutrition_history_audit",
            level="WARN",
            message=(
                f"Found {len(findings)} historical nutrition anomaly(s)"
            ),
            repairable=False,
        )
    return _pass(
        "nutrition_history_audit",
        "No historical nutrition anomalies found",
    )


def _directory_check(data_paths: DataPaths) -> CheckResult:
    directories = (
        Path(data_paths.root),
        Path(data_paths.backups),
        Path(data_paths.exports),
        Path(data_paths.reports),
        Path(data_paths.cache),
    )
    wrong_type = [path for path in directories if path.exists() and not path.is_dir()]
    if wrong_type:
        return _failure(
            "directory_check",
            "Expected directories are non-directories: "
            + ", ".join(str(path) for path in wrong_type),
        )
    missing = [path for path in directories if not path.is_dir()]
    if missing:
        repairable = all(
            path in {Path(data_paths.reports), Path(data_paths.cache)}
            for path in missing
        )
        return CheckResult(
            code="directory_check",
            level="WARN",
            message="Missing data directories: " + ", ".join(str(path) for path in missing),
            repairable=repairable,
        )
    return _pass("directory_check", "All required data directories exist")


def _expired_preview_check(
    connection: sqlite3.Connection,
    now: datetime,
) -> CheckResult:
    try:
        count = connection.execute(
            "SELECT count(*) FROM operation_previews WHERE expires_at <= ?",
            (_utc_text(now),),
        ).fetchone()[0]
    except sqlite3.DatabaseError as error:
        return _failure("expired_previews", f"Preview check failed: {error}")
    if count:
        return CheckResult(
            code="expired_previews",
            level="WARN",
            message=f"Found {count} expired operation preview(s)",
            repairable=True,
        )
    return _pass("expired_previews", "No expired operation previews found")


def _pass(code: str, message: str) -> CheckResult:
    return CheckResult(code=code, level="PASS", message=message, repairable=False)


def _failure(code: str, message: str) -> CheckResult:
    return CheckResult(code=code, level="FAIL", message=message, repairable=False)


def _write_health_report(
    data_paths: DataPaths,
    results: tuple[CheckResult, ...],
) -> None:
    path = data_paths.health_report
    body = ["# Personal Diet Pantry Health Check", ""]
    body.extend(
        f"- **{result.level}** `{result.code}` — {result.message}"
        for result in results
    )
    atomic_write_text(path, "\n".join(body) + "\n", data_paths=data_paths)


def _aware_utc(value: datetime | None) -> datetime:
    candidate = value or datetime.now(timezone.utc)
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValueError("Self-check timestamps must be timezone-aware")
    return candidate.astimezone(timezone.utc).replace(microsecond=0)


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
