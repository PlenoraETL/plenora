"""Contract tests — ruolo `esecutore` (kiosk-only).

Il ruolo `esecutore` e' stato aggiunto come 5o ruolo (post audit RBAC
2026-04-09) per uso "kiosk": vede SOLO la lista degli eseguibili del
proprio gruppo (+ condivisi) e puo' lanciarli, ma e' bloccato su
qualsiasi router di gestione (connections, datasources, projects,
schedules, logs, ecc.).

Nessun test mirato esisteva su questo ruolo: la maggior parte dei test
in `tests2/` usa `app.dependency_overrides[require_not_esecutore] =
lambda: current_user` per BYPASSARE il guard mentre testa altre cose.
Questo file invece BLINDA il guard, in 5 sezioni:

  Sez. A — contratto positivo eseguibili: list/get/execute permessi
           (override solo `get_current_user` con esecutore, senza
           toccare guard/visibility helpers reali nei punti critici)
  Sez. B — contratto negativo parametrico: 403 atteso su un dummy
           endpoint per ognuna delle 3 dependency che escludono
           esecutore (require_not_esecutore / require_admin /
           require_user_or_above) + smoke wiring su 1 router reale
  Sez. C — eseguibili admin-mutativo: POST/PUT/DELETE bloccati per
           esecutore (require_admin guard)
  Sez. D — dependency-direct: chiamata diretta su
           `require_not_esecutore` — 403 per esecutore, pass per altri
  Sez. E — `GET /api/auth/me` consentito a qualsiasi autenticato,
           incluso esecutore

Nota di scope:
- niente prod code
- niente migration
- niente FE
- niente DB live
- mock minimi: solo quanto basta per ottenere 200/403 sul contratto
  RBAC, NON per testare la business logic di eseguibili
"""

from __future__ import annotations

from typing import Tuple
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser, ROLES_KIOSK_ONLY, VALID_ROLES
from app.core.auth.auth import get_current_user, require_admin, require_not_esecutore, require_superadmin, require_user_or_above


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.security]


# Helpers


def _user(role: str, group_id: str = "g1", user_id: str = "u1") -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        username=f"{role}_user",
        group_id=group_id,
        role=role,
    )


def _esecutore() -> CurrentUser:
    return _user("esecutore")


def _build_dummy_app() -> FastAPI:
    """App con 3 endpoint dummy, uno per ogni dependency che esclude
    esecutore. Sufficiente per testare la semantica del guard senza
    importare router reali (limita rumore + tempo di import)."""
    app = FastAPI()
    r = APIRouter()

    @r.get("/dummy/not-esecutore")
    def _ne(u: CurrentUser = Depends(require_not_esecutore)):
        return {"role": u.role}

    @r.get("/dummy/admin")
    def _a(u: CurrentUser = Depends(require_admin)):
        return {"role": u.role}

    @r.get("/dummy/user-or-above")
    def _uoa(u: CurrentUser = Depends(require_user_or_above)):
        return {"role": u.role}

    app.include_router(r)
    return app


# Sez. D — Dependency-direct tests


def test_valid_roles_includes_esecutore():
    assert "esecutore" in VALID_ROLES


def test_roles_kiosk_only_constant_holds_only_esecutore():
    assert ROLES_KIOSK_ONLY == ("esecutore",)


def test_require_not_esecutore_raises_403_for_esecutore():
    """Chiamata diretta sulla dependency, senza FastAPI in mezzo."""
    with pytest.raises(HTTPException) as exc:
        require_not_esecutore(current_user=_esecutore())
    assert exc.value.status_code == 403
    assert "esecutore" in str(exc.value.detail).lower()


@pytest.mark.parametrize("role", ["viewer", "user", "admin", "superadmin"])
def test_require_not_esecutore_passes_for_non_kiosk_roles(role: str):
    """I 4 ruoli non-kiosk devono attraversare il guard senza errore."""
    out = require_not_esecutore(current_user=_user(role))
    assert out.role == role


# Sez. B — Negative parametric (dummy endpoints + smoke router reale)


@pytest.fixture
def dummy_client_as_esecutore() -> TestClient:
    app = _build_dummy_app()
    app.dependency_overrides[get_current_user] = _esecutore
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    [
        "/dummy/not-esecutore",
        "/dummy/admin",
        "/dummy/user-or-above",
    ],
)
def test_esecutore_blocked_on_excluding_dependencies(
    dummy_client_as_esecutore: TestClient, path: str
):
    """Per ognuna delle 3 dependency che escludono esecutore,
    l'endpoint deve rispondere 403. Niente bypass del guard."""
    resp = dummy_client_as_esecutore.get(path)
    assert resp.status_code == 403, (
        f"{path}: atteso 403, ricevuto {resp.status_code} ({resp.text})"
    )


@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
def test_non_esecutore_passes_user_or_above(role: str):
    """Smoke positive: i ruoli sopra viewer attraversano
    require_user_or_above."""
    app = _build_dummy_app()
    app.dependency_overrides[get_current_user] = lambda: _user(role)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/dummy/user-or-above")
    assert resp.status_code == 200
    assert resp.json()["role"] == role


