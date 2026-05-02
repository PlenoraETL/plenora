"""Contract test - BE-HARDEN-001C E3: save_file_sources_batch atomic.

Verifica le invarianti del nuovo `app.db.config.file_sources.save_file_sources_batch`:

  1. Happy path: N row passate -> 1 transazione, 1 cur.executemany con
     N tuple, autocommit a fine `with transaction()`.
  2. Validation pre-transaction: row senza data ne storage_key ->
     ValueError SOLLEVATA PRIMA di acquisire la connessione DB
     (zero side effect).
  3. DB exception inside executemany -> rollback automatico via
     transaction(), eccezione propaga al caller.
  4. Empty rows list -> early return, nessuna connessione acquisita.
  5. Ritorno: None (no fetchone post-insert).
  6. SQL preserva ON CONFLICT (id) DO UPDATE SET (idempotency upsert).

Mock di `app.db.config.file_sources.transaction` (context manager):
nessun DB reale, solo verifica chiamate al cursor.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_conn_cursor():
    """Mock conn + cursor compatibili con `with transaction() as conn:
    with conn.cursor() as cur: cur.executemany(...)`."""
    cursor = MagicMock()
    cursor.executemany = MagicMock()

    @contextmanager
    def cursor_cm():
        yield cursor

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=cursor_cm)
    return conn, cursor


@pytest.fixture
def patched_transaction(fake_conn_cursor):
    """Patcher di file_sources.transaction -> context manager che yielda
    fake_conn. Ritorna la tupla (patcher, conn, cursor) per assertions."""
    conn, cursor = fake_conn_cursor

    @contextmanager
    def tx_cm():
        yield conn

    return patch("app.db.config.file_sources.transaction", side_effect=tx_cm), conn, cursor


def _row(file_id: str, **overrides):
    base = {
        "file_id": file_id,
        "group_id": "g1",
        "name": f"name_{file_id}",
        "original_filename": "f.csv",
        "fmt": "csv",
        "data": b"shared bytes",
        "storage_key": None,
        "size_bytes": 12,
        "metadata": {"k": "v"},
        "created_by": "u1",
    }
    base.update(overrides)
    return base


# =============================================================================
# 1. Happy path: 1 transazione + 1 executemany con N tuple
# =============================================================================


def test_save_batch_happy_path_single_executemany_in_transaction(patched_transaction):
    from app.db.config import file_sources as fs_db

    patcher, conn, cursor = patched_transaction
    rows = [_row("f1"), _row("f2"), _row("f3")]

    with patcher:
        out = fs_db.save_file_sources_batch(rows)

    assert out is None, "save_file_sources_batch deve ritornare None"
    cursor.executemany.assert_called_once()
    sql_arg, params_list = cursor.executemany.call_args.args
    assert "INSERT INTO file_sources" in sql_arg
    assert "ON CONFLICT (id) DO UPDATE SET" in sql_arg
    assert len(params_list) == 3
    # Ogni tuple ha 12 colonne (id, group_id, name, original_filename, format,
    # data, storage_key, size_bytes, metadata, created_at, updated_at, created_by)
    for t in params_list:
        assert len(t) == 12


def test_save_batch_shares_single_timestamp_across_rows(patched_transaction):
    """now() chiamato UNA volta -> created_at == updated_at e identico
    per tutte le N row del batch."""
    from app.db.config import file_sources as fs_db

    patcher, conn, cursor = patched_transaction
    rows = [_row("f1"), _row("f2")]

    with patcher:
        fs_db.save_file_sources_batch(rows)

    params_list = cursor.executemany.call_args.args[1]
    # tuple layout: (..., size_bytes, metadata_json, created_at, updated_at, created_by)
    created_at_values = {t[9] for t in params_list}
    updated_at_values = {t[10] for t in params_list}
    assert len(created_at_values) == 1, "created_at deve essere shared"
    assert created_at_values == updated_at_values
    # E ovviamente created_at == updated_at per ogni row
    for t in params_list:
        assert t[9] == t[10]


# =============================================================================
# 2. Validation pre-transaction: fail-fast SENZA acquisire connessione
# =============================================================================


def test_save_batch_raises_before_transaction_when_row_missing_blob():
    """Row con data=None E storage_key=None -> ValueError SOLLEVATA PRIMA
    che `transaction()` venga chiamato. Zero side effect DB."""
    from app.db.config import file_sources as fs_db

    bad_rows = [
        _row("f1"),
        _row("f2", data=None, storage_key=None),
        _row("f3"),
    ]

    with patch("app.db.config.file_sources.transaction") as mock_tx:
        with pytest.raises(ValueError, match="senza data ne storage_key"):
            fs_db.save_file_sources_batch(bad_rows)

    mock_tx.assert_not_called(), (
        "validation deve fail-fast prima di acquisire la transazione"
    )


def test_save_batch_storage_key_only_row_is_valid(patched_transaction):
    """Row con data=None ma storage_key valorizzato -> validation pass,
    INSERT eseguito (object_storage path)."""
    from app.db.config import file_sources as fs_db

    patcher, conn, cursor = patched_transaction
    rows = [_row("f1", data=None, storage_key="file-sources/batch_x/file.csv",
                 size_bytes=2048)]

    with patcher:
        fs_db.save_file_sources_batch(rows)

    cursor.executemany.assert_called_once()


# =============================================================================
# 3. DB exception inside transaction -> propaga, rollback gestito da tx CM
# =============================================================================


def test_save_batch_rolls_back_on_executemany_exception(patched_transaction):
    """executemany solleva -> exception propaga al caller. Il rollback e'
    responsabilita' di `transaction()` (verificato in pg_connection)."""
    from app.db.config import file_sources as fs_db

    patcher, conn, cursor = patched_transaction
    cursor.executemany.side_effect = RuntimeError("DB constraint violated")
    rows = [_row("f1"), _row("f2")]

    with patcher:
        with pytest.raises(RuntimeError, match="DB constraint violated"):
            fs_db.save_file_sources_batch(rows)


# =============================================================================
# 4. Empty rows -> early return, no DB call
# =============================================================================


def test_save_batch_empty_rows_is_noop():
    from app.db.config import file_sources as fs_db

    with patch("app.db.config.file_sources.transaction") as mock_tx:
        out = fs_db.save_file_sources_batch([])

    assert out is None
    mock_tx.assert_not_called()


# =============================================================================
# 5. No fetchone post-insert (E3: caller possiede gia' i DTO)
# =============================================================================


def test_save_batch_does_not_call_fetchone_post_insert(patched_transaction):
    """Risparmio N round-trip: nessun SELECT per row post-INSERT."""
    from app.db.config import file_sources as fs_db

    patcher, conn, cursor = patched_transaction
    rows = [_row("f1"), _row("f2")]

    with patcher, patch("app.db.config.file_sources.fetchone") as mock_fo:
        fs_db.save_file_sources_batch(rows)

    mock_fo.assert_not_called()
