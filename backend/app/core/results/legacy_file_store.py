"""Legacy filesystem result boundary.

This keeps pre-result-store files under RESULTS_DIR isolated from API routers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings
from app.shared.resources.path_policy import assert_path_within


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
    "delete_output_file",
    "project_results_dir",
    "resolve_latest_file",
    "resolve_specific_file",
]
