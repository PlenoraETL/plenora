"""
Integration contract test: sanitizer applicato sui 2 punti HIGH RISK

di ``eseguibili/router.py`` (Batch Fix 6.2).
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import get_current_user
from app.core.eseguibili import router as eseguibili_router_module
from app.core.eseguibili.router import router as eseguibili_router


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.eseguibili,
    pytest.mark.regression,
    pytest.mark.security,
]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def client(current_user: CurrentUser, monkeypatch) -> TestClient:
    # Stub DB + check visibilita': il test si concentra sul boundary,
    # non sulla logica di accesso.
    monkeypatch.setattr(
        eseguibili_router_module.eseguibili_db,
        "get_eseguibile",
        lambda eid: {"id": eid, "group_id": "grp-1", "created_by": "u-1"},
    )
    monkeypatch.setattr(
        eseguibili_router_module, "_check_visible", lambda *a, **kw: None,
    )

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.include_router(eseguibili_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_analyze_upload_mime_reject_sanitizes_detail(client, monkeypatch):
    """Il MIME validator solleva con payload che include path Windows e URL:
    il detail esposto al client NON deve contenerli."""
    from app.utils import mime_validator

    sporco = (
        r"File eseguibile rifiutato: C:\Users\Marco\malware.exe "
        r"referenced from https://attacker.example.com/?token=abc123"
    )

    def raise_mime(content, filename):
        raise mime_validator.MimeMismatchError(sporco)

    monkeypatch.setattr(mime_validator, "validate_no_executable", raise_mime)

    response = client.post(
        "/api/eseguibili/ese-1/analyze-upload",
        files={"file": ("malware.exe", b"payload", "application/octet-stream")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    # Nessuno dei payload sensibili sopravvive.
    assert "C:\\Users" not in detail
    assert "Marco" not in detail
    assert "attacker.example.com" not in detail
    assert "token=abc123" not in detail
    assert "https://" not in detail
    # Marker sanitizer presenti.
    assert "[PATH]" in detail or "[URL]" in detail


def test_analyze_upload_value_error_sanitizes_detail(client, monkeypatch):
    """Il service solleva ValueError con path temp + URL + token:
    il detail deve essere sanitizzato."""
    from app.utils import mime_validator
    from app.core.eseguibili import service as eseguibili_service

    # MIME pass-through.
    monkeypatch.setattr(
        mime_validator, "validate_no_executable", lambda content, filename: None,
    )

    sporco = (
        r"Parser error on /tmp/urby/xyz789/upload.dxf: "
        r"failed to fetch https://projector.internal/srid?api_key=SECRET42 "
        r"at line 45"
    )

    def raise_value(*args, **kwargs):
        raise ValueError(sporco)

    monkeypatch.setattr(eseguibili_service, "analyze_file_upload", raise_value)

    response = client.post(
        "/api/eseguibili/ese-1/analyze-upload",
        files={"file": ("test.dxf", b"dxf-bytes", "application/octet-stream")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    # Nessun leak.
    assert "/tmp/urby" not in detail
    assert "xyz789" not in detail
    assert "https://" not in detail
    assert "projector.internal" not in detail
    assert "SECRET42" not in detail
    # Al contrario, parte benigna preservata.
    assert "at line 45" in detail
    # Marker sanitizer.
    assert "[PATH]" in detail
    assert "[URL]" in detail
