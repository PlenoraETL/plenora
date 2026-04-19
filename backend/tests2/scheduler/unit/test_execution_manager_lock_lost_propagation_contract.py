"""Propagazione del flag lock_lost al result dict di execute (C3 round 3).

Contract: se durante l'esecuzione il thread heartbeat rileva che il
lock e' stato perso e non riacquisibile (stato['lock_lost']=True),
execute() deve:
1. Includere `lock_lost: True` nel dict ritornato.
2. Propagarlo nei log entry (warning su possibile concorrenza).

Senza questo, la rilevazione del Fix A del round 2 resta silenziosa:
visibile solo negli ERROR log del heartbeat thread, invisibile nel
risultato JSON dello schedule run.
"""

import inspect

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_execute_source_reads_lock_lost_flag_from_heartbeat_state():
    """Il source di execute() deve leggere heartbeat_state['lock_lost']
    nel finally o dove assembla il result."""
    source = inspect.getsource(ExecutionManager.execute)
    assert 'heartbeat_state["lock_lost"]' in source or \
           "heartbeat_state['lock_lost']" in source or \
           'heartbeat_state.get("lock_lost"' in source, (
        "execute() NON legge heartbeat_state['lock_lost']. "
        "La detection del thread heartbeat resta silente nel result."
    )


def test_execute_result_includes_lock_lost_key_in_result_schema():
    """Il source di execute() deve includere 'lock_lost' come chiave
    nel dict di response, almeno nei rami success/failure/partial."""
    source = inspect.getsource(ExecutionManager.execute)
    # Cerca il pattern di assegnazione nel result dict
    assert '"lock_lost"' in source, (
        "Il result dict di execute() non include la chiave 'lock_lost'"
    )


def test_execute_logs_warning_when_lock_lost_true():
    """Il source deve emettere un log WARNING/ERROR quando lock_lost
    diventa True — altrimenti il flag nel result e' senza audit."""
    source = inspect.getsource(ExecutionManager.execute)
    # Cerca pattern di logger.warning o logger.error con lock_lost
    assert ("logger.warning" in source or "logger.error" in source), (
        "execute() non logga nulla sul lock_lost"
    )
    # Scan piu' mirato: c'e' almeno una riga che logga con LOCK_LOST / lock_lost
    assert any(
        "lock_lost" in line.lower() and ("logger" in line or "LOCK" in line)
        for line in source.splitlines()
    ), "Nessun logger.xxx menziona lock_lost nel body di execute()"
