"""Contract test P-CLR — POST /api/logs/clear rimosso (anti-reintroduzione).

Contesto: P-CLR ha rimosso `POST /logs/clear` (commit 698ff9d, 2026-04-22).
La log retention passa al beat worker (O-ARC pipeline archive-before-purge)
come unico consumer autorizzato di DELETE su `plenora.logs`. Riferimento:
`orchestrate/phase_2_hardening/p_clr_finalization.md §5.4` +
`backend/docs/architecture/invariants.md §1.4`.

Questo test blinda la rimozione: se qualcuno reintroduce
`@router.post("/clear")` in `backend/app/core/logs/router.py` (anche come
no-op o compat) la suite rompe e richiede review esplicita del team.

Comportamento HTTP atteso: la route e' stata completamente rimossa
(nessun verbo la serve). FastAPI ritorna **404 Not Found** — non 405
Method Not Allowed — perche' il path `/logs/clear` non e' registrato
per alcun metodo.

Scope: regression unit-level via TestClient isolato, stesso pattern
di `test_logs_cross_tenant_contract.py` e `test_logs_search_input_validation.py`.
Nessuna chiamata al DB (fetchall/fetchone stubbati).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _user(role: str = "superadmin") -> SimpleNamespace:
    return SimpleNamespace(
        user_id="u1", username="alice",
        group_id="g1", role=role,
    )


@pytest.fixture
def client(monkeypatch):
    from app.core.logs import router as logs_router
    from app.core.auth.auth import require_not_esecutore

    # Stub DB: se qualcuno reintroducesse l'endpoint, il test deve fallire
    # sull'asserzione HTTP prima che il DB venga toccato.
    monkeypatch.setattr(logs_router, "_fetchall", MagicMock(return_value=[]))
    monkeypatch.setattr(logs_router, "_fetchone", MagicMock(return_value={"c": 0}))

    app = FastAPI()
    # Auth override innocuo per parita' con gli altri test logs/* nella
    # cartella. La route non esiste, quindi il codice HTTP viene deciso
    # dal routing FastAPI prima della dependency resolution.
    app.dependency_overrides[require_not_esecutore] = lambda: _user()
    app.include_router(logs_router.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_post_logs_clear_returns_404_after_removal(client):
    """POST /api/logs/clear -> 404 Not Found.

    Hard guard anti-reintroduzione: la route non esiste piu' in
    `logs/router.py`. Reintrodurla anche come alias/no-op fa rompere
    questo test.
    """
    response = client.post("/api/logs/clear")
    assert response.status_code == 404, (
        f"REGRESSIONE P-CLR: POST /api/logs/clear deve essere 404 dopo la "
        f"rimozione dell'endpoint, ottenuto {response.status_code}. "
        f"Body: {response.text[:200]}"
    )


def test_get_logs_clear_returns_404(client):
    """GET /api/logs/clear -> 404. Anti-alias accidentale su altro verbo."""
    response = client.get("/api/logs/clear")
    assert response.status_code == 404, (
        f"REGRESSIONE P-CLR: GET /api/logs/clear deve essere 404, "
        f"ottenuto {response.status_code}."
    )


def test_logs_clear_absent_from_openapi_schema(client):
    """L'OpenAPI schema non deve piu' esporre `/logs/clear` in alcun
    verbo. Blinda documentazione API generata e client auto-generati."""
    schema = client.get("/openapi.json").json()
    paths = schema.get("paths", {})
    offending = [p for p in paths if p.endswith("/logs/clear")]
    assert not offending, (
        f"REGRESSIONE P-CLR: path /logs/clear reintrodotto nell'OpenAPI "
        f"schema: {offending}"
    )
