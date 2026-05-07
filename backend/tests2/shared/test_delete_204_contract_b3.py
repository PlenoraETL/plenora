"""
Contract test F2-B3 (2026-04-24) — DELETE → 204 (F2 chiuso).

Ultimo batch del cluster F2. Canonicalizza 204 sui 4 endpoint DELETE
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# BE-GO-READY-TEST-INFRA-001: helper extracted to a shared module.
# Body + assertion messages preserved byte-exact.
from tests2.shared._route_status_helpers import _assert_delete_route_status_204


def test_delete_tickets_declares_204_on_route():
    """F2-B3: DELETE /api/tickets/{id} dichiara status_code=204."""
    from app.core.tickets.router import router as tickets_router

    _assert_delete_route_status_204(
        tickets_router,
        "/tickets/{ticket_id}",
        "DELETE /api/tickets/{id}",
    )


def test_delete_ticket_attachment_declares_204_on_route():
    """F2-B3: DELETE /api/tickets/{id}/attachments/{aid} dichiara status_code=204."""
    from app.core.tickets.router import router as tickets_router

    _assert_delete_route_status_204(
        tickets_router,
        "/tickets/{ticket_id}/attachments/{attachment_id}",
        "DELETE /api/tickets/{id}/attachments/{aid}",
    )


def test_delete_files_declares_204_on_route():
    """F2-B3: DELETE /api/files/{id} dichiara status_code=204."""
    from app.core.files.router import router as files_router

    _assert_delete_route_status_204(
        files_router,
        "/files/{file_id}",
        "DELETE /api/files/{id}",
    )


def test_delete_imdf_bundles_declares_204_on_route():
    """F2-B3: DELETE /api/imdf/bundles/{bundle_task_id} dichiara status_code=204."""
    from app.core.imdf.router import router as imdf_router

    _assert_delete_route_status_204(
        imdf_router,
        "/imdf/bundles/{bundle_task_id}",
        "DELETE /api/imdf/bundles/{bundle_task_id}",
    )
