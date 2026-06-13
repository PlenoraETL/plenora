"""
Retry policy del carica_task (C4).

Contract: `carica_task` scrive su DB cliente. Un retry automatico
"""


import pytest

from workers.tasks import carica_task


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_carica_actor_is_configured_with_max_retries_zero():
    """L'actor Dramatiq carica.execute deve dichiarare max_retries=0 per
    evitare retry automatici che duplicherebbero write su DB cliente in
    modalita' append + per_batch.

    Plenora 2.0 Fase 5: il corpo e' una funzione pura; la policy no-retry
    vive ora sull'actor Dramatiq (era l'attributo Celery Task.max_retries).
    """
    from workers.dramatiq_app import carica_execute

    max_retries = carica_execute.options.get("max_retries")
    assert max_retries == 0, (
        f"carica.execute actor max_retries={max_retries} (atteso 0). "
        "Retry automatico duplicherebbe write su DB cliente in "
        "append/per_batch mode."
    )


def test_carica_task_docstring_mentions_no_retry_rationale():
    """La docstring del task deve spiegare perche' non c'e' retry
    automatico — altrimenti qualcuno domani riapre max_retries=3
    senza capire perche' era 0."""
    docstring = carica_task.execute_carica_task.__doc__ or ""
    assert docstring, "execute_carica_task senza docstring"
    # Cerca menzione di retry / idempotency / duplicazione
    lowered = docstring.lower()
    markers = ["retry", "idempot", "duplic", "fail-fast", "manual"]
    matched = [m for m in markers if m in lowered]
    assert matched, (
        f"Docstring di execute_carica_task non spiega la scelta "
        f"no-retry. Markers cercati: {markers}"
    )
