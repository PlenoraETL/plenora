"""
Contract test — graceful degradation di admin_system_status/service.

Fonte di verita' container-friendly:
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# get_backup_status — dipende da admin_backups.list_backups (boto3 + S3)


def _fake_backup(age_hours: float, key: str = "urby_20260418_020000Z.dump",
                 size_bytes: int = 1024 * 1024) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {
        "key": key,
        "size_bytes": size_bytes,
        "last_modified": ts.isoformat(),
        "storage_class": "STANDARD",
    }


def test_backup_status_when_list_backups_raises_returns_unknown():
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.list_backups",
        side_effect=RuntimeError("boto3 explode"),
    ):
        out = service.get_backup_status()

    assert out["status"] == "unknown"
    assert out["message"] == "S3 non accessibile"
    assert "boto3 explode" in out["error"]


def test_backup_status_when_bucket_empty_returns_error():
    from app.core.admin_system_status import service

    with patch("app.core.admin_system_status.service.list_backups", return_value=[]):
        out = service.get_backup_status()

    assert out["status"] == "error"
    assert out["message"] == "Nessun backup trovato su S3"


def test_backup_status_age_over_error_threshold():
    """>48h → status=error."""
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.list_backups",
        return_value=[_fake_backup(age_hours=60.0)],
    ):
        out = service.get_backup_status()

    assert out["status"] == "error"
    assert out["age_hours"] is not None
    assert out["age_hours"] >= 59.0


def test_backup_status_age_in_warn_band():
    """26h-48h → status=warn."""
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.list_backups",
        return_value=[_fake_backup(age_hours=30.0)],
    ):
        out = service.get_backup_status()

    assert out["status"] == "warn"


def test_backup_status_age_fresh_under_one_hour():
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.list_backups",
        return_value=[_fake_backup(age_hours=0.3)],
    ):
        out = service.get_backup_status()

    assert out["status"] == "ok"
    assert out["message"] == "Ultimo backup <1h fa"


def test_backup_status_age_unparseable_returns_unknown():
    """Se last_modified e' una stringa non-ISO, non solleva, ritorna unknown."""
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.list_backups",
        return_value=[{
            "key": "x.dump",
            "size_bytes": 1,
            "last_modified": "not-a-date",
            "storage_class": "STANDARD",
        }],
    ):
        out = service.get_backup_status()

    assert out["status"] == "unknown"
    assert "timestamp non parsabile" in out["message"]


def test_backup_status_null_last_modified_returns_typed_unknown():
    """Null timestamp stays graceful and service returns the typed block."""
    from app.core.admin_system_status import service
    from app.core.admin_system_status.models import AdminStatusBlock

    with patch(
        "app.core.admin_system_status.service.list_backups",
        return_value=[{
            "key": "x.dump",
            "size_bytes": 1,
            "last_modified": None,
            "storage_class": "STANDARD",
        }],
    ):
        out = service.get_backup_status()

    assert isinstance(out, AdminStatusBlock)
    assert out["status"] == "unknown"
    assert out["age_hours"] is None


# get_verify_restore_status — legge da plenora.verify_runs (DB)


def test_verify_restore_when_db_raises_returns_unknown():
    from app.core.admin_system_status import service

    with patch(
        "app.core.admin_system_status.service.fetch_latest_verify_run",
        side_effect=ConnectionError("postgres unreachable"),
    ):
        out = service.get_verify_restore_status()

    assert out["status"] == "unknown"
    assert out["message"] == "DB non accessibile"
    assert "postgres unreachable" in out["error"]
    assert out["last_result"] == "unknown"


def test_verify_restore_when_no_runs_registered():
    from app.core.admin_system_status import service

    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=None):
        out = service.get_verify_restore_status()

    assert out["status"] == "unknown"
    assert out["message"] == "Nessuna verifica registrata"
    assert out["last_run_at"] is None


