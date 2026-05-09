"""
Contract test - BE-HARDEN-001C E2 + E4 (BE-DELETE-002):

reference-aware + race-safe `delete_file_source`.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures e helper


@pytest.fixture(autouse=True)
def _no_op_reconciliation_boundary(monkeypatch):
    """BE-GO-READY-STORAGE-ENGINE-005: no-op `record_pending` /
    `mark_done` / `mark_failed_attempt` so the reconciliation log
    INSERT (added inside `delete_file_source` post-005) does NOT
    issue an extra `cur.execute` + `cur.fetchone` that this suite's
    cursor mocks aren't shaped to handle.

    Reconciliation behavior is verified separately in
    `tests2/services/test_file_sources_reconciliation_integration.py`.
    Here we only verify the refcount/transactional contract, which
    is orthogonal."""
    from app.core.resources import storage_reconciliation as _recon

    fake_ref = _recon.ReconciliationEntryRef(
        id="autouse-noop",
        domain="file_source",
        storage_key="autouse",
        operation="delete",
    )
    monkeypatch.setattr(
        _recon, "record_pending", lambda cursor, **_kw: fake_ref,
    )
    monkeypatch.setattr(
        _recon, "mark_done", lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        _recon, "mark_failed_attempt", lambda *_a, **_kw: None,
    )


@pytest.fixture
def fake_storage():
    """Mock object storage con .delete(key) registrato."""
    storage = MagicMock()
    storage.delete = MagicMock()
    return storage


def _make_tx_patcher(*, current_storage_key, siblings, delete_rowcount):
    """Costruisce un patcher di `file_sources.transaction` che simula:

      - 1a `cur.execute` SELECT storage_key (con `FOR UPDATE`),
        cur.fetchone() -> {"storage_key": current_storage_key}
        (oppure None se current_storage_key sentinel `_NO_ROW`).
      - 2a `cur.execute` SELECT siblings (con `FOR UPDATE`),
        cur.fetchall() -> list di {"id": "..."}
        Chiamata SOLO se current_storage_key truthy.
      - 3a `cur.execute` DELETE FROM file_sources,
        cur.rowcount -> delete_rowcount.

    Ritorna `(patcher_ctx, cursor_mock, conn_mock)` per assertions.
    """
    cursor = MagicMock()

    fetchone_responses = []
    fetchall_responses = []

    if current_storage_key is _NO_ROW:
        fetchone_responses.append(None)
    else:
        fetchone_responses.append({"storage_key": current_storage_key})
        if current_storage_key:
            fetchall_responses.append([{"id": s} for s in siblings])

    cursor.fetchone = MagicMock(side_effect=fetchone_responses)
    cursor.fetchall = MagicMock(side_effect=fetchall_responses)
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
        patch("app.core.files.db.transaction", side_effect=tx_cm),
        cursor,
        conn,
    )


# Sentinel: nessuna row trovata (fetchone -> None) per il primo SELECT
_NO_ROW = object()


def _executed_sqls(cursor):
    """Lista delle SQL string passate a cur.execute (positional 0)."""
    return [c.args[0] for c in cursor.execute.call_args_list]


# 1. Row unica con storage_key (refcount=0) -> storage.delete chiamata


def test_delete_with_no_refs_proceeds_with_storage_cleanup(fake_storage):
    """Storage_key univoco (legacy data o ultimo ref): nessun sibling ->
    storage.delete chiamata DOPO commit. SQL usa FOR UPDATE."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/file_abc/x.csv",
        siblings=[],
        delete_rowcount=1,
    )

    with patcher, \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    fake_storage.delete.assert_called_once_with("file-sources/file_abc/x.csv")

    sqls = _executed_sqls(cursor)
    # 3 statement: SELECT storage_key + SELECT siblings + DELETE
    assert len(sqls) == 3
    assert "SELECT storage_key FROM file_sources WHERE id = %s FOR UPDATE" in sqls[0]
    assert "WHERE storage_key = %s AND id <> %s FOR UPDATE" in sqls[1]
    assert "DELETE FROM file_sources WHERE id = %s" in sqls[2]
    # Parametri delle SELECT
    assert cursor.execute.call_args_list[0].args[1] == ("file_abc",)
    assert cursor.execute.call_args_list[1].args[1] == (
        "file-sources/file_abc/x.csv", "file_abc",
    )
    assert cursor.execute.call_args_list[2].args[1] == ("file_abc",)


# 2. Row con altri ref -> storage.delete SKIPPATA + log


