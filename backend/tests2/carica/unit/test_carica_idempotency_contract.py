"""
Idempotency guard per carica_task (C4 round 2, opzione B).

Contract: se carica_task viene invocato con un `run_id` esplicito che
"""

import inspect
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


# Helper puro: is_already_completed


def test_is_already_completed_returns_false_when_run_does_not_exist():
    from app.shared.idempotency import is_already_completed

    with patch("app.core.intermediate.runs_db.get_run", return_value=None):
        assert is_already_completed("run-missing") is False


def test_is_already_completed_returns_true_for_completed_run():
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "completed"},
    ):
        assert is_already_completed("run-1") is True


def test_is_already_completed_returns_false_for_failed_run():
    """Status 'failed' deve consentire retry, NON essere treated come completed."""
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "failed"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_returns_false_for_running_run():
    """Status 'running' e' una race rara — non blocchiamo un retry
    manuale dell'operatore su un task che sembra bloccato."""
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        return_value={"run_id": "run-1", "status": "running"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_swallows_db_error_returning_false():
    """Se la query DB fallisce, fallback a False (meglio re-eseguire
    un task gia' fatto che skippare legittimo). Mai sollevare."""
    from app.shared.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.runs_db.get_run",
        side_effect=RuntimeError("db down"),
    ):
        assert is_already_completed("run-1") is False


# Integration nel task: accetta run_id kwarg + skip se completato


def test_carica_task_accepts_run_id_kwarg():
    """execute_carica_task deve accettare `run_id` optional."""
    from workers.tasks.carica_task import execute_carica_task

    sig = inspect.signature(execute_carica_task)
    assert "run_id" in sig.parameters, (
        "execute_carica_task non accetta run_id come kwarg per idempotency"
    )
    # Default None → generazione fresh (backward compat)
    assert sig.parameters["run_id"].default is None


def test_carica_task_references_idempotency_helper():
    """Il task deve referenziare `is_already_completed` come callee.

    Replacement R-04b di `inspect.getsource(...) + "is_already_completed" in source`:
    bytecode probe sul callee identifier (refactor-resilient).
    """
    from workers.tasks.carica_task import execute_carica_task

    code = getattr(execute_carica_task, "__wrapped__", execute_carica_task).__code__

    def _walk(co):
        if "is_already_completed" in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    assert _walk(code), (
        "execute_carica_task non riferisce is_already_completed. "
        "Retry manuale con stesso run_id eseguirebbe due volte."
    )
