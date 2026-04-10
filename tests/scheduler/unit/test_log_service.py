"""
tests/scheduler/unit/test_log_service.py
=========================================
Unit test su ScheduleLogService metodi puri.

NON richiede backend o filesystem reale.

Copre:
  - _generate_log_id: formato, unicità, microsecondi
  - _get_day_dir: formato data
  - ScheduleLogEntry: validazione campi
  - LogStep: campi
  - ScheduleLogList: paginazione default
"""

import pytest
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import (
    ScheduleLogEntry, LogStep, ScheduleLogList
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = Path("/tmp/scheduler_logs")
    return svc


def make_log_entry(schedule_id="sched-001", event="PIPELINE_COMPLETED"):
    return ScheduleLogEntry(
        log_id="sched_001_090000_000000",
        schedule_id=schedule_id,
        timestamp=datetime.now(timezone.utc),
        event=event,
        triggered_by="cron",
    )


# =============================================================================
# _generate_log_id
# =============================================================================

class TestGenerateLogId:

    def test_formato_sched_prefix(self):
        svc = make_svc()
        ts = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
        log_id = svc._generate_log_id("schedule_abc123def456", ts)
        assert log_id.startswith("sched_")

    def test_contiene_timestamp(self):
        svc = make_svc()
        ts = datetime(2026, 3, 15, 9, 30, 45, tzinfo=timezone.utc)
        log_id = svc._generate_log_id("schedule_abc123", ts)
        assert "093045" in log_id

    def test_unicita_microsecondi(self):
        """Due timestamp diversi per microsecondo → ID diversi."""
        svc = make_svc()
        ts1 = datetime(2026, 3, 15, 9, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 15, 9, 0, 0, 1, tzinfo=timezone.utc)
        id1 = svc._generate_log_id("schedule_abc", ts1)
        id2 = svc._generate_log_id("schedule_abc", ts2)
        assert id1 != id2

    def test_schedule_id_troncato(self):
        """schedule_id molto lungo → troncato nel log_id."""
        svc = make_svc()
        ts = datetime.now(timezone.utc)
        long_id = "schedule_" + "x" * 100
        log_id = svc._generate_log_id(long_id, ts)
        # Non deve essere lunghissimo
        assert len(log_id) < 60

    def test_prefix_schedule_rimosso(self):
        """'schedule_' rimosso dallo short_id."""
        svc = make_svc()
        ts = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
        log_id = svc._generate_log_id("schedule_abc123", ts)
        # Il log_id non deve avere "schedule_" duplicato
        assert "schedule_schedule_" not in log_id


# =============================================================================
# _get_day_dir
# =============================================================================

class TestGetDayDir:

    def test_formato_yyyy_mm_dd(self):
        svc = make_svc()
        dt = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
        day_dir = svc._get_day_dir(dt)
        assert day_dir.name == "2026-03-15"

    def test_directory_sotto_logs_dir(self):
        svc = make_svc()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        day_dir = svc._get_day_dir(dt)
        assert svc.logs_dir in day_dir.parents or day_dir.parent == svc.logs_dir

    def test_primo_gennaio(self):
        svc = make_svc()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert svc._get_day_dir(dt).name == "2026-01-01"

    def test_ultimo_dicembre(self):
        svc = make_svc()
        dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        assert svc._get_day_dir(dt).name == "2026-12-31"


# =============================================================================
# ScheduleLogEntry
# =============================================================================

class TestScheduleLogEntry:

    def test_campi_base(self):
        entry = make_log_entry()
        assert entry.schedule_id == "sched-001"
        assert entry.event == "PIPELINE_COMPLETED"
        assert entry.module == "SCHEDULER"

    def test_level_default_info(self):
        entry = make_log_entry()
        assert entry.level == "INFO"

    def test_triggered_by_default_cron(self):
        entry = make_log_entry()
        assert entry.triggered_by == "cron"

    def test_triggered_by_manual(self):
        entry = ScheduleLogEntry(
            log_id="test", schedule_id="s-1",
            timestamp=datetime.now(timezone.utc),
            event="SCHEDULE_TRIGGERED",
            triggered_by="manual",
        )
        assert entry.triggered_by == "manual"

    def test_steps_default_vuoto(self):
        entry = make_log_entry()
        assert entry.steps == []

    def test_error_message_default_none(self):
        entry = make_log_entry()
        assert entry.error_message is None

    def test_total_duration_default_zero(self):
        entry = make_log_entry()
        assert entry.total_duration_ms == 0

    def test_tutti_gli_eventi(self):
        eventi = [
            "SCHEDULE_TRIGGERED", "SCHEDULE_QUEUED", "SCHEDULE_SKIPPED",
            "PRE_CHECK_PASSED", "PRE_CHECK_FAILED", "PIPELINE_STARTED",
            "PIPELINE_COMPLETED", "PIPELINE_PARTIAL", "PIPELINE_FAILED",
            "RETRY_SCHEDULED", "RETRY_EXHAUSTED"
        ]
        for ev in eventi:
            entry = make_log_entry(event=ev)
            assert entry.event == ev


# =============================================================================
# LogStep
# =============================================================================

class TestLogStep:

    def test_step_estrai(self):
        step = LogStep(step="ESTRAI", status="success", duration_ms=1500, rows=100)
        assert step.step == "ESTRAI"
        assert step.status == "success"
        assert step.rows == 100

    def test_step_carica_con_campi_extra(self):
        step = LogStep(
            step="CARICA", status="partial",
            rows_inserted=80, rows_skipped=20,
            destination="postgresql://test/immobili"
        )
        assert step.rows_inserted == 80
        assert step.rows_skipped == 20

    def test_duration_default_zero(self):
        step = LogStep(step="OUTPUT", status="success")
        assert step.duration_ms == 0

    def test_tutti_gli_steps(self):
        for s in ["ESTRAI", "MANIPOLA", "ARRICCHISCI", "OUTPUT", "CARICA"]:
            step = LogStep(step=s, status="success")
            assert step.step == s

    def test_tutti_gli_status(self):
        for st in ["success", "partial", "failed", "skipped"]:
            step = LogStep(step="ESTRAI", status=st)
            assert step.status == st


# =============================================================================
# ScheduleLogList
# =============================================================================

class TestScheduleLogList:

    def test_lista_vuota(self):
        ll = ScheduleLogList(logs=[], total=0)
        assert ll.total == 0
        assert ll.logs == []

    def test_paginazione_default(self):
        ll = ScheduleLogList(logs=[])
        assert ll.page == 1
        assert ll.page_size == 20

    def test_lista_con_entries(self):
        entries = [make_log_entry(), make_log_entry("sched-002")]
        ll = ScheduleLogList(logs=entries, total=2)
        assert ll.total == 2
        assert len(ll.logs) == 2
