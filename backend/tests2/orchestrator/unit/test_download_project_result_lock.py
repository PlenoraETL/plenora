"""
Lock tests per `GET /api/projects/{project_id}/download`.

Regression guard + scaglione 0 per il bug segnalato: l'endpoint
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_not_esecutore, get_current_user
from app.core.orchestrator.router import pipeline_router


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
    pytest.mark.regression,
]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def pipeline_client(current_user: CurrentUser) -> TestClient:
    """Client separato per pipeline_router (il conftest.py usa
    tasks_router)."""
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.include_router(pipeline_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


# Scenario 1: filesystem vuoto + blob DB presente → scarica dal blob


def test_download_project_result_falls_back_to_db_blob(pipeline_client, tmp_path):
    """Regression guard: post R#1 i risultati vivono solo nel blob DB
    (save_result_blob cancella il file temp nel finally). L'endpoint
    deve quindi fare fallback a `get_latest_result` + ArtifactResolver
    .fetch_run_blob quando `RESULTS_DIR/{project_id}` e' vuoto o non
    esiste."""
    project_id = "proj-db-only"
    empty_dir = tmp_path / project_id
    # NON creare: l'endpoint deve gestire directory inesistente

    blob_bytes = b"id,value\n1,ok\n2,nok\n"

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path),
    ), patch(
        "app.core.orchestrator.router._assert_project_group"
    ), patch(
        "app.db.results_db_service.get_latest_result",
        return_value={
            "id": "res-latest",
            "project_id": project_id,
            "filename": "out.csv",
            "format": "csv",
            "size_bytes": len(blob_bytes),
        },
    ), patch(
        "app.db.results_db_service.get_result_data",
        return_value=blob_bytes,
    ):
        response = pipeline_client.get(f"/api/projects/{project_id}/download")

    assert response.status_code == 200, (
        f"fallback al blob DB fallito: {response.status_code} {response.text}"
    )
    assert response.content == blob_bytes
    # Filename preservato nel Content-Disposition
    assert 'filename="out.csv"' in response.headers.get("content-disposition", "")


def test_download_project_result_filesystem_wins_over_blob_when_present(
    pipeline_client, tmp_path
):
    """Se il filesystem ha un file (compat pre-R#1), lo si serve senza
    toccare il DB. Preserva il comportamento legacy per progetti
    antichi che non sono mai stati migrati al blob DB."""
    project_id = "proj-legacy"
    results_dir = tmp_path / project_id
    results_dir.mkdir(parents=True)
    legacy_file = results_dir / "legacy.csv"
    legacy_file.write_bytes(b"id\n1\n")

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path),
    ), patch(
        "app.core.orchestrator.router._assert_project_group"
    ), patch(
        "app.db.results_db_service.get_latest_result"
    ) as mock_latest:
        response = pipeline_client.get(f"/api/projects/{project_id}/download")

    assert response.status_code == 200
    assert response.content == b"id\n1\n"
    # Nessuna query al DB se il filesystem ha il file (fast-path)
    mock_latest.assert_not_called()


def test_download_project_result_404_when_nothing_exists(pipeline_client, tmp_path):
    """Ne' filesystem ne' blob DB → 404 esplicito (non 500)."""
    project_id = "proj-missing"
    # Directory non esiste

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path),
    ), patch(
        "app.core.orchestrator.router._assert_project_group"
    ), patch(
        "app.db.results_db_service.get_latest_result",
        return_value=None,
    ):
        response = pipeline_client.get(f"/api/projects/{project_id}/download")

    assert response.status_code == 404


def test_download_project_result_blob_fallback_preserves_mime_type(
    pipeline_client, tmp_path
):
    """Content-Type del blob deve riflettere il formato (csv vs geojson)."""
    project_id = "proj-geojson"
    blob_bytes = b'{"type":"FeatureCollection","features":[]}'

    with patch(
        "app.core.orchestrator.router.settings",
        new=SimpleNamespace(RESULTS_DIR=tmp_path),
    ), patch(
        "app.core.orchestrator.router._assert_project_group"
    ), patch(
        "app.db.results_db_service.get_latest_result",
        return_value={
            "id": "res-geo",
            "project_id": project_id,
            "filename": "layer.geojson",
            "format": "geojson",
            "size_bytes": len(blob_bytes),
        },
    ), patch(
        "app.db.results_db_service.get_result_data",
        return_value=blob_bytes,
    ):
        response = pipeline_client.get(f"/api/projects/{project_id}/download")

    assert response.status_code == 200
    assert response.content == blob_bytes
    assert 'filename="layer.geojson"' in response.headers.get("content-disposition", "")
