"""Idempotency guard per carica_task (C4 round 2, opzione B).

Contract: se carica_task viene invocato con un `run_id` esplicito che
esiste gia' in intermediate.runs con status='completed', il task
SKIPPA l'esecuzione e ritorna immediatamente un risultato cache-aware.

Razionale: con `max_retries=0` il retry automatico Celery e' gia' stato
eliminato (C4 round 1). Rimane il caso "retry manuale / double-click /
replay da log" dove un task completato con successo viene ri-invocato
con la stessa chiave. L'idempotency chiude questo gap usando
`intermediate.runs` come backend (tabella gia' esistente, no nuova
migration).

Chiave: `run_id` passato dal caller. Per scheduler/orchestrator, il
run_id e' stabile nel contesto di un singolo tick; un doppio invio
dello stesso task Celery (es. doppio click UI, replay da restore DB)
viene quindi deduplicato.

Se `run_id` non e' passato → task genera un fresh UUID (behavior
legacy). Niente idempotency, tutto procede come prima.

Se il run esiste ma status e' `running` o `failed` → il task procede
(running = race rara, failed = retry legittimo per completare quello
che era andato storto).
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


# =============================================================================
# Helper puro: is_already_completed
# =============================================================================


def test_is_already_completed_returns_false_when_run_does_not_exist():
    from app.core.carica.idempotency import is_already_completed

    with patch("app.core.intermediate.db.get_run", return_value=None):
        assert is_already_completed("run-missing") is False


def test_is_already_completed_returns_true_for_completed_run():
    from app.core.carica.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "run-1", "status": "completed"},
    ):
        assert is_already_completed("run-1") is True


def test_is_already_completed_returns_false_for_failed_run():
    """Status 'failed' deve consentire retry, NON essere treated come completed."""
    from app.core.carica.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "run-1", "status": "failed"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_returns_false_for_running_run():
    """Status 'running' e' una race rara — non blocchiamo un retry
    manuale dell'operatore su un task che sembra bloccato."""
    from app.core.carica.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        return_value={"run_id": "run-1", "status": "running"},
    ):
        assert is_already_completed("run-1") is False


def test_is_already_completed_swallows_db_error_returning_false():
    """Se la query DB fallisce, fallback a False (meglio re-eseguire
    un task gia' fatto che skippare legittimo). Mai sollevare."""
    from app.core.carica.idempotency import is_already_completed

    with patch(
        "app.core.intermediate.db.get_run",
        side_effect=RuntimeError("db down"),
    ):
        assert is_already_completed("run-1") is False


# =============================================================================
# Integration nel task: accetta run_id kwarg + skip se completato
# =============================================================================


def test_carica_task_accepts_run_id_kwarg():
    """execute_carica_task deve accettare `run_id` optional."""
    from workers.tasks.carica_task import execute_carica_task

    sig = inspect.signature(execute_carica_task)
    assert "run_id" in sig.parameters, (
        "execute_carica_task non accetta run_id come kwarg per idempotency"
    )
    # Default None → generazione fresh (backward compat)
    assert sig.parameters["run_id"].default is None


def test_carica_task_source_checks_idempotency_when_run_id_passed():
    """Il body del task deve invocare is_already_completed (o equivalente)
    PRIMA di procedere se run_id e' stato passato dall'esterno."""
    from workers.tasks.carica_task import execute_carica_task

    source = inspect.getsource(execute_carica_task)
    assert "is_already_completed" in source or "idempotency" in source.lower(), (
        "Il task non fa check di idempotency. Un retry manuale con "
        "stesso run_id eseguirebbe due volte."
    )
