"""Contract tests for the orchestrator execution-log DB boundary."""

from __future__ import annotations

from typing import List, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _capture_execute(monkeypatch) -> List[Tuple[str, tuple]]:
    calls: List[Tuple[str, tuple]] = []

    def _fake_execute(sql, params=()):
        calls.append((sql, tuple(params)))

    from app.core.orchestrator import execution_logs

    monkeypatch.setattr(execution_logs, "execute", _fake_execute)
    return calls


def _capture_fetchall(monkeypatch, *, response):
    calls: List[Tuple[str, tuple]] = []

    def _fake_fetchall(sql, params=()):
        calls.append((sql, tuple(params)))
        return response

    from app.core.orchestrator import execution_logs

    monkeypatch.setattr(execution_logs, "fetchall", _fake_fetchall)
    return calls


def test_log_execution_inserts_execution_log_row(monkeypatch):
    from app.core.orchestrator.execution_logs import log_execution

    calls = _capture_execute(monkeypatch)
    out = log_execution(
        "log-1",
        "group-1",
        project_id="project-1",
        schedule_id="schedule-1",
        status="running",
        message="started",
        triggered_by="user-1",
    )

    assert out == {"id": "log-1", "status": "running"}
    assert len(calls) == 1
    sql, params = calls[0]
    assert sql.startswith("INSERT INTO execution_logs")
    assert params[:5] == (
        "log-1",
        "group-1",
        "project-1",
        "schedule-1",
        "running",
    )
    assert params[6:] == ("started", "user-1")


def test_update_log_updates_execution_status(monkeypatch):
    from app.core.orchestrator.execution_logs import update_log

    calls = _capture_execute(monkeypatch)
    update_log(
        "log-1",
        "success",
        finished_at="2026-05-07T12:00:00+00:00",
        duration_s=1.5,
        rows_count=42,
        message="done",
        error=None,
    )

    assert calls == [
        (
            "UPDATE execution_logs SET status=?, finished_at=?, duration_s=?, "
            "rows_count=?, message=?, error=? WHERE id=?",
            (
                "success",
                "2026-05-07T12:00:00+00:00",
                1.5,
                42,
                "done",
                None,
                "log-1",
            ),
        )
    ]


def test_list_logs_applies_filters_and_bounds(monkeypatch):
    from app.core.orchestrator.execution_logs import list_logs

    rows = [{"id": "log-1", "status": "success"}]
    calls = _capture_fetchall(monkeypatch, response=rows)
    out = list_logs(
        project_id="project-1",
        group_id="group-1",
        limit=999,
        offset=-10,
    )

    assert out == rows
    assert calls == [
        (
            "SELECT * FROM execution_logs WHERE project_id = ? AND group_id = ? "
            "ORDER BY started_at DESC LIMIT ? OFFSET ?",
            ("project-1", "group-1", 500, 0),
        )
    ]


def test_results_db_service_keeps_execution_log_compat_exports():
    import app.db.results_db_service as results_db
    from app.core.orchestrator import execution_logs

    assert results_db.log_execution is execution_logs.log_execution
    assert results_db.update_log is execution_logs.update_log
    assert results_db.list_logs is execution_logs.list_logs
