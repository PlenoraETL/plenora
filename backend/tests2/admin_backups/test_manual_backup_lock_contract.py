from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests2.shared._ast_identifiers import all_identifiers, uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _manual_trigger_dev_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("CLUSTER_MODE", raising=False)
    monkeypatch.delenv("PLENORA_CLUSTER_MODE", raising=False)
    monkeypatch.delenv("MULTI_POD_MODE", raising=False)
    monkeypatch.delenv("PLENORA_MULTI_POD", raising=False)
    monkeypatch.setenv("ADMIN_BACKUP_MANUAL_TRIGGER_ENABLED", "true")


def _lock_row(*, acquired_at: datetime | None = None, expires_at: datetime | None = None):
    now = datetime.now(timezone.utc)
    return {
        "lock_name": "manual_backup",
        "owner_token": "token-1",
        "owner_pid": None,
        "acquired_at": acquired_at or now,
        "expires_at": expires_at or now + timedelta(hours=1),
    }


def test_admin_backup_service_does_not_spawn_local_processes():
    from app.core.admin_backups import service

    source = Path(service.__file__).read_text(encoding="utf-8")

    assert not uses_attribute(source, "subprocess", "Popen")
    assert "BACKUP_SCRIPT" not in all_identifiers(source)
    assert "MANUAL_LOG_FILE" not in all_identifiers(source)


def test_trigger_manual_backup_uses_db_lock_before_enqueue(monkeypatch):
    from app.core.admin_backups import service

    acquire = MagicMock(return_value=_lock_row())
    apply_async = MagicMock(return_value=SimpleNamespace(id="backup-task-1"))

    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", acquire)
    monkeypatch.setattr(
        "app.core.task_runtime.dispatch.dispatch_task_by_name",
        lambda name, **kwargs: apply_async(**kwargs),
    )

    out = service.trigger_manual_backup()

    assert out["started"] is True
    assert out["pid"] is None
    assert out["task_id"] == "backup-task-1"
    acquire.assert_called_once()
    apply_async.assert_called_once_with(
        kwargs={"owner_token": acquire.call_args.kwargs["owner_token"]}
    )


def test_trigger_manual_backup_rejects_when_db_lock_is_active(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", lambda **_: None)
    monkeypatch.setattr(service, "is_manual_backup_running", lambda: True)
    apply_async = MagicMock()
    monkeypatch.setattr(
        "app.core.task_runtime.dispatch.dispatch_task_by_name",
        lambda name, **kwargs: apply_async(**kwargs),
    )

    out = service.trigger_manual_backup()

    assert out == {
        "started": False,
        "pid": None,
        "task_id": None,
        "message": "Un backup manuale e' gia' in corso. Attendi che finisca.",
    }
    apply_async.assert_not_called()


def test_trigger_manual_backup_refuses_enqueue_when_disabled(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setenv("ADMIN_BACKUP_MANUAL_TRIGGER_ENABLED", "false")
    acquire = MagicMock()
    apply_async = MagicMock()
    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", acquire)
    monkeypatch.setattr(
        "app.core.task_runtime.dispatch.dispatch_task_by_name",
        lambda name, **kwargs: apply_async(**kwargs),
    )

    out = service.trigger_manual_backup()

    assert out == {
        "started": False,
        "pid": None,
        "task_id": None,
        "message": (
            "Trigger backup manuale disabilitato in questa "
            "configurazione. Usa lo scheduler/job di deployment."
        ),
    }
    acquire.assert_not_called()
    apply_async.assert_not_called()


def test_trigger_manual_backup_can_enqueue_in_cluster(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setenv("CLUSTER_MODE", "true")
    acquire = MagicMock(return_value=_lock_row())
    apply_async = MagicMock(return_value=SimpleNamespace(id="backup-task-cluster"))
    monkeypatch.setattr(service, "try_acquire_manual_backup_lock", acquire)
    monkeypatch.setattr(
        "app.core.task_runtime.dispatch.dispatch_task_by_name",
        lambda name, **kwargs: apply_async(**kwargs),
    )

    out = service.trigger_manual_backup()

    assert out["started"] is True
    assert out["pid"] is None
    assert out["task_id"] == "backup-task-cluster"
    acquire.assert_called_once()
    apply_async.assert_called_once()


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
