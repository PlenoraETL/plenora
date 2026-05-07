"""
Tests the http cross tenant behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from app.core.orchestrator.router import tasks_router
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.security, pytest.mark.regression]


def _build_client():
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: build_current_user()
    app.include_router(tasks_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_create_task_rejects_cross_tenant_project_before_task_creation():
    client = _build_client()

    with patch("app.shared.authz_helpers.assert_project_access", side_effect=HTTPException(status_code=403, detail="Accesso negato")), \
         patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
        response = client.post(
            "/api/tasks",
            json={
                "task_type": "enrichment",
                "project_id": "proj-foreign",
                "canvas": {"nodes": [], "edges": []},
            },
        )

    assert response.status_code == 403
    create_task.assert_not_called()
