"""Contract test F2-B2 (2026-04-24) — DELETE → 204 (estensione F2-B1).

Estensione del pattern `DELETE → 204 No Content` a 3 endpoint CRUD
restanti dopo F2-B1 (connections/api-connections/schedules):

  - DELETE /api/projects/{id}               -> 204 No Content
  - DELETE /api/eseguibili/{id}             -> 204 No Content
  - DELETE /api/features/connections/{id}   -> 204 No Content

Breaking change: body dict di conferma rimosso
(`{"status":"deleted",...}` / `{"success":True,...}`). Audit FE
pre-fix conferma tutti e 3 fanno "fire-and-refresh" senza leggere il
body.

Strategia: introspection del router (coerente con F2-B1 + F3-B2/B3).

Fuori scope F2-B2 (batch F2-B3):
  - DELETE /api/tickets/{id}
  - DELETE /api/tickets/{id}/attachments/{aid}
  - DELETE /api/files/{id}
  - DELETE /api/imdf/bundles/{id}
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _assert_delete_route_status_204(router, path: str, context: str):
    """Helper: cerca route DELETE esatto, verifica status_code=204.

    Duplicato intenzionale dello stesso helper di
    test_delete_204_contract.py (F2-B1): scope F2-B2 estende il pattern
    senza consolidare shared helper (niente helper shared come da
    vincoli utente)."""
    delete_routes = [
        r for r in router.routes
        if getattr(r, "path", None) == path
        and "DELETE" in getattr(r, "methods", set())
    ]
    assert len(delete_routes) == 1, (
        f"{context}: mi aspetto esattamente 1 route DELETE {path!r}, "
        f"trovato: {[(r.path, r.methods) for r in router.routes if 'DELETE' in getattr(r, 'methods', set())]!r}"
    )
    assert delete_routes[0].status_code == 204, (
        f"{context}: DELETE {path} DEVE dichiarare status_code=204 nel "
        f"decoratore; ottenuto {delete_routes[0].status_code!r} "
        f"(None = default 200)"
    )


def test_delete_projects_declares_204_on_route():
    """F2-B2: DELETE /api/projects/{id} dichiara status_code=204."""
    from app.core.projects.router import router as projects_router

    _assert_delete_route_status_204(
        projects_router,
        "/projects/{project_id}",
        "DELETE /api/projects/{id}",
    )


def test_delete_eseguibili_declares_204_on_route():
    """F2-B2: DELETE /api/eseguibili/{id} dichiara status_code=204."""
    from app.core.eseguibili.router import router as eseguibili_router

    _assert_delete_route_status_204(
        eseguibili_router,
        "/eseguibili/{eseguibile_id}",
        "DELETE /api/eseguibili/{id}",
    )


def test_delete_features_connections_declares_204_on_route():
    """F2-B2: DELETE /api/features/connections/{id} dichiara status_code=204."""
    from app.core.features.routes import router as features_router

    _assert_delete_route_status_204(
        features_router,
        "/features/connections/{connection_id}",
        "DELETE /api/features/connections/{id}",
    )
