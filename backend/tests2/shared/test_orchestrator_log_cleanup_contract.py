"""Contract tests for the orchestrator-owned log_runs cleanup boundary."""

from __future__ import annotations

from typing import List, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Helpers


class _FakeResult:
    """Mirror the `_Result` shape returned by `pg_connection.execute`."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def fetchone(self):
        return None


def _capture_execute(
    monkeypatch, *, rowcount: int = 1, raise_exc: Exception | None = None,
) -> List[Tuple[str, tuple]]:
    """Spy `app.db.pg_connection.execute`.

    The DAL imports `execute` lazily inside the function body so
    that pre-existing tests that monkeypatch
    `app.db.pg_connection.execute` (e.g. orchestrator cleanup
    suites) continue to work. We honor the same convention here.
    """
    calls: List[Tuple[str, tuple]] = []

    def _fake_execute(sql, params=()):
        calls.append((sql, tuple(params)))
        if raise_exc is not None:
            raise raise_exc
        return _FakeResult(rowcount)

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _fake_execute)
    return calls


# delete_task_log_runs


def test_delete_task_log_runs_issues_canonical_sql(monkeypatch):
    from app.core.orchestrator.log_cleanup import delete_task_log_runs

    calls = _capture_execute(monkeypatch, rowcount=1)
    delete_task_log_runs("task-abc-123")
    assert len(calls) == 1
    sql, params = calls[0]
    # Canonical SQL: unqualified `log_runs` (search_path resolved).
    assert sql == "DELETE FROM log_runs WHERE run_id = ?"
    # Single positional placeholder bound to the supplied run_id.
    assert params == ("task-abc-123",)


def test_delete_task_log_runs_returns_rowcount_when_present(monkeypatch):
    from app.core.orchestrator.log_cleanup import delete_task_log_runs

    _capture_execute(monkeypatch, rowcount=1)
    out = delete_task_log_runs("task-abc")
    assert isinstance(out, int)
    assert out == 1


def test_delete_task_log_runs_returns_zero_when_absent(monkeypatch):
    """Idempotent: deleting a non-existing run_id is not an error,
    rowcount is 0. Caller treats this as a no-op."""
    from app.core.orchestrator.log_cleanup import delete_task_log_runs

    _capture_execute(monkeypatch, rowcount=0)
    assert delete_task_log_runs("task-unknown") == 0


def test_delete_task_log_runs_does_not_translate_rowcount(monkeypatch):
    """Contract: the helper returns `int` rowcount, NOT a deleted
    bool. Translation would lose information for the caller and
    couple the boundary to a boolean-only consumer."""
    from app.core.orchestrator.log_cleanup import delete_task_log_runs

    _capture_execute(monkeypatch, rowcount=2)  # hypothetical multi-row
    out = delete_task_log_runs("task-multi")
    assert out == 2  # NOT True, NOT 1
    assert not isinstance(out, bool)


def test_delete_task_log_runs_propagates_sql_error(monkeypatch):
    """Contract: boundary must NOT swallow.

    The router runs in a best-effort cascade and decides log+continue at the
    call site.
    """
    from app.core.orchestrator.log_cleanup import delete_task_log_runs

    _capture_execute(
        monkeypatch, raise_exc=RuntimeError("DB unreachable"),
    )
    with pytest.raises(RuntimeError, match="DB unreachable"):
        delete_task_log_runs("task-x")


# Router source-bound: orchestrator no longer imports pg_connection


def test_orchestrator_router_uses_domain_cleanup_not_db_service():
    """`orchestrator/router.py` must delegate the `log_runs`
    cleanup to the orchestrator domain cleanup boundary and not import
    DB helpers directly. Pinned by the architecture-guard `Rule 3`
    allowlist (orchestrator removed) plus this source-bound check."""
    from app.core.orchestrator import router as orchestrator_router

    src = open(orchestrator_router.__file__, encoding="utf-8").read()
    legacy_module = "app.db." + "log_runs" + "_db_" + "service"
    assert "from app.db.pg_connection" not in src
    assert "import app.db.pg_connection" not in src
    assert legacy_module not in src
    assert "delete_task_log_runs" in src


def test_orchestrator_log_cleanup_owns_sql_not_db_service():
    from app.core.orchestrator import log_cleanup

    src = open(log_cleanup.__file__, encoding="utf-8").read()
    legacy_module = "app.db." + "log_runs" + "_db_" + "service"
    assert legacy_module not in src
    assert "DELETE FROM log_runs WHERE run_id = ?" in src


# Module hygiene


def test_legacy_log_runs_adapter_stays_tombstoned():
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent.parent
    adapter_file = "log_runs" + "_db_service.py"
    assert not (backend_root / "app/db" / adapter_file).exists()
