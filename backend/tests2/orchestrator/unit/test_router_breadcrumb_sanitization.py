"""
C2 — breadcrumb + detail fisso in orchestrator/router.

Audit finding:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.orchestrator.task_service import TaskInfo, TaskStatus, TaskType


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


def test_preview_status_uses_tenant_from_persisted_reference(client):
    task_info = TaskInfo(
        task_id="task-preview-1",
        task_type=TaskType.PREVIEW,
        project_id="p1",
        status=TaskStatus.SUCCESS,
        progress=100,
        message="done",
        result={
            "project_id": "p1",
            "preview_storage_key": "tenants/grp-1/previews/p1/task-preview-1.json",
            "preview_group_id": "grp-1",
        },
    )
    hydrated = {"project_id": "p1", "columns": [], "preview": []}

    with patch(
        "app.core.orchestrator.router._assert_task_group",
        return_value=task_info,
    ), patch(
        "app.core.projects.service.ProjectService.get_project_metadata",
        side_effect=AssertionError("redundant project lookup"),
    ), patch(
        "app.core.orchestrator.preview_payload_store.hydrate_preview_payload",
        return_value=hydrated,
    ) as hydrate:
        response = client.get("/api/tasks/task-preview-1")

    assert response.status_code == 200
    assert response.json()["result"] == hydrated
    hydrate.assert_called_once_with(
        task_info.result,
        expected_group_id="grp-1",
    )


# F6.2 — get_task_status endpoint detail fisso (no task_id leak in response)


def test_get_task_status_internal_failure_is_not_reported_as_not_found(client):
    """Internal mapper exceptions return a sanitized 500 envelope."""
    # task_id plausibile ma univoco, cosi' possiamo cercarlo nel body
    canary_task_id = "canary-xyz-abc-123"

    with patch(
        "app.core.orchestrator.router._assert_task_group",
        return_value=SimpleNamespace(),
    ), patch(
        "app.core.orchestrator.router._task_info_to_dict",
        side_effect=RuntimeError("DB pool exhausted — internal details")
    ):
        response = client.get(f"/api/tasks/{canary_task_id}")

    assert response.status_code == 500
    # detail canonico senza interpolazione dell'input utente
    detail = response.json()["detail"]
    assert detail["error"] == "internal_error"
    assert detail["message"] == "Errore interno del server"
    # il task_id NON deve comparire nel detail (no reflected input)
    assert canary_task_id not in str(detail)


# F6.1 — _assert_task_group breadcrumb su lookup fail


def test_assert_task_group_reports_store_unavailable_on_lookup_fail(client):
    """A runtime lookup failure is operational unavailability, not a 404."""
    with patch(
        "app.core.orchestrator.router.logger"
    ) as mock_logger, patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        side_effect=OSError("database connection reset")
    ):
        # Qualsiasi endpoint che passi attraverso _assert_task_group va bene.
        # Usiamo DELETE /api/tasks/{id}/full-cleanup perche' _assert_task_group
        # non e' invocato li', uso invece DELETE /api/tasks/{id} (cancel_task)
        # che chiama _assert_task_group come primo step.
        response = client.delete("/api/tasks/task-xyz")

    unavailable_calls = [
        c for c in mock_logger.error.call_args_list
        if "task access lookup unavailable" in str(c)
    ]
    assert unavailable_calls, (
        "atteso log task access lookup unavailable, "
        f"ricevuto: {mock_logger.error.call_args_list}"
    )
    assert any(
        call.kwargs.get("exc_info") is True for call in unavailable_calls
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "task_store_unavailable"


# F6.3 — full-cleanup tenant check breadcrumb su ProjectService lookup


def test_full_cleanup_project_lookup_failure_is_service_unavailable(client, tmp_path):
    """A project DB outage must never be treated as a deleted project."""
    task_info = SimpleNamespace(
        project_id="p1",
        result={"project_id": "p1"},
    )

    with patch(
        "app.core.orchestrator.router.logger"
    ) as mock_logger, patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        return_value=task_info
    ), patch(
        "app.core.projects.service.ProjectService.get_project_metadata",
        side_effect=OSError("config DB connection reset")
    ), patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path)
    ), patch(
        "app.core.results.cleanup.delete_result", return_value=True
    ), patch(
        "app.core.orchestrator.log_cleanup_repository.execute",
        return_value=SimpleNamespace(rowcount=0)
    ), patch(
        "app.core.orchestrator.task_service.task_service.forget_task_result"
    ):
        response = client.delete("/api/tasks/task-xyz/full-cleanup")

    unavailable_calls = [
        c for c in mock_logger.error.call_args_list
        if "task access lookup unavailable" in str(c)
    ]
    assert unavailable_calls
    assert any(
        call.kwargs.get("exc_info") is True for call in unavailable_calls
    )
    assert response.status_code == 503
