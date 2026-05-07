"""
Tests the scheduler http cross tenant behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.auth.auth import require_schedule_owner
from app.core.scheduler.router import router
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.security, pytest.mark.regression]


def _build_client():
    app = FastAPI()
    app.dependency_overrides[require_schedule_owner] = lambda: build_current_user()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_update_schedule_rejects_cross_tenant_project_rebind_before_service_update():
    client = _build_client()

    with patch("app.shared.authz_helpers.assert_project_access", side_effect=HTTPException(status_code=403, detail="Accesso negato")), \
         patch("app.core.scheduler.schedule_service.ScheduleService.update_schedule") as update_schedule:
        response = client.put(
            "/api/schedules/schedule_123456789abc",
            json={"project_id": "proj-foreign"},
        )

    assert response.status_code == 403
    update_schedule.assert_not_called()
