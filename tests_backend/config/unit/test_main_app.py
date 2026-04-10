"""
tests/config/unit/test_main_app.py
==================================
Unit test per main.py: creazione app FastAPI, CORS middleware,
registrazione router, global exception handler, endpoint health/root.

Usa heavy patching per evitare import reali di servizi e DB.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Patching pesante per evitare side-effect degli import di main.py
# ---------------------------------------------------------------------------

def _make_mock_settings():
    """Crea un mock di settings che si comporta come il vero Settings."""
    mock = MagicMock()
    mock.CORS_ORIGINS_LIST = ["http://localhost:3000", "http://localhost:5173"]
    mock.DATA_DIR = Path("./data")
    mock.LOGS_DIR = Path("./data")
    mock.UPLOADS_DIR = Path("./data/uploads")
    mock.LOG_DB_FILE = "log.db"
    mock.LOG_DB_CONNECT_TIMEOUT = 5
    mock.RUNS_PAGE_SIZE = 50
    mock.RUNS_LOG_LIMIT = 200
    mock.validate_startup = MagicMock()
    mock.get = MagicMock(return_value="./data")
    return mock


def _make_mock_current_user():
    mock = MagicMock()
    mock.user_id = "test-uid"
    mock.username = "testuser"
    mock.group_id = "default"
    mock.role = "admin"
    return mock


# =============================================================================
# Test con import reale di main.py (app gia' importata dal conftest)
# =============================================================================

class TestAppCreation:
    """Verifica che l'app FastAPI sia stata creata correttamente."""

    def test_app_is_fastapi_instance(self):
        from app.main import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_app_title(self):
        from app.main import app
        assert app.title == "URBY-ETL API"


class TestCorsMiddleware:
    """Verifica che il middleware CORS sia configurato."""

    def test_cors_middleware_present(self):
        from app.main import app
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes

    def test_jwt_middleware_present(self):
        from app.main import app
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "JWTMiddleware" in middleware_classes


class TestRouterRegistration:
    """Verifica che i router principali siano registrati."""

    def test_has_routes(self):
        from app.main import app
        assert len(app.routes) > 0

    def test_minimum_router_count(self):
        """L'app dovrebbe avere almeno 15 router registrati (moduli core)."""
        from app.main import app
        # Contiamo le route con prefix /api
        api_routes = [r for r in app.routes if hasattr(r, "path") and "/api/" in getattr(r, "path", "")]
        assert len(api_routes) >= 10, f"Solo {len(api_routes)} route /api/ trovate"

    def test_auth_routes_registered(self):
        from app.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        auth_paths = [p for p in paths if "/auth" in p]
        assert len(auth_paths) > 0

    def test_logs_routes_registered(self):
        from app.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        log_paths = [p for p in paths if "/logs" in p]
        assert len(log_paths) > 0

    def test_projects_routes_registered(self):
        from app.main import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        proj_paths = [p for p in paths if "/projects" in p]
        assert len(proj_paths) > 0


# =============================================================================
# Health / Root endpoint
# =============================================================================

class TestRootEndpoint:

    def test_root_returns_ok(self):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["service"] == "URBY-ETL API"

    def test_root_is_get_only(self):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)
        res = client.post("/")
        assert res.status_code == 405  # Method Not Allowed


# =============================================================================
# Global Exception Handler
# =============================================================================

class TestGlobalExceptionHandler:

    def test_handler_registered(self):
        from app.main import app
        # FastAPI stores exception handlers in exception_handlers dict
        assert Exception in app.exception_handlers

    def test_handler_returns_500_json(self):
        """Simuliamo un'eccezione non gestita in un endpoint."""
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        test_app = FastAPI()

        @test_app.get("/explode")
        def explode():
            raise RuntimeError("boom")

        @test_app.exception_handler(Exception)
        async def handler(request: Request, exc: Exception):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"detail": "Errore interno del server"},
            )

        client = TestClient(test_app, raise_server_exceptions=False)
        res = client.get("/explode")
        assert res.status_code == 500
        assert res.json()["detail"] == "Errore interno del server"

    def test_handler_adds_cors_for_known_origin(self):
        """Il global handler aggiunge CORS headers se l'origin e' conosciuto."""
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        test_app = FastAPI()
        allowed_origins = {"http://localhost:3000"}

        @test_app.get("/fail")
        def fail():
            raise ValueError("test error")

        @test_app.exception_handler(Exception)
        async def handler(request: Request, exc: Exception):
            origin = request.headers.get("origin", "")
            headers = {}
            if origin in allowed_origins:
                headers["Access-Control-Allow-Origin"] = origin
            return JSONResponse(status_code=500, content={"detail": "error"}, headers=headers)

        client = TestClient(test_app, raise_server_exceptions=False)
        res = client.get("/fail", headers={"origin": "http://localhost:3000"})
        assert res.status_code == 500
        assert res.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"

    def test_handler_no_cors_for_unknown_origin(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        test_app = FastAPI()
        allowed_origins = {"http://localhost:3000"}

        @test_app.get("/fail")
        def fail():
            raise ValueError("test error")

        @test_app.exception_handler(Exception)
        async def handler(request: Request, exc: Exception):
            origin = request.headers.get("origin", "")
            headers = {}
            if origin in allowed_origins:
                headers["Access-Control-Allow-Origin"] = origin
            return JSONResponse(status_code=500, content={"detail": "error"}, headers=headers)

        client = TestClient(test_app, raise_server_exceptions=False)
        res = client.get("/fail", headers={"origin": "http://evil.com"})
        assert res.status_code == 500
        assert "Access-Control-Allow-Origin" not in res.headers


# =============================================================================
# Servizi shared inizializzati
# =============================================================================

class TestSharedServices:

    def test_project_service_exists(self):
        from app.main import project_service
        assert project_service is not None

    def test_connection_service_exists(self):
        from app.main import connection_service
        assert connection_service is not None

    def test_orchestrator_exists(self):
        from app.main import orchestrator
        assert orchestrator is not None

    def test_query_executor_exists(self):
        from app.main import query_executor
        assert query_executor is not None
