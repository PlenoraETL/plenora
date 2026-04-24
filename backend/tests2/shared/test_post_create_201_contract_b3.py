"""Contract test F3-B3 (2026-04-24) — POST create → 201 (chiusura cluster F3).

Chiusura di F3: dopo F3-B1 (connections/api-connections/schedules) e
F3-B2 (projects/eseguibili/features-connections), restano i 2 endpoint
upload file. Questi hanno pattern multipart (`UploadFile` + `Form`)
diverso dagli altri create, ma la convenzione REST 201 Created vale
ugualmente.

Endpoint in scope:
  - POST /api/files/upload              → 201 + FileSource
  - POST /api/files/upload-multi-layer  → 201 + List[FileSource]

Strategia: introspection del router (coerente con B1-schedules e
tutti i 3 test di B2). Il TestClient su multipart richiederebbe
fixture pesanti (UploadFile fake + blob storage mock + audit decorator
side-effect + DB create file_sources row) senza aggiungere coverage
superiore rispetto al contract `APIRoute.status_code == 201`.

Dopo F3-B3: F3 cluster chiuso — 8/8 endpoint CRUD create canonicalizzati.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _assert_post_route_status_201(router, path: str, context: str):
    """Helper: cerca route POST esatto, verifica status_code=201.

    Duplicato intenzionale dello stesso helper di
    test_post_create_201_contract_b2.py: scope F3-B3 e' chiudere il
    cluster, non consolidare helper cross-file (niente shared helper,
    come da vincoli utente)."""
    post_routes = [
        r for r in router.routes
        if getattr(r, "path", None) == path
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(post_routes) == 1, (
        f"{context}: mi aspetto esattamente 1 route POST {path!r}, "
        f"trovato: {[(r.path, r.methods) for r in router.routes if 'POST' in getattr(r, 'methods', set())]!r}"
    )
    assert post_routes[0].status_code == 201, (
        f"{context}: POST {path} DEVE dichiarare status_code=201 nel "
        f"decoratore; ottenuto {post_routes[0].status_code!r} "
        f"(None = default 200)"
    )


def test_post_files_upload_declares_201_on_route():
    """F3-B3: POST /api/files/upload dichiara status_code=201."""
    from app.core.files.router import router as files_router

    _assert_post_route_status_201(
        files_router, "/files/upload", "POST /api/files/upload"
    )


def test_post_files_upload_multi_layer_declares_201_on_route():
    """F3-B3: POST /api/files/upload-multi-layer dichiara status_code=201."""
    from app.core.files.router import router as files_router

    _assert_post_route_status_201(
        files_router,
        "/files/upload-multi-layer",
        "POST /api/files/upload-multi-layer",
    )
