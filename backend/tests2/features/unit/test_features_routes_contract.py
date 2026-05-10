"""
Tests the features routes behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

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
    write_features_mock = MagicMock(return_value={
        "success": True, "message": "Aggiunte 0 features", "count": 0, "errors": [],
    })
    service = SimpleNamespace(write_features=write_features_mock)

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.core.features.routes.assert_project_visible") as assert_project_visible:
        response = client.post(
            "/api/features/write",
            json={
                "connection_id": "conn-1",
                "layer_url": "https://example.test/FeatureServer/0",
                "project_id": "proj-1",
            },
        )

    assert response.status_code == 200
    assert Path(write_features_mock.call_args.kwargs["input_path"]) == Path("/tmp/result.parquet")
    assert_project_visible.assert_called_once_with("proj-1", current_user)


def test_write_features_skips_project_visibility_for_explicit_input_path(client):
    write_features_mock = MagicMock(return_value={
        "success": True, "message": "Aggiunte 0 features", "count": 0, "errors": [],
    })
    service = SimpleNamespace(write_features=write_features_mock)

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.core.features.routes.assert_project_visible") as assert_project_visible:
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
    write_features_mock.assert_called_once()
    assert write_features_mock.call_args.kwargs["operation"] == "add"
    assert write_features_mock.call_args.kwargs["match_column"] == ""
    assert write_features_mock.call_args.kwargs["batch_size"] is None


def test_write_features_accepts_arcgis_writer_knobs(client):
    write_features_mock = MagicMock(return_value={
        "success": True,
        "message": "Upsert: 1 aggiornate, 1 inserite su 2 totali",
        "count": 2,
        "errors": [],
    })
    service = SimpleNamespace(write_features=write_features_mock)

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")):
        response = client.post(
            "/api/features/write",
            json={
                "connection_id": "conn-1",
                "layer_url": "https://example.test/FeatureServer/0",
                "operation": "add",
                "upsert": True,
                "input_path": "/tmp/result.parquet",
                "match_column": "external_id",
                "batch_size": 250,
                "column_mappings": [
                    {"source": "external_id", "dest": "EXT_ID"},
                ],
            },
        )

    assert response.status_code == 200
    write_features_mock.assert_called_once()
    assert write_features_mock.call_args.kwargs["operation"] == "upsert"
    assert write_features_mock.call_args.kwargs["match_column"] == "external_id"
    assert write_features_mock.call_args.kwargs["batch_size"] == 250
    assert write_features_mock.call_args.kwargs["column_mappings"] == [
        {"source": "external_id", "dest": "EXT_ID"},
    ]


def test_create_service_checks_project_visibility_before_loading_project_output(client, current_user):
    create_service_mock = MagicMock(return_value={
        "success": True, "service_url": "https://example.test/svc",
        "layer_url": "https://example.test/svc/0", "item_id": "item-1",
        "feature_count": 0, "errors": [],
    })
    service = SimpleNamespace(create_service=create_service_mock)

    with patch("app.core.features.routes._check_arcgis_access"), \
         patch("app.core.features.routes.get_service", return_value=service), \
         patch("app.core.features.routes._resolve_input_path", return_value=_fake_materialized("/tmp/result.parquet")), \
         patch("app.core.features.routes.assert_project_visible") as assert_project_visible:
        response = client.post(
            "/api/features/create-service",
            json={
                "connection_id": "conn-1",
                "service_name": "svc",
                "project_id": "proj-1",
            },
        )

    assert response.status_code == 200
    assert Path(create_service_mock.call_args.kwargs["input_path"]) == Path("/tmp/result.parquet")
    assert_project_visible.assert_called_once_with("proj-1", current_user)