def test_esecutore_smoke_blocked_on_real_connections_router():
    """Smoke wiring: il guard reale e' montato su un router reale.
    Se qualcuno rimuove `require_not_esecutore` da connections, questo
    test fallisce."""
    from app.core.connections.router import router as connections_router

    app = FastAPI()
    app.dependency_overrides[get_current_user] = _esecutore
    app.include_router(connections_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/connections")
    assert resp.status_code == 403


# Sez. A — Positive eseguibili (mock minimi)


@pytest.fixture
def eseguibili_app_as_esecutore() -> Tuple[TestClient, MagicMock]:
    """App con il router eseguibili reale + override di
    `get_current_user` con esecutore. Mock minimi su `eseguibili_db` /
    `visible_resource_ids` / `_check_visible` solo dove serve per
    superare i check di visibility — NON testiamo business logic, solo
    contratto RBAC."""
    from app.core.eseguibili import router as router_module
    from app.core.eseguibili.router import router as eseg_router

    app = FastAPI()
    app.dependency_overrides[get_current_user] = _esecutore
    app.include_router(eseg_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), router_module


def test_esecutore_can_list_eseguibili(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`GET /api/eseguibili` deve essere 200 per esecutore.
    Nessun guard `require_not_esecutore` su questo endpoint."""
    client, router_module = eseguibili_app_as_esecutore
    # Tutti i campi obbligatori di EseguibileSummary (response_model)
    fake_row = {
        "id": "e1",
        "name": "test-exec",
        "description": "",
        "default_output_format": "csv",
        "icon": None,
        "group_id": "g1",
        "created_by": "u-admin",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "expected_columns_count": 0,
    }
    with patch.object(
        router_module.eseguibili_db, "list_eseguibili", return_value=[fake_row]
    ), patch(
        "app.core.eseguibili.router.visible_resource_ids", return_value={"e1"}
    ), patch(
        "app.core.eseguibili.router.compute_can_edit", return_value=False
    ):
        resp = client.get("/api/eseguibili")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    # mock ritorna un solo record visibile
    assert len(body) == 1
    assert body[0]["id"] == "e1"


def test_esecutore_can_get_eseguibile_visible(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`GET /api/eseguibili/{id}` per risorsa visibile -> 200."""
    client, router_module = eseguibili_app_as_esecutore
    fake_row = {
        "id": "e1",
        "name": "test-exec",
        "description": "",
        "expected_columns": [],
        "default_output_format": "csv",
        "default_output_name": None,
        "icon": None,
        "source_canvas_id": None,
        "group_id": "g1",
        "created_by": "u-admin",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "output_is_file_based": False,
        "inferred_output_extension": None,
    }
    with patch.object(
        router_module.eseguibili_db, "get_eseguibile", return_value=fake_row
    ), patch.object(
        router_module, "assert_resource_visible", lambda *a, **kw: None
    ), patch.object(
        router_module, "compute_can_edit", return_value=False
    ):
        resp = client.get("/api/eseguibili/e1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "e1"


def test_esecutore_cannot_get_eseguibile_cross_tenant(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`GET /api/eseguibili/{id}` per risorsa di altro gruppo non
    visibile -> 403/404 (a seconda del helper assert_resource_visible).
    Il punto e': NON 200, niente leak cross-tenant."""
    client, router_module = eseguibili_app_as_esecutore
    fake_row = {
        "id": "e2",
        "name": "other-group-exec",
        "group_id": "g2",  # altro gruppo
        "created_by": "u-admin-other",
        "project_id": "p2",
    }

    def _deny(*a, **kw):
        raise HTTPException(status_code=404, detail="Eseguibile non trovato")

    with patch.object(
        router_module.eseguibili_db, "get_eseguibile", return_value=fake_row
    ), patch.object(
        router_module, "assert_resource_visible", side_effect=_deny
    ):
        resp = client.get("/api/eseguibili/e2")

    assert resp.status_code in (403, 404), resp.text


# Sez. C — Eseguibili admin-mutativo bloccato per esecutore


def test_esecutore_blocked_on_create_eseguibile(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`POST /api/eseguibili` ha `require_admin` -> 403 per esecutore."""
    client, _ = eseguibili_app_as_esecutore
    resp = client.post(
        "/api/eseguibili",
        json={"project_id": "p1", "name": "x", "expected_columns": []},
    )
    assert resp.status_code == 403


def test_esecutore_blocked_on_update_eseguibile(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`PUT /api/eseguibili/{id}` ha `require_admin` -> 403."""
    client, _ = eseguibili_app_as_esecutore
    resp = client.put("/api/eseguibili/e1", json={"name": "renamed"})
    assert resp.status_code == 403


def test_esecutore_blocked_on_delete_eseguibile(
    eseguibili_app_as_esecutore: Tuple[TestClient, MagicMock],
):
    """`DELETE /api/eseguibili/{id}` ha `require_admin` -> 403."""
    client, _ = eseguibili_app_as_esecutore
    resp = client.delete("/api/eseguibili/e1")
    assert resp.status_code == 403


# Sez. E — auth/me consentito


def test_esecutore_can_access_auth_me():
    """`GET /api/auth/me` usa solo `get_current_user`, quindi qualsiasi
    autenticato (incluso esecutore) deve ricevere 200."""
    from app.core.auth.auth import router as auth_router

    # Tutti i campi obbligatori di UserResponse
    fake_user_record = {
        "id": "u1",
        "username": "esecutore_user",
        "email": None,
        "group_id": "g1",
        "role": "esecutore",
        "is_active": True,
        "created_at": "2026-01-01T00:00:00",
    }

    # auth_router gia' ha prefix='/auth' interno, includere con prefix='/api'
    app = FastAPI()
    app.dependency_overrides[get_current_user] = _esecutore
    app.include_router(auth_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    with patch("app.core.auth.auth.db") as mock_db:
        mock_db.get_user.return_value = fake_user_record
        resp = client.get("/api/auth/me")

    assert resp.status_code == 200, resp.text
    assert resp.json().get("role") == "esecutore"
