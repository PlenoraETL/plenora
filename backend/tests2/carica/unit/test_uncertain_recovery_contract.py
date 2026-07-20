from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.task_runtime import uncertain_recovery as recovery


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


class _Session:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def fetchone(self, sql, params):
        self.calls.append((sql, params))
        return self.row

    def fetchall(self, sql, params):
        self.calls.append((sql, params))
        return [self.row]

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return SimpleNamespace(rowcount=1)


def _install_session(monkeypatch, session):
    @contextmanager
    def _uow():
        yield session

    monkeypatch.setattr(recovery, "unit_of_work", _uow)


def test_confirm_completed_is_atomic_and_audited(monkeypatch):
    session = _Session({
        "run_id": "run-1",
        "status": "completion_uncertain",
        "module": "carica",
        "error_message": "response lost",
    })
    _install_session(monkeypatch, session)

    outcome = recovery.resolve_uncertain_carica_run(
        run_id="run-1",
        resolution="confirm_completed",
        operator_user_id="admin-1",
        note="Verificate 12 righe sulla destinazione",
    )

    assert outcome.status == "completed"
    update_runs = next(call for call in session.calls if "UPDATE runs SET status" in call[0])
    assert update_runs[1] == ("completed", "run-1")
    update_log = next(call for call in session.calls if "UPDATE log_runs SET status" in call[0])
    assert update_log[1][0] == "success"
    assert "admin-1" in update_log[1][1]
    assert "Verificate 12 righe" in update_log[1][1]


def test_allow_retry_rejects_non_uncertain_run(monkeypatch):
    session = _Session({
        "run_id": "run-2",
        "status": "failed",
        "module": "carica",
        "error_message": None,
    })
    _install_session(monkeypatch, session)

    with pytest.raises(recovery.UncertainRunConflict):
        recovery.resolve_uncertain_carica_run(
            run_id="run-2",
            resolution="allow_retry",
            operator_user_id="admin-1",
            note="Destinazione verificata vuota",
        )


def test_list_only_reads_explicit_uncertainty_barriers(monkeypatch):
    now = datetime.now(timezone.utc)
    session = _Session({
        "run_id": "run-3",
        "project_id": "project-1",
        "group_id": "tenant-1",
        "timestamp": now,
        "error_message": "unknown",
    })
    _install_session(monkeypatch, session)

    rows = recovery.list_uncertain_carica_runs(limit=10)

    assert rows[0].run_id == "run-3"
    assert "status = 'completion_uncertain'" in session.calls[0][0]
    assert "module = 'carica'" in session.calls[0][0]
