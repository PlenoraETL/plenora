"""[phase-2-err][batch8] C5 — delete_all_runs_for_canvas fail-closed outer.

Audit Batch 8 §3.1 S4 / fix F4: `intermediate/db.py:delete_all_runs_for_canvas`
pre-fix aveva outer `except Exception: logger.warning + return 0`. Un
catastrophic failure (DB pool exhausted, fetchall raise, vincolo FK inatteso)
restituiva `0` indistinguibile da "canvas senza run" → il caller
`ProjectService.delete_project` proseguiva allo step 5 (config DELETE) →
orfani cross-DB garantiti.

Post-fix:
  - Outer catch → `logger.error('[CRASH] ...', exc_info=True) + raise`
    (fail-closed). Il caller Batch 9 C5 cattura + accumula critical_failures
    + solleva ProjectCleanupPartialError → 500 JSON strutturato.
  - Inner per-row catch invariato nel comportamento (best-effort continue),
    con `exc_info=True` aggiunto per diagnosticita'.
  - Firma `-> int` invariata.
  - Nessuna nuova exception class: bare `raise` propaga l'eccezione originale.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# HAPPY PATH / EARLY EXIT (firma -> int preservata)


def test_delete_all_runs_happy_path_returns_count():
    """Mock fetchall → 3 run, _delete_run sempre ok → return 3."""
    from app.core.intermediate import db as idb

    rows = [{"run_id": "r1"}, {"run_id": "r2"}, {"run_id": "r3"}]
    with patch.object(idb, "fetchall", return_value=rows), \
         patch.object(idb, "_delete_run") as mock_delete:
        result = idb.delete_all_runs_for_canvas("canvas-1")

    assert result == 3
    assert mock_delete.call_count == 3
    mock_delete.assert_any_call("r1")
    mock_delete.assert_any_call("r2")
    mock_delete.assert_any_call("r3")


def test_delete_all_runs_no_runs_returns_zero():
    """Mock fetchall → [] → return 0 (early exit invariato, NO raise)."""
    from app.core.intermediate import db as idb

    with patch.object(idb, "fetchall", return_value=[]), \
         patch.object(idb, "_delete_run") as mock_delete:
        result = idb.delete_all_runs_for_canvas("canvas-empty")

    assert result == 0
    mock_delete.assert_not_called()


# INNER PER-ROW FAIL — best-effort continue (fail-open con breadcrumb)


def test_delete_all_runs_inner_fail_continues_and_logs_warning():
    """1 run specifica solleva → continue, altre 2 cancellate → return 2,
    warning con exc_info=True emesso con il run_id fallito nel messaggio."""
    from app.core.intermediate import db as idb

    rows = [{"run_id": "r-ok-1"}, {"run_id": "r-broken"}, {"run_id": "r-ok-2"}]

    def flaky_delete(run_id):
        if run_id == "r-broken":
            raise RuntimeError("FK constraint violation on this run")

    with patch.object(idb, "fetchall", return_value=rows), \
         patch.object(idb, "_delete_run", side_effect=flaky_delete), \
         patch.object(idb, "logger") as mock_logger:
        result = idb.delete_all_runs_for_canvas("canvas-partial")

    # count include solo i successi
    assert result == 2
    # warning emesso per la run broken, con exc_info=True
    inner_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "errore su run r-broken" in str(c)
    ]
    assert len(inner_warnings) == 1
    assert inner_warnings[0].kwargs.get("exc_info") is True


# OUTER CATASTROPHIC FAIL — fail-closed (raise invece di return 0)


def test_delete_all_runs_outer_fail_raises_not_returns_zero():
    """fetchall solleva (DB down) → la funzione **solleva** l'eccezione
    originale invece di ritornare 0 (pre-fix behavior). Fail-closed esplicito
    permette al caller `project_service.delete_project` di riconoscere lo
    step 2 critical come fallito e sollevare ProjectCleanupPartialError."""
    from app.core.intermediate import db as idb

    with patch.object(
        idb, "fetchall",
        side_effect=RuntimeError("DB pool exhausted"),
    ), patch.object(idb, "_delete_run") as mock_delete:
        with pytest.raises(RuntimeError, match="DB pool exhausted"):
            idb.delete_all_runs_for_canvas("canvas-catastrophic")

    # _delete_run mai chiamato: il fail e' a monte (fetchall)
    mock_delete.assert_not_called()


def test_delete_all_runs_outer_fail_logs_crash_with_exc_info():
    """Sullo stesso scenario, verifica che prima del raise venga emesso
    `logger.error('[CRASH] ...', exc_info=True)` con canvas_id nel messaggio,
    coerente col pattern diagnostico Batch 9 C5 su step critical."""
    from app.core.intermediate import db as idb

    with patch.object(
        idb, "fetchall",
        side_effect=RuntimeError("fetchall exploded"),
    ), patch.object(idb, "logger") as mock_logger:
        with pytest.raises(RuntimeError):
            idb.delete_all_runs_for_canvas("canvas-xyz")

    # logger.error con [CRASH] + canvas_id + exc_info=True
    crash_errors = [
        c for c in mock_logger.error.call_args_list
        if "[CRASH]" in str(c)
        and "canvas=canvas-xyz" in str(c)
        and "delete_all_runs_for_canvas" in str(c)
    ]
    assert len(crash_errors) == 1, (
        f"atteso 1 logger.error con [CRASH] canvas=canvas-xyz, ricevuto: "
        f"{mock_logger.error.call_args_list}"
    )
    assert crash_errors[0].kwargs.get("exc_info") is True
