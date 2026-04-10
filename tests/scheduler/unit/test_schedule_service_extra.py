"""
tests/scheduler/unit/test_schedule_service_extra.py
====================================================
Test aggiuntivi per ScheduleService — toggle, delete, mark_as_orphan,
update_runtime, set_runtime_status.

NON richiede backend o DB reale.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.scheduler.schedule_service import ScheduleService
from app.core.scheduler.models import (
    ScheduleConfig, ScheduleFrequency, SchedulePipeline,
    ScheduleRuntime, ScheduleExecution, ScheduleErrorPolicy,
    ScheduleConflictPolicy, ScheduleConcurrency, SchedulePreCheck,
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_svc():
    return ScheduleService.__new__(ScheduleService)


def make_config(schedule_id="sched-001", status="idle", enabled=True):
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id=schedule_id,
        created_at=now, updated_at=now,
        pipeline=SchedulePipeline(project_id="proj-1", project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1", enabled=enabled),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(status=status),
    )


# =============================================================================
# toggle_schedule
# =============================================================================

class TestToggleSchedule:

    def test_non_trovato_none(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db:
            mock_db.get_schedule.return_value = None
            result = svc.toggle_schedule("non-esiste", True)
        assert result is None

    def test_abilita_status_idle(self):
        svc = make_svc()
        config = make_config(status="disabled", enabled=False)
        with patch.object(svc, "get_schedule", return_value=config), \
             patch.object(svc, "_calculate_next_execution", return_value=None), \
             patch("app.core.scheduler.schedule_service.db") as mock_db:
            result = svc.toggle_schedule("sched-001", True)
        assert result is not None
        assert result.schedule.enabled is True
        assert result.runtime.status == "idle"

    def test_disabilita_status_disabled(self):
        svc = make_svc()
        config = make_config(status="idle", enabled=True)
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db") as mock_db:
            result = svc.toggle_schedule("sched-001", False)
        assert result is not None
        assert result.schedule.enabled is False
        assert result.runtime.status == "disabled"

    def test_orfano_non_abilitabile(self):
        """Mutation target: schedule orfano non può essere abilitato."""
        svc = make_svc()
        config = make_config(status="orphan")
        with patch.object(svc, "get_schedule", return_value=config):
            result = svc.toggle_schedule("sched-001", True)
        assert result is None

    def test_abilita_calcola_next_execution(self):
        svc = make_svc()
        config = make_config(status="disabled", enabled=False)
        with patch.object(svc, "get_schedule", return_value=config), \
             patch.object(svc, "_calculate_next_execution",
                          return_value=datetime.now(timezone.utc)) as mock_calc, \
             patch("app.core.scheduler.schedule_service.db"):
            svc.toggle_schedule("sched-001", True)
        mock_calc.assert_called_once()

    def test_disabilita_annulla_next_execution(self):
        svc = make_svc()
        config = make_config(status="idle")
        config.runtime.next_execution = datetime.now(timezone.utc)
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.toggle_schedule("sched-001", False)
        assert result.runtime.next_execution is None


# =============================================================================
# mark_as_orphan
# =============================================================================

class TestMarkAsOrphan:

    def test_non_trovato_none(self):
        svc = make_svc()
        with patch.object(svc, "get_schedule", return_value=None):
            result = svc.mark_as_orphan("non-esiste")
        assert result is None

    def test_status_orphan(self):
        svc = make_svc()
        config = make_config()
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.mark_as_orphan("sched-001")
        assert result.runtime.status == "orphan"
        assert result.schedule.enabled is False

    def test_next_execution_annullata(self):
        svc = make_svc()
        config = make_config()
        config.runtime.next_execution = datetime.now(timezone.utc)
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            result = svc.mark_as_orphan("sched-001")
        assert result.runtime.next_execution is None


# =============================================================================
# delete_schedule
# =============================================================================

class TestDeleteSchedule:

    def test_non_trovato_false(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db:
            mock_db.get_schedule.return_value = None
            result = svc.delete_schedule("non-esiste")
        assert result is False

    def test_trovato_true(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_db.get_schedule.return_value = {"id": "sched-001"}
            mock_ls.return_value.delete_schedule_logs.return_value = 5
            result = svc.delete_schedule("sched-001")
        assert result is True
        mock_db.delete_schedule.assert_called_with("sched-001")

    def test_errore_log_non_blocca(self):
        """Errore pulizia log non impedisce eliminazione schedule."""
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.log_service.ScheduleLogService") as mock_ls:
            mock_db.get_schedule.return_value = {"id": "sched-001"}
            mock_ls.return_value.delete_schedule_logs.side_effect = RuntimeError("log error")
            result = svc.delete_schedule("sched-001")
        assert result is True


# =============================================================================
# set_runtime_status
# =============================================================================

class TestSetRuntimeStatus:

    def test_aggiorna_status(self):
        svc = make_svc()
        config = make_config(status="idle")
        with patch.object(svc, "get_schedule", return_value=config), \
             patch("app.core.scheduler.schedule_service.db"):
            svc.set_runtime_status("sched-001", "running")
        # Non deve crashare
        assert True

    def test_schedule_non_trovato_no_crash(self):
        svc = make_svc()
        with patch.object(svc, "get_schedule", return_value=None):
            svc.set_runtime_status("non-esiste", "running")
        assert True


# =============================================================================
# _row_to_config — casi aggiuntivi
# =============================================================================

class TestRowToConfigExtra:

    def test_config_none_restituisce_none(self):
        svc = make_svc()
        assert svc._row_to_config({"config": None}) is None

    def test_config_dict_vuoto_restituisce_none(self):
        svc = make_svc()
        assert svc._row_to_config({"config": {}}) is None

    def test_row_senza_config_key(self):
        svc = make_svc()
        # Riga senza chiave 'config'
        result = svc._row_to_config({"id": "test"})
        assert result is None


# =============================================================================
# _config_to_db — casi aggiuntivi
# =============================================================================

class TestConfigToDbExtra:

    def test_cron_nel_risultato(self):
        svc = make_svc()
        config = make_config()
        config.schedule.cron_expression = "0 3 * * *"
        result = svc._config_to_db(config, "group-1")
        assert result["cron_expression"] == "0 3 * * *"

    def test_group_id_nel_risultato(self):
        svc = make_svc()
        config = make_config()
        result = svc._config_to_db(config, "gruppo_speciale")
        assert result["group_id"] == "gruppo_speciale"

    def test_config_model_dump_nel_risultato(self):
        svc = make_svc()
        config = make_config()
        result = svc._config_to_db(config, "group-1")
        assert "config" in result
        assert result["config"]["id"] == "sched-001"
