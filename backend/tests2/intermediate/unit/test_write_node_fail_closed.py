"""
C7 — write_node fail-closed + IntermediateWriteError.

Audit finding: `intermediate/writer.py:write_node` Previous behavior
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.intermediate.exceptions import IntermediateWriteError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# HAPPY PATH — nessun cambio al ramo success, return hash reale


def test_write_node_happy_path_returns_real_hash():
    """Baseline: status='ok' + rows → write_node_atomic ok → ritorna hash
    reale (64 hex), NON il sentinel sha256(b'error'). Il fix C7 non tocca
    il ramo success.

    node-write atomicity contract (atomicity refactor): il ramo success ora chiama
    `db.write_node_atomic` (singola transazione idempotente) invece di
    `insert_node_rows` + `upsert_node_meta` separati. Il test verifica
    che il helper sia invocato una sola volta sul ramo success.
    """
    from app.core.intermediate import writer as w

    rows = [{"col_a": 1, "col_b": "x"}, {"col_a": 2, "col_b": "y"}]
    schema = {"col_a": "int64", "col_b": "object"}

    with patch.object(w.node_snapshots_db, "write_node_atomic") as mock_atomic, \
         patch.object(w.node_snapshots_db, "upsert_node_meta") as mock_meta:
        result = w.write_node(
            run_id="run-ok", node_id="node-1", node_type="estrai",
            rows=rows, col_schema=schema, exec_time=0.5, status="ok",
        )

    assert isinstance(result, str)
    assert len(result) == 64  # sha256 hex digest
    # NOT il sentinel fake
    import hashlib
    fake_sentinel = hashlib.sha256(b"error").hexdigest()
    assert result != fake_sentinel
    # node-write atomicity contract: success branch usa il helper atomico (rows non vuoto).
    mock_atomic.assert_called_once()
    # upsert_node_meta NON viene chiamato direttamente sul ramo success
    # perche' write_node_atomic gia' include l'upsert in singola tx.
    mock_meta.assert_not_called()


# PRIMARY FAIL → raise IntermediateWriteError (NO fake hash)


def test_write_node_primary_fail_raises_intermediate_write_error():
    """`db.write_node_atomic` solleva → `pytest.raises(IntermediateWriteError)`
    invece di ritornare il sentinel fake. Attributi tipizzati popolati.

    node-write atomicity contract: il primary call ora e' write_node_atomic (atomico
    rows+meta), non insert_node_rows separato. Il fail-closed semantico
    e' invariato.
    """
    from app.core.intermediate import writer as w

    original_exc = RuntimeError("write_node_atomic DB pool exhausted")
    rows = [{"col_a": 1}]
    schema = {"col_a": "int64"}

    with patch.object(w.node_snapshots_db, "write_node_atomic", side_effect=original_exc), \
         patch.object(w.node_snapshots_db, "upsert_node_meta"):
        with pytest.raises(IntermediateWriteError) as exc_info:
            w.write_node(
                run_id="run-1", node_id="node-x", node_type="estrai",
                rows=rows, col_schema=schema, exec_time=0.0, status="ok",
            )

    # Attributi tipizzati popolati correttamente
    assert exc_info.value.run_id == "run-1"
    assert exc_info.value.node_id == "node-x"
    assert exc_info.value.original_error is original_exc


# FALLBACK upsert_node_meta chiamato PRIMA del raise (resource guard)


def test_write_node_fallback_upsert_node_meta_called_before_raise():
    """Quando primary fallisce, il fallback `upsert_node_meta(status='error',
    error_msg=...)` viene chiamato PRIMA del raise come resource guard.

    node-write atomicity contract: primary = write_node_atomic; il fallback meta-only resta
    `db.upsert_node_meta` standalone (l'helper atomico ha gia' fatto
    rollback, ne' senso ne' possibilita' di riusarlo qui).
    """
    from app.core.intermediate import writer as w

    original_exc = RuntimeError("primary atomic write fail")
    rows = [{"col_a": 1}]
    schema = {"col_a": "int64"}

    with patch.object(w.node_snapshots_db, "write_node_atomic", side_effect=original_exc), \
         patch.object(w.node_snapshots_db, "upsert_node_meta") as mock_meta:
        with pytest.raises(IntermediateWriteError):
            w.write_node(
                run_id="run-2", node_id="node-y", node_type="manipola",
                rows=rows, col_schema=schema, exec_time=1.5, status="ok",
            )

    # Fallback chiamato con status="error" + error_msg = str(original)
    mock_meta.assert_called_once()
    kwargs = mock_meta.call_args.kwargs
    assert kwargs["run_id"] == "run-2"
    assert kwargs["node_id"] == "node-y"
    assert kwargs["node_type"] == "manipola"
    assert kwargs["row_count"] == 0
    assert kwargs["status"] == "error"
    assert "primary atomic write fail" in kwargs["error_msg"]


# PRIMARY + FALLBACK entrambi falliscono → raise con original = primary


def test_write_node_primary_and_fallback_fail_still_raises_with_original():
    """Primary (write_node_atomic) e fallback (upsert_node_meta) entrambi
    sollevano → raise `IntermediateWriteError` con `original_error` =
    primary (NOT fallback). Debug log per fallback fail emesso."""
    from app.core.intermediate import writer as w

    primary_exc = RuntimeError("primary fail original")
    fallback_exc = RuntimeError("fallback upsert_node_meta also fail")

    rows = [{"col_a": 1}]
    schema = {"col_a": "int64"}

    with patch.object(w.node_snapshots_db, "write_node_atomic", side_effect=primary_exc), \
         patch.object(w.node_snapshots_db, "upsert_node_meta", side_effect=fallback_exc), \
         patch.object(w, "logger") as mock_logger:
        with pytest.raises(IntermediateWriteError) as exc_info:
            w.write_node(
                run_id="run-double", node_id="node-z", node_type="estrai",
                rows=rows, col_schema=schema, exec_time=0.0, status="ok",
            )

    # original_error = primary (non fallback)
    assert exc_info.value.original_error is primary_exc
    # Debug log per fallback fail emesso
    debug_calls = [
        c for c in mock_logger.debug.call_args_list
        if "upsert_node_meta fallback fallito" in str(c)
    ]
    assert len(debug_calls) == 1


# CHAIN TRACEBACK — `raise ... from e` preserva __cause__


def test_write_node_exception_chain_preserved():
    """`raise IntermediateWriteError(...) from e` preserva la chain traceback:
    `exc.__cause__` e' l'eccezione originale, utile per postmortem."""
    from app.core.intermediate import writer as w

    original_exc = ValueError("schema mismatch on row 5")
    rows = [{"col_a": 1}]
    schema = {"col_a": "int64"}

    with patch.object(w.node_snapshots_db, "write_node_atomic", side_effect=original_exc), \
         patch.object(w.node_snapshots_db, "upsert_node_meta"):
        with pytest.raises(IntermediateWriteError) as exc_info:
            w.write_node(
                run_id="run-chain", node_id="node-c", node_type="estrai",
                rows=rows, col_schema=schema, exec_time=0.0, status="ok",
            )

    # __cause__ preservato via `raise ... from e`
    assert exc_info.value.__cause__ is original_exc
    # E anche sull'attributo .original_error
    assert exc_info.value.original_error is original_exc


# BREADCRUMB — [CRASH] + exc_info=True


def test_write_node_logs_crash_with_exc_info():
    """Su outer fail, prima del raise verifichiamo `logger.error('[CRASH]
    Errore scrittura nodo node-x run run-1: ...', exc_info=True)`. Prefix
    [CRASH] coerente con pattern current contract C5/C6."""
    from app.core.intermediate import writer as w

    with patch.object(
        w.node_snapshots_db, "write_node_atomic",
        side_effect=RuntimeError("bang"),
    ), patch.object(w.node_snapshots_db, "upsert_node_meta"), \
        patch.object(w, "logger") as mock_logger:
        with pytest.raises(IntermediateWriteError):
            w.write_node(
                run_id="run-crashy", node_id="node-x", node_type="estrai",
                rows=[{"col_a": 1}], col_schema={"col_a": "int64"},
                exec_time=0.0, status="ok",
            )

    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
        and "Errore scrittura nodo node-x" in str(c)
        and "run run-crashy" in str(c)
    ]
    assert len(crash_errors) == 1, (
        f"atteso 1 logger.error [CRASH] su node-x/run-crashy, ricevuto: "
        f"{mock_logger.error.call_args_list}"
    )
    assert crash_errors[0].kwargs.get("exc_info") is True
