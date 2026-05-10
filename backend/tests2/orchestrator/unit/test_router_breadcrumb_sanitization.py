"""
C2 â€” breadcrumb + detail fisso in orchestrator/router.

Audit finding:
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]


# F6.2 â€” get_task_status endpoint detail fisso (no task_id leak in response)


def test_get_task_status_detail_fixed_on_broad_exception(client):
    """Quando task_service.get_task_status solleva non-HTTPException dentro
    l'endpoint GET /api/tasks/{id}, il detail della 404 deve essere fisso
    'Task non trovato' (NON interpolare task_id)."""
    # task_id plausibile ma univoco, cosi' possiamo cercarlo nel body
    canary_task_id = "canary-xyz-abc-123"

    with patch(
        "app.core.orchestrator.router._assert_task_group"
    ), patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        side_effect=RuntimeError("DB pool exhausted â€” internal details")
    ):
        response = client.get(f"/api/tasks/{canary_task_id}")

    assert response.status_code == 404
    # detail canonico senza interpolazione dell'input utente
    detail = response.json()["detail"]
    if isinstance(detail, dict):
        assert detail["error"] == "task_not_found"
        assert detail["message"] == "Task non trovato"
    else:
        assert detail == "Task non trovato"
    # il task_id NON deve comparire nel detail (no reflected input)
    assert canary_task_id not in str(detail)


# F6.1 â€” _assert_task_group breadcrumb su lookup fail


def test_assert_task_group_logs_breadcrumb_on_lookup_fail(client):
    """Quando task_service.get_task_status solleva dentro _assert_task_group,
    deve emettere logger.warning('[GESTITO] _assert_task_group: ...')
    con exc_info=True prima del raise HTTPException(404)."""
    with patch(
        "app.core.orchestrator.router.logger"
    ) as mock_logger, patch(
        "app.core.orchestrator.task_service.task_service.get_task_status",
        side_effect=RuntimeError("redis connection reset")
    ):
        # Qualsiasi endpoint che passi attraverso _assert_task_group va bene.
        # Usiamo DELETE /api/tasks/{id}/full-cleanup perche' _assert_task_group
        # non e' invocato li', uso invece DELETE /api/tasks/{id} (cancel_task)
        # che chiama _assert_task_group come primo step.
        response = client.delete("/api/tasks/task-xyz")

    # Il breadcrumb deve essere stato emesso prima del 404
    gestito_calls = [
        c for c in mock_logger.warning.call_args_list
        if "[GESTITO]" in str(c) and "_assert_task_group" in str(c)
    ]
    assert gestito_calls, (
        f"atteso logger.warning('[GESTITO] _assert_task_group: ...'), "
        f"ricevuto: {mock_logger.warning.call_args_list}"
    )
    # exc_info=True preservato (traceback visibile per diagnosi transient)
    assert any(
        call.kwargs.get("exc_info") is True for call in gestito_calls
    )
    # L'endpoint risponde comunque 404 (comportamento UX invariato)
    assert response.status_code == 404


# F6.3 â€” full-cleanup tenant check breadcrumb su ProjectService lookup


def test_full_cleanup_tenant_check_breadcrumb_on_project_lookup_fail(client, tmp_path):
    """Quando ProjectService.get_project_metadata solleva nel tenant check
    di full-cleanup, deve emettere logger.warning('[GESTITO] full-cleanup
    tenant check: ...') con exc_info=True prima di impostare _meta=None.

    User admin bypass resta attivo (l'endpoint conftest usa role='admin')
    ma il motivo del bypass ora e' visibile in log."""
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
        side_effect=RuntimeError("config DB lock wait timeout")
    ), patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path)
    ), patch(
        "app.core.results.cleanup.delete_result", return_value=True
    ), patch(
        "app.db.pg_connection.execute", return_value=SimpleNamespace(rowcount=0)
    ), patch(
        "celery.result.AsyncResult",
        return_value=SimpleNamespace(forget=lambda: None)
    ):
        response = client.delete("/api/tasks/task-xyz/full-cleanup")

    # Il breadcrumb tenant-check deve essere stato emesso
    gestito_calls = [
        c for c in mock_logger.warning.call_args_list
        if "[GESTITO]" in str(c) and "full-cleanup tenant check" in str(c)
    ]
    assert gestito_calls, (
        f"atteso warning '[GESTITO] full-cleanup tenant check: ...', "
        f"ricevuto: {mock_logger.warning.call_args_list}"
    )
    assert any(
        call.kwargs.get("exc_info") is True for call in gestito_calls
    )
    # 204 invariato: admin bypass continua (role=admin in fixture)
    assert response.status_code == 204
