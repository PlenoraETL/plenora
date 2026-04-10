"""
tests/scheduler/unit/test_scheduler_coverage.py
=================================================
Test mirati a coprire metodi NON ancora testati:

  - execution_manager._execute_carica: file non trovato, df vuoto, success, failure
  - log_service.get_logs: paginazione, filtro, dir vuota
  - log_service.get_latest_log: con/senza log
  - log_service.create_log_entry: tutti i parametri
  - schedule_service.update_schedule: vari campi
  - concurrency_manager._get: TTL scaduto, key inesistente
  - concurrency_manager._set_nx: chiave esistente
  - router: endpoint /runs, PUT update
"""

import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.concurrency_manager import ConcurrencyManager
from app.core.scheduler.execution_manager import ExecutionManager
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
    ScheduleDestination, ScheduleUpdate, LogStep, ScheduleLogEntry,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_config(with_dest=False):
    now = datetime.now(timezone.utc)
    dest = None
    if with_dest:
        dest = ScheduleDestination(connection_id="conn-1", table_name="test_tbl")
    return ScheduleConfig(
        id="sched-001", created_at=now, updated_at=now,
        pipeline=SchedulePipeline(project_id="proj-1", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
        destination=dest,
    )


def make_log_svc(tmpdir: Path) -> ScheduleLogService:
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = tmpdir
    return svc


def make_entry(schedule_id="sched-001", event="PIPELINE_COMPLETED"):
    now = datetime.now(timezone.utc)
    svc = ScheduleLogService.__new__(ScheduleLogService)
    svc.logs_dir = Path("/tmp")
    log_id = svc._generate_log_id(schedule_id, now)
    return ScheduleLogEntry(
        log_id=log_id, schedule_id=schedule_id,
        timestamp=now, event=event,
    )


# =============================================================================
# execution_manager._execute_carica
# =============================================================================

class TestExecuteCarica:

    def make_mgr(self):
        mgr = ExecutionManager.__new__(ExecutionManager)
        mgr.schedule_service = MagicMock()
        mgr.log_service = MagicMock()
        mgr.concurrency = MagicMock()
        mgr.policy = MagicMock()
        mgr.log_service.create_log_entry.return_value = MagicMock()
        mgr.log_service.write_log.return_value = "log.json"
        return mgr

    def test_file_non_trovato_failure(self):
        mgr = self.make_mgr()
        config = make_config(with_dest=True)
        with patch("app.utils.result_store.get_result_as_tempfile",
                   return_value=None):
            result = mgr._execute_carica(config, {"output_path": "/non/esiste.csv"})
        assert result["success"] is False
        assert "non trovato" in result["error"].lower()

    def test_df_vuoto_failure(self):
        mgr = self.make_mgr()
        config = make_config(with_dest=True)
        import tempfile as tf, pandas as pd
        with tf.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"id,nome\n")  # solo header, nessuna riga
            tmp_path = Path(f.name)
        try:
            with patch("app.utils.result_store.get_result_as_tempfile",
                       return_value=tmp_path), \
                 patch("app.utils.dataframe.load_dataframe_from_file",
                       return_value=pd.DataFrame()):
                result = mgr._execute_carica(config, {})
        finally:
            tmp_path.unlink(missing_ok=True)
        assert result["success"] is False

    def test_carica_success(self):
        mgr = self.make_mgr()
        config = make_config(with_dest=True)
        import pandas as pd
        df = pd.DataFrame({"id": [1, 2], "nome": ["a", "b"]})
        mock_write_result = MagicMock()
        from app.core.carica.models import WriteStatus
        mock_write_result.status = WriteStatus.SUCCESS
        mock_write_result.rows_written = 2
        mock_write_result.rows_inserted = 2
        mock_write_result.rows_updated = 0
        mock_write_result.total_rows = 2
        mock_write_result.error_message = ""
        import tempfile as tf
        with tf.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            df.to_csv(f.name, index=False)
            tmp_path = Path(f.name)
        try:
            with patch("app.utils.result_store.get_result_as_tempfile",
                       return_value=tmp_path), \
                 patch("app.utils.dataframe.load_dataframe_from_file",
                       return_value=df), \
                 patch("app.core.carica.executor.CaricaExecutor") as mock_ex, \
                 patch.object(mgr, "_get_connection_service", return_value=MagicMock()):
                mock_ex.return_value.execute.return_value = mock_write_result
                result = mgr._execute_carica(config, {})
        finally:
            tmp_path.unlink(missing_ok=True)
        assert result["success"] is True
        assert result["rows_written"] == 2

    def test_carica_write_failure(self):
        mgr = self.make_mgr()
        config = make_config(with_dest=True)
        import pandas as pd
        df = pd.DataFrame({"id": [1]})
        mock_write_result = MagicMock()
        from app.core.carica.models import WriteStatus
        mock_write_result.status = WriteStatus.WRITE_ERROR
        mock_write_result.error_message = "Connessione DB persa"
        import tempfile as tf
        with tf.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            with patch("app.utils.result_store.get_result_as_tempfile",
                       return_value=tmp_path), \
                 patch("app.utils.dataframe.load_dataframe_from_file",
                       return_value=df), \
                 patch("app.core.carica.executor.CaricaExecutor") as mock_ex, \
                 patch.object(mgr, "_get_connection_service", return_value=MagicMock()):
                mock_ex.return_value.execute.return_value = mock_write_result
                result = mgr._execute_carica(config, {})
        finally:
            tmp_path.unlink(missing_ok=True)
        assert result["success"] is False

    def test_eccezione_gestita(self):
        mgr = self.make_mgr()
        config = make_config(with_dest=True)
        with patch("app.utils.result_store.get_result_as_tempfile",
                   side_effect=RuntimeError("crash")):
            result = mgr._execute_carica(config, {})
        assert result["success"] is False


