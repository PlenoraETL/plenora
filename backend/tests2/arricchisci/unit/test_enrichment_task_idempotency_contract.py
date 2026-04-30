"""Idempotency guard per enrichment_task (C4 round 3).

Stesso pattern del carica_task: se un run_id viene passato
esplicitamente E esiste gia' in intermediate.runs con
status='completed', il task skippa l'esecuzione.

Il caller naturale e' lo scheduler (passa schedule_run_id) oppure
un retry-handler UI.

PR test/phase-1-test-reliability — modernizzazione behavior-bound:
i 2 test pre-fix usavano `inspect.getsource(execute_enrichment_task)`
+ grep stringhe (`"is_already_completed" in source`,
`"skipped_idempotent" in source`). Approccio source-bound fragile:
dopo refactor (es. estrazione del check di idempotency in helper
modulo-level, o rinomina della stringa di status) il source potrebbe
cambiare anche preservando il behavior.

Post-fix: i test sono behavior-bound. Verificano la **proprieta'
osservabile** che il source-scan voleva proteggere:
  - se `run_id` e' passato e `is_already_completed(run_id)` ritorna
    True, il task ritorna immediatamente con status='skipped_idempotent'
    senza eseguire alcuna delle dep downstream
  - il dict ritornato include `success=True`, il `run_id` echo, il
    task_id e un message
"""

from __future__ import annotations

import inspect

import pytest

from workers.tasks.enrichment_task import execute_enrichment_task
# Import fixture come simbolo locale per discovery pytest senza conftest.
from tests2._helpers.celery_eager import celery_eager  # noqa: F401


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def test_enrichment_task_accepts_run_id_kwarg():
    """execute_enrichment_task deve accettare `run_id` optional
    per permettere l'idempotency check. (Signature-bound, refactor-safe.)"""
    sig = inspect.signature(execute_enrichment_task)
    assert "run_id" in sig.parameters, (
        "execute_enrichment_task non accetta run_id per idempotency"
    )
    assert sig.parameters["run_id"].default is None


def test_enrichment_task_invokes_idempotency_check_when_run_id_passed(
    celery_eager, monkeypatch,
):
    """Behavior: con run_id passato, il task invoca
    `is_already_completed(run_id)` PRIMA di qualsiasi altra logica."""
    from tests2._helpers.celery_eager import (
        install_enrichment_task_mocks, invoke_enrichment_task,
    )

    # Spy custom su is_already_completed
    captured: list = []

    def _spy(run_id):
        captured.append(run_id)
        return False  # non-completato → task prosegue (e fallira' su altro)

    install_enrichment_task_mocks(monkeypatch)
    monkeypatch.setattr("app.shared.idempotency.is_already_completed", _spy)

    # Canvas vuoto → il task fallira' su "Nessun nodo nel canvas" o simile
    # ma DOPO l'idempotency check.
    invoke_enrichment_task(
        project_id="test-proj",
        canvas={"nodes": [], "edges": []},
        run_id="run-abc",
    )

    assert captured == ["run-abc"], (
        f"is_already_completed non chiamata con il run_id atteso. "
        f"Captured: {captured}"
    )


def test_enrichment_task_returns_skipped_idempotent_when_already_completed(
    celery_eager, monkeypatch,
):
    """Behavior: se `is_already_completed` ritorna True per il run_id,
    il task ritorna immediatamente uno status='skipped_idempotent' e
    NON istanzia ApiConnectionService ne' carica dati.

    Coerenza col carica_task: stesso shape `{status, success, message,
    run_id, task_id}`."""
    from tests2._helpers.celery_eager import (
        install_enrichment_task_mocks, invoke_enrichment_task,
    )

    spies = install_enrichment_task_mocks(
        monkeypatch, is_already_completed_returns=True,
    )

    result = invoke_enrichment_task(
        project_id="test-proj",
        canvas={"nodes": [], "edges": []},
        run_id="run-already-done",
    )

    assert result.get("status") == "skipped_idempotent", (
        f"Il task DEVE ritornare status='skipped_idempotent' quando "
        f"l'idempotency check hit. Ricevuto: {result}"
    )
    assert result.get("success") is True
    assert result.get("run_id") == "run-already-done"
    assert "task_id" in result
    # Critico: NESSUNA dep downstream invocata (early return)
    assert spies.api_cs_instances == [], (
        "Idempotency hit: NON deve istanziare ApiConnectionService"
    )
    assert spies.intermediate_run_creates == [], (
        "Idempotency hit: NON deve creare run intermediate"
    )
