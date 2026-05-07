"""
Retry policy del carica_task (C4).

Contract: `carica_task` scrive su DB cliente. Un retry automatico
"""

import inspect

import pytest

from workers.tasks import carica_task


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_carica_task_is_configured_with_max_retries_zero():
    """carica_task decorator deve dichiarare max_retries=0 per evitare
    retry automatici che duplicherebbero write su DB cliente in
    modalita' append + per_batch.

    Replacement R-04b di `inspect.getsource(carica_task)` + lines scan:
    osservazione runtime dell'attributo Celery `Task.max_retries` esposto
    dal task. Behavior-bound: cattura l'effective configuration usata
    dal worker, non la stringa nel source.
    """
    task = carica_task.execute_carica_task
    # Celery Task subclass espone max_retries come attribute di classe;
    # se override non passato al decorator, e' l'app default (3).
    assert task.max_retries == 0, (
        f"execute_carica_task.max_retries={task.max_retries} (atteso 0). "
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
