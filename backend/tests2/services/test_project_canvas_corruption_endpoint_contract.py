"""
Endpoint contract test — /projects/{id} traduce CanvasCorruptedError

in HTTP 500 esplicito via global exception handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixture: app minimale con router + handler globale registrato


@pytest.fixture
def app_with_handler(monkeypatch):
    """FastAPI con global exception handler per CanvasCorruptedError
    + router projects parzialmente mockato."""
    from app.core.projects import router as projects_router_mod
    from app.core.projects.exceptions import CanvasCorruptedError
    from app.core.auth import auth as auth_mod

    # Bypass auth: require_not_esecutore → sempre autorizza un user fittizio
    def _fake_user():
        return auth_mod.CurrentUser(
            user_id="u1",
            username="tester",
            group_id="g1",
            role="admin",
        )

    # Mock del project_service globale usato dal router
    fake_svc = MagicMock()
    fake_metadata = MagicMock()
    fake_metadata.model_dump.return_value = {
        "id": "p-corrupt",
        "name": "Corrupt Project",
        "group_id": "g1",
        "project_type": "manipola",
    }
    fake_metadata.group_id = "g1"
    fake_svc.get_project_metadata.return_value = fake_metadata
    fake_svc.get_project_columns_metadata.return_value = None

    # Il core del test: get_project_canvas solleva CanvasCorruptedError
    fake_svc.get_project_canvas.side_effect = CanvasCorruptedError(
        project_id="p-corrupt",
        reason="JSON malformato (Expecting value at line 1)",
    )

    # Patch del service globale nel modulo router
    monkeypatch.setattr(projects_router_mod, "_project_service", fake_svc)

    # Bypass assert_group_access → no-op
    monkeypatch.setattr(projects_router_mod, "assert_group_access",
                        lambda *a, **kw: None)

    # Setup FastAPI con handler globale replicato da main.py
    app = FastAPI()

    @app.exception_handler(CanvasCorruptedError)
    async def canvas_corrupted_handler(request: Request, exc: CanvasCorruptedError):
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    f"Canvas del progetto '{exc.project_id}' corrotto "
                    f"({exc.reason}). Contattare l'amministratore per il "
                    f"ripristino da backup."
                ),
            },
        )

    # Override dell'auth dependency per bypassare JWT nei test
    from app.core.auth.auth import require_not_esecutore
    app.dependency_overrides[require_not_esecutore] = _fake_user

    app.include_router(projects_router_mod.router, prefix="/api")

    with TestClient(app) as tc:
        yield tc, fake_svc


# Test: endpoint ritorna 500 strutturato su canvas corrotto


def test_get_project_with_corrupted_canvas_returns_500_explicit(app_with_handler):
    tc, fake_svc = app_with_handler
    r = tc.get("/api/projects/p-corrupt")

    assert r.status_code == 500, (
        f"Atteso 500 per canvas corrotto, got {r.status_code}: {r.text}"
    )

    body = r.json()
    # Messaggio in italiano, esplicito sulla natura del problema
    assert "corrotto" in body["detail"].lower()
    assert "p-corrupt" in body["detail"]
    assert "backup" in body["detail"].lower()

    # Il servizio e' stato chiamato (non un falso positivo dal mock setup)
    fake_svc.get_project_canvas.assert_called_once_with("p-corrupt")


def test_get_project_response_does_NOT_contain_empty_canvas_fallback(
    app_with_handler,
):
    """Regressione diretta: la risposta non deve MAI contenere
    `canvas: {"nodes":[],"edges":[]}` quando il canvas sottostante e'
    corrotto. Pre-fix, rispondeva 200 con canvas vuoto → utente sovrascrive.
    """
    tc, _ = app_with_handler
    r = tc.get("/api/projects/p-corrupt")

    assert r.status_code != 200, (
        "Endpoint ha risposto 200 su canvas corrotto — regressione al "
        "silent-fallback pre-fix"
    )
    body = r.json()
    # Il body non deve contenere un `canvas` vuoto ingannevole
    assert "canvas" not in body, (
        "Response di errore contiene campo `canvas`: rischia di confondere "
        "la UI che potrebbe salvarlo sovrascrivendo i dati corrotti"
    )
