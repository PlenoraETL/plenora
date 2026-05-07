"""
Contract test — validazione input `search` su /api/logs.

Reperto: il parametro `search` veniva iniettato in `msg LIKE '%...%'`
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _user(role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="u1", username="alice",
        group_id="g1", role=role,
    )


@pytest.fixture
def client(monkeypatch):
    from app.core.logs import router as logs_router
    from app.core.auth.auth import require_not_esecutore

    # Stub DB per isolare al solo input-validation path
    monkeypatch.setattr(logs_router, "_fetchall", MagicMock(return_value=[]))
    monkeypatch.setattr(logs_router, "_fetchone", MagicMock(return_value={"c": 0}))

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(logs_router.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_search_within_limit_is_accepted(client):
    """100 char esatti: ok."""
    response = client.get("/api/logs", params={"search": "x" * 100})
    assert response.status_code == 200


def test_search_short_string_is_accepted(client):
    response = client.get("/api/logs", params={"search": "error 500"})
    assert response.status_code == 200


def test_search_over_limit_rejected_422(client):
    """101 char: FastAPI/Pydantic rifiuta con 422 Unprocessable Entity."""
    response = client.get("/api/logs", params={"search": "y" * 101})
    assert response.status_code == 422
    body = response.json()
    # Il detail di validazione cita la soglia
    assert any("100" in str(err) for err in body["detail"])


def test_search_dos_attempt_very_long_rejected(client):
    """10k char (DoS attempt): rigettato senza raggiungere il DB."""
    from app.core.logs import router as logs_router
    # Snapshot del count chiamate al DB PRIMA
    response = client.get("/api/logs", params={"search": "z" * 10000})
    assert response.status_code == 422
    # Il DB non deve essere stato toccato — questo e' il beneficio vero
    # del fix (il LIKE con 10k+ char sarebbe stato eseguito lo stesso
    # ma con un costo di pattern match lineare nella lunghezza).
    assert logs_router._fetchall.call_count == 0


def test_search_without_value_is_ok(client):
    """Nessun search → endpoint risponde normalmente."""
    response = client.get("/api/logs")
    assert response.status_code == 200


def test_search_empty_string_is_ok(client):
    """Stringa vuota: il router salta il filtro LIKE (if search:)"""
    response = client.get("/api/logs", params={"search": ""})
    assert response.status_code == 200
