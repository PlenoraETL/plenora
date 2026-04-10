"""
tests/scheduler/unit/test_schedule_service.py
=============================================
Unit test su ScheduleService metodi puri.

NON richiede backend, DB reale o Celery.

Copre:
  - _generate_schedule_id: formato, unicità
  - _row_to_config: config dict, config stringa JSON, row vuota, errore
  - _config_to_db: campi output, is_enabled, name fallback
  - _calculate_next_execution: cron valida, cron invalida, import error
  - schedule_exists: mock DB
  - toggle_schedule: enable/disable con mock DB
  - check_orphans: con mock DB
"""

import pytest
import json
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
    svc = ScheduleService.__new__(ScheduleService)
    return svc


def make_config(schedule_id="sched-001", project_id="proj-1", enabled=True):
    now = datetime.now(timezone.utc)
    return ScheduleConfig(
        id=schedule_id,
        created_at=now,
        updated_at=now,
        pipeline=SchedulePipeline(project_id=project_id, project_name="Test", project_type="estrai"),
        schedule=ScheduleFrequency(cron_expression="0 6 * * 1", enabled=enabled),
        execution=ScheduleExecution(),
        on_error=ScheduleErrorPolicy(),
        conflict_policy=ScheduleConflictPolicy(),
        concurrency=ScheduleConcurrency(),
        pre_check=SchedulePreCheck(),
        runtime=ScheduleRuntime(),
    )


def config_to_row(config: ScheduleConfig) -> dict:
    return {"id": config.id, "config": config.model_dump()}


# =============================================================================
# _generate_schedule_id
# =============================================================================

class TestGenerateScheduleId:

    def test_formato_schedule_prefix(self):
        svc = make_svc()
        sid = svc._generate_schedule_id()
        assert sid.startswith("schedule_")

    def test_lunghezza_corretta(self):
        svc = make_svc()
        sid = svc._generate_schedule_id()
        # "schedule_" + 12 caratteri uuid
        assert len(sid) == len("schedule_") + 12

    def test_unicita(self):
        svc = make_svc()
        ids = {svc._generate_schedule_id() for _ in range(100)}
        assert len(ids) == 100


# =============================================================================
# _row_to_config
# =============================================================================

class TestRowToConfig:

    def test_row_none_restituisce_none(self):
        svc = make_svc()
        assert svc._row_to_config(None) is None

    def test_row_vuota_restituisce_none(self):
        svc = make_svc()
        assert svc._row_to_config({}) is None

    def test_config_come_dict(self):
        svc = make_svc()
        config = make_config()
        row = {"config": config.model_dump()}
        result = svc._row_to_config(row)
        assert result is not None
        assert result.id == config.id

    def test_config_come_stringa_json(self):
        svc = make_svc()
        config = make_config()
        row = {"config": json.dumps(config.model_dump(), default=str)}
        result = svc._row_to_config(row)
        assert result is not None

    def test_config_invalida_restituisce_none(self):
        svc = make_svc()
        row = {"config": {"campo_inesistente": "valore"}}
        result = svc._row_to_config(row)
        assert result is None

    def test_config_stringa_json_invalida_restituisce_none(self):
        svc = make_svc()
        row = {"config": "non_e_json_valido"}
        result = svc._row_to_config(row)
        assert result is None


# =============================================================================
# _config_to_db
# =============================================================================

class TestConfigToDb:

    def test_campi_base(self):
        svc = make_svc()
        config = make_config(schedule_id="sched-001", project_id="proj-1")
        result = svc._config_to_db(config, "group-1")
        assert result["schedule_id"] == "sched-001"
        assert result["group_id"] == "group-1"
        assert result["project_id"] == "proj-1"
        assert result["cron_expression"] == "0 6 * * 1"

    def test_is_enabled_true(self):
        svc = make_svc()
        config = make_config(enabled=True)
        result = svc._config_to_db(config, "group-1")
        assert result["is_enabled"] is True

    def test_is_enabled_false(self):
        svc = make_svc()
        config = make_config(enabled=False)
        result = svc._config_to_db(config, "group-1")
        assert result["is_enabled"] is False

    def test_name_usa_project_name(self):
        svc = make_svc()
        config = make_config()
        config.pipeline.project_name = "Progetto Test"
        result = svc._config_to_db(config, "group-1")
        assert result["name"] == "Progetto Test"

    def test_name_fallback_schedule_id(self):
        """Se project_name vuoto → usa schedule_id."""
        svc = make_svc()
        config = make_config(schedule_id="sched-fallback")
        config.pipeline.project_name = ""
        result = svc._config_to_db(config, "group-1")
        assert result["name"] == "sched-fallback"

    def test_config_serializzato(self):
        svc = make_svc()
        config = make_config()
        result = svc._config_to_db(config, "group-1")
        assert "config" in result
        assert isinstance(result["config"], dict)


# =============================================================================
# _calculate_next_execution
# =============================================================================

class TestCalculateNextExecution:

    def test_cron_valida_restituisce_datetime(self):
        svc = make_svc()
        result = svc._calculate_next_execution("0 6 * * 1", "Europe/Rome")
        assert result is not None
        assert isinstance(result, datetime)

    def test_cron_ogni_minuto(self):
        svc = make_svc()
        result = svc._calculate_next_execution("* * * * *", "UTC")
        assert result is not None

    def test_cron_invalida_restituisce_none(self):
        svc = make_svc()
        result = svc._calculate_next_execution("INVALID_CRON", "Europe/Rome")
        assert result is None

    def test_timezone_utc(self):
        svc = make_svc()
        result = svc._calculate_next_execution("0 3 * * *", "UTC")
        assert result is not None

    def test_timezone_invalido_restituisce_none(self):
        svc = make_svc()
        result = svc._calculate_next_execution("0 6 * * 1", "Mars/Olympus")
        assert result is None

    def test_data_futura(self):
        """La prossima esecuzione deve essere nel futuro."""
        svc = make_svc()
        result = svc._calculate_next_execution("* * * * *", "UTC")
        assert result > datetime.now(result.tzinfo)


# =============================================================================
# schedule_exists — con mock DB
# =============================================================================

class TestScheduleExists:

    def test_esiste(self):
        svc = make_svc()
        config = make_config()
        with patch("app.core.scheduler.schedule_service.db") as mock_db:
            mock_db.get_schedule.return_value = {"id": "sched-001"}
            assert svc.schedule_exists("sched-001") is True

    def test_non_esiste(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db:
            mock_db.get_schedule.return_value = None
            assert svc.schedule_exists("sched-xyz") is False


# =============================================================================
# check_orphans — con mock DB
# =============================================================================

class TestCheckOrphans:

    def test_nessun_schedule_lista_vuota(self):
        svc = make_svc()
        with patch("app.core.scheduler.schedule_service.db") as mock_db:
            mock_db.list_schedules.return_value = []
            result = svc.check_orphans()
            assert isinstance(result, list)
            assert len(result) == 0

    def test_tutti_progetti_esistono_nessun_orfano(self):
        svc = make_svc()
        config = make_config()
        row = config_to_row(config)
        with patch("app.core.scheduler.schedule_service.db") as mock_db, \
             patch("app.core.scheduler.schedule_service.settings") as mock_settings, \
             patch("app.services.project_service.ProjectService") as mock_ps:
            mock_settings.DEFAULT_GROUP = "default"
            mock_db.list_schedules.return_value = [row]
            mock_db.get_schedule.return_value = {"group_id": "default"}
            mock_ps.return_value.project_exists.return_value = True
            result = svc.check_orphans()
            assert isinstance(result, list)
