"""Sessione 5 contract test: backend executor + orchestration P1 residui.

Bug coperti:
1. manipola/executor.py:281-301 — except catch-all mancante (solo
   ValueError/MemoryLimit pre-fix)
2. manipola/executor.py:_carica_sorgenti_statiche — no logger.error
3. arricchisci/executor.py:133-136 — exc_info=True mancante
4. arricchisci/executor.py:219 — `_nd` ridefinito nel loop (waste)
5. (storico) celery_app.py — `autoretry_for` includeva OSError; rimosso
   col cutover Dramatiq (Fase 5), gli actor sono max_retries=0
6. beat_scheduler.py — catchup audit fail-open DOPO send_task
   (duplicati al prossimo tick se INSERT fallisce)
7. scheduler_execution_runtime.py:174 — set_runtime_status(RUNNING)
   prima di enqueue (status orphan se enqueue fallisce)
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

from tests2.shared._ast_identifiers import uses_attribute


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
    # RAW: "except Exception" e' un compound sintattico (except clause), non un semplice identificatore
    assert "except Exception" in window, (
        "manipola execute deve avere except Exception catch-all per "
        "KeyError/AttributeError/TypeError dei plugin transform "
        "(no piu' propagation senza append_step)"
    )
    # RAW: "exc_info=True" e' un keyword=value compound; call_has_keyword richiede func_name specifico
    assert "exc_info=True" in window


# Path 2: manipola _carica_sorgenti_statiche logger.error

def test_manipola_carica_sorgenti_statiche_has_logger_error():
    """Catch in `_carica_sorgenti_statiche` deve loggare a ERROR con
    `exc_info=True` e includere il node.id per triageability."""
    from app.core.manipola import executor

    src = textwrap.dedent(inspect.getsource(
        executor.ManipolaExecutor._carica_sorgenti_statiche
    ))

    assert uses_attribute(src, "logger", "error"), (
        "_carica_sorgenti_statiche deve loggare a ERROR su exception "
        "(pre-fix: solo return dict senza traccia diagnostica)"
    )
    # RAW: "exc_info=True" e' un keyword=value compound (non solo identificatore); call_has_keyword richiede func_name specifico
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
    # RAW: la stringa e' un messaggio di log/prosa, non un identificatore codice
    idx = src.find("output_contract arricchisci: schema id non disponibili")
    assert idx >= 0
    window = src[idx:idx + 200]
    # RAW: "exc_info=True" e' un keyword=value compound; call_has_keyword richiede func_name specifico
    assert "exc_info=True" in window, (
        "Schema inference warning deve avere exc_info=True per traceback"
    )


# Path 4 rimosso (B step C2): `preview()` non ha piu' un loop per-nodo
# inline (ne' l'helper `_nd`) — delega al loop canonico `run_enrichment_canvas`.


# Path 5 (storico): celery_app.py BaseTaskWithRetry.autoretry_for senza
# OSError. Rimosso in Plenora 2.0 Fase 5 (fetta 7): niente piu' celery_app
# ne' BaseTaskWithRetry; gli actor Dramatiq sono tutti max_retries=0 (niente
# autoretry, i corpi ritornano dict su errore) -> il bug di OSError nella
# lista autoretry non e' piu' rappresentabile. Parita' no-retry blindata da
# tests2/carica/unit/test_carica_task_retry_policy_contract.py.


# Path 6: beat_scheduler catchup audit insert PRIMA di send_task

def test_beat_scheduler_catchup_audit_before_send_task():
    """In `_emit_single_catchup`, l'INSERT audit DEVE precedere l'emit del
    task. Pre-fix: emit PRIMA, INSERT fail-open DOPO → duplicati al
    prossimo tick se INSERT fallisce. F3.2: l'emit passa da
    _dispatch_schedule_task (layer dispatch) invece di self.app.send_task."""
    from workers import beat_scheduler

    src = inspect.getsource(beat_scheduler.PlenoraBeatScheduler._emit_single_catchup)

    # RAW: "INSERT INTO schedule_catchup_history" e' un SQL literal, non un identificatore codice
    insert_idx = src.find("INSERT INTO schedule_catchup_history")
    # RAW: il test verifica ORDINE (insert_idx < emit_idx); gli helper AST restituiscono bool non posizione
    emit_idx = src.find("self._dispatch_schedule_task(")

    assert insert_idx >= 0 and emit_idx >= 0
    assert insert_idx < emit_idx, (
        "L'INSERT audit DEVE precedere l'emit per garantire dedupe "
        "history-based. Pre-fix: emit PRIMA → se INSERT falliva, "
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
    # RAW: 'if decision["action"] == "retry":' e' uno statement con dict-key literal, non un identificatore
    retry_idx = src.find('if decision["action"] == "retry":')
    assert retry_idx >= 0
    window = src[retry_idx:retry_idx + 2000]

    # Stripping commenti
    window_no_comments = "\n".join(
        line for line in window.splitlines() if not line.lstrip().startswith("#")
    )

    # RAW: il test verifica ORDINE (enqueue_idx < status_idx); gli helper AST restituiscono bool non posizione
    enqueue_idx = window_no_comments.find("enqueue_schedule_run(")
    # RAW: "set_runtime_status(schedule_id, RuntimeStatus.RUNNING)" e' uno statement completo con argomenti, non identificatore semplice
    status_idx = window_no_comments.find("set_runtime_status(schedule_id, RuntimeStatus.RUNNING)")

    assert enqueue_idx >= 0 and status_idx >= 0
    assert enqueue_idx < status_idx, (
        "enqueue_schedule_run DEVE precedere set_runtime_status(RUNNING) "
        "nel retry path — altrimenti enqueue fallito lascia status orfano."
    )
