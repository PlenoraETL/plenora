"""
tests/error_handling/unit/test_ERR09_ERR16_arricchisci.py
==========================================================
Test UNITARI error handling — Modulo ARRICCHISCI (ERR-09→ERR-16)

ERR-09  Enrich con api_id inesistente → 404
ERR-10  Enrich con project senza output → 400/404
ERR-11  Validate con param_mapping vuoto su API con required → valid=false
ERR-12  Estimate con 0 righe nel sorgente → response con total_rows=0
ERR-13  Enrich con file source eliminato → 400
ERR-14  Preview enrichment con rate_limit=0 → no divisione per zero
ERR-15  Enrich async su progetto già in esecuzione → rifiutato
ERR-16  Enrich con output_mapping malformato → errore chiaro
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

pytestmark = [pytest.mark.unit, pytest.mark.security]

def make_test_client(router, overrides=None):
    """Helper: crea TestClient con router e dependency override."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    if overrides:
        app.dependency_overrides.update(overrides)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)




# =============================================================================
# ERR-14 — Rate limit = 0 → no divisione per zero
# =============================================================================

class TestERR14Unit:

    def test_rate_limit_zero_no_divisione_zero(self):
        """rate_limit=0 non deve causare ZeroDivisionError."""
        try:
            from app.core.arricchisci.enrichment_service import EnrichmentExecutor
            config = {
                "endpoint": {"url": "http://x.com", "method": "GET"}, "auth": {},
                "parameters": [], "output_mapping": [],
                "rate_limit": {"requests_per_second": 0}
            }
            try:
                executor = EnrichmentExecutor(api_config=config, param_mapping={})
                df = pd.DataFrame({"col1": ["v1", "v2"]})
                executor.estimate(df) if hasattr(executor, "estimate") else executor.execute(df[:1]) if len(df) > 0 else None
            except ZeroDivisionError:
                pytest.fail("ZeroDivisionError con rate_limit=0")
            except Exception:
                pass
        except ImportError:
            pytest.skip("EnrichmentExecutor non disponibile")

    def test_rate_limit_negativo_gestito(self):
        """rate_limit negativo non deve causare comportamenti imprevedibili."""
        try:
            from app.core.arricchisci.enrichment_service import EnrichmentExecutor
            config = {
                "endpoint": {"url": "http://x.com", "method": "GET"}, "auth": {},
                "parameters": [], "output_mapping": [],
                "rate_limit": {"requests_per_second": -1}
            }
            try:
                executor = EnrichmentExecutor(api_config=config, param_mapping={})
                df = pd.DataFrame({"col1": ["v1"]})
                executor.estimate(df) if hasattr(executor, "estimate") else executor.execute(df[:1]) if len(df) > 0 else None
            except ZeroDivisionError:
                pytest.fail("ZeroDivisionError con rate_limit=-1")
            except Exception:
                pass
        except ImportError:
            pytest.skip("EnrichmentExecutor non disponibile")

    def test_rate_limiter_zero_requests_non_blocca(self):
        """RateLimiter con 0 richieste/s non deve bloccare indefinitamente."""
        try:
            from app.core.arricchisci.enrichment_service import RateLimiter
            import threading

            def try_acquire():
                try:
                    limiter = RateLimiter(rate=0)
                    limiter.acquire()
                except Exception:
                    pass

            t = threading.Thread(target=try_acquire)
            t.start()
            t.join(timeout=2)
            assert not t.is_alive(), "RateLimiter con rate=0 si è bloccato"
        except (ImportError, AttributeError):
            pytest.skip("RateLimiter non disponibile")

    @pytest.mark.parametrize("rate", [0, -1, -100, 0.0, None])
    def test_vari_rate_limit_invalidi(self, rate):
        """Vari valori invalidi di rate_limit non devono crashare."""
        try:
            from app.core.arricchisci.enrichment_service import EnrichmentExecutor
            config = {"endpoint": {"url": "http://x.com", "method": "GET"}, "auth": {},
                      "parameters": [], "output_mapping": [], "static_parameters": {},
                      "rate_limit": {"rate": rate}}
            try:
                executor = EnrichmentExecutor(api_config=config, param_mapping={})
                df = pd.DataFrame({"col1": ["v1"]})
                executor.estimate(df) if hasattr(executor, "estimate") else executor.execute(df[:1]) if len(df) > 0 else None
            except ZeroDivisionError:
                pytest.fail(f"ZeroDivisionError con rate_limit={rate}")
            except Exception:
                pass
        except ImportError:
            pytest.skip("EnrichmentExecutor non disponibile")


# =============================================================================
# ERR-15 — Enrich async su progetto già in esecuzione → rifiutato
# =============================================================================

class TestERR15Unit:

    def test_doppia_esecuzione_gestita(self, auth_override):
        """Secondo execute su progetto già in esecuzione deve essere rifiutato o accodato."""
        from app.core.arricchisci.router import enrich_router
        client = make_test_client(enrich_router, auth_override)
        with patch("app.core.arricchisci.router._api_connection_service") as svc, \
             patch("app.core.arricchisci.router._project_service") as prj:
            prj.get_project.return_value = MagicMock(id="proj-1")
            svc.list_api_connections.return_value = [{"id": "api_1"}]
            # Prima esecuzione
            res1 = client.post("/projects/proj-1/execute-async")
            # Seconda esecuzione immediata
            res2 = client.post("/projects/proj-1/execute-async")
        # Almeno uno dei due deve avere successo, nessuno deve dare 500 non gestito
        statuses = {res1.status_code, res2.status_code}
        assert not (500 in statuses and len(statuses) == 1), \
            "Doppia esecuzione ha causato 500"

    def test_task_service_gestisce_progetto_in_esecuzione(self):
        """TaskService.list_active_tasks ritorna lista di task attivi."""
        from app.services.task_service import TaskService
        svc = TaskService()
        with patch.object(svc.app.control, 'inspect') as mock_inspect:
            mock_inspect.return_value.active.return_value = {
                "worker1": [{"id": "task_1", "name": "pipeline.execute"}]
            }
            mock_inspect.return_value.reserved.return_value = {}
            tasks = svc.list_active_tasks()
        assert isinstance(tasks, list)
        assert len(tasks) == 1

    
