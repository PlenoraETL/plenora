from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.features.routes import _mask_secrets
from tests2.helpers.builders import build_arcgis_connection


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract, pytest.mark.regression]


def _fake_materialized(path_str: str) -> MagicMock:
    """Mock MaterializedArtifact context-manager per i test di contratto.
    Il `with _resolve_input_path(...) as materialized:` deve ricevere
    qualcosa con `.path` e supporto __enter__/__exit__."""
    fake = MagicMock()
    fake.path = Path(path_str)
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = None
    return fake


def test_mask_secrets_hides_password_and_client_secret(fixed_now):
    conn = build_arcgis_connection(fixed_now)

    masked = _mask_secrets(conn)

    assert masked["password"] == "***"
    assert masked["client_secret"] == "***"
    assert conn.password == "pwd-123"
    assert conn.client_secret == "secret-123"


def test_write_features_checks_project_visibility_before_loading_project_output(client, current_user):
    service = SimpleNamespace(
        write_features=lambda **kwargs: {"success": True, "input_path": kwargs["input_path"]},
    )

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible:
        response = client.post(
            "/api/features/write",
            json={
                "connection_id": "conn-1",
                "layer_url": "https://example.test/FeatureServer/0",
                "project_id": "proj-1",
            },
        )

    assert response.status_code == 200
    assert Path(response.json()["input_path"]) == Path("/tmp/result.parquet")
    assert_project_visible.assert_called_once_with("proj-1", current_user)


def test_write_features_skips_project_visibility_for_explicit_input_path(client):
    service = SimpleNamespace(
        write_features=lambda **kwargs: {"success": True, "input_path": kwargs["input_path"]},
    )

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible:
        response = client.post(
            "/api/features/write",
            json={
                "connection_id": "conn-1",
                "layer_url": "https://example.test/FeatureServer/0",
                "input_path": "/tmp/result.parquet",
            },
        )

    assert response.status_code == 200
    assert_project_visible.assert_not_called()


def test_create_service_checks_project_visibility_before_loading_project_output(client, current_user):
    service = SimpleNamespace(
        create_service=lambda **kwargs: {"success": True, "input_path": kwargs["input_path"]},
    )

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible:
        response = client.post(
            "/api/features/create-service",
            json={
                "connection_id": "conn-1",
                "service_name": "svc",
                "project_id": "proj-1",
            },
        )

    assert response.status_code == 200
    assert Path(response.json()["input_path"]) == Path("/tmp/result.parquet")
    assert_project_visible.assert_called_once_with("proj-1", current_user)
