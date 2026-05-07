"""[phase-2-err][batch9] C6 — shape 500 `project_cleanup_partial`.

Invariante blindato dal fix C6:
    Quando `ProjectService.delete_project` solleva `ProjectCleanupPartialError`
    (C5/C6), il router DELETE /api/projects/{id} mappa a HTTP 500 con shape:
        {
            "detail": {
                "error": "project_cleanup_partial",
                "message": <str human-readable italiano>,
                "project_id": <str>,
                "failed_steps": {
                    "critical": [<str>, ...],   # subset di {intermediate_runs, results_blobs}
                    "derived":  [<str>, ...]    # subset di {schedules_orphan, comparison_keys}
                },
                "project_preserved": true
            }
        }

Il test protegge contro:
  - regressioni alla shape (campi rinominati, annidamenti diversi)
  - reintroduzioni del 500 generico FastAPI (`{"detail": "Internal Server Error"}`)
  - ritorno di status code diverso da 500
  - dimenticanza del flag `project_preserved: true` (telegramma dell'invariante
    fail-closed introdotto in C5)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_project_owner
from app.core.projects import router as projects_router_module
from app.core.projects.router import router as projects_router
from app.shared.exceptions import ProjectCleanupPartialError


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def client(current_user: CurrentUser, monkeypatch) -> TestClient:
    """TestClient con `_project_service` mock. Le singole test settano
    `mock_service.delete_project.side_effect = ProjectCleanupPartialError(...)`."""
    mock_service = MagicMock()
    monkeypatch.setattr(
        projects_router_module, "_project_service", mock_service,
    )
    app = FastAPI()
    app.dependency_overrides[require_project_owner] = lambda: current_user
    app.include_router(projects_router, prefix="/api")
    app.state.mock_service = mock_service
    return TestClient(app, raise_server_exceptions=False)


# Shape 500 canonica — campi top-level nel detail


def test_delete_project_500_shape_on_cleanup_partial_critical(client):
    """Quando il service solleva ProjectCleanupPartialError con solo critical,
    il router risponde 500 con detail strutturato + `project_preserved:true`."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectCleanupPartialError(
        project_id="p-target",
        critical_failed=["intermediate_runs"],
        derived_failed=[],
    )

    response = client.delete("/api/projects/p-target")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert isinstance(detail, dict), (
        f"detail deve essere oggetto strutturato, non stringa: {detail}"
    )
    # Token canonico macchina-leggibile
    assert detail["error"] == "project_cleanup_partial"
    # Echo project_id
    assert detail["project_id"] == "p-target"
    # Human-readable message in italiano contiene project_id + step critico
    assert "p-target" in detail["message"]
    assert "intermediate_runs" in detail["message"]
    # Invariante fail-closed esposto al caller
    assert detail["project_preserved"] is True


def test_delete_project_500_shape_includes_failed_steps_split(client):
    """`failed_steps` deve essere un dict con chiavi `critical` e `derived`
    (liste), non una lista piatta. Split preservato dal livello service."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectCleanupPartialError(
        project_id="p-target",
        critical_failed=["intermediate_runs", "results_blobs"],
        derived_failed=["schedules_orphan"],
    )

    response = client.delete("/api/projects/p-target")

    assert response.status_code == 500
    detail = response.json()["detail"]
    # Struttura annidata failed_steps = {critical: [...], derived: [...]}
    assert "failed_steps" in detail
    failed_steps = detail["failed_steps"]
    assert isinstance(failed_steps, dict)
    assert failed_steps["critical"] == ["intermediate_runs", "results_blobs"]
    assert failed_steps["derived"] == ["schedules_orphan"]


def test_delete_project_500_shape_project_preserved_true(client):
    """Il campo `project_preserved` deve essere SEMPRE True — telegramma
    dell'invariante fail-closed introdotto in C5 (config row non rimossa)."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectCleanupPartialError(
        project_id="p-x",
        critical_failed=["results_blobs"],
        derived_failed=[],
    )

    response = client.delete("/api/projects/p-x")

    detail = response.json()["detail"]
    assert detail["project_preserved"] is True
    # Non un altro booleano ambiguo ("true" string, 1, ecc.)
    assert detail["project_preserved"] is not False


def test_delete_project_500_message_human_readable(client):
    """Il campo `message` deve essere una stringa human-readable in italiano
    contenente project_id e riferimento a step critici. Il FE lo mostra
    direttamente nel toast → deve essere parseable da umano senza tool."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectCleanupPartialError(
        project_id="p-humans",
        critical_failed=["intermediate_runs", "results_blobs"],
        derived_failed=["comparison_keys"],
    )

    response = client.delete("/api/projects/p-humans")

    detail = response.json()["detail"]
    msg = detail["message"]
    assert isinstance(msg, str)
    # Copertura contenuto: project_id, step critici
    assert "p-humans" in msg
    # Messaggio italiano — verifica presenza di token chiave in italiano
    assert "Cleanup parziale" in msg or "cancellato" in msg
    # Step critici riportati (in qualche forma testuale)
    assert "intermediate_runs" in msg or "results_blobs" in msg


# Anti-regressione: 500 generico FastAPI non deve mai scattare


def test_delete_project_does_not_fall_back_to_generic_500(client):
    """Se mai si dimenticasse il catch nel router, FastAPI risponderebbe
    con `{"detail": "Internal Server Error"}` (stringa, non dict). Questo
    test blinda che la shape strutturata sia sempre presente."""
    mock_service = client.app.state.mock_service
    mock_service.delete_project.side_effect = ProjectCleanupPartialError(
        project_id="p-x",
        critical_failed=["intermediate_runs"],
    )

    response = client.delete("/api/projects/p-x")

    assert response.status_code == 500
    detail = response.json()["detail"]
    # Anti-pattern: detail stringa "Internal Server Error"
    assert detail != "Internal Server Error"
    assert not isinstance(detail, str)
