"""Contract test per `app.core.scheduler.policy_engine.PolicyEngine`.

Gap reale: la dir `tests2/scheduler/unit/` ha 27 file ma ZERO test su
``policy_engine.py`` (214 LOC). PolicyEngine è stateless e gestisce
retry/backoff, conflict resolution e decisione di status finale — la
dorsale di ogni retry automatico post-failure dello scheduler.

Contratti blindati:

- ``should_retry(policy, attempt)`` ritorna ``(True, backoff[attempt])``
  finché ``attempt < max_retries``; ``(False, 0)`` oltre.
- Backoff list più corta di ``max_retries`` → ripete l'ULTIMO valore
  (documentato in source: *"Se ci sono più retry che backoff, usa
  l'ultimo valore"*). Regressione classica: qualcuno introduce un
  raise IndexError al posto del fallback.
- Backoff list vuota → fallback a 60s. Regressione: rimuovere il
  check ``backoff_list else 60`` lascerebbe passare un IndexError su
  ``backoff_list[-1]``.
- ``evaluate_result`` three-way: failed (success=False) > partial
  (skipped>0) > completed. Regressione: invertire l'ordine di check
  farebbe risultare "completed" un run con skipped > 0 (silente).
- ``decide_after_failure`` con attempts disponibili ritorna
  ``action=retry``, ``attempt+1``, ``event=RETRY_SCHEDULED``,
  ``log_level=WARNING``. Esauriti → ``action=give_up``,
  ``event=RETRY_EXHAUSTED``, ``log_level=ERROR``, attempt invariato.
  La distinzione log_level è critica per l'alerting operativo.
- ``decide_after_success`` differenzia PIPELINE_PARTIAL (WARNING) da
  PIPELINE_COMPLETED (INFO). Regressione: uniformare a INFO
  nasconderebbe i partial success nell'observability.
- Idempotenza: PolicyEngine è stateless, chiamate ripetute sullo
  stesso input DEVONO restituire lo stesso output e non mutare la
  policy passata. Regressione: introdurre cache interna o mutazione
  del config rompe il contract stateless.
- ``resolve_conflict`` + ``should_log_skipped`` sono passthrough, ma
  blindati per bloccare rename silenzioso dei field della policy.

Invariants blindati (``backend/docs/architecture/invariants.md``):

- §1.6 scheduler fail-fast: ``decide_after_failure`` NON può
  silentemente degradare a give_up se attempts sono disponibili.
- §1.3 idempotenza policy engine: stateless = output deterministico
  per input uguali.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.scheduler.models import (
    ScheduleConflictPolicy,
    ScheduleErrorPolicy,
)
from app.core.scheduler.policy_engine import PolicyEngine


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.scheduler]


# ─── 1) should_retry progression happy path ──────────────────────────────

@pytest.mark.parametrize(
    "attempt,expected_wait",
    [(0, 60), (1, 300), (2, 900)],
)
def test_should_retry_returns_true_and_progressive_backoff_within_max(
    attempt, expected_wait
):
    """Blinda il contract di progressione backoff: finché ``attempt <
    max_retries``, ``should_retry`` DEVE ritornare ``(True,
    backoff[attempt])`` leggendo l'indice corrispondente.

    Default policy: max_retries=3, backoff=[60, 300, 900]. I tre
    tentativi validi devono mappare esattamente sui tre valori della
    lista in ordine.

    Regressione: se qualcuno sostituisse ``backoff_list[current_attempt]``
    con ``backoff_list[0]`` (backoff costante), tutti i retry
    attenderebbero 60s — comportamento diverso e non documentato.
    """
    policy = ScheduleErrorPolicy()  # default: max=3, backoff=[60,300,900]

    should, wait = PolicyEngine.should_retry(policy, current_attempt=attempt)

    assert should is True, f"attempt={attempt} DEVE essere entro max_retries"
    assert wait == expected_wait, (
        f"attempt={attempt}: atteso {expected_wait}s, ottenuto {wait}s"
    )


# ─── 2) should_retry exhaustion ──────────────────────────────────────────

def test_should_retry_returns_false_and_zero_wait_when_max_retries_reached():
    """Blinda il contract di terminazione retry: quando
    ``current_attempt >= max_retries``, ``should_retry`` DEVE ritornare
    ``(False, 0)`` — niente retry, niente attesa residua.

    Regressione: se qualcuno usasse ``>`` invece di ``>=``,
    ``current_attempt == max_retries`` restituirebbe ancora retry,
    generando un tentativo extra oltre il limite configurato.
    """
    policy = ScheduleErrorPolicy(max_retries=3, retry_backoff_seconds=[60, 300, 900])

    # Test boundary: attempt=3 = max_retries deve già essere exhausted
    should_at_max, wait_at_max = PolicyEngine.should_retry(policy, current_attempt=3)
    assert should_at_max is False
    assert wait_at_max == 0

    # Anche attempts oltre limite: stesso comportamento
    should_over, wait_over = PolicyEngine.should_retry(policy, current_attempt=10)
    assert should_over is False
    assert wait_over == 0


# ─── 3) Backoff list shorter than max_retries → repeat last value ───────

def test_should_retry_uses_last_backoff_value_when_list_shorter_than_max():
    """Blinda il contract documentato nel source: *"Se ci sono più retry
    che backoff, usa l'ultimo valore"*.

    Scenario: policy con max_retries=5 ma backoff=[30, 60] (solo 2
    valori). Tentativi 0,1 usano lista; tentativi 2,3,4 devono
    riutilizzare 60 (l'ultimo).

    Regressione: senza il fallback ``backoff_list[-1]``, l'accesso a
    ``backoff_list[2]`` lancerebbe ``IndexError`` interrompendo lo
    scheduler invece di retryare con l'ultimo wait documentato.
    """
    policy = ScheduleErrorPolicy(max_retries=5, retry_backoff_seconds=[30, 60])

    # 0,1: da lista
    assert PolicyEngine.should_retry(policy, 0) == (True, 30)
    assert PolicyEngine.should_retry(policy, 1) == (True, 60)

    # 2,3,4: fallback all'ultimo valore (60)
    assert PolicyEngine.should_retry(policy, 2) == (True, 60)
    assert PolicyEngine.should_retry(policy, 3) == (True, 60)
    assert PolicyEngine.should_retry(policy, 4) == (True, 60)


# ─── 4) Empty backoff list → fallback 60s ────────────────────────────────

def test_should_retry_falls_back_to_60s_when_backoff_list_is_empty():
    """Blinda il fallback di sicurezza quando la config ha
    ``retry_backoff_seconds=[]``: ``should_retry`` DEVE ritornare
    ``(True, 60)``, non crashare.

    Caso edge ma plausibile: qualcuno configura ``max_retries=3`` senza
    fornire backoff (lista vuota). Il fallback ``60 if not list else
    list[-1]`` evita IndexError runtime.

    Regressione: rimuovere il ternario ``backoff_list[-1] if backoff_list
    else 60`` lascerebbe un accesso out-of-range che propagherebbe
    IndexError allo scheduler, che killare il task invece di retryarlo.
    """
    policy = ScheduleErrorPolicy(max_retries=3, retry_backoff_seconds=[])

    should, wait = PolicyEngine.should_retry(policy, current_attempt=0)

    assert should is True
    assert wait == 60, (
        f"Backoff list vuota DEVE fallback a 60s, ottenuto {wait}s"
    )


# ─── 5) evaluate_result three-way ────────────────────────────────────────

@pytest.mark.parametrize(
    "success,rows_written,rows_skipped,expected_status",
    [
        (False, 0, 0, "failed"),       # failure vince sempre
        (False, 100, 50, "failed"),    # failure vince anche con rows_written
        (True, 100, 0, "completed"),   # success pulito
        (True, 100, 5, "partial"),     # success con skipped → partial
        (True, 0, 10, "partial"),      # zero written ma skipped → partial
    ],
)
def test_evaluate_result_classifies_status_by_success_and_skipped(
    success, rows_written, rows_skipped, expected_status
):
    """Blinda il contract di classificazione status a tre stati:

    - success=False → "failed" sempre (priority 1)
    - success=True + skipped > 0 → "partial"
    - success=True + skipped = 0 → "completed"

    Regressione: invertire i check (controllare skipped prima di
    success) ritornerebbe "partial" invece di "failed" su un run
    fallito con parziali skipped lato CARICA. L'alerting
    interpreterebbe come partial success un fail effettivo.
    """
    status = PolicyEngine.evaluate_result(
        success=success,
        rows_written=rows_written,
        rows_skipped=rows_skipped,
    )

    assert status == expected_status


# ─── 6) decide_after_failure: retry scheduled ────────────────────────────

def test_decide_after_failure_schedules_retry_with_warning_and_increments_attempt(
    schedule_config,
):
    """Blinda il payload completo quando il retry è disponibile:

    - ``action == "retry"``
    - ``wait_seconds == backoff[current_attempt]``
    - ``attempt == current_attempt + 1`` (prossimo tentativo)
    - ``event == "RETRY_SCHEDULED"``
    - ``log_level == "WARNING"``

    La fixture ``schedule_config`` usa ``ScheduleErrorPolicy()`` default
    (max=3, backoff=[60, 300, 900]).

    Regressione:
    - Downgrade del log_level a "INFO" renderebbe invisibili i retry
      nel sistema di alerting.
    - Mancato increment di attempt porterebbe a retry loop infinito
      sullo stesso valore di attempt.
    """
    decision = PolicyEngine.decide_after_failure(
        config=schedule_config,
        current_attempt=1,
        error_message="connessione persa",
    )

    assert decision == {
        "action": "retry",
        "wait_seconds": 300,   # backoff[1]
        "attempt": 2,          # current_attempt + 1
        "event": "RETRY_SCHEDULED",
        "log_level": "WARNING",
    }


# ─── 7) decide_after_failure: retry exhausted ────────────────────────────

def test_decide_after_failure_emits_give_up_with_error_and_preserves_attempt(
    schedule_config,
):
    """Blinda il payload completo quando il retry budget è esaurito:

    - ``action == "give_up"``
    - ``wait_seconds == 0`` (nessuna attesa, errore finale)
    - ``attempt == current_attempt`` (NON incrementato — tentativi
      consumati)
    - ``event == "RETRY_EXHAUSTED"``
    - ``log_level == "ERROR"`` (distinto da WARNING del retry in corso)

    La distinzione tra WARNING (retry in corso) ed ERROR (esaurimento)
    è critica per la dashboard oncall: ERROR deve pagare, WARNING no.

    Regressione:
    - Uniformare a WARNING farebbe perdere la page su esaurimento retry.
    - Incrementare attempt su give_up falserebbe le metriche
      consecutive_failures / total_runs.
    """
    # max_retries default = 3; uso attempt=3 per forzare exhaustion
    decision = PolicyEngine.decide_after_failure(
        config=schedule_config,
        current_attempt=3,
        error_message="ultimo fallimento",
    )

    assert decision == {
        "action": "give_up",
        "wait_seconds": 0,
        "attempt": 3,           # NON incrementato
        "event": "RETRY_EXHAUSTED",
        "log_level": "ERROR",
    }


# ─── 8) decide_after_success: partial vs completed ───────────────────────

@pytest.mark.parametrize(
    "rows_written,rows_skipped,expected_status,expected_event,expected_log_level",
    [
        (100, 0, "completed", "PIPELINE_COMPLETED", "INFO"),
        (100, 5, "partial", "PIPELINE_PARTIAL", "WARNING"),
        (0, 3, "partial", "PIPELINE_PARTIAL", "WARNING"),
    ],
)
def test_decide_after_success_differentiates_partial_from_completed(
    rows_written,
    rows_skipped,
    expected_status,
    expected_event,
    expected_log_level,
):
    """Blinda la differenziazione partial vs completed:

    - skipped=0 → "completed" / PIPELINE_COMPLETED / INFO
    - skipped>0 → "partial" / PIPELINE_PARTIAL / WARNING

    La distinzione log_level è intenzionale: partial success merita
    attenzione (rows droppate per conflicts) ma non pager. Una
    regressione che uniformasse a INFO nasconderebbe i parziali
    dall'observability — si scoprirebbero solo dal conteggio righe in
    destination.
    """
    decision = PolicyEngine.decide_after_success(
        rows_written=rows_written,
        rows_skipped=rows_skipped,
    )

    assert decision == {
        "status": expected_status,
        "event": expected_event,
        "log_level": expected_log_level,
    }


# ─── 9) Idempotency / pure function contract ─────────────────────────────

def test_policy_engine_is_stateless_and_does_not_mutate_policy(schedule_config):
    """Blinda il contract stateless documentato in source: *"Stateless:
    riceve la config e lo stato corrente, restituisce la decisione senza
    effetti collaterali"*.

    Chiamate ripetute con stesso input DEVONO ritornare stesso output e
    NON mutare l'oggetto policy passato.

    Regressione: se qualcuno introducesse una cache interna (es.
    ``_last_decision``) o mutasse ``policy.retry_backoff_seconds`` (es.
    ``.pop(0)`` per scalare il backoff), il comportamento diventerebbe
    stateful → due scheduler paralleli che condividono la stessa config
    osserverebbero decisioni diverse, violando l'invariante di
    determinismo.
    """
    # Snapshot deep della policy prima delle chiamate
    policy_snapshot = deepcopy(schedule_config.on_error)

    # Chiamate ripetute sullo stesso input
    decision_1 = PolicyEngine.decide_after_failure(
        schedule_config, current_attempt=0, error_message="x"
    )
    decision_2 = PolicyEngine.decide_after_failure(
        schedule_config, current_attempt=0, error_message="x"
    )
    decision_3 = PolicyEngine.decide_after_failure(
        schedule_config, current_attempt=0, error_message="diverso"
    )

    # Idempotenza sulla decisione (error_message non influisce)
    assert decision_1 == decision_2 == decision_3, (
        "PolicyEngine.decide_after_failure DEVE essere idempotente sullo "
        "stesso (config, attempt); error_message è solo diagnostico."
    )

    # Nessuna mutazione sul policy object
    assert schedule_config.on_error.max_retries == policy_snapshot.max_retries
    assert schedule_config.on_error.retry_backoff_seconds == (
        policy_snapshot.retry_backoff_seconds
    ), (
        "PolicyEngine NON deve mutare retry_backoff_seconds (es. pop/append). "
        "Stateless = config immutata dopo la call."
    )


# ─── 10) resolve_conflict + should_log_skipped passthrough ───────────────

@pytest.mark.parametrize("mode", ["skip", "force", "abort"])
def test_resolve_conflict_returns_configured_mode_exactly(mode):
    """Blinda il passthrough del field ``append_conflicts``: i tre
    valori canonici (skip/force/abort) DEVONO essere restituiti così
    come configurati.

    Contract minimo ma necessario: se qualcuno rinominasse
    ``append_conflicts`` (es. in ``on_conflict``) o lo defaultasse a
    None, silenziosamente gli schedule configurati con ``force``
    inizierebbero a seguire un default diverso — bug di
    silent-policy-downgrade tipico.
    """
    policy = ScheduleConflictPolicy(append_conflicts=mode, log_skipped_rows=True)

    assert PolicyEngine.resolve_conflict(policy) == mode


def test_should_log_skipped_returns_configured_bool_exactly():
    """Blinda che ``should_log_skipped`` ritorni il bool configurato
    senza trasformazioni. Default è True (log verboso), ma la config
    utente può disabilitarlo per append massivi.

    Regressione: se qualcuno inserisse un ``or True`` di default,
    l'utente perderebbe la possibilità di silenziare il log delle
    skipped rows, rompendo il contract della policy.
    """
    policy_on = ScheduleConflictPolicy(log_skipped_rows=True)
    policy_off = ScheduleConflictPolicy(log_skipped_rows=False)

    assert PolicyEngine.should_log_skipped(policy_on) is True
    assert PolicyEngine.should_log_skipped(policy_off) is False
