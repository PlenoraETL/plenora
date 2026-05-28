"""
Contract tests BE-GO-READY-STORAGE-ENGINE-016-INTERMEDIATE-ARTIFACT-RECONCILIATION.

Lock the W1 reconciliation wiring on `retention._delete_run`.
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL set before any pg_connection import.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

from contextlib import contextmanager
from typing import Any, List, Tuple
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Helpers


_NEXT_ID = [0]


def _next_recon_id() -> str:
    _NEXT_ID[0] += 1
    return f"recon-uuid-{_NEXT_ID[0]:08d}"


def _make_ref(domain: str, storage_key: str, operation: str = "delete"):
    """Build a `ReconciliationEntryRef` that record_pending stub returns."""
    from app.core.resources.storage_reconciliation import ReconciliationEntryRef
    return ReconciliationEntryRef(
        id=_next_recon_id(),
        domain=domain,
        storage_key=storage_key,
        operation=operation,
    )


def _make_tx_patcher(*, delete_rowcount=1):
    """Build a `transaction()` context manager whose cursor records
    every `cur.execute(...)` call. Returns the patcher + cursor mock."""
    cursor = MagicMock()
    cursor.rowcount = delete_rowcount

    @contextmanager
    def cursor_cm():
        yield cursor

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=cursor_cm)

    @contextmanager
    def tx_cm():
        yield conn

    return (
        patch("app.core.intermediate.retention.unit_of_work", side_effect=tx_cm),
        cursor,
        conn,
    )


def _patch_fetchall(monkeypatch, *, response):
    """Monkeypatch the bound `_fetchall` symbol used by `_delete_run`."""
    calls: List[Tuple[str, tuple]] = []

    def _fake_fetchall(sql, params=()):
        calls.append((sql, tuple(params)))
        return list(response)

    import app.core.intermediate.retention as retention
    monkeypatch.setattr(retention, "_fetchall", _fake_fetchall)
    return calls


def _executed_sqls(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list]


# 1. No artifacts: only DELETE, no reconciliation calls


def test_no_artifacts_only_deletes_run_no_reconciliation(monkeypatch):
    """Dominant case in production (`metadata_only`): no node_meta
    row has a non-NULL artifact_uri with cleanup-eligible status."""
    from app.core.intermediate import retention as idb

    fa_calls = _patch_fetchall(monkeypatch, response=[])
    patcher, cursor, _conn = _make_tx_patcher()

    record_pending_calls: List[dict] = []
    compensate_calls: List[dict] = []
    mark_done_calls: List[str] = []
    mark_fail_calls: List[tuple] = []

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=lambda cur, **kw: record_pending_calls.append(kw)), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               side_effect=lambda key, **kw: (compensate_calls.append({"key": key, **kw}), "ok")[1]), \
         patch("app.core.resources.storage_reconciliation.mark_done",
               side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt",
               side_effect=lambda rid, **kw: mark_fail_calls.append((rid, kw))):
        idb._delete_run("run-empty")

    # Pre-fetch SQL filtered by cleanup-eligible status + artifact_uri NOT NULL.
    assert len(fa_calls) == 1
    sql, params = fa_calls[0]
    assert "FROM node_meta" in sql
    assert "artifact_uri IS NOT NULL" in sql
    assert "artifact_status IN ('complete', 'failed')" in sql
    assert params == ("run-empty",)

    # Only DELETE inside the transaction.
    sqls = _executed_sqls(cursor)
    assert len(sqls) == 1
    assert "DELETE FROM runs WHERE run_id = %s" in sqls[0]

    # Zero reconciliation activity.
    assert record_pending_calls == []
    assert compensate_calls == []
    assert mark_done_calls == []
    assert mark_fail_calls == []


# 2. Single complete artifact: 2 pending (parquet + manifest), 2 mark_done


def test_single_complete_artifact_records_pair_and_marks_done(monkeypatch):
    """One node with canonical `<prefix>/data.parquet` produces:
    - 2 record_pending entries (parquet + derived manifest sibling)
    - DELETE inside same transaction
    - 2 compensating_delete + 2 mark_done post-commit."""
    from app.core.intermediate import retention as idb

    artifact_key = "intermediate/group=g1/project=p1/run=r1/module=manipola/node=n1/kind=final/data.parquet"
    expected_manifest = "intermediate/group=g1/project=p1/run=r1/module=manipola/node=n1/kind=final/manifest.json"

    _patch_fetchall(
        monkeypatch,
        response=[{"node_id": "n1", "artifact_uri": artifact_key}],
    )
    patcher, cursor, _conn = _make_tx_patcher()

    record_pending_calls: List[dict] = []

    def _fake_record_pending(cur, **kwargs):
        record_pending_calls.append(kwargs)
        return _make_ref(kwargs["domain"], kwargs["storage_key"])

    compensate_calls: List[str] = []
    mark_done_calls: List[str] = []

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=_fake_record_pending), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               side_effect=lambda key, **kw: (compensate_calls.append(key), "ok")[1]), \
         patch("app.core.resources.storage_reconciliation.mark_done",
               side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt",
               side_effect=lambda rid, **kw: pytest.fail(
                   "mark_failed_attempt called on happy path: "
                   f"{rid} {kw}",
               )):
        idb._delete_run("r1")

    # Two pending entries: parquet + manifest.
    assert len(record_pending_calls) == 2
    keys_recorded = [kw["storage_key"] for kw in record_pending_calls]
    assert artifact_key in keys_recorded
    assert expected_manifest in keys_recorded
    # Both share the canonical domain + reason + extras shape.
    for kw in record_pending_calls:
        assert kw["domain"] == "intermediate_artifact"
        assert kw["operation"] == "delete"
        assert kw["reason"] == "run_delete_cascade"
        assert kw["extras"]["run_id"] == "r1"
        assert kw["extras"]["node_id"] == "n1"
        assert kw["extras"]["kind"] in ("data.parquet", "manifest.json")
    # Kinds distinct.
    kinds = {kw["extras"]["kind"] for kw in record_pending_calls}
    assert kinds == {"data.parquet", "manifest.json"}

    # SQL order on the cursor: 2 INSERT (record_pending happens via
    # the patched function, which doesn't go through cur.execute since
    # it's stubbed) + 1 DELETE. Since record_pending is patched out,
    # only the DELETE shows up in cursor.execute history.
    sqls = _executed_sqls(cursor)
    assert len(sqls) == 1
    assert "DELETE FROM runs" in sqls[0]

    # Both compensating_delete calls fired with the right keys.
    assert set(compensate_calls) == {artifact_key, expected_manifest}

    # Both mark_done called.
    assert len(mark_done_calls) == 2


# 3. URI with scheme is skipped (Python-side filter)


def test_artifact_uri_with_scheme_is_skipped(monkeypatch):
    """Defense: if a future writer stores `s3://...` or `file://...`,
    the W1 path skips it (cannot guess how to delete via runtime
    object storage). No record_pending, no compensating_delete."""
    from app.core.intermediate import retention as idb

    _patch_fetchall(
        monkeypatch,
        response=[
            {"node_id": "n1", "artifact_uri": "s3://bucket/path/data.parquet"},
            {"node_id": "n2", "artifact_uri": "file:///tmp/data.parquet"},
        ],
    )
    patcher, cursor, _conn = _make_tx_patcher()

    rec: List[dict] = []
    comp: List[str] = []

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=lambda cur, **kw: rec.append(kw)), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               side_effect=lambda key, **kw: (comp.append(key), "ok")[1]), \
         patch("app.core.resources.storage_reconciliation.mark_done"), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt"):
        idb._delete_run("r-scheme")

    assert rec == []
    assert comp == []
    # DELETE still happens.
    sqls = _executed_sqls(cursor)
    assert len(sqls) == 1


# 4. Non-canonical path: parquet recorded, manifest NOT derived


def test_artifact_uri_not_ending_in_data_parquet_skips_manifest(monkeypatch):
    """Future writer with a different layout (e.g. single-file
    `<prefix>/snapshot.parquet`): we record the parquet itself but
    DO NOT derive a manifest sibling we don't know exists."""
    from app.core.intermediate import retention as idb

    custom_key = "intermediate/group=g/project=p/run=r/module=m/node=n/kind=k/snapshot.parquet"
    _patch_fetchall(
        monkeypatch,
        response=[{"node_id": "n1", "artifact_uri": custom_key}],
    )
    patcher, cursor, _conn = _make_tx_patcher()

    rec: List[dict] = []

    def _fake_record_pending(cur, **kwargs):
        rec.append(kwargs)
        return _make_ref(kwargs["domain"], kwargs["storage_key"])

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=_fake_record_pending), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               return_value="ok"), \
         patch("app.core.resources.storage_reconciliation.mark_done"), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt"):
        idb._delete_run("r-custom")

    assert len(rec) == 1
    assert rec[0]["storage_key"] == custom_key
    assert rec[0]["extras"]["kind"] == "data.parquet"


