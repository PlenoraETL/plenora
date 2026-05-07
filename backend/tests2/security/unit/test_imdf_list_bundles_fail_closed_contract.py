"""
Tests the imdf list bundles fail closed behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import get_current_user
from app.core.imdf.router import router
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def _build_client():
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_list_bundles_raises_500_when_visible_project_ids_lookup_fails(tmp_path):
    """RB.4.A.16: list_bundles non passa piu' per visible_resource_ids per
    "projects" (non shareable), ma legge tramite `list_project_ids_for_group`.
    Il fail-closed deve restare se la query DB fallisce."""
    client = _build_client()

    with patch("app.core.imdf.router.settings", type("S", (), {"IMDF_BUNDLES_DIR": tmp_path})()), \
         patch("app.db.config.projects.list_project_ids_for_group", side_effect=RuntimeError("db down")):
        response = client.get("/api/imdf/bundles")

    assert response.status_code == 500
