"""Data directory resolution and containment for plugin-owned files."""

import os
from pathlib import Path
from typing import Any, Mapping

from .models import ConfigurationError, DataPaths


class PathSafetyError(ConfigurationError):
    """Raised when a plugin-owned path can escape the configured data root."""


def resolve_data_paths(
    plugin_config: Mapping[str, Any] | None,
    env: Mapping[str, str] | None,
    openclaw_data_root: Path | None,
) -> DataPaths:
    """Resolve immutable data paths using plugin, environment, then OpenClaw defaults."""

    configured = (plugin_config or {}).get("dataDir")
    from_env = (env or {}).get("PERSONAL_DIET_PANTRY_DATA_DIR")
    if configured:
        root = Path(configured)
    elif from_env:
        root = Path(from_env)
    elif openclaw_data_root is not None:
        root = Path(openclaw_data_root) / "personal-diet-pantry"
    else:
        raise ConfigurationError("A data directory could not be resolved")
    resolved = root.expanduser().resolve()
    return DataPaths(
        root=resolved,
        database=resolved / "diet.sqlite",
        control=resolved / "control",
        maintenance_database=resolved / "control" / "maintenance.sqlite",
        backups=resolved / "backups",
        exports=resolved / "exports",
        imports=resolved / "imports",
        reports=resolved / "reports",
        cache=resolved / "cache",
        health_report=resolved / "health-report.md",
    )


def ensure_data_directories(data_paths: DataPaths) -> None:
    """Create only the directory layout owned by a resolved data root."""

    for directory in (
        data_paths.root,
        data_paths.control,
        data_paths.backups,
        data_paths.exports,
        data_paths.imports,
        data_paths.reports,
        data_paths.cache,
    ):
        validate_owned_path(data_paths, directory)
        directory.mkdir(parents=True, exist_ok=True)
        validate_owned_path(data_paths, directory)


def validate_owned_path(data_paths: DataPaths, target: Path) -> Path:
    """Reject lexical escapes and child symlink/reparse traversal."""

    root = Path(data_paths.root).absolute()
    candidate = Path(target).absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise PathSafetyError(f"Path escapes the configured data root: {candidate}") from error

    current = root
    for part in relative.parts:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current != root and _is_reparse_point(current):
            raise PathSafetyError(
                f"Plugin-owned path traverses a symbolic link or reparse point: {current}"
            )
    return candidate


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as error:
        raise PathSafetyError(f"Unable to inspect plugin-owned path: {path}") from error
    return bool(attributes & getattr(os.stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