# =============================================================================
# log_service.get_logs
# =============================================================================

class TestGetLogs:

    def test_dir_non_esistente_lista_vuota(self):
        svc = make_log_svc(Path("/tmp/non_esiste_scheduler_logs_xyz"))
        result = svc.get_logs("sched-001")
        assert result.total == 0
        assert result.logs == []

    def test_get_logs_con_entries(self):
        import time as _t
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            for ev in ["PIPELINE_COMPLETED", "PIPELINE_FAILED"]:
                entry = make_entry("sched-001", ev)
                svc.write_log(entry)
                _t.sleep(0.01)
            result = svc.get_logs("sched-001")
        assert result.total == 2

    def test_get_logs_filtra_schedule_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            svc.write_log(make_entry("sched-001"))
            svc.write_log(make_entry("sched-002"))
            result = svc.get_logs("sched-001")
        assert result.total == 1
        assert result.logs[0].schedule_id == "sched-001"

    def test_get_logs_paginazione(self):
        import time as _t
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            for i in range(5):
                svc.write_log(make_entry("sched-001"))
                _t.sleep(0.01)
            result = svc.get_logs("sched-001", page=1, page_size=3)
        assert len(result.logs) == 3
        assert result.total == 5
        assert result.page == 1
        assert result.page_size == 3

    def test_get_logs_page_2(self):
        import time as _t
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            for i in range(5):
                svc.write_log(make_entry("sched-001"))
                _t.sleep(0.01)
            result = svc.get_logs("sched-001", page=2, page_size=3)
        assert len(result.logs) == 2  # 5 - 3 = 2 nella seconda pagina


# =============================================================================
# log_service.get_latest_log
# =============================================================================

class TestGetLatestLog:

    def test_senza_log_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            result = svc.get_latest_log("sched-non-esiste")
        assert result is None

    def test_con_log_ritorna_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = make_log_svc(Path(tmpdir))
            entry = make_entry("sched-001")
            svc.write_log(entry)
            result = svc.get_latest_log("sched-001")
        assert result is not None
        assert result.schedule_id == "sched-001"


# =============================================================================
# log_service.create_log_entry
# =============================================================================

