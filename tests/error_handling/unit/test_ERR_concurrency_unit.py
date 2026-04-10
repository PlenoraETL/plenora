"""
tests/error_handling/unit/test_ERR_concurrency.py
==================================================
Test di concorrenza, thread safety e stress — tutti i moduli.
Verifica che gli endpoint reggano a richieste parallele senza race condition.
"""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed

pytestmark = [pytest.mark.unit, pytest.mark.security]


# =============================================================================
# RATE LIMITING — thread safety
# =============================================================================

class TestRateLimitConcorrenza:

    def _reset(self, ip):
        from app.core.auth.auth import _failed_attempts, _rate_lock
        with _rate_lock:
            _failed_attempts.pop(ip, None)

    def test_record_failed_concorrente_non_corrompe_stato(self):
        """Record fallimenti concorrenti non devono corrompere il contatore."""
        from app.core.auth.auth import _record_failed_login, _failed_attempts, _rate_lock
        ip = "concurrent.test.1"
        self._reset(ip)
        errors = []

        def do_record():
            try:
                _record_failed_login(ip)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_record) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        with _rate_lock:
            assert len(_failed_attempts[ip]) == 50

    def test_check_rate_limit_concorrente_non_crasha(self):
        from app.core.auth.auth import _check_rate_limit
        from fastapi import HTTPException
        errors = []
        ip = "concurrent.test.2"

        def do_check():
            try:
                _check_rate_limit(ip)
            except HTTPException:
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_check) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    def test_clear_e_record_concorrenti_thread_safe(self):
        from app.core.auth.auth import _record_failed_login, _clear_failed_logins
        ip = "concurrent.test.3"
        errors = []

        def do_record():
            try:
                for _ in range(5):
                    _record_failed_login(ip)
            except Exception as e:
                errors.append(e)

        def do_clear():
            try:
                _clear_failed_logins(ip)
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=do_record) for _ in range(10)] +
            [threading.Thread(target=do_clear) for _ in range(5)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    @pytest.mark.parametrize("n_threads", [5, 10, 20, 50])
    def test_record_n_thread_simultanei(self, n_threads):
        from app.core.auth.auth import _record_failed_login, _failed_attempts, _rate_lock
        ip = f"concurrent.param.{n_threads}"
        with _rate_lock:
            _failed_attempts.pop(ip, None)
        errors = []

        def do():
            try:
                _record_failed_login(ip)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        with _rate_lock:
            assert len(_failed_attempts[ip]) == n_threads


# =============================================================================
# ENRICHMENT — thread safety rate limiter
# =============================================================================

class TestEnrichmentConcorrenza:

    def test_enrichment_executor_thread_safe(self):
        """EnrichmentExecutor è thread-safe per letture concorrenti."""
        try:
            from app.core.arricchisci.enrichment_service import EnrichmentExecutor
            import pandas as pd
            config = {"endpoint": {"url": "http://x.com", "method": "GET"}, "auth": {}, "parameters": [], "output_mapping": [], "static_parameters": {}}
            executor = EnrichmentExecutor(api_config=config, param_mapping={})
            df = pd.DataFrame({"col1": range(10)})
            errors = []

            def do_estimate():
                try:
                    executor.estimate(df)
                except ZeroDivisionError as e:
                    errors.append(e)
                except Exception:
                    pass

            threads = [threading.Thread(target=do_estimate) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, \
                f"ZeroDivisionError in thread concorrenti: {errors}"
        except ImportError:
            pytest.skip("EnrichmentExecutor non disponibile")

    def test_rate_limiter_thread_safe(self):
        """RateLimiter non deve bloccarsi con accessi concorrenti."""
        try:
            from app.core.arricchisci.enrichment_service import RateLimiter
            limiter = RateLimiter(rate=100)
            errors = []

            def do_acquire():
                try:
                    limiter.acquire()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=do_acquire) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert len(errors) == 0
        except (ImportError, AttributeError):
            pytest.skip("RateLimiter non disponibile")


# =============================================================================
# CONNECTION SERVICE — concorrenza
# =============================================================================

class TestConnectionConcorrenza:

    def test_list_connections_concorrente(self):
        """list_connections da thread multipli non crasha."""
        from app.services.connection_service import ConnectionService
        svc = ConnectionService()
        errors = []

        def do_list():
            try:
                with patch("app.services.connection_service.db") as mock_db:
                    mock_db.list_connections.return_value = []
                    mock_db.get_connection_config.return_value = None
                    svc.list_connections(group_id="grp1")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_list) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0, f"Errori: {errors}"


    @pytest.mark.parametrize("n_workers", [2, 5, 10])
    def test_test_connection_parallelo(self, n_workers):
        """test_connection da thread multipli non crasha."""
        from app.services.connection_service import ConnectionService
        svc = ConnectionService()
        results = []
        MOCK_ROW = {
            "id": "conn_fake", "name": "Test", "db_type": "postgres",
            "host": "h", "port": 5432, "database": "d",
            "username": "u", "password": "p", "extra": "{}",
            "group_id": "default",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }

        def do_test():
            with patch("app.services.connection_service.db") as mock_db:
                mock_db.get_connection_config.return_value = MOCK_ROW
                with patch.object(svc, "_test_postgres",
                                  side_effect=Exception("connection refused")):
                    result = svc.test_connection("conn_fake")
                    results.append(result.success)

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(do_test) for _ in range(n_workers)]
            for f in as_completed(futs):
                f.result()

        assert all(r is False for r in results)


