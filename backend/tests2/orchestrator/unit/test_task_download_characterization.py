"""
Tests the task download characterization behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.artifacts.types import ArtifactResolutionError, RunBlob


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract, pytest.mark.regression]


def test_download_task_result_sanitizes_filename_headers(client):
    task_info = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        result={"project_id": "proj-1", "result_id": "res-1"},
    )

    with patch("app.core.orchestrator.router._assert_task_group"), \
         patch("app.core.orchestrator.task_service.task_service.get_task_status", return_value=task_info), \
         patch(
            "app.core.artifacts.ArtifactResolver.fetch_run_blob",
            return_value=RunBlob(
                data=b"id\n1\n",
                filename='evil\\r\\nname.csv',
                format="csv",
                project_id="proj-1",
                result_id="res-1",
            ),
         ):
        response = client.get("/api/tasks/task-1/download")

    assert response.status_code == 200
    assert "\r" not in response.headers["content-disposition"]
    assert "\n" not in response.headers["content-disposition"]


def test_download_task_result_rejects_project_result_mismatch(client):
    task_info = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        result={"project_id": "proj-1", "result_id": "res-1"},
    )

    with patch("app.core.orchestrator.router._assert_task_group"), \
         patch("app.core.orchestrator.task_service.task_service.get_task_status", return_value=task_info), \
         patch(
            "app.core.artifacts.ArtifactResolver.fetch_run_blob",
            side_effect=ArtifactResolutionError("mismatch"),
         ):
        response = client.get("/api/tasks/task-1/download")

    assert response.status_code == 404
    assert "mismatch" in response.json()["detail"] or "Risultato del task non trovato" in response.json()["detail"]


def test_download_task_result_streams_body_with_content_length(client):
    """Regression guard: il download deve usare StreamingResponse con
    `Content-Length` esplicito (per mostrare progress bar al client)
    e ritornare il body completo. Prima del fix usava
    `Response(content=blob.data)` che caricava tutto in memoria PRIMA
    di iniziare il send → alta latenza percepita su blob grandi."""
    payload = b"id,value\n" + b"1,ok\n" * 1000  # ~6 KB
    task_info = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        result={"project_id": "proj-1", "result_id": "res-1"},
    )

    with patch("app.core.orchestrator.router._assert_task_group"), \
         patch(
            "app.core.orchestrator.task_service.task_service.get_task_status",
            return_value=task_info,
         ), \
         patch(
            "app.core.artifacts.ArtifactResolver.fetch_run_blob",
            return_value=RunBlob(
                data=payload,
                filename="out.csv",
                format="csv",
                project_id="proj-1",
                result_id="res-1",
            ),
         ):
        response = client.get("/api/tasks/task-1/download")

    assert response.status_code == 200
    # Content-Length deve essere settato esplicito (StreamingResponse con
    # generator di solito non lo fa — noi glielo passiamo via headers)
    assert response.headers.get("content-length") == str(len(payload))
    # Il body completo deve arrivare
    assert response.content == payload
    # Filename header preservato
    assert 'filename="out.csv"' in response.headers["content-disposition"]
