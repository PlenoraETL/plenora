from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _lock_row(*, acquired_at: datetime | None = None, expires_at: datetime | None = None):
    now = datetime.now(timezone.utc)
    return {
        "lock_name": "manual_backup",
        "owner_token": "token-1",
        "owner_pid": None,
        "acquired_at": acquired_at or now,
        "expires_at": expires_at or now + timedelta(hours=1),
    }


def test_trigger_manual_backup_uses_db_lock_before_spawn(monkeypatch, tmp_path):
    from app.core.admin_backups import service

    script = tmp_path / "backup_db.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    log_file = tmp_path / "manual.log"

    monkeypatch.setattr(service, "BACKUP_SCRIPT", script)
    monkeypatch.setattr(service, "MANUAL_LOG_FILE", log_file)

    acquire = MagicMock(return_value=_lock_row())
    set_pid = MagicMock(return_value=True)
    popen = MagicMock(return_value=SimpleNamespace(pid=12345))

    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", acquire)
    monkeypatch.setattr(service, "set_manual_backup_lock_pid", set_pid)
    monkeypatch.setattr(service.subprocess, "Popen", popen)

    out = service.trigger_manual_backup()

    assert out["started"] is True
    assert out["pid"] == 12345
    acquire.assert_called_once()
    popen.assert_called_once()
    set_pid.assert_called_once_with(
        owner_token=acquire.call_args.kwargs["owner_token"],
        pid=12345,
    )


def test_trigger_manual_backup_rejects_when_db_lock_is_active(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", lambda **_: None)
    monkeypatch.setattr(service, "is_manual_backup_running", lambda: True)
    popen = MagicMock()
    monkeypatch.setattr(service.subprocess, "Popen", popen)

    out = service.trigger_manual_backup()

    assert out == {
        "started": False,
        "pid": None,
        "message": "Un backup manuale e' gia' in corso. Attendi che finisca.",
    }
    popen.assert_not_called()


def test_is_manual_backup_running_releases_lock_after_finished_run(monkeypatch):
    from app.core.admin_backups import service

    acquired = datetime(2026, 5, 16, 10, 0, tzinfo=timezone.utc)
    finished = acquired + timedelta(minutes=3)
    release = MagicMock(return_value=True)

    monkeypatch.setattr(
        service,
        "fetch_manual_backup_lock",
        lambda: _lock_row(acquired_at=acquired),
    )
    monkeypatch.setattr(
        service,
        "fetch_latest_backup_run",
        lambda: {
            "id": 1,
            "started_at": acquired + timedelta(seconds=1),
            "finished_at": finished,
            "result": "success",
        },
    )
    monkeypatch.setattr(service, "release_manual_backup_lock", release)

    assert service.is_manual_backup_running() is False
    release.assert_called_once_with(owner_token="token-1")


def test_is_manual_backup_running_keeps_lock_while_child_is_starting(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setattr(service, "fetch_manual_backup_lock", lambda: _lock_row())
    monkeypatch.setattr(service, "fetch_latest_backup_run", lambda: None)
    release = MagicMock()
    monkeypatch.setattr(service, "release_manual_backup_lock", release)

    assert service.is_manual_backup_running() is True
    release.assert_not_called()
