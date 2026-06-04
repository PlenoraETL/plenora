"""Contract tests per i cleanup periodici del beat scheduler."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import time

import pytest

from workers.beat_scheduler import PlenoraBeatScheduler

from tests2.shared._ast_identifiers import uses_attribute, call_has_keyword


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _make_scheduler() -> PlenoraBeatScheduler:
    scheduler = PlenoraBeatScheduler.__new__(PlenoraBeatScheduler)
    scheduler._last_stuck_check = 0.0
    scheduler._last_eseguibili_cleanup = 0.0
    scheduler._last_logs_retention = 0.0
    scheduler._last_schedule_logs_retention = 0.0
    scheduler._last_imdf_bundles_cleanup = 0.0
    scheduler._last_temp_orphan_cleanup = 0.0
    return scheduler


def test_beat_janitors_use_shared_filesystem_cleanup_boundary() -> None:
    scheduler_path = _BACKEND_ROOT / "workers" / "beat_scheduler.py"
    src = scheduler_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    # Audit follow-up 2026-05-26: il modulo cleanup e' stato spostato
    # in `app/core/io/local_cleanup.py` come boundary I/O orizzontale.
    assert "app.core.io.local_cleanup" in imports
    assert not uses_attribute(src, "shutil", "rmtree")
    assert not uses_attribute(src, "_shutil", "rmtree")
    assert not call_has_keyword(src, "unlink", "missing_ok")


def test_cleanup_eseguibili_outputs_removes_only_expired(tmp_path, monkeypatch):
    scheduler = _make_scheduler()
    now = 2_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    outputs_dir = tmp_path / "outputs"
    fresh = outputs_dir / "fresh"
    expired = outputs_dir / "expired"
    fresh.mkdir(parents=True)
    expired.mkdir(parents=True)
    import os

    os.utime(fresh, (now - 60, now - 60))
    os.utime(expired, (now - 2 * 3600 - 10, now - 2 * 3600 - 10))

    with patch(
        "app.config.settings",
        new=SimpleNamespace(ESEGUIBILI_OUTPUTS_DIR=outputs_dir),
    ):
        scheduler._cleanup_eseguibili_outputs()

    assert fresh.exists()
    assert not expired.exists()


def test_cleanup_old_logs_deletes_rows_older_than_retention():
    """SET LOCAL + DELETE devono essere emessi sullo stesso cursor
    (stessa tx), cosi' il flag `plenora.retention_cleanup` e' visibile
    ai trigger/policy PG durante la DELETE."""
    scheduler = _make_scheduler()

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = False
    mock_tx_cm = MagicMock()
    mock_tx_cm.__enter__.return_value = mock_conn
    mock_tx_cm.__exit__.return_value = False

    with patch(
        "app.config.settings",
        new=SimpleNamespace(LOG_RETENTION_DAYS=30),
    ), patch(
        "app.db.pg_connection.fetchall",
        return_value=[{"c": 7}],
    ) as mock_fetchall, patch(
        "app.db.pg_connection.unit_of_work",
        return_value=mock_tx_cm,
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_called_once()
    mock_transaction.assert_called_once()
    # Sullo stesso cursor: prima SET LOCAL, poi DELETE
    assert mock_cursor.execute.call_count == 2
    set_local_call = mock_cursor.execute.call_args_list[0]
    assert "SET LOCAL plenora.retention_cleanup" in set_local_call.args[0]
    assert "'true'" in set_local_call.args[0]
    delete_call = mock_cursor.execute.call_args_list[1]
    assert "DELETE FROM logs" in delete_call.args[0]
    assert delete_call.args[1] == (30,)


def test_cleanup_old_logs_skips_when_retention_disabled():
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(LOG_RETENTION_DAYS=-1),
    ), patch(
        "app.db.pg_connection.fetchall"
    ) as mock_fetchall, patch(
        "app.db.pg_connection.unit_of_work"
    ) as mock_transaction:
        scheduler._cleanup_old_logs()

    mock_fetchall.assert_not_called()
    mock_transaction.assert_not_called()


# Schedule logs retention (#33)


def test_cleanup_old_schedule_logs_calls_log_service_with_cutoff():
    """Default 90gg: chiama LogService().delete_logs_before(today - 90gg)."""
    from datetime import datetime, timezone, timedelta
    scheduler = _make_scheduler()

    expected_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).date()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(SCHEDULE_LOGS_RETENTION_DAYS=90),
    ), patch(
        "app.core.scheduler.log_service.ScheduleLogService"
    ) as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        mock_svc.delete_logs_before.return_value = 12
        scheduler._cleanup_old_schedule_logs()

    mock_svc_cls.assert_called_once()
    mock_svc.delete_logs_before.assert_called_once()
    cutoff_arg = mock_svc.delete_logs_before.call_args.args[0]
    # Tolleranza ±1 giorno per evitare flake al cambio data
    assert abs((cutoff_arg - expected_cutoff).days) <= 1


def test_cleanup_old_schedule_logs_respects_custom_retention():
    """SCHEDULE_LOGS_RETENTION_DAYS=7 → cutoff = today - 7gg."""
    from datetime import datetime, timezone, timedelta
    scheduler = _make_scheduler()
    expected_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(SCHEDULE_LOGS_RETENTION_DAYS=7),
    ), patch(
        "app.core.scheduler.log_service.ScheduleLogService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.delete_logs_before.return_value = 0
        scheduler._cleanup_old_schedule_logs()

    cutoff_arg = mock_svc_cls.return_value.delete_logs_before.call_args.args[0]
    assert abs((cutoff_arg - expected_cutoff).days) <= 1


def test_cleanup_old_schedule_logs_disabled_when_zero():
    """SCHEDULE_LOGS_RETENTION_DAYS=0 → no chiamata al service."""
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(SCHEDULE_LOGS_RETENTION_DAYS=0),
    ), patch(
        "app.core.scheduler.log_service.ScheduleLogService"
    ) as mock_svc_cls:
        scheduler._cleanup_old_schedule_logs()

    mock_svc_cls.assert_not_called()


def test_cleanup_old_schedule_logs_24h_gate():
    """Se chiamato due volte in <24h, la 2a chiamata e' no-op."""
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(SCHEDULE_LOGS_RETENTION_DAYS=90),
    ), patch(
        "app.core.scheduler.log_service.ScheduleLogService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.delete_logs_before.return_value = 0
        scheduler._cleanup_old_schedule_logs()
        scheduler._cleanup_old_schedule_logs()

    # Solo 1 invocazione del service (2a chiamata blocked dal gate temporal)
    assert mock_svc_cls.call_count == 1


def test_cleanup_old_schedule_logs_swallows_errors():
    """Se LogService.delete_logs_before solleva, beat continua a girare."""
    scheduler = _make_scheduler()

    with patch(
        "app.config.settings",
        new=SimpleNamespace(SCHEDULE_LOGS_RETENTION_DAYS=90),
    ), patch(
        "app.core.scheduler.log_service.ScheduleLogService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.delete_logs_before.side_effect = RuntimeError("DB down")
        # Non deve sollevare
        scheduler._cleanup_old_schedule_logs()


def test_cleanup_orphan_temp_removes_only_expired(tmp_path, monkeypatch):
    """File/dir in DATA_DIR/temp > TTL vengono cancellati; i recenti no."""
    scheduler = _make_scheduler()
    now = 3_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    fresh_dir = temp_dir / "urby_12345_abcd"
    expired_dir = temp_dir / "urby_99999_ffff"
    fresh_file = temp_dir / "plenora_cached_aaaa.xlsx"
    expired_file = temp_dir / "plenora_cached_bbbb.xlsx"

    fresh_dir.mkdir(parents=True)
    expired_dir.mkdir(parents=True)
    fresh_file.parent.mkdir(parents=True, exist_ok=True)
    fresh_file.write_text("fresh")
    expired_file.write_text("old")

    import os as _os
    # fresh: 1h fa (< 24h TTL)
    _os.utime(fresh_dir, (now - 3600, now - 3600))
    _os.utime(fresh_file, (now - 3600, now - 3600))
    # expired: 25h fa (> 24h TTL)
    _os.utime(expired_dir, (now - 25 * 3600, now - 25 * 3600))
    _os.utime(expired_file, (now - 25 * 3600, now - 25 * 3600))

    with patch("app.config.settings", new=SimpleNamespace(DATA_DIR=data_dir)):
        scheduler._cleanup_orphan_temp_files()

    assert fresh_dir.exists()
    assert fresh_file.exists()
    assert not expired_dir.exists()
    assert not expired_file.exists()


def test_cleanup_orphan_temp_preserves_eseguibili_uploads_dir(tmp_path, monkeypatch):
    """`eseguibili_uploads/` e' cache permanente cross-worker: la dir
    resta, ma i file dentro > TTL vengono puliti."""
    scheduler = _make_scheduler()
    now = 3_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    uploads_dir = temp_dir / "eseguibili_uploads"
    uploads_dir.mkdir(parents=True)

    fresh_meta = uploads_dir / "fresh.meta.json"
    expired_meta = uploads_dir / "old.meta.json"
    fresh_meta.write_text("{}")
    expired_meta.write_text("{}")

    import os as _os
    _os.utime(fresh_meta, (now - 3600, now - 3600))
    _os.utime(expired_meta, (now - 25 * 3600, now - 25 * 3600))
    # Tocca la dir parent come se fosse vecchia (age-based cleanup
    # dovrebbe comunque skipparla perche' e' eseguibili_uploads).
    _os.utime(uploads_dir, (now - 25 * 3600, now - 25 * 3600))

    with patch("app.config.settings", new=SimpleNamespace(DATA_DIR=data_dir)):
        scheduler._cleanup_orphan_temp_files()

    # dir preserva, file vecchio cancellato, fresh resta
    assert uploads_dir.exists()
    assert fresh_meta.exists()
    assert not expired_meta.exists()


def test_cleanup_orphan_temp_skips_when_gate_not_elapsed(tmp_path, monkeypatch):
    scheduler = _make_scheduler()
    now = 3_000_000.0
    scheduler._last_temp_orphan_cleanup = now - 60  # 1 min fa, gate 30min
    monkeypatch.setattr(time, "time", lambda: now)

    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    temp_dir.mkdir(parents=True)
    expired_file = temp_dir / "plenora_cached_old.xlsx"
    expired_file.write_text("old")
    import os as _os
    _os.utime(expired_file, (now - 25 * 3600, now - 25 * 3600))

    with patch("app.config.settings", new=SimpleNamespace(DATA_DIR=data_dir)):
        scheduler._cleanup_orphan_temp_files()

    # gate non elapsed → nessuna pulizia anche se il file e' > TTL
    assert expired_file.exists()


def test_cleanup_orphan_temp_respects_env_ttl(tmp_path, monkeypatch):
    """TEMP_ORPHAN_CLEANUP_HOURS override default 24h."""
    scheduler = _make_scheduler()
    now = 3_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setenv("TEMP_ORPHAN_CLEANUP_HOURS", "1")

    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    temp_dir.mkdir(parents=True)
    borderline_file = temp_dir / "plenora_cached_x.xlsx"
    borderline_file.write_text("x")
    import os as _os
    # 2h fa: con TTL=1h cadrà, con default 24h no
    _os.utime(borderline_file, (now - 2 * 3600, now - 2 * 3600))

    with patch("app.config.settings", new=SimpleNamespace(DATA_DIR=data_dir)):
        scheduler._cleanup_orphan_temp_files()

    assert not borderline_file.exists()


def test_check_stuck_tasks_removes_expired_locks_and_checks_running_schedules():
    scheduler = _make_scheduler()

    expired_locks = [{"key": "k1", "value": "v1", "expires_at": "ts"}]
    stuck_schedules = [{"id": "s-1", "name": "Nightly"}]
    fetchall_side_effect = [expired_locks, stuck_schedules]

    with patch(
        "app.config.settings",
        new=SimpleNamespace(CELERY_SCHEDULE_HARD_LIMIT=3600),
    ), patch(
        "app.db.pg_connection.fetchall",
        side_effect=fetchall_side_effect,
    ) as mock_fetchall, patch(
        "app.db.pg_connection.execute"
    ) as mock_execute:
        scheduler._check_stuck_tasks()

    assert mock_fetchall.call_count == 2
    mock_execute.assert_called_once()
    sql = mock_execute.call_args.args[0]
    assert "DELETE FROM scheduler_locks" in sql
