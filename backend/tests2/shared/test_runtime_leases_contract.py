"""Cross-platform worker and task lease persistence contracts."""

from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace

from app.core.task_runtime import runtime_leases


class _Session:
    def __init__(self, *, fetchone_rows=None, fetchall_rows=None):
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return SimpleNamespace(rowcount=1)

    def fetchone(self, sql, params=()):
        self.calls.append((sql, params))
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self, sql, params=()):
        self.calls.append((sql, params))
        return self.fetchall_rows.pop(0) if self.fetchall_rows else []


def _install_session(monkeypatch, session: _Session) -> None:
    @contextmanager
    def fake_unit_of_work():
        yield session

    monkeypatch.setattr(runtime_leases, "unit_of_work", fake_unit_of_work)


def test_claim_task_is_atomic_and_deduplicates_completed(monkeypatch) -> None:
    acquired = _Session(fetchone_rows=[{"task_id": "t1"}])
    _install_session(monkeypatch, acquired)
    assert runtime_leases.claim_task(
        task_id="t1",
        actor_name="pipeline.execute",
        worker_id="w1",
        lease_seconds=45,
    ) == "acquired"
    assert "ON CONFLICT (task_id) DO NOTHING" in acquired.calls[0][0]

    completed = _Session(fetchone_rows=[None, None, {"state": "completed"}])
    _install_session(monkeypatch, completed)
    assert runtime_leases.claim_task(
        task_id="t1",
        actor_name="pipeline.execute",
        worker_id="w2",
        lease_seconds=45,
    ) == "completed"


def test_recover_stale_claim_publishes_terminal_worker_lost(monkeypatch) -> None:
    session = _Session(
        fetchall_rows=[
            [
                {
                    "task_id": "t1",
                    "actor_name": "pipeline.execute",
                    "worker_id": "w1",
                }
            ]
        ]
    )
    _install_session(monkeypatch, session)

    stale = runtime_leases.recover_stale_task_claims(
        limit=10,
        result_ttl_seconds=3600,
    )

    assert stale[0].task_id == "t1"
    result_insert = next(
        (params for sql, params in session.calls if "INSERT INTO task_results" in sql),
        None,
    )
    assert result_insert is not None
    payload = json.loads(result_insert[2])
    assert payload["error"] == "worker_lost"
    assert payload["success"] is False


def test_process_memory_limit_falls_back_without_a_valid_limit() -> None:
    from workers.process_memory_limit import (
        FallbackMemoryLimitHandle,
        apply_process_memory_limit,
    )

    handle = apply_process_memory_limit(pid=0, limit_bytes=0)

    assert isinstance(handle, FallbackMemoryLimitHandle)
    assert handle.enforced is False
