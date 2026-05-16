"""Filesystem reader for archived scheduled-run artifacts."""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from app.shared.resources.path_policy import assert_path_within


@dataclass(frozen=True)
class RunFileInfo:
    """Metadata for a file inside an archived run directory."""

    name: str
    size_bytes: int
    result_id: Optional[str] = None


@dataclass(frozen=True)
class RunInfo:
    """Snapshot of an archived scheduled run."""

    timestamp: str
    path: Optional[Path]
    files: Tuple[RunFileInfo, ...] = field(default_factory=tuple)
    result_id: Optional[str] = None

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


_RUN_TOKEN_PATTERN = r"^[0-9A-Za-z_-]{1,32}$"


def schedule_short(schedule_id: str) -> str:
    """Return the stable archive folder token for a schedule id."""
    return schedule_id.replace("schedule_", "")[:12]


class ScheduleRunArchiveStore:
    """Read-only filesystem boundary for schedule run archives."""

    def __init__(self, results_dir: Optional[Path] = None) -> None:
        if results_dir is None:
            from app.config import settings

            self._results_dir = Path(settings.RESULTS_DIR)
        else:
            self._results_dir = Path(results_dir)

    def runs_root_for(self, project_id: str, schedule_id: str) -> Path:
        return (
            self._results_dir / project_id / "runs" / schedule_short(schedule_id)
        ).resolve()

    def list_runs(self, project_id: str, schedule_id: str) -> list[RunInfo]:
        runs_root = self.runs_root_for(project_id, schedule_id)
        if not runs_root.exists() or not runs_root.is_dir():
            return []

        runs: list[RunInfo] = []
        for run_folder in sorted(runs_root.iterdir(), reverse=True):
            if not run_folder.is_dir():
                continue
            files = tuple(
                RunFileInfo(name=f.name, size_bytes=f.stat().st_size)
                for f in run_folder.iterdir()
                if f.is_file()
            )
            runs.append(
                RunInfo(
                    timestamp=run_folder.name,
                    path=run_folder,
                    files=files,
                )
            )
        return runs

    def get_run_dir(
        self,
        project_id: str,
        schedule_id: str,
        run_token: str,
    ) -> Path:
        if not run_token or not re.match(_RUN_TOKEN_PATTERN, run_token):
            raise ValueError(f"run_token malformato: {run_token!r}")

        runs_root = self.runs_root_for(project_id, schedule_id)
        try:
            run_dir = assert_path_within(runs_root / run_token, [runs_root])
        except ValueError as exc:
            raise ValueError(f"run_token non valido: {run_token!r}") from exc

        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(
                f"Run '{run_token}' non trovata per schedule '{schedule_id}'"
            )
        return run_dir

    def latest_run_dir(self, project_id: str, schedule_id: str) -> Optional[Path]:
        runs_root = self.runs_root_for(project_id, schedule_id)
        if not runs_root.exists() or not runs_root.is_dir():
            return None

        run_dirs = [d for d in runs_root.iterdir() if d.is_dir()]
        if not run_dirs:
            return None

        run_dirs.sort(key=lambda d: (d.name, d.stat().st_mtime), reverse=True)
        return run_dirs[0]

    def first_file_in(self, run_dir: Path) -> Optional[Path]:
        if not run_dir.exists() or not run_dir.is_dir():
            return None
        for file_path in run_dir.iterdir():
            if file_path.is_file():
                return file_path
        return None


__all__ = [
    "RunFileInfo",
    "RunInfo",
    "ScheduleRunArchiveStore",
    "schedule_short",
]