def test_verify_restore_success_fresh_under_one_day():
    from app.core.admin_system_status import service

    finished = datetime.now(timezone.utc) - timedelta(hours=3)
    row = {
        "started_at": finished - timedelta(minutes=5),
        "finished_at": finished,
        "exit_code": 0,
        "result": "success",
        "error_message": None,
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "ok"
    assert out["message"] == "Verifica OK <1gg fa"
    assert out["exit_code"] == 0


def test_verify_restore_success_in_warn_band():
    """8-14gg → warn."""
    from app.core.admin_system_status import service

    finished = datetime.now(timezone.utc) - timedelta(days=10)
    row = {
        "started_at": finished - timedelta(minutes=5),
        "finished_at": finished,
        "exit_code": 0,
        "result": "success",
        "error_message": None,
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "warn"


def test_verify_restore_success_over_error_threshold():
    """>14gg → error."""
    from app.core.admin_system_status import service

    finished = datetime.now(timezone.utc) - timedelta(days=20)
    row = {
        "started_at": finished - timedelta(minutes=5),
        "finished_at": finished,
        "exit_code": 0,
        "result": "success",
        "error_message": None,
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "error"


def test_verify_restore_failed_result():
    from app.core.admin_system_status import service

    finished = datetime.now(timezone.utc) - timedelta(hours=2)
    row = {
        "started_at": finished - timedelta(minutes=5),
        "finished_at": finished,
        "exit_code": 1,
        "result": "failed",
        "error_message": "pg_restore exit 1: schema mismatch",
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "error"
    assert "FALLITA" in out["message"]
    assert "schema mismatch" in out["message"]


def test_verify_restore_running_result():
    """Job in corso (started_at ma non finished_at) → ok / verifica in corso."""
    from app.core.admin_system_status import service

    row = {
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "exit_code": None,
        "result": "running",
        "error_message": None,
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "ok"
    assert out["message"] == "Verifica in corso"


def test_verify_restore_malformed_finished_at_stays_graceful():
    from app.core.admin_system_status import service

    row = {
        "started_at": datetime.now(timezone.utc),
        "finished_at": "not-a-datetime",
        "exit_code": 0,
        "result": "success",
        "error_message": None,
    }
    with patch("app.core.admin_system_status.service.fetch_latest_verify_run", return_value=row):
        out = service.get_verify_restore_status()

    assert out["status"] == "ok"
    assert out["age_days"] is None
    assert out["last_run_at"] is None


# get_schedules_status — DB query schedules


def test_schedules_status_when_db_raises_returns_unknown(monkeypatch):
    from app.core.admin_system_status import service

    def _boom(*a, **kw):
        raise ConnectionError("postgres unreachable")

    monkeypatch.setattr(service, "fetch_schedule_rows", _boom)

    out = service.get_schedules_status()

    assert out["status"] == "unknown"
    assert out["message"] == "Schedule table non accessibile"
    assert "postgres unreachable" in out["error"]


def test_schedules_status_empty_table_returns_ok():
    from app.core.admin_system_status import service

    with patch("app.core.admin_system_status.service.fetch_schedule_rows", return_value=[]):
        out = service.get_schedules_status()

    assert out["status"] == "ok"
    assert out["message"] == "Nessuno schedule attivo"
    assert out["total_active"] == 0


def test_schedules_status_half_or_more_failing_is_error():
    from app.core.admin_system_status import service

    rows = [
        {"id": "a", "name": "a", "is_enabled": True, "last_status": "failed"},
        {"id": "b", "name": "b", "is_enabled": True, "last_status": "success"},
    ]
    with patch("app.core.admin_system_status.service.fetch_schedule_rows", return_value=rows):
        out = service.get_schedules_status()

    assert out["status"] == "error"
    assert out["failing_count"] == 1
    assert out["total_active"] == 2


def test_schedules_status_ignores_disabled():
    from app.core.admin_system_status import service

    rows = [
        {"id": "a", "name": "a", "is_enabled": False, "last_status": "failed"},
        {"id": "b", "name": "b", "is_enabled": True, "last_status": "success"},
    ]
    with patch("app.core.admin_system_status.service.fetch_schedule_rows", return_value=rows):
        out = service.get_schedules_status()

    assert out["total_active"] == 1
    assert out["failing_count"] == 0
    assert out["status"] == "ok"


# get_workers_status — derivato da PostgreSQL (Plenora 2.0 Fase 5)


def test_admin_status_service_does_not_import_celery_or_probes():
    """Plenora 2.0 Fase 5: l'osservabilita' worker e' derivata da PG
    (task_results), non dall'inspect Celery. Il service non deve importare
    ne' Celery ne' i probe broker; usa il task_result_store."""
    import ast
    from pathlib import Path

    service_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "core"
        / "admin_system_status"
        / "service.py"
    )
    tree = ast.parse(service_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert "workers.celery_app" not in imports
    assert "app.core.task_runtime.probes" not in imports
    assert "app.core.orchestrator.task_result_store" in imports


def _patch_running_count(monkeypatch, value=None, *, raises=None):
    from app.core.admin_system_status import service

    class _FakeStore:
        def count_in_state(self, state):
            if raises is not None:
                raise raises
            return value

    monkeypatch.setattr(service, "default_task_result_store", lambda: _FakeStore())


def test_workers_status_reports_running_tasks_from_pg(monkeypatch):
    from app.core.admin_system_status import service

    _patch_running_count(monkeypatch, value=3)

    out = service.get_workers_status()

    assert out["status"] == "ok"
    assert out["active_tasks"] == 3
    assert out["running_tasks"] == 3
    assert "3 task in esecuzione" in out["message"]


def test_workers_status_idle_when_no_running_tasks(monkeypatch):
    from app.core.admin_system_status import service

    _patch_running_count(monkeypatch, value=0)

    out = service.get_workers_status()

    assert out["status"] == "ok"
    assert out["active_tasks"] == 0
    assert "Nessun task in esecuzione" in out["message"]


def test_workers_status_unknown_when_pg_unavailable(monkeypatch):
    """PG non raggiungibile: catch + shape coerente, non solleva."""
    from app.core.admin_system_status import service
    from app.core.admin_system_status.models import AdminStatusBlock

    _patch_running_count(monkeypatch, raises=ConnectionError("PG down"))

    out = service.get_workers_status()

    assert isinstance(out, AdminStatusBlock)
    assert out["status"] == "unknown"
    assert "PG down" in out["error"]
