"""Contract del kwarg `scheduled_for` su schedule_pipeline_task (C3 fix 3).

Il beat, quando emette un catchup run, passa un `scheduled_for` ISO
al task. Il task deve:
1. Accettare il kwarg senza errori (retrocompat: default None).
2. Loggarlo in modo visibile (l'operatore capisce in audit che run
   logico sta eseguendo, non il wall-clock time).
3. Propagarlo all'open_run() come metadato di `triggered_by` esteso
   o campo dedicato (per lineage).
"""

import inspect

import pytest

from workers.tasks.schedule_task import schedule_pipeline_task


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_schedule_pipeline_task_accepts_scheduled_for_kwarg():
    """Il task deve dichiarare `scheduled_for` tra i parametri (default None)."""
    sig = inspect.signature(schedule_pipeline_task)
    params = sig.parameters
    assert "scheduled_for" in params
    # Default deve essere None per retrocompat con chiamate non-catchup
    assert params["scheduled_for"].default is None


def test_task_source_mentions_scheduled_for_in_log_or_payload():
    """Il source del task deve usare `scheduled_for` (non solo
    dichiararlo): se dichiarato e mai usato diventa un'ombra morta.
    Verifichiamo per pattern di grep nel source del task."""
    source = inspect.getsource(schedule_pipeline_task)

    # Il kwarg deve apparire ALMENO 2 volte: una nella signature,
    # una nell'uso (log, open_run, o payload).
    occurrences = source.count("scheduled_for")
    assert occurrences >= 2, (
        "scheduled_for dichiarato ma non usato nel body del task"
    )