# 5. compensating_delete failure → mark_failed_attempt


def test_compensating_delete_failed_marks_failed_attempt(monkeypatch):
    """ADR-0011: storage failure does NOT propagate; the run row is
    still gone. The new contract: failure is now persisted via
    mark_failed_attempt."""
    from app.core.intermediate import retention as idb

    artifact_key = "intermediate/group=g/project=p/run=r/module=m/node=n/kind=final/data.parquet"
    _patch_fetchall(
        monkeypatch,
        response=[{"node_id": "n1", "artifact_uri": artifact_key}],
    )
    patcher, cursor, _conn = _make_tx_patcher()

    refs: List[Any] = []

    def _fake_record_pending(cur, **kwargs):
        ref = _make_ref(kwargs["domain"], kwargs["storage_key"])
        refs.append(ref)
        return ref

    mark_done_calls: List[str] = []
    mark_fail_calls: List[tuple] = []

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=_fake_record_pending), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               return_value="failed"), \
         patch("app.core.resources.storage_reconciliation.mark_done",
               side_effect=lambda rid: mark_done_calls.append(rid)), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt",
               side_effect=lambda rid, **kw: mark_fail_calls.append((rid, kw))):
        idb._delete_run("r-fail")

    # Both pair entries marked failed (parquet + manifest).
    assert len(mark_fail_calls) == 2
    assert mark_done_calls == []
    for rid, kw in mark_fail_calls:
        assert kw == {"error": "compensating_delete_outcome=failed"}
        # Each ref id appears exactly once in the failed list.
        ref_ids = [r.id for r in refs]
        assert rid in ref_ids


