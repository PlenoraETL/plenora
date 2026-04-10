"""
tests/scheduler/unit/test_concurrency_manager.py
=================================================
Unit test su ConcurrencyManager con DB mockato.

NON richiede backend o DB reale.

Copre:
  - try_acquire: acquired (primo), queued (secondo), skipped (coda piena)
  - release: lock rimosso, coda decrementata
  - get_status: locked/unlocked, queue_size
  - is_running: True/False
  - force_release: rimuove lock e coda
  - _decr: decremento, rimozione a zero
"""

import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit

from app.core.scheduler.concurrency_manager import ConcurrencyManager, LOCK_TTL_SECONDS


# ─── Helper ───────────────────────────────────────────────────────────────────

def make_manager():
    """ConcurrencyManager con _ensure_table mockato."""
    with patch("app.core.scheduler.concurrency_manager.db"):
        mgr = ConcurrencyManager.__new__(ConcurrencyManager)
        mgr._available = True
    return mgr


# =============================================================================
# try_acquire
# =============================================================================

class TestTryAcquire:

    def test_primo_acquire_acquired(self):
        """Lock libero → acquired."""
        mgr = make_manager()
        with patch.object(mgr, "_set_nx", return_value=True):
            result = mgr.try_acquire("sched-1", max_queue_size=3)
        assert result == "acquired"

    def test_lock_occupato_coda_disponibile_queued(self):
        """Lock occupato, coda < max → queued."""
        mgr = make_manager()
        with patch.object(mgr, "_set_nx", return_value=False), \
             patch.object(mgr, "_get", return_value="1"), \
             patch.object(mgr, "_incr", return_value=2):
            result = mgr.try_acquire("sched-1", max_queue_size=3)
        assert result == "queued"

    def test_lock_occupato_coda_piena_skipped(self):
        """Lock occupato, coda >= max → skipped."""
        mgr = make_manager()
        with patch.object(mgr, "_set_nx", return_value=False), \
             patch.object(mgr, "_get", return_value="3"):
            result = mgr.try_acquire("sched-1", max_queue_size=3)
        assert result == "skipped"

    def test_primo_acquire_coda_vuota_acquired(self):
        mgr = make_manager()
        with patch.object(mgr, "_set_nx", return_value=True):
            result = mgr.try_acquire("sched-1")
        assert result == "acquired"

    def test_coda_nulla_trattata_come_zero(self):
        """_get ritorna None per coda → trattata come 0."""
        mgr = make_manager()
        with patch.object(mgr, "_set_nx", return_value=False), \
             patch.object(mgr, "_get", return_value=None), \
             patch.object(mgr, "_incr", return_value=1):
            result = mgr.try_acquire("sched-1", max_queue_size=3)
        assert result == "queued"


# =============================================================================
# release
# =============================================================================

class TestRelease:

    def test_release_chiama_delete_e_decr(self):
        mgr = make_manager()
        with patch.object(mgr, "_delete") as mock_del, \
             patch.object(mgr, "_decr", return_value=0) as mock_decr:
            result = mgr.release("sched-1")
        mock_del.assert_called_once_with("scheduler:lock:sched-1")
        mock_decr.assert_called_once_with("scheduler:queue:sched-1")
        assert result is True

    def test_release_restituisce_true(self):
        mgr = make_manager()
        with patch.object(mgr, "_delete"), \
             patch.object(mgr, "_decr", return_value=0):
            assert mgr.release("sched-1") is True


# =============================================================================
# get_status
# =============================================================================

class TestGetStatus:

    def test_non_locked_queue_zero(self):
        mgr = make_manager()
        with patch.object(mgr, "_get", return_value=None):
            status = mgr.get_status("sched-1")
        assert status["locked"] is False
        assert status["queue_size"] == 0

    def test_locked_queue_due(self):
        mgr = make_manager()
        call_count = [0]
        def side_get(key):
            call_count[0] += 1
            if "lock" in key:
                return "running"
            return "2"
        with patch.object(mgr, "_get", side_effect=side_get):
            status = mgr.get_status("sched-1")
        assert status["locked"] is True
        assert status["queue_size"] == 2


# =============================================================================
# is_running
# =============================================================================

class TestIsRunning:

    def test_running_true(self):
        mgr = make_manager()
        with patch.object(mgr, "_get", return_value="running"):
            assert mgr.is_running("sched-1") is True

    def test_non_running_false(self):
        mgr = make_manager()
        with patch.object(mgr, "_get", return_value=None):
            assert mgr.is_running("sched-1") is False


# =============================================================================
# force_release
# =============================================================================

class TestForceRelease:

    def test_cancella_lock_e_coda(self):
        mgr = make_manager()
        deleted = []
        with patch.object(mgr, "_delete", side_effect=lambda k: deleted.append(k)):
            mgr.force_release("sched-1")
        assert "scheduler:lock:sched-1" in deleted
        assert "scheduler:queue:sched-1" in deleted


# =============================================================================
# _decr
# =============================================================================

class TestDecr:

    def test_decrementa_da_2_a_1(self):
        mgr = make_manager()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {"value": "2"}
            result = mgr._decr("some_key")
        assert result == 1

    def test_decrementa_da_1_a_0_rimuove(self):
        """Mutation target: se arriva a 0 → DELETE invece di UPDATE."""
        mgr = make_manager()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = {"value": "1"}
            result = mgr._decr("some_key")
        assert result == 0
        mock_db.execute.assert_called()

    def test_key_inesistente_restituisce_zero(self):
        mgr = make_manager()
        with patch("app.core.scheduler.concurrency_manager.db") as mock_db:
            mock_db.fetchone.return_value = None
            result = mgr._decr("some_key")
        assert result == 0


# =============================================================================
# available property
# =============================================================================

class TestAvailable:

    def test_disponibile_default_true(self):
        mgr = make_manager()
        assert mgr.available is True

    def test_indisponibile_false(self):
        mgr = make_manager()
        mgr._available = False
        assert mgr.available is False
