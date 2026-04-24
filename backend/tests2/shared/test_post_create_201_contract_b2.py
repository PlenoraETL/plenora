"""Contract test F3-B2 (2026-04-24) — POST create → 201 (estensione F3-B1).

Estensione di `test_post_create_201_contract.py` (F3-B1) ai 3 endpoint
CRUD create rimasti del cluster F3:

  - POST /api/projects/           → 201 Created
  - POST /api/eseguibili          → 201 Created
  - POST /api/features/connections → 201 Created

Strategia: introspection del router (come per schedules in F3-B1) —
questi endpoint hanno schemi Pydantic nested (ProjectCreateWithCanvas,
EseguibileDetails, ArcGIS) che rendono fragile costruire fixture
TestClient complete. Il contract da blindare e' la dichiarazione
`status_code=201` sul decoratore, non il roundtrip end-to-end (gia'
coperto da integration test esistenti dove il backend gira davvero).

Fuori scope F3-B2 (riservato F3-B3):
  - POST /api/files/upload
  - POST /api/files/upload-multi-layer
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _assert_post_route_status_201(router, path: str, context: str):
    """Helper comune: cerca il route POST esatto e verifica status_code=201."""
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


# ─── 1) POST /api/projects/ → 201 ────────────────────────────────────────


def test_post_projects_declares_201_on_route():
    """F3-B2: POST /api/projects/ dichiara status_code=201.

    Il router projects usa `@router.post("")` che con prefix e' montato
    come `/projects` (senza trailing slash dal decoratore, FastAPI
    normalizza)."""
    from app.core.projects.router import router as projects_router

    _assert_post_route_status_201(
        projects_router, "/projects", "POST /api/projects"
    )


# ─── 2) POST /api/eseguibili → 201 ───────────────────────────────────────


def test_post_eseguibili_declares_201_on_route():
    """F3-B2: POST /api/eseguibili dichiara status_code=201."""
    from app.core.eseguibili.router import router as eseguibili_router

    _assert_post_route_status_201(
        eseguibili_router, "/eseguibili", "POST /api/eseguibili"
    )


# ─── 3) POST /api/features/connections → 201 ────────────────────────────


def test_post_features_connections_declares_201_on_route():
    """F3-B2: POST /api/features/connections dichiara status_code=201."""
    from app.core.features.routes import router as features_router

    _assert_post_route_status_201(
        features_router,
        "/features/connections",
        "POST /api/features/connections",
    )