# 6. SQL filter: status complete/failed + artifact_uri IS NOT NULL


def test_pre_fetch_sql_includes_failed_and_excludes_null_at_db_level(monkeypatch):
    """Failed artifacts with artifact_uri are cleanup-eligible; null rows
    remain filtered at the DB level."""
    from app.core.intermediate import retention as idb

    fa_calls = _patch_fetchall(monkeypatch, response=[])
    patcher, _cursor, _conn = _make_tx_patcher()

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending"), \
         patch("app.infrastructure.storage.compensation.compensating_delete"), \
         patch("app.core.resources.storage_reconciliation.mark_done"), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt"):
        idb._delete_run("r-x")

    sql, _ = fa_calls[0]
    assert "artifact_uri IS NOT NULL" in sql
    assert "artifact_status IN ('complete', 'failed')" in sql
    # Schema-agnostic via search_path: no `plenora.` prefix leak.
    assert "plenora.node_meta" not in sql


# 7. Multiple nodes: scales to 2N pending + 2N compensating_delete


def test_multiple_nodes_each_yield_pair_entries(monkeypatch):
    """A run with 3 full_artifact nodes produces 6 pending entries
    (3 parquet + 3 manifest) inside the same transaction."""
    from app.core.intermediate import retention as idb

    keys = [
        f"intermediate/group=g/project=p/run=r/module=m/node=n{i}/kind=final/data.parquet"
        for i in range(3)
    ]
    _patch_fetchall(
        monkeypatch,
        response=[
            {"node_id": f"n{i}", "artifact_uri": keys[i]} for i in range(3)
        ],
    )
    patcher, cursor, _conn = _make_tx_patcher()

    rec: List[dict] = []

    def _fake_record_pending(cur, **kwargs):
        rec.append(kwargs)
        return _make_ref(kwargs["domain"], kwargs["storage_key"])

    comp: List[str] = []
    done: List[str] = []

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=_fake_record_pending), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               side_effect=lambda key, **kw: (comp.append(key), "ok")[1]), \
         patch("app.core.resources.storage_reconciliation.mark_done",
               side_effect=lambda rid: done.append(rid)), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt"):
        idb._delete_run("r-multi")

    # 3 parquet + 3 manifest = 6 pending.
    assert len(rec) == 6
    # 6 compensating_delete + 6 mark_done.
    assert len(comp) == 6
    assert len(done) == 6
    # Each parquet produced its derived manifest.
    parquet_count = sum(1 for k in comp if k.endswith("/data.parquet"))
    manifest_count = sum(1 for k in comp if k.endswith("/manifest.json"))
    assert parquet_count == 3
    assert manifest_count == 3


