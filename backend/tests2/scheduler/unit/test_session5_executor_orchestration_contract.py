"""Sessione 5 contract test: backend executor + orchestration P1 residui.

Bug coperti:
1. manipola/executor.py:281-301 — except catch-all mancante (solo
   ValueError/MemoryLimit pre-fix)
2. manipola/executor.py:_carica_sorgenti_statiche — no logger.error
3. arricchisci/executor.py:133-136 — exc_info=True mancante
4. arricchisci/executor.py:219 — `_nd` ridefinito nel loop (waste)
5. celery_app.py — `autoretry_for` includeva OSError (catturava
   FileNotFoundError/PermissionError permanent)
6. beat_scheduler.py — catchup audit fail-open DOPO send_task
   (duplicati al prossimo tick se INSERT fallisce)
7. scheduler_execution_runtime.py:174 — set_runtime_status(RUNNING)
   prima di enqueue (status orphan se enqueue fallisce)
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Path 1: manipola except catch-all

def test_manipola_applica_trasformazioni_has_generic_exception_catch():
    """Il blocco try attorno a `_applica_trasformazioni` deve catturare
    ANCHE Exception generica (oltre a ValueError + MemoryLimitExceededError)
    per emettere `append_step` su KeyError/AttributeError/TypeError dei
    plugin transform."""
    from app.core.manipola import executor

    src = inspect.getsource(executor.ManipolaExecutor.execute)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Cerca il blocco try del _applica_trasformazioni
    idx = src_no_comments.find("_applica_trasformazioni(")
    assert idx >= 0
    window = src_no_comments[idx:idx + 2500]

    # Deve avere `except Exception` come catch-all (non solo ValueError/MemoryLimit)
    assert "except Exception" in window, (
        "manipola execute deve avere except Exception catch-all per "
        "KeyError/AttributeError/TypeError dei plugin transform "
        "(no piu' propagation senza append_step)"
    )
    # Logger.error + exc_info per triageability
    assert "exc_info=True" in window


# Path 2: manipola _carica_sorgenti_statiche logger.error

def test_manipola_carica_sorgenti_statiche_has_logger_error():
    """Catch in `_carica_sorgenti_statiche` deve loggare a ERROR con
    `exc_info=True` e includere il node.id per triageability."""
    from app.core.manipola import executor

    src = inspect.getsource(
        executor.ManipolaExecutor._carica_sorgenti_statiche
    )

    assert "logger.error" in src, (
        "_carica_sorgenti_statiche deve loggare a ERROR su exception "
        "(pre-fix: solo return dict senza traccia diagnostica)"
    )
    assert "exc_info=True" in src


# Path 3: arricchisci exc_info su schema inference

def test_arricchisci_schema_inference_warning_has_exc_info():
    """L'except attorno alla schema inference in arricchisci/executor.py
    deve avere exc_info=True per produrre traceback su bug di codice
    (TypeError/AttributeError) anche se livello e' WARNING per dato
    mancante."""
    from app.core.arricchisci import executor

    src = inspect.getsource(executor.EnrichExecutor.execute)

    # Cerca il warning della schema inference
    idx = src.find("output_contract arricchisci: schema id non disponibili")
    assert idx >= 0
    window = src[idx:idx + 200]
    assert "exc_info=True" in window, (
        "Schema inference warning deve avere exc_info=True per traceback"
    )


# Path 4: arricchisci _nd extracted out of loop

def test_arricchisci_nd_helper_defined_outside_loop():
    """`_nd` helper in `preview()` deve essere definito FUORI dal for
    loop (era ridefinito N volte ad ogni iterazione)."""
    from app.core.arricchisci import executor

    # _nd e' nel metodo `preview` non `execute`
    src = inspect.getsource(executor.EnrichExecutor.preview)

    # Trova il def _nd
    def_idx = src.find("def _nd(")
    assert def_idx >= 0, "_nd helper non trovato in preview()"
    # Trova il for loop
    for_idx = src.find("for node_id in execution_order")
    assert for_idx >= 0, "for loop execution_order non trovato"
    # def _nd DEVE essere PRIMA del for loop
    assert def_idx < for_idx, (
        "`_nd` deve essere definito PRIMA del for loop "
        "(pre-fix: ridefinito ad ogni iterazione)"
    )


# Path 5: celery_app autoretry_for senza OSError

def test_celery_app_autoretry_excludes_oserror():
    """`BaseTaskWithRetry.autoretry_for` non deve includere OSError
    generico (catturava FileNotFoundError/PermissionError permanent)."""
    from workers.celery_app import BaseTaskWithRetry

    autoretry = BaseTaskWithRetry.autoretry_for
    assert OSError not in autoretry, (
        "OSError NON deve essere in autoretry_for (catturava sottoclassi "
        "permanent come FileNotFoundError, PermissionError). Pre-fix: "
        "retry N volte su errori permanenti bloccava il worker."
    )
    # Le sottoclassi transient specifiche restano
    assert ConnectionError in autoretry
    assert TimeoutError in autoretry


# Path 6: beat_scheduler catchup audit insert PRIMA di send_task

def test_beat_scheduler_catchup_audit_before_send_task():
    """In `_emit_single_catchup`, l'INSERT audit DEVE precedere il
    `send_task`. Pre-fix: send_task PRIMA, INSERT fail-open DOPO →
    duplicati al prossimo tick se INSERT fallisce."""
    from workers import beat_scheduler

    src = inspect.getsource(beat_scheduler.PlenoraBeatScheduler._emit_single_catchup)

    insert_idx = src.find("INSERT INTO schedule_catchup_history")
    send_task_idx = src.find("self.app.send_task(")

    assert insert_idx >= 0 and send_task_idx >= 0
    assert insert_idx < send_task_idx, (
        "L'INSERT audit DEVE precedere send_task per garantire dedupe "
        "history-based. Pre-fix: send_task PRIMA → se INSERT falliva, "
        "prossimo tick re-emetteva → duplicato in volo."
    )


# Path 7: scheduler_execution_runtime enqueue PRIMA di set_runtime_status

def test_scheduler_runtime_enqueue_before_set_running():
    """In `handle_failure` retry path, `enqueue_schedule_run` DEVE
    precedere `set_runtime_status(RUNNING)`. Pre-fix: status RUNNING
    impostato PRIMA → se enqueue fallisce, status resta RUNNING orfano."""
    from app.core.scheduler import scheduler_execution_runtime as runtime_mod

    src = inspect.getsource(runtime_mod)

    # Localizza il retry block
    retry_idx = src.find('if decision["action"] == "retry":')
    assert retry_idx >= 0
    window = src[retry_idx:retry_idx + 2000]

    # Stripping commenti
    window_no_comments = "\n".join(
        line for line in window.splitlines() if not line.lstrip().startswith("#")
    )

    enqueue_idx = window_no_comments.find("enqueue_schedule_run(")
    status_idx = window_no_comments.find("set_runtime_status(schedule_id, RuntimeStatus.RUNNING)")

    assert enqueue_idx >= 0 and status_idx >= 0
    assert enqueue_idx < status_idx, (
        "enqueue_schedule_run DEVE precedere set_runtime_status(RUNNING) "
        "nel retry path — altrimenti enqueue fallito lascia status orfano."
    )
