"""Catalog view for scheduled run history.

Post-cleanup `ScheduleArtifactStore`: il catalog NON legge piu' dal
filesystem archive (`RESULTS_DIR/{project_id}/runs/{schedule}/{ts}/`).
Le run sono enumerate dal DB log (`schedule_logs` events
PIPELINE_COMPLETED / PIPELINE_PARTIAL) + result_id -> DB blob / S3.

Le HTTP download endpoint passano sempre per `result_id`; le run
storiche archived-only-on-filesystem non sono piu' supportate.
"""

from __future__ import annotations

import json

from app.core.scheduler.run_archive_store import (
    RunFileInfo,
    RunInfo,
    schedule_short,
)
from app.core.scheduler.run_log_store import ScheduleRunLogStore


class ScheduleRunsCatalog:
    """DB-backed view of scheduled runs.

    Enumera le run dal `schedule_logs` event stream, associa `result_id`
    quando presente, e ritorna `RunInfo` con file info derivati dalla
    row `results`. Niente filesystem reads.
    """

    def __init__(
        self,
        *,
        log_store: ScheduleRunLogStore | None = None,
    ) -> None:
        self._log_store = log_store or ScheduleRunLogStore()

    @staticmethod
    def _schedule_short(schedule_id: str) -> str:
        return schedule_short(schedule_id)

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
            files=files,
            result_id=result_id,
        )

    def list_runs(self, project_id: str, schedule_id: str) -> list[RunInfo]:
        """List run history dal DB log, newest first."""
        runs_by_timestamp: dict[str, RunInfo] = {}
        for entry in self._log_store.list_completed_runs(schedule_id):
            run = self._run_info_from_log(entry, project_id=project_id)
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
        raise FileNotFoundError(
            f"Run '{run_token}' non trovata per schedule '{schedule_id}'"
        )

    def latest_run(self, project_id: str, schedule_id: str) -> RunInfo | None:
        runs = self.list_runs(project_id, schedule_id)
        return runs[0] if runs else None


__all__ = ["RunFileInfo", "RunInfo", "ScheduleRunsCatalog"]
