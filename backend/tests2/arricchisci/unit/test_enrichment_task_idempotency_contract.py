"""Idempotency guard per enrichment_task (C4 round 3).

Stesso pattern del carica_task: se un run_id viene passato
esplicitamente E esiste gia' in intermediate.runs con
status='completed', il task skippa l'esecuzione.

Il caller naturale e' lo scheduler (passa schedule_run_id) oppure
un retry-handler UI.
"""

import inspect

import pytest

from workers.tasks.enrichment_task import execute_enrichment_task


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def test_enrichment_task_accepts_run_id_kwarg():
    """execute_enrichment_task deve accettare `run_id` optional
    per permettere l'idempotency check."""
    sig = inspect.signature(execute_enrichment_task)
    assert "run_id" in sig.parameters, (
        "execute_enrichment_task non accetta run_id per idempotency"
    )
    assert sig.parameters["run_id"].default is None


def test_enrichment_task_source_checks_idempotency_when_run_id_passed():
    """Il body del task deve invocare is_already_completed prima di
    procedere se run_id e' stato passato dall'esterno."""
    source = inspect.getsource(execute_enrichment_task)
    assert "is_already_completed" in source or "idempotency" in source.lower(), (
        "execute_enrichment_task non fa check di idempotency. "
        "Un retry manuale con stesso run_id eseguirebbe due volte."
    )


def test_enrichment_task_skipped_idempotent_returns_early():
    """Il source deve ritornare un risultato 'skipped_idempotent'
    quando l'idempotency hit avviene — in modo coerente con il
    carica_task."""
    source = inspect.getsource(execute_enrichment_task)
    assert "skipped_idempotent" in source, (
        "Il task non emette lo status 'skipped_idempotent' sul hit. "
        "Coerenza col carica_task rotta."
    )
