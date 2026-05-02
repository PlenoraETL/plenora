"""Contract test - BE-HARDEN-001C E2: reference-aware delete_file_source.

Verifica le invarianti del nuovo refcount-aware cleanup S3 in
`app.db.config.file_sources.delete_file_source`:

  1. row unica con storage_key (refcount=0): storage.delete chiamata
     (comportamento legacy invariato).
  2. row con altri ref sullo stesso storage_key (refcount>0):
     storage.delete NON chiamata + log structured `delete_skip_storage_shared_ref`.
     La row corrente viene comunque rimossa dal DB.
  3. delete dell'ULTIMO ref (refcount=0 dopo pre-delete dei sibling):
     storage.delete chiamata (cleanup avviene quando l'ultimo ref muore).
  4. BYTEA mode (storage_key=None): nessuna query refcount, nessuna
     storage.delete, solo DELETE row.

Mock dei primitive DB (`fetchone`/`execute` di app.db.pg_connection)
e dello storage layer (`app.shared.cloud_backends.get_runtime_object_storage`).
Pattern coerente con altri smoke test del repo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_storage():
    """Mock object storage con .delete(key) registrato."""
    storage = MagicMock()
    storage.delete = MagicMock()
    return storage


def _execute_result(rowcount: int = 1):
    """Helper: simula il _Result ritornato da app.db.pg_connection.execute."""
    res = MagicMock()
    res.rowcount = rowcount
    return res


# =============================================================================
# 1. Row unica con storage_key (refcount=0) -> storage.delete chiamata
# =============================================================================


def test_delete_with_no_refs_proceeds_with_storage_cleanup(fake_storage):
    """Storage_key univoco (legacy data o ultimo ref): refcount=0 ->
    storage.delete chiamata (comportamento invariato vs pre-E2)."""
    from app.db.config import file_sources as fs_db

    fetchone_responses = [
        # 1a fetchone: SELECT storage_key WHERE id=?
        {"storage_key": "file-sources/file_abc/x.csv"},
        # 2a fetchone: SELECT COUNT(*) refcount
        {"refcount": 0},
    ]

    with patch.object(fs_db, "fetchone", side_effect=fetchone_responses) as mock_fo, \
         patch.object(fs_db, "execute", return_value=_execute_result(1)) as mock_exec, \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_abc")

    assert out is True
    # Storage delete invocata col key corretto
    fake_storage.delete.assert_called_once_with("file-sources/file_abc/x.csv")
    # Refcount query eseguita
    assert mock_fo.call_count == 2
    refcount_call = mock_fo.call_args_list[1]
    assert "COUNT(*)" in refcount_call.args[0]
    assert "storage_key = ? AND id != ?" in refcount_call.args[0]
    assert refcount_call.args[1] == ("file-sources/file_abc/x.csv", "file_abc")
    # DELETE row eseguita
    mock_exec.assert_called_once()
    assert "DELETE FROM file_sources" in mock_exec.call_args.args[0]


# =============================================================================
# 2. Row con altri ref (refcount>0) -> storage.delete SKIPPATA + log
# =============================================================================


def test_delete_with_other_refs_skips_storage_cleanup_and_logs(fake_storage):
    """Storage_key condiviso con altre 2 row: refcount=2 -> storage.delete
    NON chiamata + log structured. La row corrente viene comunque DELETE."""
    from app.db.config import file_sources as fs_db

    fetchone_responses = [
        {"storage_key": "file-sources/batch_xyz/multi.gpkg"},
        {"refcount": 2},  # altri 2 sibling referenziano lo stesso blob
    ]

    fake_logger = MagicMock()
    with patch.object(fs_db, "fetchone", side_effect=fetchone_responses), \
         patch.object(fs_db, "execute", return_value=_execute_result(1)) as mock_exec, \
         patch.object(fs_db, "logger", fake_logger), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_layer1")

    assert out is True
    # Storage delete NON chiamata (sibling ancora referenziano)
    fake_storage.delete.assert_not_called()
    # Log structured emesso (warning level)
    fake_logger.warning.assert_called_once()
    msg = fake_logger.warning.call_args[0][0]
    assert "event=delete_skip_storage_shared_ref" in msg
    assert "file_id=file_layer1" in msg
    assert "storage_key='file-sources/batch_xyz/multi.gpkg'" in msg
    assert "remaining_refs=2" in msg
    # DELETE row eseguita comunque
    mock_exec.assert_called_once()


# =============================================================================
# 3. Delete dell'ultimo ref -> storage.delete chiamata
# =============================================================================


def test_delete_last_remaining_ref_proceeds_with_storage_cleanup(fake_storage):
    """Multi-layer: dopo aver cancellato N-1 fratelli, il delete dell'ultimo
    ref ottiene refcount=0 -> storage.delete chiamata. Cleanup deferred al
    'last drop'."""
    from app.db.config import file_sources as fs_db

    fetchone_responses = [
        {"storage_key": "file-sources/batch_xyz/multi.gpkg"},
        {"refcount": 0},  # nessun sibling rimasto: e' l'ultimo
    ]

    with patch.object(fs_db, "fetchone", side_effect=fetchone_responses), \
         patch.object(fs_db, "execute", return_value=_execute_result(1)), \
         patch("app.shared.cloud_backends.object_storage_enabled", return_value=True), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage):
        out = fs_db.delete_file_source("file_last_layer")

    assert out is True
    # Storage delete chiamata (cleanup quando l'ultimo ref muore)
    fake_storage.delete.assert_called_once_with("file-sources/batch_xyz/multi.gpkg")


# =============================================================================
# 4. BYTEA mode (storage_key=None) -> no refcount query, no storage.delete
# =============================================================================


def test_delete_bytea_mode_skips_refcount_and_storage(fake_storage):
    """BYTEA: storage_key None -> early skip senza query refcount,
    nessuna interazione object storage, solo DELETE row."""
    from app.db.config import file_sources as fs_db

    # Solo 1 fetchone (lookup storage_key) -> None; NESSUNA fetchone refcount
    with patch.object(fs_db, "fetchone",
                      return_value={"storage_key": None}) as mock_fo, \
         patch.object(fs_db, "execute", return_value=_execute_result(1)) as mock_exec, \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("file_bytea")

    assert out is True
    # Solo 1 fetchone (no refcount query in BYTEA mode)
    assert mock_fo.call_count == 1
    # No storage interaction
    mock_get_storage.assert_not_called()
    fake_storage.delete.assert_not_called()
    # DELETE row eseguita
    mock_exec.assert_called_once()


# =============================================================================
# 5. Backward-compat: row inesistente (storage_key None via row None)
# =============================================================================


def test_delete_nonexistent_row_returns_false_no_side_effects(fake_storage):
    """Row inesistente: storage_key resta None, nessuna interazione storage,
    DELETE rowcount=0 -> ritorna False."""
    from app.db.config import file_sources as fs_db

    with patch.object(fs_db, "fetchone", return_value=None) as mock_fo, \
         patch.object(fs_db, "execute", return_value=_execute_result(0)), \
         patch("app.shared.cloud_backends.get_runtime_object_storage",
               return_value=fake_storage) as mock_get_storage:
        out = fs_db.delete_file_source("ghost")

    assert out is False
    assert mock_fo.call_count == 1
    mock_get_storage.assert_not_called()
    fake_storage.delete.assert_not_called()
