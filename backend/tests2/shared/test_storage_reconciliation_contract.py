"""Contract test BE-GO-READY-STORAGE-ENGINE-005 — typed boundary
`app.shared.storage_reconciliation`.

Locks the 4 boundary invariants:

1. `record_pending` runs INSERT with RETURNING id inside the
   caller's cursor; returns a `ReconciliationEntryRef` with the
   freshly-issued UUID and echoes `domain` / `storage_key` /
   `operation`. Frozen dataclass.
2. `record_pending` propagates SQL errors to the caller (no
   silent swallow — the caller's transaction owns rollback).
3. `mark_done` / `mark_failed_attempt` / `mark_orphan_deleted`
   delegate to `app.db.pg_connection.execute` (autocommit) with
   the right state transition; never raise (best-effort).
4. `extras` is JSON-serialized; `extras=None` writes SQL NULL.

Boundary
========
Source-bound: monkeypatch `app.db.pg_connection.execute`. No
DB connection required. No import of FastAPI / boto3 / settings
in the module under test (asserted explicitly).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

from app.shared.storage_reconciliation import (
    ReconciliationEntryRef,
    mark_done,
    mark_failed_attempt,
    mark_orphan_deleted,
    record_pending,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# record_pending


def _make_cursor(*, return_id: str = "11111111-1111-1111-1111-111111111111"):
    cur = MagicMock()
    cur.fetchone.return_value = {"id": return_id}
    return cur


def test_record_pending_returns_ref_with_returned_id():
    cur = _make_cursor(return_id="aaaa1111-bbbb-2222-cccc-333344445555")
    ref = record_pending(
        cur,
        domain="file_source",
        storage_key="file-sources/abc/file.csv",
        operation="delete",
        reason="row_delete_post_commit",
        extras={"file_id": "fs-123"},
    )
    assert isinstance(ref, ReconciliationEntryRef)
    assert ref.id == "aaaa1111-bbbb-2222-cccc-333344445555"
    assert ref.domain == "file_source"
    assert ref.storage_key == "file-sources/abc/file.csv"
    assert ref.operation == "delete"


def test_reconciliation_entry_ref_is_frozen():
    ref = ReconciliationEntryRef(
        id="x", domain="d", storage_key="k", operation="delete",
    )
    with pytest.raises(Exception):
        ref.id = "mutated"  # type: ignore[misc]


def test_record_pending_executes_insert_with_pending_state_and_jsonb_cast():
    cur = _make_cursor()
    record_pending(
        cur,
        domain="file_source",
        storage_key="key",
        operation="delete",
        reason="row_delete_post_commit",
        extras={"file_id": "fs-1"},
    )
    sql, params = cur.execute.call_args[0]
    assert "INSERT INTO plenora.storage_reconciliation_log" in sql
    # State is hard-coded to 'pending' in the SQL, not a parameter.
    assert "'pending'" in sql
    assert "RETURNING id" in sql
    # JSONB cast is required because psycopg2 doesn't auto-coerce
    # str -> jsonb on a JSONB column.
    assert "::jsonb" in sql
    assert params[0] == "file_source"
    assert params[1] == "key"
    assert params[2] == "delete"
    assert params[3] == "row_delete_post_commit"
    # extras is serialized to JSON.
    assert json.loads(params[4]) == {"file_id": "fs-1"}


def test_record_pending_extras_none_passes_sql_null():
    cur = _make_cursor()
    record_pending(
        cur,
        domain="result_blob",
        storage_key="results/p/r.bin",
        operation="delete",
        reason="row_delete_post_commit",
        extras=None,
    )
    _, params = cur.execute.call_args[0]
    assert params[4] is None


def test_record_pending_handles_tuple_cursor_fetchone():
    """Some psycopg2 cursor flavors return a tuple, not a dict."""
    cur = MagicMock()
    cur.fetchone.return_value = ("uuid-from-tuple",)
    ref = record_pending(
        cur,
        domain="d",
        storage_key="k",
        operation="delete",
        reason="r",
    )
    assert ref.id == "uuid-from-tuple"


def test_record_pending_propagates_sql_error():
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError("constraint violation")
    with pytest.raises(RuntimeError, match="constraint violation"):
        record_pending(
            cur,
            domain="d",
            storage_key="k",
            operation="delete",
            reason="r",
        )


def test_record_pending_accepts_upload_intent_operation():
    """Boundary accepts both 'delete' and 'upload_intent' even though
    only 'delete' is used by the first integration. The schema's
    state CHECK does not restrict `operation`; the future batch 008
    consumer can land without a boundary change."""
    cur = _make_cursor()
    ref = record_pending(
        cur,
        domain="result",
        storage_key="results/p/r.bin",
        operation="upload_intent",
        reason="upload_before_db_save",
    )
    assert ref.operation == "upload_intent"


# mark_done / mark_failed_attempt / mark_orphan_deleted


def _capture_pg_execute(monkeypatch) -> List[Tuple[str, tuple]]:
    """Monkeypatch `app.db.pg_connection.execute` to record the calls
    and return a no-op result."""
    calls: List[Tuple[str, tuple]] = []

    def _fake_execute(sql: str, params: tuple = ()):
        calls.append((sql, params))

        class _Result:
            rowcount = 1

            def fetchone(self):
                return None

        return _Result()

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _fake_execute)
    return calls


def test_mark_done_issues_update_to_state_done(monkeypatch):
    calls = _capture_pg_execute(monkeypatch)
    mark_done("rec-1")
    assert len(calls) == 1
    sql, params = calls[0]
    assert "UPDATE plenora.storage_reconciliation_log" in sql
    assert "state = 'done'" in sql
    assert "updated_at = now()" in sql
    assert params == ("rec-1",)


def test_mark_orphan_deleted_issues_update_to_state_orphan_deleted(monkeypatch):
    calls = _capture_pg_execute(monkeypatch)
    mark_orphan_deleted("rec-2")
    assert len(calls) == 1
    sql, params = calls[0]
    assert "state = 'orphan_deleted'" in sql
    assert params == ("rec-2",)


def test_mark_failed_attempt_increments_and_sets_error(monkeypatch):
    calls = _capture_pg_execute(monkeypatch)
    mark_failed_attempt("rec-3", error="RuntimeError: S3 timeout")
    assert len(calls) == 1
    sql, params = calls[0]
    assert "attempts = attempts + 1" in sql
    assert "last_error = ?" in sql
    assert "next_retry_at = ?" in sql
    # Does NOT auto-promote to 'lost' (reconciler's job, batch 006).
    assert "state = 'lost'" not in sql
    assert params == ("RuntimeError: S3 timeout", None, "rec-3")


def test_mark_failed_attempt_passes_next_retry_at(monkeypatch):
    calls = _capture_pg_execute(monkeypatch)
    when = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    mark_failed_attempt("rec-4", error="boom", next_retry_at=when)
    _, params = calls[0]
    assert params == ("boom", when, "rec-4")


# Best-effort contract: mark_* helpers swallow exceptions.
#
# The log shape itself is exercised by the happy-path tests above
# (they pin the SQL + params). These swallow tests pin the contract
# that an exception in pg_connection.execute does NOT propagate to
# the caller, because the caller is in a post-commit cleanup path
# whose user-visible response is already on the wire.


def _install_raising_pg_execute(monkeypatch):
    def _raise(*_a, **_kw):
        raise RuntimeError("DB unreachable")

    import app.db.pg_connection as pg_conn

    monkeypatch.setattr(pg_conn, "execute", _raise)


def test_mark_done_swallows_exception(monkeypatch):
    _install_raising_pg_execute(monkeypatch)
    # Must NOT raise. Returning None is the only contract.
    assert mark_done("rec-x") is None


def test_mark_failed_attempt_swallows_exception(monkeypatch):
    _install_raising_pg_execute(monkeypatch)
    assert mark_failed_attempt("rec-y", error="boom") is None


def test_mark_orphan_deleted_swallows_exception(monkeypatch):
    _install_raising_pg_execute(monkeypatch)
    assert mark_orphan_deleted("rec-z") is None


# Module hygiene: leaf module, no FastAPI / boto3 / settings imports


def test_module_is_leaf_no_fastapi_boto3_settings():
    """The boundary must stay pure: typed dataclass + 4 functions.
    Importing it must not transitively pull FastAPI, boto3, or
    settings — that would make the future reconciler harder to test
    in isolation. Asserted on actual import statements only (the
    docstring mentions these names rhetorically and must not trigger
    a false positive)."""
    import re

    import app.shared.storage_reconciliation  # noqa: F401

    src = open(
        app.shared.storage_reconciliation.__file__, encoding="utf-8"
    ).read()
    # Strip module docstring + comments before pattern-matching.
    # Conservative approach: scan line-by-line for `import` / `from`
    # statements only. Comments (`#`) and docstrings (lines inside
    # triple-quoted strings) are excluded by checking for code-only
    # lines that contain an import keyword at the start of a token.
    forbidden = ("fastapi", "boto3", "starlette", "app.config")
    for line in src.splitlines():
        stripped = line.strip()
        # Skip comments and lines clearly inside a docstring
        # (heuristic: lines not starting with `import` or `from`
        # cannot be import statements).
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for forbidden_mod in forbidden:
            assert forbidden_mod not in stripped, (
                f"storage_reconciliation must not import {forbidden_mod!r}; "
                f"offending line: {stripped!r}"
            )
