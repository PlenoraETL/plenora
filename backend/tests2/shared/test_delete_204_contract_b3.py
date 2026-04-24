"""Contract test F2-B3 (2026-04-24) — DELETE → 204 (F2 chiuso).

Ultimo batch del cluster F2. Canonicalizza 204 sui 4 endpoint DELETE
restanti: tickets x2 + files + imdf-bundles.

Dopo F2-B3: F2 cluster chiuso — 10/10 DELETE canonicalizzati a
204 No Content. Unico DELETE a 200 residuo e' /api/shares/...
(ritorna SharesResponse aggiornato, legittimo 200, fuori scope F2).

Endpoint in scope:
  - DELETE /api/tickets/{id}                        -> 204
  - DELETE /api/tickets/{id}/attachments/{aid}      -> 204
  - DELETE /api/files/{id}                          -> 204
  - DELETE /api/imdf/bundles/{bundle_task_id}       -> 204

Nota su tickets/{id}: il body pre-fix era
``{"ok": True, "hard": result.get("hard", False)}``. Il campo ``hard``
era informativo (distingue soft-delete da purge via ?hard=true). Post
F2-B3 il body e' vuoto e la semantica soft/hard resta verificabile
via dispatch al service (mock assertion nel test di contract) +
logger op "ticket_delete" (PURGED vs soft-deleted). I test
contract esistenti in tests2/tickets/ sono stati aggiornati per
asserire via mock invece che via body.

Strategia: introspection del router (coerente con F2-B1/B2 + F3-B2/B3).
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _assert_delete_route_status_204(router, path: str, context: str):
    """Helper: cerca route DELETE esatto, verifica status_code=204.

    Duplicato intenzionale di F2-B1/B2 (niente helper shared per vincolo
    utente)."""
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
