"""
tests/orchestrator/unit/test_cleanup_tenant_check.py
====================================================
Unit tests per il fix IDOR su DELETE /api/tasks/{task_id}/full-cleanup.

Verifica che il tenant check impedisca a un utente di un gruppo di cancellare
risorse di task appartenenti a un altro gruppo.
"""

import pytest
import logging
from unittest.mock import MagicMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# Override del fixture autouse `mock_reset` (root conftest.py) che fa skip
# se il mock_api_server non gira. Questi test usano TestClient, niente network.
@pytest.fixture(autouse=True)
def mock_reset():
    yield


@pytest.fixture(autouse=True)
def silence_logs(caplog):
    with caplog.at_level(logging.CRITICAL, logger="app.core.orchestrator.router"):
        yield


from app.core.orchestrator.router import tasks_router
from app.core.auth.auth import get_current_user, CurrentUser


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _user(group_id="group-A", role="user"):
    return CurrentUser(
        user_id=f"u-{group_id}", username="x",
        group_id=group_id, role=role,
    )


def make_client(user=None):
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    app.include_router(tasks_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _mock_task_info(project_id="proj-1"):
    info = MagicMock()
    info.project_id = project_id
    info.result = None
    info.task_id = "task-xyz"
    return info


def _mock_project_meta(group_id="group-A"):
    """Mocka un ProjectMetadata per assert_group_access."""
    meta = MagicMock()
    meta.group_id = group_id
    meta.id = "proj-1"
    meta.name = "Test Project"
    return meta


# ============================================================================
# DELETE /tasks/{id}/full-cleanup — tenant check
# ============================================================================

class TestCleanupTenantCheck:

    def test_cleanup_same_group_succeeds(self):
        """User di group-A su task di group-A → 204."""
        client = make_client(user=_user(group_id="group-A"))
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.db.results_db_service.delete_results_for_project", return_value=0), \
             patch("celery.result.AsyncResult"):
            mock_ts.get_task_status.return_value = _mock_task_info(project_id="proj-A")
            mock_ps = MagicMock()
            mock_ps.get_project_metadata.return_value = _mock_project_meta(group_id="group-A")
            mock_ps_cls.return_value = mock_ps
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 204

    def test_cleanup_cross_tenant_blocked(self):
        """User di group-A su task di group-B → 403."""
        client = make_client(user=_user(group_id="group-A"))
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.services.project_service.ProjectService") as mock_ps_cls:
            mock_ts.get_task_status.return_value = _mock_task_info(project_id="proj-B")
            mock_ps = MagicMock()
            # Il progetto appartiene a un altro gruppo
            mock_ps.get_project_metadata.return_value = _mock_project_meta(group_id="group-B")
            mock_ps_cls.return_value = mock_ps
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 403

    def test_cleanup_no_project_id_404(self):
        """Task senza project_id → 404 (impossibile verificare ownership)."""
        client = make_client()
        info = MagicMock()
        info.project_id = None
        info.result = None
        with patch("app.services.task_service.task_service") as mock_ts:
            mock_ts.get_task_status.return_value = info
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 404

    def test_cleanup_project_id_from_task_result(self):
        """Se project_id è in task.result invece che task.project_id, viene letto."""
        client = make_client(user=_user(group_id="group-A"))
        info = MagicMock()
        info.project_id = None
        info.result = {"project_id": "proj-A"}
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.db.results_db_service.delete_results_for_project", return_value=0), \
             patch("celery.result.AsyncResult"):
            mock_ts.get_task_status.return_value = info
            mock_ps = MagicMock()
            mock_ps.get_project_metadata.return_value = _mock_project_meta(group_id="group-A")
            mock_ps_cls.return_value = mock_ps
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 204

    def test_cleanup_deleted_project_admin_allowed(self):
        """Progetto già cancellato + admin → cleanup residui consentito (204)."""
        client = make_client(user=_user(group_id="group-A", role="admin"))
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.services.project_service.ProjectService") as mock_ps_cls, \
             patch("app.db.results_db_service.delete_results_for_project", return_value=0), \
             patch("celery.result.AsyncResult"):
            mock_ts.get_task_status.return_value = _mock_task_info(project_id="proj-deleted")
            mock_ps = MagicMock()
            mock_ps.get_project_metadata.return_value = None  # progetto sparito
            mock_ps_cls.return_value = mock_ps
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 204

    def test_cleanup_deleted_project_user_404(self):
        """Progetto già cancellato + user normale → 404."""
        client = make_client(user=_user(group_id="group-A", role="user"))
        with patch("app.services.task_service.task_service") as mock_ts, \
             patch("app.services.project_service.ProjectService") as mock_ps_cls:
            mock_ts.get_task_status.return_value = _mock_task_info(project_id="proj-deleted")
            mock_ps = MagicMock()
            mock_ps.get_project_metadata.return_value = None
            mock_ps_cls.return_value = mock_ps
            res = client.delete("/api/tasks/task-xyz/full-cleanup")
        assert res.status_code == 404
