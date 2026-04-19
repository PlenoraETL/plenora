"""Retry policy del carica_task (C4).

Contract: `carica_task` scrive su DB cliente. Un retry automatico
dopo fallimento parziale di un write in append-mode + per-batch
(senza unique constraints) produce duplicazioni. Politica di
progetto: **fail-fast**, niente retry automatico.

Razionale: preferiamo un task fallito visibile (l'operatore vede
"carica fallita al batch 500", ri-lancia manualmente dopo aver
verificato la tabella) a un retry silenzioso che può scrivere
N volte lo stesso subset di righe.

Il pipeline_task ha gia' `max_retries=0` per ragione analoga
(compute-heavy + scritture intermedie). Allineiamo carica_task.
"""

import inspect

import pytest

from workers.tasks import carica_task


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_carica_task_is_configured_with_max_retries_zero():
    """carica_task decorator deve dichiarare max_retries=0 per evitare
    retry automatici che duplicherebbero write su DB cliente in
    modalita' append + per_batch."""
    # Il source del modulo contiene la dichiarazione del decorator.
    # Cerchiamo il pattern "max_retries=0" nel body del file vicino
    # alla dichiarazione del task.
    source = inspect.getsource(carica_task)
    lines = source.splitlines()

    # Trova la linea con `@celery_app.task(` seguita da "carica.execute"
    for i, line in enumerate(lines):
        if "celery_app.task" in line:
            # Controlla le 10 righe successive per max_retries
            decorator_block = "\n".join(lines[i:i + 15])
            if "carica.execute" in decorator_block:
                assert "max_retries=0" in decorator_block, (
                    f"carica_task ha max_retries != 0. Block trovato:\n"
                    f"{decorator_block}"
                )
                return
    pytest.fail("Non trovo il decorator di execute_carica_task")


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
