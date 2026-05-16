"""Catalog view for archived executions of a schedule."""

from __future__ import annotations

import json
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

    def _result_file_for_log(
        self,
        entry: dict,
        *,
        project_id: str,
    ) -> tuple[str | None, RunFileInfo | None]:
        execution_info = entry.get("execution_info")
        if isinstance(execution_info, str):
            try:
                execution_info = json.loads(execution_info)
            except json.JSONDecodeError:
                execution_info = None
        if not isinstance(execution_info, dict):
            return None, None

        result_id = execution_info.get("result_id")
        if not result_id:
            return None, None

        try:
            from app.core.results import store as results_store

            row = results_store.get_result_row(str(result_id))
        except Exception:
            row = None

        if row is None or row.project_id != project_id:
            return str(result_id), None

        return str(result_id), RunFileInfo(
            name=row.filename or f"{result_id}.bin",
            size_bytes=row.size_bytes or 0,
            result_id=str(result_id),
        )

    def _run_info_from_log(
        self,
        entry: dict,
        *,
        project_id: str,
        schedule_id: str,
    ) -> RunInfo | None:
        ts = entry.get("timestamp_str") or entry.get("timestamp")
        if ts is None:
            return None
        if not isinstance(ts, str):
            ts = ts.strftime("%Y%m%d_%H%M%S")

        result_id, file_info = self._result_file_for_log(
            entry,
            project_id=project_id,
        )
        files = (file_info,) if file_info is not None else ()
        return RunInfo(
            timestamp=ts,
            path=self.runs_root_for(project_id, schedule_id) / ts,
            files=files,
            result_id=result_id,
        )

    def list_runs(self, project_id: str, schedule_id: str) -> list[RunInfo]:
        """List archived runs, newest first, including DB-only runs."""
        runs = list(self._archive_store.list_runs(project_id, schedule_id))
        runs_by_timestamp = {run.timestamp: run for run in runs}

        for entry in self._log_store.list_completed_runs(schedule_id):
            run = self._run_info_from_log(
                entry,
                project_id=project_id,
                schedule_id=schedule_id,
            )
            if run is None:
                continue
            existing = runs_by_timestamp.get(run.timestamp)
            if existing is not None and run.result_id is None:
                continue
            runs_by_timestamp[run.timestamp] = run

        runs = list(runs_by_timestamp.values())
        runs.sort(key=lambda run: run.timestamp, reverse=True)
        return runs

    def get_run(
        self,
        project_id: str,
        schedule_id: str,
        run_token: str,
    ) -> RunInfo:
        if not run_token:
            raise ValueError("run_token mancante")
        for run in self.list_runs(project_id, schedule_id):
            if run.timestamp == run_token:
                return run
        # Preserve legacy validation/error behavior for filesystem-only archives.
        self._archive_store.get_run_dir(project_id, schedule_id, run_token)
        raise FileNotFoundError(
            f"Run '{run_token}' non trovata per schedule '{schedule_id}'"
        )

    def latest_run(self, project_id: str, schedule_id: str) -> Optional[RunInfo]:
        runs = self.list_runs(project_id, schedule_id)
        return runs[0] if runs else None

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
