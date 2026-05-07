"""Contract test R-25 PR-B3 — endpoint dual-auth intermediate_mode.

Endpoint coperti:
  - POST /api/projects/{project_id}/execute
    (orchestrator/router.py:execute_project_pipeline)
  - POST /api/arricchisci/{project_id}/enrich-async
    (arricchisci/router.py:execute_enrichment_async)

Invarianti:
  1. Nessun param `intermediate_mode` -> behavior identico al pre-PR
     (compat: admin/viewer accedono).
  2. `intermediate_mode=full_pg` con superadmin -> 200 + task service
     riceve `intermediate_mode_override="full_pg"`.
  3. `intermediate_mode=full_pg` con admin/viewer/esecutore -> 403
     ("intermediate_mode override richiede ruolo superadmin").
  4. `intermediate_mode=INVALID_MODE` (qualsiasi ruolo) -> 422 da
     Pydantic Literal validation (boundary HTTP, prima dell'auth).
  5. Esecutori (require_not_esecutore) restano bloccati dal gate
     base, anche senza param.

Test usano FastAPI TestClient con dependency override.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import require_not_esecutore
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.security, pytest.mark.contract]


# Fixtures: TestClient orchestrator

def _build_orchestrator_client(role: str = "admin"):
    """Monta un FastAPI minimale con il router orchestrator + dependency
    override per current_user. Il param `role` controlla il ruolo
    iniettato (admin / superadmin / esecutore / viewer)."""
    from app.core.orchestrator.router import (
        pipeline_router, init_orchestrator,
    )

    # Init orchestrator service mock per `require_service` non sollevi.
    init_orchestrator(MagicMock())

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: build_current_user(role=role)
    app.include_router(pipeline_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _build_arricchisci_client(role: str = "admin"):
    from app.core.arricchisci.router import (
        enrich_router, init_arricchisci,
    )

    # Init dependencies of arricchisci router (mock services).
    project_service = MagicMock()
    project_service.get_project_metadata = MagicMock(return_value=MagicMock(
        project_type="arricchisci",
        group_id="grp-1",
    ))
    project_service.get_project_canvas = MagicMock(return_value=MagicMock(
        nodes=[], edges=[],
        model_dump=lambda: {"nodes": [], "edges": []},
    ))
    api_connection_service = MagicMock()
    init_arricchisci(project_service, api_connection_service)

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: build_current_user(role=role)
    app.include_router(enrich_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


# A. orchestrator.execute_project_pipeline

class TestOrchestratorExecuteEndpoint:
    """POST /api/projects/{id}/execute con e senza intermediate_mode."""

    def test_no_param_admin_passes_compat(self):
        """Sanity compat: senza param, admin esegue come pre-PR."""
        client = _build_orchestrator_client(role="admin")

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task") as create_task:
            mock_task_info = MagicMock()
            mock_task_info.to_dict.return_value = {
                "task_id": "t-1", "status": "pending",
            }
            create_task.return_value = mock_task_info

            response = client.post("/api/projects/proj-1/execute")

        assert response.status_code == 200
        # `intermediate_mode_override` deve essere None (default).
        create_task.assert_called_once()
        kwargs = create_task.call_args.kwargs
        assert kwargs.get("intermediate_mode_override") is None

    def test_param_full_pg_superadmin_propaga(self):
        """Superadmin con override valido -> task riceve kwarg."""
        client = _build_orchestrator_client(role="superadmin")

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task") as create_task:
            mock_task_info = MagicMock()
            mock_task_info.to_dict.return_value = {
                "task_id": "t-1", "status": "pending",
            }
            create_task.return_value = mock_task_info

            response = client.post(
                "/api/projects/proj-1/execute?intermediate_mode=full_pg",
            )

        assert response.status_code == 200
        kwargs = create_task.call_args.kwargs
        assert kwargs.get("intermediate_mode_override") == "full_pg"

    @pytest.mark.parametrize("role", ["admin", "viewer"])
    def test_param_full_pg_non_superadmin_403(self, role):
        """Admin/viewer con override -> 403 esplicito."""
        client = _build_orchestrator_client(role=role)

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task") as create_task:
            response = client.post(
                "/api/projects/proj-1/execute?intermediate_mode=full_pg",
            )

        assert response.status_code == 403
        assert "superadmin" in response.json().get("detail", "").lower()
        # Il task NON deve essere creato (auth gate prima del dispatch).
        create_task.assert_not_called()

    def test_param_invalid_mode_422_pydantic(self):
        """Mode invalido -> 422 (Pydantic Literal validation a boundary HTTP)."""
        client = _build_orchestrator_client(role="superadmin")

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task") as create_task:
            response = client.post(
                "/api/projects/proj-1/execute?intermediate_mode=NOT_A_MODE",
            )

        assert response.status_code == 422
        # Errore Pydantic deve menzionare i valori ammessi.
        body = response.json()
        # FastAPI ritorna detail con lista errori di validation.
        # Verifichiamo che almeno uno dei valori ammessi appaia.
        body_str = str(body).lower()
        assert any(m in body_str for m in [
            "metadata_only", "preview_only", "full_pg", "full_artifact",
        ])
        create_task.assert_not_called()

    def test_param_invalid_mode_non_superadmin_still_422(self):
        """Validazione boundary HTTP precede l'auth gate: anche un admin
        riceve 422 (non 403) per mode invalido."""
        client = _build_orchestrator_client(role="admin")

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task"):
            response = client.post(
                "/api/projects/proj-1/execute?intermediate_mode=NOT_A_MODE",
            )

        # Pydantic gira PRIMA dell'handler -> 422 anche se l'utente
        # non e' superadmin.
        assert response.status_code == 422

    @pytest.mark.parametrize("mode", [
        "metadata_only", "preview_only", "full_pg", "full_artifact",
    ])
    def test_tutti_4_mode_validi_per_superadmin(self, mode):
        """Tutti e 4 i mode supportati passano per superadmin."""
        client = _build_orchestrator_client(role="superadmin")

        with patch("app.core.orchestrator.router._assert_project_group"), \
             patch("app.services.task_service.task_service.create_pipeline_task") as create_task:
            mock_task_info = MagicMock()
            mock_task_info.to_dict.return_value = {
                "task_id": "t-1", "status": "pending",
            }
            create_task.return_value = mock_task_info

            response = client.post(
                f"/api/projects/proj-1/execute?intermediate_mode={mode}",
            )

        assert response.status_code == 200, f"mode={mode} respinto"
        kwargs = create_task.call_args.kwargs
        assert kwargs.get("intermediate_mode_override") == mode


# B. arricchisci.execute_enrichment_async

class TestArricchisciEnrichAsyncEndpoint:
    """POST /api/arricchisci/{id}/enrich-async con dual-auth."""

    def test_no_param_admin_passes_compat(self):
        client = _build_arricchisci_client(role="admin")

        with patch("app.core.arricchisci.router._check_project_ownership"), \
             patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
            mock_task_info = MagicMock()
            mock_task_info.task_id = "t-1"
            mock_task_info.status = MagicMock()
            mock_task_info.status.value = "pending"
            create_task.return_value = mock_task_info

            response = client.post("/api/projects/proj-1/enrich-async")

        assert response.status_code == 200
        kwargs = create_task.call_args.kwargs
        assert kwargs.get("intermediate_mode_override") is None

    def test_param_full_pg_superadmin_propaga(self):
        client = _build_arricchisci_client(role="superadmin")

        with patch("app.core.arricchisci.router._check_project_ownership"), \
             patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
            mock_task_info = MagicMock()
            mock_task_info.task_id = "t-1"
            mock_task_info.status = MagicMock()
            mock_task_info.status.value = "pending"
            create_task.return_value = mock_task_info

            response = client.post(
                "/api/projects/proj-1/enrich-async?intermediate_mode=full_pg",
            )

        assert response.status_code == 200
        kwargs = create_task.call_args.kwargs
        assert kwargs.get("intermediate_mode_override") == "full_pg"

    def test_param_full_pg_admin_403(self):
        client = _build_arricchisci_client(role="admin")

        with patch("app.core.arricchisci.router._check_project_ownership"), \
             patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
            response = client.post(
                "/api/projects/proj-1/enrich-async?intermediate_mode=full_pg",
            )

        assert response.status_code == 403
        assert "superadmin" in response.json().get("detail", "").lower()
        create_task.assert_not_called()

    def test_param_invalid_mode_422(self):
        client = _build_arricchisci_client(role="superadmin")

        with patch("app.core.arricchisci.router._check_project_ownership"), \
             patch("app.services.task_service.task_service.create_enrichment_task") as create_task:
            response = client.post(
                "/api/projects/proj-1/enrich-async?intermediate_mode=NOT_A_MODE",
            )

        assert response.status_code == 422
        create_task.assert_not_called()
