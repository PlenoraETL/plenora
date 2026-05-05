"""Contract test F2-B1 (2026-04-24) — DELETE endpoints ritornano 204.

Blinda convenzione REST (RFC 9110): DELETE che elimina una risorsa
DEVE restituire 204 No Content con body vuoto (non 200 + dict di
conferma ridondante).

Endpoint in scope F2-B1 (3 endpoint CRUD omogenei, simmetrici a F3-B1):
  - DELETE /api/connections/{id}      -> 204 No Content
  - DELETE /api/api-connections/{id}  -> 204 No Content
  - DELETE /api/schedules/{id}        -> 204 No Content

Breaking change: il body dict `{"status": "deleted", "id": X}` non
e' piu' restituito. Audit FE verificato pre-fix: i 3 call-site
(ConnectionManager, ApiConnectionManager, ScheduleList) fanno
"delete + reload list" senza consumare il body. 204 e' safe.

Fuori scope F2-B1 (batch F2-B2+):
  - DELETE /api/projects/{id}
  - DELETE /api/eseguibili/{id}
  - DELETE /api/tickets/{id}, /api/tickets/{id}/attachments/{aid}
  - DELETE /api/files/{id}
  - DELETE /api/features/connections/{id}
  - DELETE /api/imdf/bundles/{id}

Strategia: introspection del router (coerente con F3-B2/B3). Il
contract da blindare e' la dichiarazione `status_code=204` sul
decoratore; il happy path end-to-end e' coperto dai test esistenti
aggiornati (test_delete_connection_409_shape.py + test_delete_api_
connection_409_shape.py).
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
