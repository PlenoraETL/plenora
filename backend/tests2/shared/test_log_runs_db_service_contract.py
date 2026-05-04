"""Contract test BE-GO-READY-CONNECTION-ENGINE-006 — typed boundary
`app.db.log_runs_db_service.delete_log_run_by_run_id`.

Locks:

  1. SQL shape: exactly `DELETE FROM log_runs WHERE run_id = ?`
     with the run_id bound positionally as a 1-tuple. Schema is
     resolved via the pool `search_path`; no `plenora.` prefix
     leak.
  2. Return contract: `int` echoing `cursor.rowcount`. The
     orchestrator caller treats this as "rows touched", NOT as a
     deleted-bool — the helper must NOT translate.
  3. Error propagation: any exception from
     `pg_connection.execute` propagates to the caller. The DAL
     does not log, does not swallow. (The orchestrator's best-
     effort `try/except → failed_steps.append("delete_log_runs")`
     is preserved at the call site, not duplicated here.)

Plus router source-bound assertions:

  4. `orchestrator/router.py` no longer imports
     `app.db.pg_connection` and references the helper.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# delete_log_run_by_run_id
# ---------------------------------------------------------------------------


def test_delete_log_run_by_run_id_issues_canonical_sql(monkeypatch):
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    calls = _capture_execute(monkeypatch, rowcount=1)
    delete_log_run_by_run_id("task-abc-123")
    assert len(calls) == 1
    sql, params = calls[0]
    # Canonical SQL: unqualified `log_runs` (search_path resolved).
    assert sql == "DELETE FROM log_runs WHERE run_id = ?"
    # Single positional placeholder bound to the supplied run_id.
    assert params == ("task-abc-123",)


def test_delete_log_run_by_run_id_returns_rowcount_when_present(monkeypatch):
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    _capture_execute(monkeypatch, rowcount=1)
    out = delete_log_run_by_run_id("task-abc")
    assert isinstance(out, int)
    assert out == 1


def test_delete_log_run_by_run_id_returns_zero_when_absent(monkeypatch):
    """Idempotent: deleting a non-existing run_id is not an error,
    rowcount is 0. Caller treats this as a no-op."""
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    _capture_execute(monkeypatch, rowcount=0)
    assert delete_log_run_by_run_id("task-unknown") == 0


def test_delete_log_run_by_run_id_does_not_translate_rowcount(monkeypatch):
    """Contract: the helper returns `int` rowcount, NOT a deleted
    bool. Translation would lose information for the caller and
    couple the DAL to one specific consumer."""
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    _capture_execute(monkeypatch, rowcount=2)  # hypothetical multi-row
    out = delete_log_run_by_run_id("task-multi")
    assert out == 2  # NOT True, NOT 1
    assert not isinstance(out, bool)


def test_delete_log_run_by_run_id_propagates_sql_error(monkeypatch):
    """Contract: DAL must NOT swallow. The orchestrator caller
    runs in a best-effort cascade and decides log+continue at the
    call site."""
    from app.db.log_runs_db_service import delete_log_run_by_run_id

    _capture_execute(
        monkeypatch, raise_exc=RuntimeError("DB unreachable"),
    )
    with pytest.raises(RuntimeError, match="DB unreachable"):
        delete_log_run_by_run_id("task-x")


# ---------------------------------------------------------------------------
# Router source-bound: orchestrator no longer imports pg_connection
# ---------------------------------------------------------------------------


def test_orchestrator_router_uses_helper_not_pg_connection():
    """`orchestrator/router.py` must delegate the `log_runs`
    cleanup to `app.db.log_runs_db_service.delete_log_run_by_run_id`
    and not import `pg_connection` directly. Pinned by the
    architecture-guard `Rule 3` allowlist (orchestrator removed)
    plus this source-bound check."""
    from app.core.orchestrator import router as orchestrator_router

    src = open(orchestrator_router.__file__, encoding="utf-8").read()
    assert "from app.db.pg_connection" not in src
    assert "import app.db.pg_connection" not in src
    # The helper is referenced.
    assert "delete_log_run_by_run_id" in src


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------


def test_module_is_leaf_no_app_core_or_app_services_imports():
    """The DAL must stay leaf-y: no upward imports into
    `app.core.*` or `app.services.*`. Future helpers (logs router
    extraction follow-up) extend this module without coupling
    to consumer code."""
    import app.db.log_runs_db_service  # noqa: F401

    src = open(
        app.db.log_runs_db_service.__file__, encoding="utf-8"
    ).read()
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for forbidden in ("app.core.", "app.services."):
            assert forbidden not in stripped, (
                f"log_runs_db_service must not import {forbidden}; "
                f"offending line: {stripped!r}"
            )
