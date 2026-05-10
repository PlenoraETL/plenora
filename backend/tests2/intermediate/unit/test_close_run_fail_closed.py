"""
C6 — close_run re-raise dopo nested cleanup.

Audit finding: `intermediate/writer.py:close_run` Previous behavior
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# HAPPY PATH — nessun cambio al ramo success


def test_close_run_happy_path_returns_none():
    """Baseline: tutte le db.* ok → `return None`, nessuna eccezione,
    retention tentata. Il ramo success e' invariato dal fix C6."""
    from app.core.intermediate import writer as w

    with patch.object(w.node_snapshots_db, "list_node_meta", return_value=[
        {"status": "ok", "row_count": 100},
        {"status": "ok", "row_count": 80},
    ]), patch.object(w.runs_db, "update_run") as mock_update, \
        patch.object(w.runs_db, "get_run", return_value={"canvas_id": "c-1", "group_id": "g-1"}), \
        patch.object(w.retention, "apply_runs_retention") as mock_retention:
        result = w.close_run(
            run_id="run-ok",
            input_hash="h-in",
            output_hash="h-out",
            total_exec_time=1.23,
            success=True,
        )

    assert result is None
    # update_run chiamato una sola volta (quella principale, no fallback)
    mock_update.assert_called_once()
    kwargs = mock_update.call_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["input_hash"] == "h-in"
    assert kwargs["output_hash"] == "h-out"
    # Retention FIFO best-effort tentata
    mock_retention.assert_called_once()


# PRIMARY FAIL + FALLBACK OK → raise (fail-closed)


def test_close_run_primary_fail_with_fallback_ok_raises():
    """Primary update_run solleva, fallback update_run(status='failed') ok
    → **raise** propaga l'eccezione originale. Fallback chiamato come cleanup."""
    from app.core.intermediate import writer as w

    primary_error = RuntimeError("primary update_run DB pool exhausted")

    def update_side_effect(*args, **kwargs):
        # 1a chiamata (primary con status="completed"|"failed" dal success flag)
        if kwargs.get("input_hash") is not None:
            raise primary_error
        return None

    with patch.object(w.node_snapshots_db, "list_node_meta", return_value=[
        {"status": "ok", "row_count": 10}
    ]), patch.object(w.runs_db, "update_run", side_effect=update_side_effect) as mock_update, \
        patch.object(w.runs_db, "get_run"):
        with pytest.raises(RuntimeError, match="primary update_run DB pool exhausted"):
            w.close_run(
                run_id="run-primary-fail",
                input_hash="h-in",
                output_hash="h-out",
                total_exec_time=1.0,
                success=True,
            )

    # Fallback chiamato: 1a call (primary) + 2a call (fallback)
    assert mock_update.call_count == 2
    # La 2a call e' il fallback con status="failed" e senza input_hash/output_hash
    fallback_call = mock_update.call_args_list[1]
    assert fallback_call.kwargs.get("status") == "failed"
    assert "input_hash" not in fallback_call.kwargs


# PRIMARY FAIL + FALLBACK FAIL → raise dell'ORIGINALE (non della fallback)


def test_close_run_primary_fail_and_fallback_fail_still_raises():
    """Primary e fallback entrambi falliscono → raise propaga l'eccezione
    ORIGINALE del primary (non quella del fallback). Debug log emesso per
    fallback fail."""
    from app.core.intermediate import writer as w

    primary_error = RuntimeError("primary fail original")
    fallback_error = RuntimeError("fallback also fail")

    call_count = {"n": 0}

    def update_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise primary_error
        raise fallback_error

    with patch.object(w.node_snapshots_db, "list_node_meta", return_value=[
        {"status": "ok", "row_count": 5}
    ]), patch.object(w.runs_db, "update_run", side_effect=update_side_effect), \
        patch.object(w.runs_db, "get_run"), \
        patch.object(w, "logger") as mock_logger:
        with pytest.raises(RuntimeError) as exc_info:
            w.close_run(
                run_id="run-double-fail",
                input_hash="h", output_hash="h",
                total_exec_time=1.0, success=True,
            )

    # L'eccezione che emerge e' quella ORIGINALE, non la fallback
    assert str(exc_info.value) == "primary fail original"
    # Debug log per fallback fail emesso
    debug_calls = [
        c for c in mock_logger.debug.call_args_list
        if "update_run(status=failed) fallback fallito" in str(c)
    ]
    assert len(debug_calls) == 1


# BREADCRUMB — [CRASH] + exc_info=True


def test_close_run_logs_crash_with_exc_info_on_outer_fail():
    """Su outer fail, verifica `logger.error('[CRASH] Errore chiusura run...',
    exc_info=True)` prima del raise. Prefix cambiato da [GESTITO] a [CRASH]
    per coerenza col pattern resource-guard-con-raise (current contract C5)."""
    from app.core.intermediate import writer as w

    with patch.object(
        w.node_snapshots_db, "list_node_meta",
        side_effect=RuntimeError("list_node_meta exploded"),
    ), patch.object(w.runs_db, "update_run"), \
        patch.object(w, "logger") as mock_logger:
        with pytest.raises(RuntimeError):
            w.close_run(
                run_id="run-crashy",
                input_hash="h", output_hash="h",
                total_exec_time=0.5, success=False,
            )

    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
        and "Errore chiusura run run-crashy" in str(c)
    ]
    assert len(crash_errors) == 1, (
        f"atteso 1 logger.error [CRASH] su run-crashy, ricevuto: "
        f"{mock_logger.error.call_args_list}"
    )
    assert crash_errors[0].kwargs.get("exc_info") is True
    # Post-C6: prefix [GESTITO] non deve piu' comparire (sostituito)
    gestito_errors = [
        c for c in mock_logger.error.call_args_list
        if "[GESTITO]" in str(c) and "chiusura run" in str(c)
    ]
    assert gestito_errors == []


# FALLBACK update_run(status="failed") — firma esatta


def test_close_run_fallback_update_run_called_with_status_failed():
    """Quando il primary fallisce, il fallback chiama `db.update_run(run_id,
    status='failed')` con firma esatta prima del raise."""
    from app.core.intermediate import writer as w

    with patch.object(
        w.node_snapshots_db, "list_node_meta",
        side_effect=RuntimeError("bang"),
    ), patch.object(w.runs_db, "update_run") as mock_update:
        with pytest.raises(RuntimeError):
            w.close_run(
                run_id="run-fallback-check",
                input_hash="h", output_hash="h",
                total_exec_time=1.0, success=True,
            )

    # Solo una chiamata: il fallback (perche' list_node_meta fallisce prima
    # del primary update_run → non arriva al primary)
    assert mock_update.call_count == 1
    fallback_call = mock_update.call_args_list[0]
    assert fallback_call.args == ("run-fallback-check",)
    assert fallback_call.kwargs == {"status": "failed"}
