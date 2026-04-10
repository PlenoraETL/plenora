"""
tests/scheduler/unit/test_log_service_extra.py
================================================
Test aggiuntivi ScheduleLogService — write, get, get_logs, delete.
Usa filesystem reale su tempdir.
"""

import pytest
import json
import tempfile
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit

from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import ScheduleLogEntry, LogStep


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc(tmpdir: Path) -> ScheduleLogService:
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = tmpdir
    return svc


def make_entry(schedule_id="sched-001", event="PIPELINE_COMPLETED", steps=None):
    now = datetime.now(timezone.utc)
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = Path("/tmp")
    log_id = svc._generate_log_id(schedule_id, now)
    return ScheduleLogEntry(
        log_id=log_id,
        schedule_id=schedule_id,
        timestamp=now,
        event=event,
        triggered_by="cron",
        steps=steps or [],
        total_duration_ms=1500,
    )


# =============================================================================
# write_log
# =============================================================================

class TestWriteLog:

    def test_write_crea_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            path = svc.write_log(entry)
            assert path is not None
            assert len(path) > 0

    def test_write_crea_directory_giornaliera(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            svc.write_log(entry)
            # Verifica che esista almeno una directory data
            dirs = list(Path(tmpdir).iterdir())
            assert len(dirs) == 1
            assert dirs[0].is_dir()

    def test_write_file_json_valido(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            svc.write_log(entry)
            # Trova il file
            day_dirs = list(Path(tmpdir).iterdir())
            files = list(day_dirs[0].iterdir())
            assert len(files) == 1
            content = json.loads(files[0].read_text(encoding="utf-8"))
            assert content["schedule_id"] == "sched-001"

    def test_write_path_relativo_restituito(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            path = svc.write_log(entry)
            # Deve essere relativo: YYYY-MM-DD/sched_xxx.json
            assert "/" in path or "\\" in path
            assert path.endswith(".json")

    def test_write_due_log_stesso_schedule(self):
        """Due log con log_id distinti → 2 file separati."""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry1 = ScheduleLogEntry(
                log_id="sched_001_090000_000001",
                schedule_id="sched-001",
                timestamp=datetime.now(timezone.utc),
                event="PIPELINE_COMPLETED",
            )
            entry2 = ScheduleLogEntry(
                log_id="sched_001_090000_000002",
                schedule_id="sched-001",
                timestamp=datetime.now(timezone.utc),
                event="PIPELINE_FAILED",
            )
            svc.write_log(entry1)
            svc.write_log(entry2)
            day_dirs = list(Path(tmpdir).iterdir())
            files = list(day_dirs[0].iterdir())
            assert len(files) == 2

    def test_write_con_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            steps = [
                LogStep(step="ESTRAI", status="success", duration_ms=1000, rows=100),
                LogStep(step="OUTPUT", status="success", duration_ms=200),
            ]
            entry = make_entry(steps=steps)
            svc.write_log(entry)
            day_dirs = list(Path(tmpdir).iterdir())
            files = list(day_dirs[0].iterdir())
            content = json.loads(files[0].read_text(encoding="utf-8"))
            assert len(content["steps"]) == 2


# =============================================================================
# get_log
# =============================================================================

class TestGetLog:

    def test_get_log_esistente(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            svc.write_log(entry)
            result = svc.get_log(entry.log_id)
            assert result is not None
            assert result.log_id == entry.log_id

    def test_get_log_inesistente_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            result = svc.get_log("sched_inesistente_000000_000000")
            assert result is None

    def test_get_log_schedule_id_corretto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry("sched-42")
            svc.write_log(entry)
            result = svc.get_log(entry.log_id)
            assert result.schedule_id == "sched-42"

    def test_get_log_event_corretto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry(event="PIPELINE_FAILED")
            svc.write_log(entry)
            result = svc.get_log(entry.log_id)
            assert result.event == "PIPELINE_FAILED"


# =============================================================================
# delete_logs_before
# =============================================================================

class TestDeleteLogsBefore:

    def test_delete_directory_vecchia(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            # Crea directory vecchia manualmente
            old_dir = Path(tmpdir) / "2025-01-01"
            old_dir.mkdir()
            (old_dir / "sched_test_010000_000000.json").write_text("{}")
            deleted = svc.delete_logs_before(date(2026, 1, 1))
            assert deleted >= 1

    def test_delete_non_cancella_recenti(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry()
            svc.write_log(entry)
            # Non cancellare nulla (data futura)
            deleted = svc.delete_logs_before(date(2020, 1, 1))
            assert deleted == 0

    def test_delete_ritorna_conteggio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            old_dir = Path(tmpdir) / "2025-06-15"
            old_dir.mkdir()
            for i in range(3):
                (old_dir / f"sched_test_{i:06d}_000000.json").write_text("{}")
            deleted = svc.delete_logs_before(date(2026, 1, 1))
            assert deleted == 3


# =============================================================================
# delete_schedule_logs
# =============================================================================

class TestDeleteScheduleLogs:

    def test_delete_schedule_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry = make_entry("sched-001")
            svc.write_log(entry)
            deleted = svc.delete_schedule_logs("sched-001")
            assert deleted >= 1

    def test_delete_schedule_logs_inesistente_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            deleted = svc.delete_schedule_logs("sched-non-esiste")
            assert deleted == 0

    def test_delete_non_rimuove_altri_schedule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_svc(Path(tmpdir))
            entry1 = make_entry("sched-001")
            entry2 = make_entry("sched-002")
            svc.write_log(entry1)
            svc.write_log(entry2)
            svc.delete_schedule_logs("sched-001")
            # sched-002 deve ancora esistere
            result = svc.get_log(entry2.log_id)
            assert result is not None