class TestCreateLogEntry:

    def test_campi_base(self):
        svc = make_log_svc(Path("/tmp"))
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="PIPELINE_COMPLETED",
        )
        assert entry.schedule_id == "sched-001"
        assert entry.event == "PIPELINE_COMPLETED"
        assert entry.module == "SCHEDULER"

    def test_con_steps(self):
        svc = make_log_svc(Path("/tmp"))
        steps = [LogStep(step="ESTRAI", status="success", rows=100)]
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="PIPELINE_COMPLETED",
            steps=steps,
        )
        assert len(entry.steps) == 1

    def test_con_error_message(self):
        svc = make_log_svc(Path("/tmp"))
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="PIPELINE_FAILED",
            level="ERROR",
            error_message="DB non raggiungibile",
        )
        assert entry.error_message == "DB non raggiungibile"
        assert entry.level == "ERROR"

    def test_con_pipeline_info(self):
        svc = make_log_svc(Path("/tmp"))
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="PIPELINE_STARTED",
            pipeline_info={"project_id": "proj-1", "project_type": "estrai"},
        )
        assert entry.pipeline_info["project_id"] == "proj-1"

    def test_log_id_generato(self):
        svc = make_log_svc(Path("/tmp"))
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="SCHEDULE_TRIGGERED",
        )
        assert entry.log_id.startswith("sched_")

    def test_triggered_by_manual(self):
        svc = make_log_svc(Path("/tmp"))
        entry = svc.create_log_entry(
            schedule_id="sched-001",
            event="SCHEDULE_TRIGGERED",
            triggered_by="manual",
        )
        assert entry.triggered_by == "manual"


# =============================================================================
# schedule_service.update_schedule
# =============================================================================

class TestUpdateSchedule:

    def make_svc(self):
        return ScheduleService.__new__(ScheduleService)

    def make_cfg(self):
        now = datetime.now(timezone.utc)
        return ScheduleConfig(
            id="sched-001", created_at=now, updated_at=now,
            pipeline=SchedulePipeline(project_id="proj-1", project_type="estrai"),
            schedule=ScheduleFrequency(cron_expression="0 6 * * 1"),
            execution=ScheduleExecution(),
            on_error=ScheduleErrorPolicy(),
            conflict_policy=ScheduleConflictPolicy(),
            concurrency=ScheduleConcurrency(),
            pre_check=SchedulePreCheck(),
            runtime=ScheduleRuntime(),
        )

    def test_update_non_trovato_none(self):
        svc = self.make_svc()
        with patch.object(svc, "get_schedule", return_value=None):
            result = svc.update_schedule("non-esiste", ScheduleUpdate())
        assert result is None

    def test_update_cron_expression(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(cron_expression="0 3 * * *"))
        assert result.schedule.cron_expression == "0 3 * * *"

    def test_update_max_retries(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(max_retries=5))
        assert result.on_error.max_retries == 5

    def test_update_append_conflicts(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(append_conflicts="force"))
        assert result.conflict_policy.append_conflicts == "force"

    def test_update_use_cache(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(use_cache=True))
        assert result.execution.use_cache is True

    def test_update_clear_destination(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        cfg.destination = ScheduleDestination(connection_id="c", table_name="t")
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(clear_destination=True))
        assert result.destination is None

    def test_update_aggiorna_updated_at(self):
        svc = self.make_svc()
        cfg = self.make_cfg()
        old_updated = cfg.updated_at
        with patch.object(svc, "get_schedule", return_value=cfg), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.update_schedule("sched-001", ScheduleUpdate(max_retries=1))
        assert result.updated_at >= old_updated


# =============================================================================
# concurrency_manager._get e _set_nx
# =============================================================================

class TestConcurrencyInternals:

    def make_mgr(self):
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
        return mgr

    def test_get_key_inesistente_none(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = None
            result = mgr._get("chiave_inesistente")
        assert result is None

    def test_get_key_esistente(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {"value": "running", "expires_at": None}
            result = mgr._get("scheduler:lock:sched-1")
        assert result == "running"

    def test_get_key_scaduta_none(self):
        mgr = self.make_mgr()
        from datetime import timedelta
        scaduta = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {"value": "running", "expires_at": scaduta}
            mock_db.execute.return_value = None
            result = mgr._get("scheduler:lock:sched-1")
        assert result is None

    def test_set_nx_chiave_libera_true(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = None
            mock_db.execute.return_value = None
            result = mgr._set_nx("nuova_chiave", "running", 100)
        assert result is True

    def test_set_nx_chiave_occupata_false(self):
        mgr = self.make_mgr()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = None
            mock_db.execute.side_effect = Exception("UNIQUE constraint failed")
            result = mgr._set_nx("chiave_occupata", "running", 100)
        assert result is False
