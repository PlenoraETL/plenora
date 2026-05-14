"""Catalog view for archived executions of a schedule."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.scheduler.run_archive_store import (
    RunFileInfo,
    RunInfo,
    ScheduleRunArchiveStore,
    schedule_short,
)
from app.core.scheduler.run_log_store import ScheduleRunLogStore


class ScheduleRunsCatalog:
    """Merge filesystem archived runs with DB-only scheduled run logs."""

    def __init__(
        self,
        results_dir: Optional[Path] = None,
        *,
        archive_store: ScheduleRunArchiveStore | None = None,
        log_store: ScheduleRunLogStore | None = None,
    ) -> None:
        self._archive_store = archive_store or ScheduleRunArchiveStore(results_dir)
        self._log_store = log_store or ScheduleRunLogStore()

    @staticmethod
    def _schedule_short(schedule_id: str) -> str:
        return schedule_short(schedule_id)

    def runs_root_for(self, project_id: str, schedule_id: str) -> Path:
        return self._archive_store.runs_root_for(project_id, schedule_id)

    def list_runs(self, project_id: str, schedule_id: str) -> list[RunInfo]:
        """List archived runs, newest first, including DB-only runs."""
        runs = list(self._archive_store.list_runs(project_id, schedule_id))
        seen_timestamps = {run.timestamp for run in runs}
        runs_root = self.runs_root_for(project_id, schedule_id)

        for entry in self._log_store.list_completed_runs(schedule_id):
            ts = entry.get("timestamp_str") or entry.get("timestamp")
            if ts is None:
                continue
            if not isinstance(ts, str):
                ts = ts.strftime("%Y%m%d_%H%M%S")
            if ts in seen_timestamps:
                continue
            seen_timestamps.add(ts)
            runs.append(RunInfo(timestamp=ts, path=runs_root / ts, files=()))

        runs.sort(key=lambda run: run.timestamp, reverse=True)
        return runs

    def get_run_dir(
        self,
        project_id: str,
        schedule_id: str,
        run_token: str,
    ) -> Path:
        return self._archive_store.get_run_dir(project_id, schedule_id, run_token)

    def latest_run_dir(self, project_id: str, schedule_id: str) -> Optional[Path]:
        return self._archive_store.latest_run_dir(project_id, schedule_id)

    def first_file_in(self, run_dir: Path) -> Optional[Path]:
        return self._archive_store.first_file_in(run_dir)


__all__ = ["RunFileInfo", "RunInfo", "ScheduleRunsCatalog"]