# 8. delete_all_runs_for_canvas integrates per-run reconciliation


def test_delete_all_runs_for_canvas_reconciles_each_run(monkeypatch):
    """`delete_all_runs_for_canvas` loops over runs calling
    `_delete_run`. Each invocation must trigger its own reconciliation
    pass (not just the last one). Sanity check: 2 runs with 1
    artifact each → 2 pre-fetch SELECTs against `node_meta`."""
    from app.core.intermediate import retention as idb

    # Two runs, each with one canonical artifact.
    runs_response = [{"run_id": "r-a"}, {"run_id": "r-b"}]
    artifact_a = "intermediate/group=g/project=p/run=r-a/module=m/node=n/kind=final/data.parquet"
    artifact_b = "intermediate/group=g/project=p/run=r-b/module=m/node=n/kind=final/data.parquet"

    fa_calls: List[Tuple[str, tuple]] = []
    pending_seen: List[str] = []

    def _fake_fetchall(sql, params=()):
        fa_calls.append((sql, tuple(params)))
        if "SELECT run_id FROM runs" in sql:
            return runs_response
        if "FROM node_meta" in sql:
            run_id = params[0]
            if run_id == "r-a":
                return [{"node_id": "n1", "artifact_uri": artifact_a}]
            if run_id == "r-b":
                return [{"node_id": "n1", "artifact_uri": artifact_b}]
        return []

    patcher, cursor, _conn = _make_tx_patcher()

    def _fake_record_pending(cur, **kwargs):
        pending_seen.append(kwargs["storage_key"])
        return _make_ref(kwargs["domain"], kwargs["storage_key"])

    with patcher, \
         patch("app.core.resources.storage_reconciliation.record_pending",
               side_effect=_fake_record_pending), \
         patch("app.infrastructure.storage.compensation.compensating_delete",
               return_value="ok"), \
         patch("app.core.resources.storage_reconciliation.mark_done"), \
         patch("app.core.resources.storage_reconciliation.mark_failed_attempt"):
        deleted = idb.delete_all_runs_for_canvas(
            "canvas-x",
            fetchall_func=_fake_fetchall,
            delete_run_func=lambda run_id: idb._delete_run(
                run_id,
                fetchall_func=_fake_fetchall,
            ),
        )

    assert deleted == 2
    # 1 outer SELECT runs + 2 per-run SELECT node_meta.
    node_meta_selects = [c for c in fa_calls if "FROM node_meta" in c[0]]
    assert len(node_meta_selects) == 2
    # Both runs' artifacts (parquet + manifest each = 4 total) recorded.
    assert artifact_a in pending_seen
    assert artifact_b in pending_seen
    assert len(pending_seen) == 4  # 2 runs × (parquet + manifest)
