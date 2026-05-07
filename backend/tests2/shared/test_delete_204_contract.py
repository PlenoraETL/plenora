"""
Contract test F2 (2026-04-24) - DELETE endpoints ritornano 204.

Blinda convenzione REST (RFC 9110): DELETE che elimina una risorsa.
"""

from __future__ import annotations

from importlib import import_module

import pytest

from tests2.shared._route_status_helpers import _assert_delete_route_status_204


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


DELETE_204_ROUTE_CASES = [
    pytest.param(
        "app.core.connections.router",
        "router",
        "/connections/{connection_id}",
        "DELETE /api/connections/{id}",
        id="connections",
    ),
    pytest.param(
        "app.core.arricchisci.router",
        "api_connections_router",
        "/api-connections/{api_id}",
        "DELETE /api/api-connections/{id}",
        id="api-connections",
    ),
    pytest.param(
        "app.core.scheduler.router",
        "router",
        "/schedules/{schedule_id}",
        "DELETE /api/schedules/{id}",
        id="schedules",
    ),
    pytest.param(
        "app.core.projects.router",
        "router",
        "/projects/{project_id}",
        "DELETE /api/projects/{id}",
        id="projects",
    ),
    pytest.param(
        "app.core.eseguibili.router",
        "router",
        "/eseguibili/{eseguibile_id}",
        "DELETE /api/eseguibili/{id}",
        id="eseguibili",
    ),
    pytest.param(
        "app.core.features.routes",
        "router",
        "/features/connections/{connection_id}",
        "DELETE /api/features/connections/{id}",
        id="features-connections",
    ),
    pytest.param(
        "app.core.tickets.router",
        "router",
        "/tickets/{ticket_id}",
        "DELETE /api/tickets/{id}",
        id="tickets",
    ),
    pytest.param(
        "app.core.tickets.router",
        "router",
        "/tickets/{ticket_id}/attachments/{attachment_id}",
        "DELETE /api/tickets/{id}/attachments/{aid}",
        id="ticket-attachments",
    ),
    pytest.param(
        "app.core.files.router",
        "router",
        "/files/{file_id}",
        "DELETE /api/files/{id}",
        id="files",
    ),
    pytest.param(
        "app.core.imdf.router",
        "router",
        "/imdf/bundles/{bundle_task_id}",
        "DELETE /api/imdf/bundles/{bundle_task_id}",
        id="imdf-bundles",
    ),
]


@pytest.mark.parametrize(
    ("router_module", "router_name", "path", "label"),
    DELETE_204_ROUTE_CASES,
)
def test_delete_declares_204_on_route(
    router_module: str,
    router_name: str,
    path: str,
    label: str,
) -> None:
    """F2: DELETE route contract dichiara status_code=204."""
    router = getattr(import_module(router_module), router_name)

    _assert_delete_route_status_204(router, path, label)
