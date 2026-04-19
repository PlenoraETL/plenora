"""Contract test — ScheduleLogService usa SOLO DB (`plenora.schedule_logs`)
come storage (FS-B3 post-migration).

Blinda il contratto "zero FS": write/read/delete passano per
`pg_connection`, niente `data/scheduler_logs/YYYY-MM-DD/*.json`.

Test cover:
  - write_log → INSERT con i campi corretti
  - get_log / get_logs / get_latest_log → SELECT col WHERE atteso
  - delete_logs_before / delete_schedule_logs → DELETE with args
  - Paginazione + status_filter
  - Nessuna dipendenza da filesystem
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _install_fake_pg(monkeypatch):
    """Sostituisce `app.db.pg_connection.execute/fetchone/fetchall` con
    mock capturabili. Ritorna un `state` dict con:
      - `rows`: lista di row dict fake (popolabile dal test)
      - `executions`: lista di (sql, args) chiamate da execute()
      - `execute_rowcount`: rowcount default ritornato da execute
    """
    import app.db.pg_connection as pg

    state = {
        "rows": [],
        "executions": [],
        "execute_rowcount": 0,
    }

    def fake_execute(sql, args=None):
        state["executions"].append((sql, args))
        cur = MagicMock()
        cur.rowcount = state["execute_rowcount"]
        return cur

    def fake_fetchone(sql, args=None):
        state["executions"].append(("FETCHONE", sql, args))
        return state["rows"][0] if state["rows"] else None

    def fake_fetchall(sql, args=None):
        state["executions"].append(("FETCHALL", sql, args))
        return list(state["rows"])

    monkeypatch.setattr(pg, "execute", fake_execute)
    monkeypatch.setattr(pg, "fetchone", fake_fetchone)
    monkeypatch.setattr(pg, "fetchall", fake_fetchall)
    return state


def _make_entry(svc, schedule_id: str = "schedule_abc123"):
    """Helper: ScheduleLogEntry con 0 steps, zero errori."""
    return svc.create_log_entry(
        schedule_id=schedule_id,
        event="PIPELINE_COMPLETED",
        triggered_by="cron",
        total_duration_ms=71000,
    )


# =============================================================================
# 1. write_log → INSERT
# =============================================================================


def test_write_log_issues_insert(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    svc = ScheduleLogService()
    entry = _make_entry(svc)

    returned = svc.write_log(entry)

    # Contract: ritorna log_id (non piu' filepath)
    assert returned == entry.log_id
    # Una sola INSERT
    assert len(state["executions"]) == 1
    sql, args = state["executions"][0]
    assert "INSERT INTO plenora.schedule_logs" in sql
    assert args[0] == entry.log_id
    assert args[1] == entry.schedule_id
    assert args[2] == entry.timestamp


def test_write_log_serializes_steps_as_jsonb(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService
    from app.core.scheduler.models import LogStep

    state = _install_fake_pg(monkeypatch)
    svc = ScheduleLogService()
    entry = svc.create_log_entry(
        schedule_id="schedule_xyz",
        event="PIPELINE_COMPLETED",
        steps=[
            LogStep(step="ESTRAI", status="success", duration_ms=1000),
            LogStep(step="MANIPOLA", status="success", duration_ms=2000),
        ],
        total_duration_ms=3000,
    )

    svc.write_log(entry)

    _, args = state["executions"][0]
    # args[6] = steps (JSON string passato a ?::jsonb cast)
    import json
    steps = json.loads(args[6])
    assert len(steps) == 2
    assert steps[0]["step"] == "ESTRAI"
    assert steps[1]["duration_ms"] == 2000


# =============================================================================
# 2. get_log → SELECT WHERE log_id
# =============================================================================


def test_get_log_by_id_returns_entry(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    ts = datetime(2026, 4, 18, 2, 0, 0, tzinfo=timezone.utc)
    state["rows"] = [{
        "log_id": "sched_abc_020000_000001",
        "schedule_id": "schedule_abc",
        "timestamp": ts,
        "level": "INFO",
        "event": "PIPELINE_COMPLETED",
        "triggered_by": "cron",
        "steps": [],
        "total_duration_ms": 71000,
        "error_message": None,
        "schedule_info": None,
        "pipeline_info": None,
        "execution_info": None,
    }]

    svc = ScheduleLogService()
    out = svc.get_log("sched_abc_020000_000001")

    assert out is not None
    assert out.log_id == "sched_abc_020000_000001"
    assert out.event == "PIPELINE_COMPLETED"
    assert out.total_duration_ms == 71000


def test_get_log_nonexistent_returns_none(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    _install_fake_pg(monkeypatch)  # rows=[] default

    svc = ScheduleLogService()
    assert svc.get_log("sched_missing") is None


# =============================================================================
# 3. get_logs → paginato, ordinato timestamp DESC
# =============================================================================


def test_get_logs_paginated(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    # COUNT(*) → 42, poi SELECT LIMIT/OFFSET → 20 rows
    # Il fake returna `state["rows"]` sia per fetchone che fetchall.
    # Per gestire count/select diversi, usiamo una side_effect.
    call_idx = {"n": 0}

    def fake_fetchone(sql, args=None):
        call_idx["n"] += 1
        if "COUNT" in sql:
            return {"n": 42}
        return None

    def fake_fetchall(sql, args=None):
        return [
            {
                "log_id": f"sched_abc_{i:06d}",
                "schedule_id": "schedule_abc",
                "timestamp": datetime(2026, 4, 18, 2, 0, i, tzinfo=timezone.utc),
                "level": "INFO",
                "event": "PIPELINE_COMPLETED",
                "triggered_by": "cron",
                "steps": [],
                "total_duration_ms": 1000,
                "error_message": None,
                "schedule_info": None,
                "pipeline_info": None,
                "execution_info": None,
            }
            for i in range(20)
        ]

    import app.db.pg_connection as pg
    monkeypatch.setattr(pg, "fetchone", fake_fetchone)
    monkeypatch.setattr(pg, "fetchall", fake_fetchall)

    svc = ScheduleLogService()
    result = svc.get_logs("schedule_abc", page=1, page_size=20)

    assert result.total == 42
    assert result.page == 1
    assert result.page_size == 20
    assert len(result.logs) == 20


def test_get_logs_status_filter_matches_pipeline_event(monkeypatch):
    """status_filter='success' deve matchare event='PIPELINE_SUCCESS'."""
    from app.core.scheduler.log_service import ScheduleLogService

    captured_args = {}

    def fake_fetchone(sql, args):
        if "COUNT" in sql:
            captured_args["count"] = (sql, args)
            return {"n": 0}
        return None

    def fake_fetchall(sql, args):
        captured_args["page"] = (sql, args)
        return []

    import app.db.pg_connection as pg
    monkeypatch.setattr(pg, "fetchone", fake_fetchone)
    monkeypatch.setattr(pg, "fetchall", fake_fetchall)

    svc = ScheduleLogService()
    svc.get_logs("schedule_x", status_filter="success")

    # Il filtro deve aver aggiunto "event = 'PIPELINE_SUCCESS'" come arg
    assert "AND event = ?" in captured_args["count"][0]
    assert captured_args["count"][1] == ("schedule_x", "PIPELINE_SUCCESS")


# =============================================================================
# 4. get_latest_log → ORDER BY DESC LIMIT 1
# =============================================================================


def test_get_latest_log_returns_first(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    ts = datetime(2026, 4, 18, 10, 30, 0, tzinfo=timezone.utc)
    state["rows"] = [{
        "log_id": "sched_latest",
        "schedule_id": "schedule_x",
        "timestamp": ts,
        "level": "INFO",
        "event": "PIPELINE_COMPLETED",
        "triggered_by": "manual",
        "steps": [],
        "total_duration_ms": 500,
        "error_message": None,
        "schedule_info": None,
        "pipeline_info": None,
        "execution_info": None,
    }]

    svc = ScheduleLogService()
    out = svc.get_latest_log("schedule_x")

    assert out is not None
    assert out.log_id == "sched_latest"
    # Query deve contenere ORDER BY timestamp DESC LIMIT 1
    executed = state["executions"][-1]
    assert "ORDER BY timestamp DESC LIMIT 1" in executed[1]


# =============================================================================
# 5. delete_* → DELETE
# =============================================================================


def test_delete_schedule_logs_issues_delete(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    state["execute_rowcount"] = 7

    svc = ScheduleLogService()
    deleted = svc.delete_schedule_logs("schedule_abc")

    assert deleted == 7
    sql, args = state["executions"][0]
    assert "DELETE FROM plenora.schedule_logs" in sql
    assert "WHERE schedule_id = ?" in sql
    assert args == ("schedule_abc",)


def test_delete_logs_before_uses_timestamp_filter(monkeypatch):
    from app.core.scheduler.log_service import ScheduleLogService

    state = _install_fake_pg(monkeypatch)
    state["execute_rowcount"] = 150

    svc = ScheduleLogService()
    deleted = svc.delete_logs_before(date(2026, 1, 1))

    assert deleted == 150
    sql, args = state["executions"][0]
    assert "DELETE FROM plenora.schedule_logs" in sql
    assert "WHERE timestamp < ?" in sql
    # arg e' datetime UTC costruito da date + 00:00:00
    assert args[0].date() == date(2026, 1, 1)
    assert args[0].hour == 0
    assert args[0].tzinfo is timezone.utc


# =============================================================================
# 6. FS-B3 invariant: nessuna dipendenza filesystem
# =============================================================================


def test_service_does_not_use_scheduler_logs_dir(monkeypatch):
    """Post FS-B3: `settings.SCHEDULER_LOGS_DIR` NON dovrebbe essere
    letto dal service. La rimozione di quella property in config.py
    blocca alla radice chiunque tenti di ri-introdurre FS storage.
    """
    from app.config import settings
    # La property e' stata rimossa → hasattr comunque torna True per via
    # di eventuali __getattr__ custom. Il test contratto: NON esiste
    # tra gli attributi concreti della classe.
    cls_attrs = set()
    for base in type(settings).__mro__:
        cls_attrs |= set(vars(base).keys())
    assert "SCHEDULER_LOGS_DIR" not in cls_attrs


def test_service_does_not_reference_logs_dir_attribute(monkeypatch):
    """Il service non espone piu' `logs_dir` (attr legacy del layout FS)."""
    from app.core.scheduler.log_service import ScheduleLogService
    svc = ScheduleLogService()
    assert not hasattr(svc, "logs_dir")