def test_delete_with_other_refs_skips_storage_cleanup_and_logs(fake_storage):
    """Storage_key condiviso con altre 2 row: siblings list len=2 ->
    storage.delete NON chiamata + log structured. Row corrente DELETE."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/batch_xyz/multi.gpkg",
        siblings=["file_layer2", "file_layer3"],
        delete_rowcount=1,
    )

    fake_logger = MagicMock()
    with patcher, \
         patch.object(fs_db, "logger", fake_logger), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("file_layer1")

    assert out is True
    fake_storage.delete.assert_not_called()
    mock_get_storage.assert_not_called()

    fake_logger.warning.assert_called_once()
    msg = fake_logger.warning.call_args[0][0]
    assert "event=delete_skip_storage_shared_ref" in msg
    assert "file_id=file_layer1" in msg
    assert "storage_key='file-sources/batch_xyz/multi.gpkg'" in msg
    assert "remaining_refs=2" in msg

    # DELETE eseguita lo stesso
    sqls = _executed_sqls(cursor)
    assert any("DELETE FROM file_sources" in s for s in sqls)


# 3. Delete dell'ultimo ref -> storage.delete chiamata


def test_delete_last_remaining_ref_proceeds_with_storage_cleanup(fake_storage):
    """Multi-layer: dopo aver cancellato N-1 fratelli, il delete dell'ultimo
    ref vede siblings vuoti -> storage.delete chiamata."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/batch_xyz/multi.gpkg",
        siblings=[],
        delete_rowcount=1,
    )

    with patcher, \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_last_layer")

    assert out is True
    fake_storage.delete.assert_called_once_with("file-sources/batch_xyz/multi.gpkg")


# 4. BYTEA mode (storage_key=None) -> no siblings query, no storage interaction


def test_delete_bytea_mode_skips_siblings_and_storage(fake_storage):
    """BYTEA: storage_key None -> nessuna sibling query, nessuna interazione
    object storage, solo SELECT corrente + DELETE."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key=None,
        siblings=[],
        delete_rowcount=1,
    )

    with patcher, \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("file_bytea")

    assert out is True
    mock_get_storage.assert_not_called()
    fake_storage.delete.assert_not_called()

    sqls = _executed_sqls(cursor)
    # Solo 2 statement: SELECT storage_key + DELETE (no siblings query)
    assert len(sqls) == 2
    assert "SELECT storage_key" in sqls[0]
    assert "FOR UPDATE" in sqls[0]
    assert "DELETE FROM file_sources" in sqls[1]


# 5. Row inesistente -> ritorna False, no side effect


def test_delete_nonexistent_row_returns_false_no_side_effects(fake_storage):
    """Row inesistente: SELECT ritorna None, no siblings query, DELETE
    rowcount=0 -> False. Nessuna interazione storage."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key=_NO_ROW,
        siblings=[],
        delete_rowcount=0,
    )

    with patcher, \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("ghost")

    assert out is False
    mock_get_storage.assert_not_called()
    fake_storage.delete.assert_not_called()

    sqls = _executed_sqls(cursor)
    # SELECT (None) + DELETE (rowcount=0). No siblings query.
    assert len(sqls) == 2


# 6. E4: usa transaction() (single conn, atomic)


def test_delete_uses_transaction_context_manager(fake_storage):
    """BE-DELETE-002 E4: tutto il flow DB avviene dentro una sola
    `pg_connection.transaction()` (single conn). Verificato indirettamente
    dal fatto che `_make_tx_patcher` patcha `transaction` e tutte le
    cur.execute call accadono sullo stesso cursor mock."""
    from app.core.files import db as fs_db

    patcher, cursor, conn = _make_tx_patcher(
        current_storage_key="file-sources/file_x/y.csv",
        siblings=[],
        delete_rowcount=1,
    )

    with patcher, \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        fs_db.delete_file_source("file_x")

    # conn.cursor() chiamato 1 volta -> single TX boundary
    assert conn.cursor.call_count == 1
    # 3 cur.execute sul medesimo cursor
    assert cursor.execute.call_count == 3


# 7+8. SELECT FOR UPDATE su row corrente E sui siblings


