"""
Contract test F2-B1 (2026-04-24) — DELETE endpoints ritornano 204.

Blinda convenzione REST (RFC 9110): DELETE che elimina una risorsa
"""

from __future__ import annotations

import pytest

# BE-GO-READY-TEST-INFRA-001: helper extracted to a shared module.
# Body + assertion messages preserved byte-exact; the local copy that
# used to live here now lives at tests2/shared/_route_status_helpers.py.
from tests2.shared._route_status_helpers import _assert_delete_route_status_204


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_delete_connections_declares_204_on_route():
    """F2-B1: DELETE /api/connections/{id} dichiara status_code=204."""
    from app.core.connections.router import router as connections_router

    _assert_delete_route_status_204(
        connections_router,
        "/connections/{connection_id}",
        "DELETE /api/connections/{id}",
    )


def test_delete_api_connections_declares_204_on_route():
    """F2-B1: DELETE /api/api-connections/{id} dichiara status_code=204."""
    from app.core.arricchisci.router import api_connections_router

    _assert_delete_route_status_204(
        api_connections_router,
        "/api-connections/{api_id}",
        "DELETE /api/api-connections/{id}",
    )


def test_delete_schedules_declares_204_on_route():
    """F2-B1: DELETE /api/schedules/{id} dichiara status_code=204."""
    from app.core.scheduler.router import router as scheduler_router

    _assert_delete_route_status_204(
        scheduler_router,
        "/schedules/{schedule_id}",
        "DELETE /api/schedules/{id}",
    )
