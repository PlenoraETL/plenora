"""Legacy filesystem result boundary.

This keeps pre-result-store files under RESULTS_DIR isolated from API routers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings
from app.shared.resources.path_policy import assert_path_within

LEGACY_LOCAL_RESULT_PATHS_FLAG = "PLENORA_ALLOW_LEGACY_LOCAL_RESULT_PATHS"

_HARDENED_ENVS = {"production", "prod", "staging", "stage"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _environment_name() -> str:
    env_name = settings.get("ENVIRONMENT", settings.get("ENV", "development"))
    return str(env_name or "development").strip().lower()


def is_cluster_or_prod() -> bool:
    """Return True when local filesystem result fallbacks must be blocked."""
    return _environment_name() in _HARDENED_ENVS or bool(
        settings.get("KUBERNETES_SERVICE_HOST")
    )


def legacy_local_result_paths_enabled() -> bool:
    """Allow legacy local paths only with explicit opt-in outside hardened envs."""
    if is_cluster_or_prod():
        return False
    raw_flag = settings.get(LEGACY_LOCAL_RESULT_PATHS_FLAG, "")
    return str(raw_flag).strip().lower() in _TRUE_VALUES


def legacy_local_result_paths_disabled_message(use_case: str) -> str:
    """Human-readable operator error for disabled local result paths."""
    if is_cluster_or_prod():
        return (
            f"{use_case} disabilitato in production/cluster: usare result_id "
            "o blob/object storage come sorgente canonica"
        )
    return (
        f"{use_case} disabilitato: impostare "
        f"{LEGACY_LOCAL_RESULT_PATHS_FLAG}=1 solo in ambienti non cluster "
        "per abilitare il fallback legacy locale esplicito"
    )


def project_results_dir(project_id: str, *, root: Path | None = None) -> Path:
    return (root or settings.RESULTS_DIR) / project_id


def resolve_specific_file(
    project_id: str,
    filename: str,
    *,
    root: Path | None = None,
) -> Optional[Path]:
    results_dir = project_results_dir(project_id, root=root)
    if not results_dir.exists():
        return None
    safe_name = Path(filename).name
    target = assert_path_within(results_dir / safe_name, [results_dir])
    return target if target.exists() else None


def resolve_latest_file(project_id: str, *, root: Path | None = None) -> Optional[Path]:
    results_dir = project_results_dir(project_id, root=root)
    if not results_dir.exists():
        return None
    result_files = [path for path in results_dir.glob("*.*") if path.is_file()]
    if not result_files:
        return None
    return max(result_files, key=lambda result_file: result_file.stat().st_mtime)


def has_project_files(project_id: str, *, root: Path | None = None) -> bool:
    results_dir = project_results_dir(project_id, root=root)
    if not results_dir.exists():
        return False
    return any(path.is_file() for path in results_dir.glob("*.*"))


def delete_output_file(
    project_id: str,
    output_file: str,
    *,
    root: Path | None = None,
) -> bool:
    results_dir = project_results_dir(project_id, root=root)
    safe_name = Path(output_file).name
    target = assert_path_within(results_dir / safe_name, [results_dir])
    if target.is_file():
        target.unlink()
        return True
    return False


__all__ = [
    "LEGACY_LOCAL_RESULT_PATHS_FLAG",
    "delete_output_file",
    "has_project_files",
    "is_cluster_or_prod",
    "legacy_local_result_paths_disabled_message",
    "legacy_local_result_paths_enabled",
    "project_results_dir",
    "resolve_latest_file",
    "resolve_specific_file",
]
