"""Project result output-file cleanup boundary."""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.core.io.local_cleanup import delete_local_file
from app.shared.resources.path_policy import assert_path_within


def project_results_dir(project_id: str, *, root: Path | None = None) -> Path:
    return (root or settings.RESULTS_DIR) / project_id


def delete_project_output_file(
    project_id: str,
    output_file: str,
    *,
    root: Path | None = None,
) -> bool:
    results_dir = project_results_dir(project_id, root=root)
    safe_name = Path(output_file).name
    target = assert_path_within(results_dir / safe_name, [results_dir])
    if target.is_file():
        delete_local_file(target)
        return True
    return False


__all__ = [
    "delete_project_output_file",
    "project_results_dir",
]