def test_delete_uses_for_update_on_current_row_and_siblings(fake_storage):
    """BE-DELETE-002 E4: entrambe le SELECT (current row + siblings)
    devono usare `FOR UPDATE` per serializzare delete concorrenti su
    blob shared (Race A.2). Il refactor sostituisce COUNT(*) con SELECT
    id FOR UPDATE perche' COUNT(*) non blocca le row."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/batch_x/data",
        siblings=["sib_a"],  # 1 sibling -> skip storage path
        delete_rowcount=1,
    )

    with patcher:
        fs_db.delete_file_source("file_x")

    sqls = _executed_sqls(cursor)
    # current row SELECT
    assert "SELECT storage_key FROM file_sources WHERE id = %s FOR UPDATE" in sqls[0]
    # siblings SELECT (NON COUNT(*) - serve FOR UPDATE per locking)
    assert "SELECT id FROM file_sources" in sqls[1]
    assert "WHERE storage_key = %s AND id <> %s FOR UPDATE" in sqls[1]
    assert "COUNT(" not in sqls[1], (
        "E4: la query siblings deve essere SELECT id ... FOR UPDATE "
        "(COUNT(*) non acquisisce row lock)"
    )


# 9+10. Storage delete OUTSIDE transaction (after commit)


def test_storage_delete_happens_after_db_commit_not_inside_transaction(
    fake_storage,
):
    """BE-DELETE-002 E4: storage.delete deve avvenire DOPO il commit del CM
    `transaction()`, NON dentro la TX. Garantisce:
      - Race D eliminata (DB gia' committed quando storage call parte);
      - ADR-0011 contract preservato (storage failure -> orphan accepted,
        DB row gia' cancellata).
    Verifica via call_order: tracciamo l'ordine di tx_cm.exit vs storage.delete."""
    from app.core.files import db as fs_db

    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"storage_key": "file-sources/file_y/z.csv"},
    ]
    cursor.fetchall.side_effect = [[]]  # no siblings
    cursor.rowcount = 1

    @contextmanager
    def cursor_cm():
        yield cursor

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=cursor_cm)

    call_order = []

    @contextmanager
    def tx_cm():
        call_order.append("tx_enter")
        yield conn
        call_order.append("tx_exit_commit")

    fake_storage.delete.side_effect = lambda key: call_order.append(
        f"storage_delete:{key}"
    )

    with patch("app.core.files.db.transaction", side_effect=tx_cm), \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_y")

    assert out is True
    # Ordine attesa: tx_enter -> tx_exit_commit -> storage_delete
    assert call_order == [
        "tx_enter",
        "tx_exit_commit",
        "storage_delete:file-sources/file_y/z.csv",
    ], (
        f"E4: storage.delete deve avvenire DOPO tx_exit. Got: {call_order}"
    )


# 11. Race C: storage failure post-commit -> warn, row gia' deleted (True)


def test_storage_delete_failure_after_commit_does_not_undo_db_delete(
    fake_storage,
):
    """Race C: storage.delete solleva DOPO il commit DB. Comportamento
    atteso (ADR-0011 contract): warning log + ritorna True (la row e'
    stata effettivamente cancellata), orphan blob accepted."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/file_q/r.csv",
        siblings=[],
        delete_rowcount=1,
    )
    fake_storage.delete.side_effect = RuntimeError("S3 5xx")

    fake_logger = MagicMock()
    with patcher, \
         patch.object(fs_db, "logger", fake_logger), \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_q")

    # Row gia' cancellata pre-storage-call -> True (no rollback DB)
    assert out is True
    # NOTE: emesso (BE-GO-READY-STORAGE-ENGINE-002: log shape
    # unificato `event=storage_compensating_delete_failed`).
    fake_logger.warning.assert_called_once()
    msg = fake_logger.warning.call_args[0]
    # log.warning("template", *fmt_args) — il template e' il primo arg
    # e i format args seguono. Ricostruiamo per assertion.
    template = msg[0]
    rendered = template % msg[1:]
    assert "event=storage_compensating_delete_failed" in rendered
    assert "domain=file_source" in rendered
    assert "file_q" in rendered
    assert "S3 5xx" in rendered


# 12. Race E: object_storage_enabled() False post-commit -> debug skip


def test_storage_delete_skipped_when_capability_disabled_post_commit(
    fake_storage,
):
    """Race E: capability disabilitata tra commit e storage call ->
    debug log, no errore, row gia' cancellata (returns True)."""
    from app.core.files import db as fs_db

    patcher, cursor, _conn = _make_tx_patcher(
        current_storage_key="file-sources/file_s/t.csv",
        siblings=[],
        delete_rowcount=1,
    )

    fake_logger = MagicMock()
    with patcher, \
         patch.object(fs_db, "logger", fake_logger), \
         patch("app.infrastructure.storage.cloud_backends.object_storage_enabled", return_value=False), \
         patch("app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("file_s")

    assert out is True
    mock_get_storage.assert_not_called()
    fake_storage.delete.assert_not_called()
    fake_logger.debug.assert_called_once()
    # BE-GO-READY-STORAGE-ENGINE-002: log shape unificato
    # `event=storage_compensating_delete_skipped`.
    args = fake_logger.debug.call_args[0]
    rendered = args[0] % args[1:]
    assert "event=storage_compensating_delete_skipped" in rendered
    assert "domain=file_source" in rendered
    assert "skip_reason=capability_disabled" in rendered
    assert "file_s" in rendered
