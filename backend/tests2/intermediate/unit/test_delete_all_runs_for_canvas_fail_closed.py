"""
C5 — delete_all_runs_for_canvas fail-closed outer.

Audit finding: `intermediate/db.py:delete_all_runs_for_canvas`
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# HAPPY PATH / EARLY EXIT (firma -> int preservata)


def test_delete_all_runs_happy_path_returns_count():
    """Mock fetchall → 3 run, _delete_run sempre ok → return 3."""
    from app.core.intermediate import retention

    rows = [{"run_id": "r1"}, {"run_id": "r2"}, {"run_id": "r3"}]
    with patch.object(retention, "_delete_run") as mock_delete:
        result = retention.delete_all_runs_for_canvas(
            "canvas-1",
            fetchall_func=lambda sql, params=(): rows,
            delete_run_func=mock_delete,
        )

    assert result == 3
    assert mock_delete.call_count == 3
    mock_delete.assert_any_call("r1")
    mock_delete.assert_any_call("r2")
    mock_delete.assert_any_call("r3")


def test_delete_all_runs_no_runs_returns_zero():
    """Mock fetchall → [] → return 0 (early exit invariato, NO raise)."""
    from app.core.intermediate import retention

    with patch.object(retention, "_delete_run") as mock_delete:
        result = retention.delete_all_runs_for_canvas(
            "canvas-empty",
            fetchall_func=lambda sql, params=(): [],
            delete_run_func=mock_delete,
        )

    assert result == 0
    mock_delete.assert_not_called()


# INNER PER-ROW FAIL — best-effort continue (fail-open con breadcrumb)


def test_delete_all_runs_inner_fail_continues_and_logs_warning():
    """1 run specifica solleva → continue, altre 2 cancellate → return 2,
    warning con exc_info=True emesso con il run_id fallito nel messaggio."""
    from app.core.intermediate import retention

    rows = [{"run_id": "r-ok-1"}, {"run_id": "r-broken"}, {"run_id": "r-ok-2"}]

    def flaky_delete(run_id):
        if run_id == "r-broken":
            raise RuntimeError("FK constraint violation on this run")

    with patch.object(retention, "logger") as mock_logger:
        result = retention.delete_all_runs_for_canvas(
            "canvas-partial",
            fetchall_func=lambda sql, params=(): rows,
            delete_run_func=flaky_delete,
            logger_obj=mock_logger,
        )

    # count include solo i successi
    assert result == 2
    # NOTE: emesso per la run broken, con exc_info=True
    inner_warnings = [
        c for c in mock_logger.warning.call_args_list
        if "errore su run r-broken" in str(c)
    ]
    assert len(inner_warnings) == 1
    assert inner_warnings[0].kwargs.get("exc_info") is True


# OUTER CATASTROPHIC FAIL — fail-closed (raise invece di return 0)


def test_delete_all_runs_outer_fail_raises_not_returns_zero():
    """fetchall solleva (DB down) → la funzione **solleva** l'eccezione
    originale invece di ritornare 0 (Previous behavior behavior). Fail-closed esplicito
    permette al caller `project_service.delete_project` di riconoscere lo
    step 2 critical come fallito e sollevare ProjectCleanupPartialError."""
    from app.core.intermediate import retention

    mock_delete = patch.object(retention, "_delete_run").start()
    try:
        with pytest.raises(RuntimeError, match="DB pool exhausted"):
            retention.delete_all_runs_for_canvas(
                "canvas-catastrophic",
                fetchall_func=lambda sql, params=(): (_ for _ in ()).throw(
                    RuntimeError("DB pool exhausted")
                ),
                delete_run_func=mock_delete,
            )
    finally:
        patch.stopall()

    # _delete_run mai chiamato: il fail e' a monte (fetchall)
    mock_delete.assert_not_called()


def test_delete_all_runs_outer_fail_logs_crash_with_exc_info():
    """Sullo stesso scenario, verifica che prima del raise venga emesso
    `logger.error('[CRASH] ...', exc_info=True)` con canvas_id nel messaggio,
    coerente col pattern diagnostico current contract C5 su step critical."""
    from app.core.intermediate import retention

    with patch.object(retention, "logger") as mock_logger:
        with pytest.raises(RuntimeError):
            retention.delete_all_runs_for_canvas(
                "canvas-xyz",
                fetchall_func=lambda sql, params=(): (_ for _ in ()).throw(
                    RuntimeError("fetchall exploded")
                ),
                logger_obj=mock_logger,
            )

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
