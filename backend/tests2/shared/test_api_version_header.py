"""
Contract test per ApiVersionMiddleware.

Garantisce che X-API-Version sia presente su ogni response (200, 4xx,
custom exception path), e che il valore corrisponda alla costante
`app.config.API_VERSION`. La pubblicita` del header e' lock per
permettere al FE di branchare su `response.headers['x-api-version']`
senza fallback opzionali.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api_version_middleware import ApiVersionMiddleware
from app.config import API_VERSION


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiVersionMiddleware)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise HTTPException(status_code=418, detail="teapot")

    return app


def test_x_api_version_present_on_success():
    client = TestClient(_build_app())
    response = client.get("/ok")
    assert response.status_code == 200
    assert response.headers["X-API-Version"] == API_VERSION


def test_x_api_version_present_on_http_exception():
    client = TestClient(_build_app())
    response = client.get("/boom")
    assert response.status_code == 418
    assert response.headers["X-API-Version"] == API_VERSION


def test_x_api_version_value_is_semver_like():
    # Lock contro accidental impostazione a None / vuoto / valore degenere.
    # Forma minima accettata: cifra-punto-cifra (es. "1.0", "2.3"). Bumpare
    # la regex quando si introduce un suffisso (es. "1.0-rc1").
    import re
    assert re.match(r"^\d+\.\d+$", API_VERSION), (
        f"API_VERSION='{API_VERSION}' non e' nel formato 'major.minor'"
    )
