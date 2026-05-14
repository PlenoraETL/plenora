"""Contracts for the scheduled-run DB log boundary."""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_run_log_store_reads_completed_and_partial_events(monkeypatch) -> None:
    from app.core.scheduler import run_log_store

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_list_schedule_logs(*, schedule_id: str, events: tuple[str, ...]):
        calls.append((schedule_id, events))
        return [{"timestamp_str": "20260101_100000"}]

    monkeypatch.setattr(
        run_log_store.scheduler_db,
        "list_schedule_logs",
        fake_list_schedule_logs,
    )

    rows = run_log_store.ScheduleRunLogStore().list_completed_runs("sched-1")

    assert rows == [{"timestamp_str": "20260101_100000"}]
    assert calls == [("sched-1", ("PIPELINE_COMPLETED", "PIPELINE_PARTIAL"))]


def test_run_log_store_db_failure_is_empty_best_effort(monkeypatch) -> None:
    from app.core.scheduler import run_log_store

    def fail_list_schedule_logs(*, schedule_id: str, events: tuple[str, ...]):
        raise RuntimeError("db offline")

    monkeypatch.setattr(
        run_log_store.scheduler_db,
        "list_schedule_logs",
        fail_list_schedule_logs,
    )

    assert run_log_store.ScheduleRunLogStore().list_completed_runs("sched-1") == []
