"""
tests/scheduler/unit/test_block_D_concurrency.py
================================================
Unit test per il Blocco D: 4 fix scheduler/concurrency.

Copre:
  D-FIX-1: ConcurrencyManager._incr/_decr atomici (race con threading)
  D-FIX-2: try_acquire queue overflow check
  D-FIX-3: LOCK_TTL_SECONDS derivato da CELERY_SCHEDULE_HARD_LIMIT
  D-FIX-4: DBConnectionCleanupMiddleware
"""

import asyncio
import threading
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# D-FIX-1 — atomic _incr/_decr
# ============================================================================

class TestAtomicIncrDecr:
    """Test contro race condition usando real SQLite + threading."""

    @pytest.fixture
    def cm(self, tmp_path, monkeypatch):
        """ConcurrencyManager con DB SQLite isolato in tmp."""
        # Reindirizza il DB_PATH di config_db_service a una temp DB
        import app.db.config_db_service as cfg
        from app.core.scheduler.concurrency_manager import ConcurrencyManager

        test_db = tmp_path / "test_concurrency.db"
        monkeypatch.setattr(cfg, "DB_PATH", test_db)
        # Reset connection cache (thread-local)
        if hasattr(cfg._local, "conn"):
            try:
                cfg._local.conn.close()
            except Exception:
                pass
            cfg._local.conn = None

        manager = ConcurrencyManager()
        yield manager

        # Cleanup
        if hasattr(cfg._local, "conn") and cfg._local.conn:
            try:
                cfg._local.conn.close()
            except Exception:
                pass
            cfg._local.conn = None

    def test_incr_creates_key(self, cm):
        assert cm._incr("test:counter") == 1
        assert cm._incr("test:counter") == 2
        assert cm._incr("test:counter") == 3

    def test_decr_to_zero_deletes(self, cm):
        cm._incr("test:counter")
        cm._incr("test:counter")
        # Counter = 2 → decr → 1
        assert cm._decr("test:counter") == 1
        # Counter = 1 → decr → 0 (e DELETE)
        assert cm._decr("test:counter") == 0
        # Decr su key non esistente → 0
        assert cm._decr("test:counter") == 0

    def test_concurrent_incr_no_lost_updates(self, cm):
        """N thread incrementano in parallelo: il counter finale deve essere ESATTAMENTE N."""
        N_THREADS = 20
        ITERATIONS_PER_THREAD = 10
        EXPECTED_TOTAL = N_THREADS * ITERATIONS_PER_THREAD

        errors = []

        def worker():
            try:
                for _ in range(ITERATIONS_PER_THREAD):
                    cm._incr("test:race")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker errors: {errors}"
        # Verifica via fetchone diretto
        import app.db.db_service as db
        row = db.fetchone("SELECT value FROM scheduler_locks WHERE key = ?", ("test:race",))
        actual = int(row["value"])
        assert actual == EXPECTED_TOTAL, (
            f"Lost updates: expected {EXPECTED_TOTAL}, got {actual}. "
            f"Race condition NOT fixed."
        )

    def test_concurrent_incr_decr_balanced(self, cm):
        """N thread fanno N incr e M decr: il counter finale = N - M (no lost updates)."""
        # Pre-popoliamo a 100
        for _ in range(100):
            cm._incr("test:balance")

        def incr_worker():
            for _ in range(50):
                cm._incr("test:balance")

        def decr_worker():
            for _ in range(30):
                cm._decr("test:balance")

        threads = (
            [threading.Thread(target=incr_worker) for _ in range(5)] +
            [threading.Thread(target=decr_worker) for _ in range(5)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # 100 + 5*50 - 5*30 = 100 + 250 - 150 = 200
        import app.db.db_service as db
        row = db.fetchone("SELECT value FROM scheduler_locks WHERE key = ?", ("test:balance",))
        actual = int(row["value"])
        assert actual == 200, f"Expected 200, got {actual}"


# ============================================================================
# D-FIX-2 — try_acquire queue overflow check
# ============================================================================

class TestTryAcquireQueueOverflow:

    @pytest.fixture
    def cm(self, tmp_path, monkeypatch):
        import app.db.config_db_service as cfg
        from app.core.scheduler.concurrency_manager import ConcurrencyManager
        test_db = tmp_path / "test_queue.db"
        monkeypatch.setattr(cfg, "DB_PATH", test_db)
        if hasattr(cfg._local, "conn"):
            try:
                cfg._local.conn.close()
            except Exception:
                pass
            cfg._local.conn = None
        yield ConcurrencyManager()
        if hasattr(cfg._local, "conn") and cfg._local.conn:
            try:
                cfg._local.conn.close()
            except Exception:
                pass
            cfg._local.conn = None

    def test_first_acquire(self, cm):
        assert cm.try_acquire("sched-1", max_queue_size=3) == "acquired"

    def test_queue_then_skip(self, cm):
        # Primo: acquisisce
        assert cm.try_acquire("sched-2", max_queue_size=2) == "acquired"
        # Secondo: in coda (1)
        assert cm.try_acquire("sched-2", max_queue_size=2) == "queued"
        # Terzo: in coda (2)
        assert cm.try_acquire("sched-2", max_queue_size=2) == "queued"
        # Quarto: skip (sopra limite)
        assert cm.try_acquire("sched-2", max_queue_size=2) == "skipped"

    def test_concurrent_acquire_no_overflow(self, cm):
        """N thread che provano ad acquisire: 1 acquired + max_queue_size queued + resto skipped."""
        MAX_QUEUE = 3
        N_THREADS = 20
        results = []
        results_lock = threading.Lock()

        def worker():
            r = cm.try_acquire("sched-stress", max_queue_size=MAX_QUEUE)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        acquired = results.count("acquired")
        queued = results.count("queued")
        skipped = results.count("skipped")

        assert acquired == 1, f"Expected exactly 1 acquired, got {acquired}"
        # La coda non deve MAI superare max_queue_size
        assert queued <= MAX_QUEUE, (
            f"Queue overflow: {queued} queued, max was {MAX_QUEUE}. "
            f"Race condition NOT fixed."
        )
        assert acquired + queued + skipped == N_THREADS

    def test_release_decrements_queue(self, cm):
        cm.try_acquire("sched-3", max_queue_size=3)
        cm.try_acquire("sched-3", max_queue_size=3)  # queued: 1
        cm.try_acquire("sched-3", max_queue_size=3)  # queued: 2
        cm.release("sched-3")
        # Dopo release, lock scompare e queue va a 1
        status = cm.get_status("sched-3")
        assert status["locked"] is False
        assert status["queue_size"] == 1


# ============================================================================
# D-FIX-3 — LOCK_TTL_SECONDS bound
# ============================================================================

class TestLockTTLBound:

    def test_lock_ttl_is_derived_from_celery_hard_limit(self):
        from app.core.scheduler.concurrency_manager import LOCK_TTL_SECONDS
        from app.config import settings
        # Deve essere 1.5x del hard limit Celery
        expected = int(settings.CELERY_SCHEDULE_HARD_LIMIT * 1.5)
        assert LOCK_TTL_SECONDS == expected

    def test_lock_ttl_is_less_than_4_hours(self):
        """Era 14400 (4h), ora deve essere meno (limita blast radius dei crash)."""
        from app.core.scheduler.concurrency_manager import LOCK_TTL_SECONDS
        assert LOCK_TTL_SECONDS < 4 * 60 * 60

    def test_lock_ttl_covers_celery_hard_limit(self):
        """Deve essere abbastanza grande da coprire una pipeline che dura il
        massimo consentito da Celery (no false expiration)."""
        from app.core.scheduler.concurrency_manager import LOCK_TTL_SECONDS
        from app.config import settings
        assert LOCK_TTL_SECONDS >= settings.CELERY_SCHEDULE_HARD_LIMIT


# ============================================================================
# D-FIX-4 — DBConnectionCleanupMiddleware
# ============================================================================

class TestDBCleanupMiddleware:

    def _make_request(self):
        request = MagicMock()
        request.url.path = "/api/test"
        return request

    def test_cleanup_called_after_success(self):
        from app.db.cleanup_middleware import DBConnectionCleanupMiddleware
        from unittest.mock import AsyncMock

        mw = DBConnectionCleanupMiddleware(app=MagicMock())
        request = self._make_request()
        response_mock = MagicMock()
        call_next = AsyncMock(return_value=response_mock)

        with patch("app.db.config_db_service.close_connection") as mock_cfg, \
             patch("app.core.intermediate.db.close_connection") as mock_idb:
            result = asyncio.run(mw.dispatch(request, call_next))

        assert result is response_mock
        mock_cfg.assert_called_once()
        mock_idb.assert_called_once()

    def test_cleanup_called_even_on_exception(self):
        from app.db.cleanup_middleware import DBConnectionCleanupMiddleware
        from unittest.mock import AsyncMock

        mw = DBConnectionCleanupMiddleware(app=MagicMock())
        request = self._make_request()

        async def _raise(_req):
            raise RuntimeError("boom")

        with patch("app.db.config_db_service.close_connection") as mock_cfg, \
             patch("app.core.intermediate.db.close_connection") as mock_idb:
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(mw.dispatch(request, _raise))

        # Cleanup deve essere chiamato anche se call_next solleva
        mock_cfg.assert_called_once()
        mock_idb.assert_called_once()

    def test_cleanup_swallows_internal_errors(self):
        """Errori interni in close_connection non devono propagare al chiamante."""
        from app.db.cleanup_middleware import DBConnectionCleanupMiddleware
        from unittest.mock import AsyncMock

        mw = DBConnectionCleanupMiddleware(app=MagicMock())
        request = self._make_request()
        call_next = AsyncMock(return_value=MagicMock())

        with patch("app.db.config_db_service.close_connection",
                   side_effect=Exception("close error")), \
             patch("app.core.intermediate.db.close_connection",
                   side_effect=Exception("close error 2")):
            # Non deve sollevare
            asyncio.run(mw.dispatch(request, call_next))

    def test_close_connection_idempotent_after_real_open(self, tmp_path, monkeypatch):
        """Smoke test: dopo aver effettivamente aperto una conn config_db, il
        cleanup la rilascia correttamente."""
        import app.db.config_db_service as cfg
        test_db = tmp_path / "test_cleanup.db"
        monkeypatch.setattr(cfg, "DB_PATH", test_db)

        # Apri esplicitamente una connessione
        if hasattr(cfg._local, "conn"):
            cfg._local.conn = None
        conn = cfg.get_connection()
        assert conn is not None
        assert cfg._local.conn is conn

        # Cleanup esplicito
        cfg.close_connection()
        assert cfg._local.conn is None

        # Idempotente: chiamare di nuovo è no-op
        cfg.close_connection()
        assert cfg._local.conn is None