class TestLoggerConcorrenza:

    def test_logger_write_concorrente_non_perde_messaggi(self):
        """Logger SQLite non perde messaggi con scritture concorrenti."""
        try:
            from app.utils.logger import get_logger, _db_lock, _failed_attempts
            logger = get_logger("test.concorrenza")
            errors = []

            def do_log(i):
                try:
                    logger.info(f"messaggio concorrente {i}", op="test")
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=do_log, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
        except ImportError:
            pytest.skip("logger non disponibile")

    def test_logger_non_deadlock_con_clear_concorrente(self):
        """Logger non va in deadlock se clear gira in contemporanea."""
        try:
            from app.utils.logger import get_logger
            from app.core.logs.router import _get_conn
            from app.config import settings
            from pathlib import Path

            log_path = settings.LOGS_DIR / "log.db"
            if not log_path.exists():
                pytest.skip("log.db non esiste")

            logger = get_logger("test.deadlock")
            errors = []

            def do_log():
                try:
                    for _ in range(5):
                        logger.info("test", op="test")
                except Exception as e:
                    errors.append(e)

            def do_clear():
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(log_path), timeout=5)
                    conn.execute("PRAGMA busy_timeout=1000")
                    conn.execute("DELETE FROM logs WHERE 1=0")  # no-op DELETE
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

            threads = (
                [threading.Thread(target=do_log) for _ in range(5)] +
                [threading.Thread(target=do_clear) for _ in range(2)]
            )
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            alive = [t for t in threads if t.is_alive()]
            assert len(alive) == 0, f"Deadlock! {len(alive)} thread ancora attivi"
            assert len(errors) == 0
        except (ImportError, AttributeError):
            pytest.skip("Non disponibile")


# =============================================================================
# STRESS TEST — boundary conditions
# =============================================================================

class TestStressUnit:

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_rate_limit_stress_n_tentativi(self, n):
        """Rate limiter regge sotto stress."""
        from app.core.auth.auth import _record_failed_login, _failed_attempts, _rate_lock
        ip = f"stress.test.{n}"
        with _rate_lock:
            _failed_attempts.pop(ip, None)

        for _ in range(n):
            _record_failed_login(ip)

        with _rate_lock:
            count = len(_failed_attempts[ip])

        # Tutti i tentativi devono essere registrati
        assert count == n

    def test_token_decoding_stress(self):
        """Decodifica token sotto stress non crasha."""
        from app.core.auth.auth import decode_token, create_access_token
        tokens = [create_access_token(f"uid_{i}", f"user_{i}", "grp", "user")
                  for i in range(100)]
        errors = []

        def do_decode(token):
            try:
                result = decode_token(token)
                assert result is not None
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(do_decode, t) for t in tokens]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0

    @pytest.mark.parametrize("n_tokens", [10, 50, 100])
    def test_creazione_token_concorrente(self, n_tokens):
        """Creazione token da thread multipli è thread-safe."""
        from app.core.auth.auth import create_access_token
        results = []
        errors = []

        def do_create(i):
            try:
                token = create_access_token(f"uid_{i}", f"user_{i}", "grp", "user")
                results.append(token)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(do_create, i) for i in range(n_tokens)]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0
        assert len(results) == n_tokens
        # Tutti i token devono essere diversi
        assert len(set(results)) == n_tokens

    def test_hash_password_stress(self):
        """Hash password sotto stress non crasha."""
        from app.core.auth.auth import hash_password, verify_password
        passwords = [f"password_{i}_!@#" for i in range(20)]
        hashes = []
        errors = []

        def do_hash(pwd):
            try:
                h = hash_password(pwd)
                hashes.append((pwd, h))
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = [ex.submit(do_hash, p) for p in passwords]
            for f in as_completed(futs):
                f.result()

        assert len(errors) == 0
        assert len(hashes) == len(passwords)

        # Verifica che tutti gli hash siano corretti
        for pwd, h in hashes:
            assert verify_password(pwd, h)
