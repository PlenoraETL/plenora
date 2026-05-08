"""Contracts for the ArcGIS feature operations boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.features.arcgis_feature_operations import ArcGISFeatureOperations
from app.core.features.features_service import FeaturesService
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


def _conn_info() -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url="https://example.test/arcgis",
        server_type="arcgis_online",
        auth_type="token",
        username="owner",
        password="secret",
    )


def _connection_record() -> SimpleNamespace:
    return SimpleNamespace(
        server_url="https://example.test/arcgis",
        server_type="arcgis_online",
        auth_type="token",
        username="owner",
        password="secret",
        client_id="",
        client_secret="",
        token_url="",
    )


def test_features_service_resolves_connection_and_delegates_service_browse() -> None:
    operations = SimpleNamespace(
        list_services=MagicMock(return_value=[{"name": "Sites"}]),
    )
    service = FeaturesService(client=SimpleNamespace())
    service.get_connection = MagicMock(return_value=_connection_record())
    service._operations = operations

    result = service.browse_services("conn-1")

    assert result == [{"name": "Sites"}]
    operations.list_services.assert_called_once()
    conn_info = operations.list_services.call_args.args[0]
    assert isinstance(conn_info, ArcGISConnectionInfo)
    assert conn_info.username == "owner"


def test_arcgis_feature_operations_write_add_preserves_existing_result_shape() -> None:
    client = SimpleNamespace(
        dataframe_to_esri_features=MagicMock(
            return_value=[{"attributes": {"CITY": "Torino"}}]
        ),
        add_features=MagicMock(
            return_value={"success": True, "added_count": 1, "errors": []}
        ),
    )
    operations = ArcGISFeatureOperations(client)

    result = operations.write_features_df_core(
        conn_info=_conn_info(),
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=pd.DataFrame([{"CITY": "Torino"}]),
        geometry_column="geometry",
        source_srid=None,
        batch_size=100,
        match_column="",
        column_mappings=None,
    )

    assert result == {
        "success": True,
        "message": "Aggiunte 1 features su 1 totali",
        "count": 1,
        "feature_count": 1,
        "errors": [],
    }
    client.add_features.assert_called_once()
