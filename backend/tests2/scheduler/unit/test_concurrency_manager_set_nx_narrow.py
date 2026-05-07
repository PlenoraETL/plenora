"""[phase-2-err][batch7] 7.d — narrow catch su concurrency_manager._set_nx.

Audit Batch 7 §3.6 [MEDIA] — pre-fix `_set_nx` aveva `except Exception:
return False` con commento "chiave gia' esistente": QUALUNQUE errore DB
(pool exhausted, connection down, bug logici) veniva mascherato come "lock
gia' preso". Il caller `try_acquire` poi incrementava `queue_key` e
ritornava "queued" → task accodato su DB non funzionante → stato corrotto.

Post-7.d:
  - Catch narrow `(psycopg2.errors.UniqueViolation, psycopg2.IntegrityError)`
    preserva la semantica control-flow UNIQUE constraint.
  - Altri errori propagano: il caller `try_acquire` li propaga a
    `ExecutionManager.execute` outer boundary che traduce in status
    failure esplicito.
  - Nessuna nuova exception class: riuso tipi psycopg2 standard.
  - Nessun logger warning/error nel path UniqueViolation (control-flow
    tipizzato, non diagnostica).
"""

from __future__ import annotations

from unittest.mock import patch

import psycopg2
import psycopg2.errors
import pytest

from app.core.scheduler.concurrency_manager import ConcurrencyManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _make_manager() -> ConcurrencyManager:
    """ConcurrencyManager bypass __init__ per evitare bootstrap `_ensure_table`
    (che chiama db.execute CREATE TABLE reale). Il test si concentra su
    `_set_nx` in isolamento."""
    mgr = ConcurrencyManager.__new__(ConcurrencyManager)
    mgr._available = True
    return mgr


# HAPPY PATH


def test_set_nx_happy_path_returns_true_on_success():
    """db.execute ok → _set_nx ritorna True, nessuna eccezione."""
    mgr = _make_manager()
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute"
    ) as mock_exec, patch.object(mgr, "_get", return_value=None):
        result = mgr._set_nx("key-new", "running", ttl_seconds=60)

    assert result is True
    mock_exec.assert_called_once()


# SEMANTICA UNIQUE CONSTRAINT — return False


def test_set_nx_unique_violation_returns_false():
    """db.execute solleva psycopg2.errors.UniqueViolation → return False
    (semantica legittima 'chiave gia' esistente')."""
    mgr = _make_manager()
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute",
        side_effect=psycopg2.errors.UniqueViolation(
            "duplicate key value violates unique constraint"
        ),
    ), patch.object(mgr, "_get", return_value=None):
        result = mgr._set_nx("key-existing", "running")

    assert result is False


def test_set_nx_integrity_error_returns_false():
    """db.execute solleva psycopg2.IntegrityError (superclass di
    UniqueViolation) → return False. Esplicitato nel narrow per
    leggibilita' del contratto."""
    mgr = _make_manager()
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute",
        side_effect=psycopg2.IntegrityError("generic integrity constraint"),
    ), patch.object(mgr, "_get", return_value=None):
        result = mgr._set_nx("key-integrity", "running")

    assert result is False


# FAIL-CLOSED — errori non-UNIQUE propagano


def test_set_nx_operational_error_propagates():
    """db.execute solleva psycopg2.OperationalError (pool exhausted / conn
    down) → **propaga** invece di ritornare False silent. Caller
    `try_acquire` vedra' il vero errore invece di misleading 'queued'."""
    mgr = _make_manager()
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute",
        side_effect=psycopg2.OperationalError("pool exhausted"),
    ), patch.object(mgr, "_get", return_value=None):
        with pytest.raises(psycopg2.OperationalError, match="pool exhausted"):
            mgr._set_nx("key-pg-down", "running")


def test_set_nx_generic_runtime_error_propagates():
    """db.execute solleva RuntimeError generico (bug non-PG, es. encoding
    error, input malformato) → **propaga**. Il fix narrow impedisce che
    bug logici vengano mascherati come 'chiave gia' esistente'."""
    mgr = _make_manager()
    with patch(
        "app.core.scheduler.concurrency_manager.db.execute",
        side_effect=RuntimeError("bug logico non-PG"),
    ), patch.object(mgr, "_get", return_value=None):
        with pytest.raises(RuntimeError, match="bug logico non-PG"):
            mgr._set_nx("key-bug", "running")
