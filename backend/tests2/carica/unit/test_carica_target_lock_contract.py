"""Contracts for CARICA distributed target locks."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.carica.target_lock import (
    CaricaTargetLock,
    acquire_carica_target_lock,
    release_carica_target_lock,
    resolve_carica_target_lock,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


class _FakeCursor:
    def __init__(self, row: dict[str, object] | None = None, rowcount: int = 0):
        self.row = row
        self.rowcount = rowcount
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@contextmanager
def _fake_unit_of_work(conn: _FakeConnection):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def test_db_target_lock_is_tenant_aware_and_project_independent_inside_group():
    node_data = {
        "connection_id": "conn-1",
        "schema_name": "public",
        "table_name": "dest",
    }

    first = resolve_carica_target_lock(
        node_data=node_data,
        project_id="project-a",
        group_id="group-1",
        write_branch="db",
    )
    same_tenant = resolve_carica_target_lock(
        node_data=node_data,
        project_id="project-b",
        group_id="group-1",
        write_branch="db",
    )
    other_tenant = resolve_carica_target_lock(
        node_data=node_data,
        project_id="project-a",
        group_id="group-2",
        write_branch="db",
    )

    assert first is not None
    assert same_tenant is not None
    assert other_tenant is not None
    assert first.key == same_tenant.key
    assert first.key != other_tenant.key
    assert '"group_id":"group-1"' in first.identity


def test_unknown_destination_keeps_legacy_no_lock_behavior():
    assert (
        resolve_carica_target_lock(
            node_data={"connection_id": "conn-1"},
            project_id="project-1",
            group_id="group-1",
            write_branch="db",
        )
        is None
    )
    assert (
        resolve_carica_target_lock(
            node_data={"dest_type": "rest"},
            project_id="project-1",
            group_id="group-1",
            write_branch="rest",
        )
        is None
    )


def test_rest_and_arcgis_targets_are_canonicalized():
    rest = resolve_carica_target_lock(
        node_data={
            "dest_type": "rest",
            "rest": {
                "url": "HTTPS://API.EXAMPLE.TEST/items/?b=2&a=1",
                "method": "post",
            },
        },
        project_id="project-1",
        group_id="group-1",
        write_branch="rest",
    )
    arcgis = resolve_carica_target_lock(
        node_data={
            "arcgis_connection_id": "arc-1",
            "arcgis_operation": "create_service",
            "arcgis_new_service_name": "DestService",
            "arcgis_folder_id": "folder-1",
        },
        project_id="project-1",
        group_id="group-1",
        write_branch="arcgis",
    )

    assert rest is not None
    assert '"method":"POST"' in rest.identity
    assert '"url":"https://api.example.test/items?a=1&b=2"' in rest.identity
    assert arcgis is not None
    assert '"service_name":"DestService"' in arcgis.identity


def test_acquire_returns_handle_when_insert_or_expired_update_wins(monkeypatch):
    cursor = _FakeCursor(row={"owner_token": "owner-1"})
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(
        "app.core.carica.target_lock_store.unit_of_work",
        lambda: _fake_unit_of_work(conn),
    )
    target = CaricaTargetLock(
        key="carica:db:hash",
        kind="db",
        identity='{"target":"db"}',
        group_id="group-1",
    )

    handle = acquire_carica_target_lock(
        target=target,
        owner_token="owner-1",
        ttl_seconds=60,
    )

    assert handle is not None
    assert handle.owner_token == "owner-1"
    assert conn.commits == 1
    assert conn.rollbacks == 0
    sql, params = cursor.executed[0]
    assert "ON CONFLICT (lock_key) DO UPDATE" in sql
    assert "owner_token = EXCLUDED.owner_token" in sql
    assert params[0] == "carica:db:hash"
    assert params[4] == "owner-1"


def test_acquire_returns_none_when_unexpired_other_owner_holds_lock(monkeypatch):
    cursor = _FakeCursor(row=None)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(
        "app.core.carica.target_lock_store.unit_of_work",
        lambda: _fake_unit_of_work(conn),
    )
    target = CaricaTargetLock(
        key="carica:db:hash",
        kind="db",
        identity="{}",
        group_id="group-1",
    )

    assert (
        acquire_carica_target_lock(
            target=target,
            owner_token="owner-2",
            ttl_seconds=60,
        )
        is None
    )
    assert conn.commits == 1


def test_release_deletes_only_matching_owner(monkeypatch):
    cursor = _FakeCursor(rowcount=1)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(
        "app.core.carica.target_lock_store.unit_of_work",
        lambda: _fake_unit_of_work(conn),
    )
    target = CaricaTargetLock(
        key="carica:db:hash",
        kind="db",
        identity="{}",
        group_id="group-1",
    )

    released = release_carica_target_lock(
        SimpleNamespace(target=target, owner_token="owner-1")
    )

    assert released is True
    sql, params = cursor.executed[0]
    assert "DELETE FROM carica_target_locks" in sql
    assert "owner_token = %s" in sql
    assert params == ("carica:db:hash", "owner-1")


def test_task_uses_target_lock_before_loading_upstream_and_releases_in_finally(monkeypatch):
    import workers.tasks.carica_task as carica_task

    events: list[str] = []
    monkeypatch.setattr(carica_task, "is_already_completed", lambda _run_id: False)
    monkeypatch.setattr(carica_task, "_update_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        carica_task.DebugPayloadBuilder,
        "build",
        lambda self, **kwargs: {},
    )
    handle = SimpleNamespace(
        target=SimpleNamespace(key="carica:db:hash", kind="db"),
        owner_token="owner-1",
    )

    monkeypatch.setattr(
        carica_task,
        "resolve_carica_target_lock",
        lambda **kwargs: SimpleNamespace(key="carica:db:hash", kind="db"),
    )
    monkeypatch.setattr(
        carica_task,
        "acquire_carica_target_lock",
        lambda **kwargs: events.append("acquire") or handle,
    )
    monkeypatch.setattr(
        carica_task,
        "release_carica_target_lock",
        lambda _handle: events.append("release") or True,
    )
    monkeypatch.setattr(
        "app.core.auth.async_user_recheck.recheck_user_authorized",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.projects.service.ProjectService.get_project_metadata",
        lambda self, project_id: SimpleNamespace(group_id="group-1"),
    )
    monkeypatch.setattr(carica_task, "open_run", lambda **kwargs: None)
    monkeypatch.setattr(carica_task, "close_run", lambda **kwargs: None)
    monkeypatch.setattr(carica_task, "set_run_context", lambda **kwargs: None)

    def _load(*args, **kwargs):
        events.append("load")
        raise RuntimeError("stop after lock")

    monkeypatch.setattr(carica_task, "_load_upstream_data", _load)
    fake_self = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        update_state=MagicMock(),
    )

    result = carica_task.execute_carica_task(
        fake_self,
        project_id="project-1",
        canvas={},
        carica_node_data={
            "connection_id": "conn-1",
            "schema_name": "public",
            "table_name": "dest",
            "write_mode": "append",
        },
        run_id="run-1",
    )

    assert result["success"] is False
    assert events == ["acquire", "load", "release"]


def test_task_fails_fast_when_target_lock_is_busy(monkeypatch):
    import workers.tasks.carica_task as carica_task

    events: list[str] = []
    monkeypatch.setattr(carica_task, "is_already_completed", lambda _run_id: False)
    monkeypatch.setattr(carica_task, "_update_progress", lambda *args, **kwargs: None)
    lock_target = SimpleNamespace(key="carica:db:hash", kind="db")
    close_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        carica_task,
        "resolve_carica_target_lock",
        lambda **kwargs: lock_target,
    )
    monkeypatch.setattr(
        carica_task,
        "acquire_carica_target_lock",
        lambda **kwargs: events.append("acquire") or None,
    )
    monkeypatch.setattr(
        carica_task,
        "release_carica_target_lock",
        lambda _handle: events.append("release") or True,
    )
    monkeypatch.setattr(
        "app.core.auth.async_user_recheck.recheck_user_authorized",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.core.projects.service.ProjectService.get_project_metadata",
        lambda self, project_id: SimpleNamespace(group_id="group-1"),
    )
    monkeypatch.setattr(carica_task, "open_run", lambda **kwargs: None)
    monkeypatch.setattr(
        carica_task,
        "close_run",
        lambda **kwargs: close_calls.append(kwargs),
    )
    monkeypatch.setattr(carica_task, "set_run_context", lambda **kwargs: None)
    monkeypatch.setattr(
        carica_task,
        "_load_upstream_data",
        lambda *args, **kwargs: events.append("load"),
    )
    fake_self = SimpleNamespace(
        request=SimpleNamespace(id="task-1"),
        update_state=MagicMock(),
    )

    result = carica_task.execute_carica_task(
        fake_self,
        project_id="project-1",
        canvas={},
        carica_node_data={
            "connection_id": "conn-1",
            "schema_name": "public",
            "table_name": "dest",
            "write_mode": "append",
        },
        run_id="run-1",
    )

    assert result["success"] is False
    assert result["status"] == "conflict"
    assert result["error"] == "carica_target_locked"
    assert events == ["acquire"]
    assert close_calls[-1]["status"] == "failure"
