"""Idempotency guard per pipeline_task (C4 round 4).

Chiude il gap rilevato dall'auditor: `pipeline_task` genera run_id
fresh ogni invocazione. Se l'admin rilancia l'intera pipeline dopo
un fail parziale, nuovo run_id → i guard di carica/enrichment
(che pur avendo run_id kwarg) vedono una chiave sconosciuta →
l'orchestrator in-process riesegue tutto → duplicazione.

Fix: `pipeline_task` accetta `run_id` optional. Se passato (dal
retry-handler UI, dal scheduler, da replay), il task:
- Se e' gia' in intermediate.runs con status='completed' → skip.
- Altrimenti procede con quel run_id stabile.

Quando il caller non passa run_id → genera fresh (legacy, no
idempotency). Coerente col pattern di carica_task / enrichment_task.

Nota: pipeline_task chiama l'orchestrator in-process, NON subtask
Celery separati. Quindi la propagazione del run_id ai "child" non
si applica qui — l'orchestrator riceve direttamente il run_id via
`execute_pipeline(run_id=...)` che e' gia' in place.
"""

import inspect

import pytest

from workers.tasks.pipeline_task import execute_pipeline_task


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


def test_pipeline_task_accepts_run_id_kwarg():
    """execute_pipeline_task deve accettare `run_id` optional."""
    sig = inspect.signature(execute_pipeline_task)
    assert "run_id" in sig.parameters, (
        "execute_pipeline_task non accetta run_id kwarg. "
        "Retry manuale della pipeline intera aggira l'idempotency dei child."
    )
    # Default None → generazione fresh (backward compat con call site esistenti)
    assert sig.parameters["run_id"].default is None


def test_pipeline_task_source_checks_idempotency_when_run_id_passed():
    """Il body deve fare il check is_already_completed quando run_id e' passato."""
    source = inspect.getsource(execute_pipeline_task)
    assert "is_already_completed" in source or "idempotency" in source.lower(), (
        "execute_pipeline_task non fa idempotency check. Retry della "
        "pipeline intera duplica output se il precedente era completed."
    )


def test_pipeline_task_emits_skipped_idempotent_status():
    """Coerenza con carica_task e enrichment_task: su hit idempotency,
    ritorna status='skipped_idempotent'."""
    source = inspect.getsource(execute_pipeline_task)
    assert "skipped_idempotent" in source, (
        "execute_pipeline_task non emette 'skipped_idempotent'. "
        "Incoerenza con carica_task/enrichment_task."
    )


def test_pipeline_task_uses_passed_run_id_not_fresh_uuid_when_provided():
    """Se run_id e' passato, NON deve generare un fresh UUID sovrascrivendolo."""
    source = inspect.getsource(execute_pipeline_task)
    # Cerca pattern: if run_id is None: run_id = str(_uuid.uuid4())
    lines = source.splitlines()
    found_conditional_generation = False
    for i, line in enumerate(lines):
        if "uuid4" in line or "_uuid.uuid4" in line:
            # Guarda le 3 linee precedenti per cercare un check "if run_id is None"
            context_lines = lines[max(0, i - 3):i + 1]
            context = "\n".join(context_lines)
            if "run_id is None" in context or "if not run_id" in context or "run_id or " in context:
                found_conditional_generation = True
                break
    assert found_conditional_generation, (
        "UUID generation non e' condizionale: il run_id passato viene "
        "sovrascritto. Deve essere `if run_id is None: run_id = str(uuid4())`."
    )
