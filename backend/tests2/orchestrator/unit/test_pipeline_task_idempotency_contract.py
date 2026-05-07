"""
Idempotency guard per pipeline_task (C4 round 4).

Chiude il gap rilevato dall'auditor: `pipeline_task` genera run_id
"""

import inspect
from unittest.mock import MagicMock

import pytest

from workers.tasks.pipeline_task import execute_pipeline_task


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


def _references_name(callable_or_task, name: str) -> bool:
    """Bytecode probe (ricorsivo per closures). R-04b refactor-resilient."""
    code = getattr(callable_or_task, "__wrapped__", callable_or_task).__code__

    def _walk(co) -> bool:
        if name in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    return _walk(code)


def test_pipeline_task_accepts_run_id_kwarg():
    """execute_pipeline_task deve accettare `run_id` optional."""
    sig = inspect.signature(execute_pipeline_task)
    assert "run_id" in sig.parameters, (
        "execute_pipeline_task non accetta run_id kwarg. "
        "Retry manuale della pipeline intera aggira l'idempotency dei child."
    )
    # Default None → generazione fresh (backward compat con call site esistenti)
    assert sig.parameters["run_id"].default is None


def test_pipeline_task_references_idempotency_helper():
    """Il task deve referenziare `is_already_completed` come callee
    (single source of truth idempotency).

    Replacement R-04b di `inspect.getsource(...) + "is_already_completed" in source`:
    bytecode probe sul callee identifier (refactor-resilient).
    """
    assert _references_name(execute_pipeline_task, "is_already_completed"), (
        "execute_pipeline_task non riferisce is_already_completed. "
        "Retry della pipeline intera duplica output se il precedente era "
        "completed."
    )


def test_pipeline_task_emits_skipped_idempotent_status_on_hit(monkeypatch):
    """Su hit idempotency, il task ritorna status='skipped_idempotent'.

    Replacement R-04b di `inspect.getsource(...) + "skipped_idempotent" in source`:
    eager mode + spy su is_already_completed → True, asserisce return shape.
    Behavior-bound vero: cattura sia la stringa sia il fatto che il
    return parte SUBITO (no esecuzione orchestrator).
    """
    from workers.celery_app import celery_app
    from workers.tasks import pipeline_task as pt

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)

    monkeypatch.setattr(
        "app.shared.idempotency.is_already_completed",
        lambda run_id: True,
    )

    # Orchestrator NON deve essere chiamato sul fast-path idempotency.
    fake_orchestrator = MagicMock()
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: fake_orchestrator)

    result = pt.execute_pipeline_task.apply(
        kwargs={"project_id": "p1", "run_id": "r1"},
    ).get()

    assert isinstance(result, dict)
    assert result.get("status") == "skipped_idempotent", (
        f"Idempotency hit deve ritornare status=skipped_idempotent. Got: {result}"
    )
    assert result.get("success") is True
    assert result.get("run_id") == "r1"
    fake_orchestrator.execute_pipeline.assert_not_called()


def test_pipeline_task_uses_passed_run_id_when_provided(monkeypatch):
    """Se run_id e' passato, viene preservato (non sovrascritto da fresh
    UUID). Verificato osservando il run_id nel return value e
    nell'argomento passato a is_already_completed.

    Replacement R-04b di `inspect.getsource(...) + lines/uuid4 scan`:
    spy su is_already_completed cattura il run_id ricevuto -> deve
    essere quello passato dal caller, non un UUID fresh.
    """
    from workers.celery_app import celery_app
    from workers.tasks import pipeline_task as pt

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)

    captured_run_ids = []

    def spy_is_already_completed(run_id):
        captured_run_ids.append(run_id)
        return True  # short-circuit per ridurre lo stub chain

    monkeypatch.setattr(
        "app.shared.idempotency.is_already_completed",
        spy_is_already_completed,
    )

    fixed_run_id = "stable-run-12345"
    result = pt.execute_pipeline_task.apply(
        kwargs={"project_id": "p1", "run_id": fixed_run_id},
    ).get()

    assert captured_run_ids == [fixed_run_id], (
        f"is_already_completed deve ricevere il run_id passato dal caller, "
        f"non un UUID fresh. Captured: {captured_run_ids}"
    )
    assert result.get("run_id") == fixed_run_id, (
        f"Return run_id non preservato. Got: {result}"
    )
