"""
Contract test F2-B2 (2026-04-24) — DELETE → 204 (estensione F2-B1).

Estensione del pattern `DELETE → 204 No Content` a 3 endpoint CRUD
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# BE-GO-READY-TEST-INFRA-001: helper extracted to a shared module.
# Body + assertion messages preserved byte-exact; the local copy
# (originally a deliberate duplicate of F2-B1's helper, scope-bounded
# at the time) now lives at tests2/shared/_route_status_helpers.py.
from tests2.shared._route_status_helpers import _assert_delete_route_status_204


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
