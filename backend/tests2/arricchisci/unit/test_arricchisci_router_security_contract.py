from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser, require_not_esecutore
from app.core.arricchisci.router import enrichment_router


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.security, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def client(current_user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(enrichment_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_validate_checks_project_and_file_source_visibility(client, current_user):
    api_conn = {"id": "api-1", "group_id": "grp-1", "parameters": []}

    with patch("app.core.arricchisci.api_connector.api_connection_service.get_api_connection", return_value=api_conn), \
         patch("app.core.arricchisci.router.assert_resource_visible"), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible, \
         patch("app.shared.authz_helpers.assert_file_source_access") as assert_file_source_access, \
         patch("app.core.arricchisci.router._load_request_data", return_value=None):
        response = client.post(
            "/api/enrichment/validate",
            json={
                "api_id": "api-1",
                "project_id": "proj-1",
                "file_source_id": "fs-1",
                "param_mapping": {},
            },
        )

    assert response.status_code == 200
    assert_project_visible.assert_called_once_with("proj-1", current_user)
    assert_file_source_access.assert_called_once_with("fs-1", current_user)


def test_estimate_checks_project_and_file_source_visibility(client, current_user):
    api_conn = {"id": "api-1", "group_id": "grp-1", "options": {"rate_limit": 5}}

    with patch("app.core.arricchisci.api_connector.api_connection_service.get_api_connection", return_value=api_conn), \
         patch("app.core.arricchisci.router.assert_resource_visible"), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible, \
         patch("app.shared.authz_helpers.assert_file_source_access") as assert_file_source_access, \
         patch("app.core.arricchisci.router._load_request_data", return_value=None):
        response = client.post(
            "/api/enrichment/estimate",
            json={
                "api_id": "api-1",
                "project_id": "proj-1",
                "file_source_id": "fs-1",
                "param_mapping": {},
            },
        )

    assert response.status_code == 200
    assert_project_visible.assert_called_once_with("proj-1", current_user)
    assert_file_source_access.assert_called_once_with("fs-1", current_user)


def test_validate_passes_through_source_loader_with_user_group_id(client, current_user):
    """Post-Refactor #3 cleanup: il router non passa piu' per il
    wrapper helper `load_data` (rimosso) ma chiama direttamente
    `SourceLoader.load(SourceRef(...), expected_group_id=...)` via
    l'inline helper `_load_request_data`. Verifichiamo che il
    SourceLoader venga chiamato col group_id del current_user."""
    api_conn = {"id": "api-1", "group_id": "grp-1", "parameters": []}

    with patch("app.core.arricchisci.api_connector.api_connection_service.get_api_connection", return_value=api_conn), \
         patch("app.core.arricchisci.router.assert_resource_visible"), \
         patch("app.shared.authz_helpers.assert_project_visible"), \
         patch("app.shared.authz_helpers.assert_file_source_access"), \
         patch("app.core.arricchisci.router.SourceLoader") as loader_cls:
        loader_cls.return_value.load.return_value = None
        client.post(
            "/api/enrichment/validate",
            json={
                "api_id": "api-1",
                "project_id": "proj-1",
                "file_source_id": "fs-1",
                "param_mapping": {},
            },
        )

    loader_cls.return_value.load.assert_called_once()
    _, kwargs = loader_cls.return_value.load.call_args
    assert kwargs.get("expected_group_id") == "grp-1"


def test_estimate_passes_through_source_loader_with_user_group_id(client, current_user):
    api_conn = {"id": "api-1", "group_id": "grp-1", "options": {"rate_limit": 5}}

    with patch("app.core.arricchisci.api_connector.api_connection_service.get_api_connection", return_value=api_conn), \
         patch("app.core.arricchisci.router.assert_resource_visible"), \
         patch("app.shared.authz_helpers.assert_project_visible"), \
         patch("app.shared.authz_helpers.assert_file_source_access"), \
         patch("app.core.arricchisci.router.SourceLoader") as loader_cls:
        loader_cls.return_value.load.return_value = None
        client.post(
            "/api/enrichment/estimate",
            json={
                "api_id": "api-1",
                "project_id": "proj-1",
                "file_source_id": "fs-1",
                "param_mapping": {},
            },
        )

    loader_cls.return_value.load.assert_called_once()
    _, kwargs = loader_cls.return_value.load.call_args
    assert kwargs.get("expected_group_id") == "grp-1"


def test_load_request_data_prefers_file_source_over_project_id():
    """Quando il caller specifica entrambi file_source_id e project_id,
    file_source_id vince — niente fallback silenzioso al progetto.
    Era la regola del wrapper helper `load_data` rimosso, ora
    enforced inline da `_load_request_data`."""
    from app.core.arricchisci.router import _load_request_data
    from app.core.auth.auth import CurrentUser

    request = SimpleNamespace(file_source_id="fs-1", project_id="proj-1")
    user = CurrentUser(user_id="u", username="t", group_id="grp-1", role="admin")

    with patch("app.core.arricchisci.router.SourceLoader") as loader_cls:
        loader_cls.return_value.load.return_value = "df-sentinel"
        result = _load_request_data(request, user)

    assert result == "df-sentinel"
    loader_cls.return_value.load.assert_called_once()
    args, kwargs = loader_cls.return_value.load.call_args
    ref = args[0]
    # Deve essere SourceRef(file_source_id="fs-1"), NON project=...
    assert ref.file_source_id == "fs-1"
    assert ref.project is None


def test_load_request_data_returns_none_when_no_source_specified():
    """Niente file_source_id ne' project_id → None, niente errore."""
    from app.core.arricchisci.router import _load_request_data
    from app.core.auth.auth import CurrentUser

    request = SimpleNamespace(file_source_id=None, project_id=None)
    user = CurrentUser(user_id="u", username="t", group_id="grp-1", role="admin")

    with patch("app.core.arricchisci.router.SourceLoader") as loader_cls:
        result = _load_request_data(request, user)

    assert result is None
    loader_cls.return_value.load.assert_not_called()


# =============================================================================
# Regression: _load_request_data propaga is_superadmin a SourceLoader
# =============================================================================


def test_load_request_data_propagates_is_superadmin_for_admin_user():
    """Regression guard: `_load_request_data` deve passare
    `is_superadmin=False` per utenti admin/non-superadmin.

    Senza, il SourceLoader applica un check stretto group_id e blocca
    legittimi accessi a progetti shared (per file_sources) o cross-tenant
    (per superadmin che la UI gestisce diversamente)."""
    from app.core.arricchisci.router import _load_request_data

    request = SimpleNamespace(file_source_id=None, project_id="proj-1")
    user_admin = CurrentUser(
        user_id="u-1",
        username="admin",
        group_id="grp-1",
        role="admin",
    )

    captured = {}

    def _capture(self, ref, *, expected_group_id, is_superadmin=False):
        captured["expected_group_id"] = expected_group_id
        captured["is_superadmin"] = is_superadmin
        return None  # short-circuit

    with patch(
        "app.core.arricchisci.router.SourceLoader.load",
        _capture,
    ):
        _load_request_data(request, user_admin)

    assert captured["expected_group_id"] == "grp-1"
    assert captured["is_superadmin"] is False


def test_load_request_data_propagates_is_superadmin_for_superadmin_user():
    """Regression guard del bug originale: superadmin che lancia
    validate su un progetto di un altro gruppo deve poter caricare i
    dati. Senza `is_superadmin=True` propagato al SourceLoader, il
    check stretto cross-tenant del resolver fail-closed → 400 Bad
    Request → UI mostra 'Errori di validazione'."""
    from app.core.arricchisci.router import _load_request_data

    request = SimpleNamespace(file_source_id=None, project_id="proj-cross")
    user_super = CurrentUser(
        user_id="u-99",
        username="superadmin",
        group_id="default",
        role="superadmin",
    )

    captured = {}

    def _capture(self, ref, *, expected_group_id, is_superadmin=False):
        captured["expected_group_id"] = expected_group_id
        captured["is_superadmin"] = is_superadmin
        return None

    with patch(
        "app.core.arricchisci.router.SourceLoader.load",
        _capture,
    ):
        _load_request_data(request, user_super)

    assert captured["expected_group_id"] == "default"
    assert captured["is_superadmin"] is True
