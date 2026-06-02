"""Contracts for the ArcGIS feature operations boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.features.arcgis_feature_writer import ArcGISFeatureWriteService
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


def test_features_service_resolves_connection_and_delegates_existing_layer_write_to_writer() -> None:
    writer = SimpleNamespace(
        write_features_df_core=MagicMock(
            return_value={
                "success": True,
                "message": "Aggiunte 1 features su 1 totali",
                "count": 1,
                "feature_count": 1,
                "errors": [],
            }
        ),
    )
    operations = SimpleNamespace(
        write_features_df_core=MagicMock(
            side_effect=AssertionError("read/discovery boundary must not handle writes")
        ),
    )
    service = FeaturesService(client=SimpleNamespace())
    service.get_connection = MagicMock(return_value=_connection_record())
    service._writer = writer
    service._operations = operations
    df = pd.DataFrame([{"CITY": "Torino"}])

    result = service.write_features_from_df(
        connection_id="conn-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=df,
        batch_size=100,
    )

    assert result["success"] is True
    writer.write_features_df_core.assert_called_once()
    operations.write_features_df_core.assert_not_called()
    conn_info = writer.write_features_df_core.call_args.kwargs["conn_info"]
    assert isinstance(conn_info, ArcGISConnectionInfo)
    assert conn_info.username == "owner"


def test_features_service_resolves_connection_and_delegates_service_create_to_writer() -> None:
    writer = SimpleNamespace(
        create_service_from_df=MagicMock(
            return_value={
                "success": True,
                "service_url": "https://example.test/FeatureServer",
                "layer_url": "https://example.test/FeatureServer/0",
                "feature_count": 1,
                "errors": [],
            }
        ),
    )
    operations = SimpleNamespace(
        create_service_from_df=MagicMock(
            side_effect=AssertionError("read/discovery boundary must not create services")
        ),
    )
    service = FeaturesService(client=SimpleNamespace())
    service.get_connection = MagicMock(return_value=_connection_record())
    service._writer = writer
    service._operations = operations

    result = service.create_service_from_df(
        connection_id="conn-1",
        service_name="DestService",
        layer_name="DestLayer",
        df=pd.DataFrame([{"CITY": "Torino"}]),
    )

    assert result["success"] is True
    writer.create_service_from_df.assert_called_once()
    operations.create_service_from_df.assert_not_called()
    conn_info = writer.create_service_from_df.call_args.args[0]
    assert isinstance(conn_info, ArcGISConnectionInfo)
    assert conn_info.username == "owner"


def test_arcgis_feature_write_service_write_add_preserves_existing_result_shape() -> None:
    client = SimpleNamespace(
        dataframe_to_esri_features=MagicMock(
            return_value=[{"attributes": {"CITY": "Torino"}}]
        ),
        add_dataframe_features=MagicMock(
            return_value={"success": True, "added_count": 1, "errors": []}
        ),
    )
    writer = ArcGISFeatureWriteService(client)

    result = writer.write_features_df_core(
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
    client.add_dataframe_features.assert_called_once()


def test_resolve_objectids_aborts_on_partial_oid_query_failure():
    # Backend master Part II #22: se la query OBJECTID fallisce su qualche batch,
    # la risoluzione è inaffidabile → abort (no update/delete parziale silenzioso;
    # in upsert eviterebbe duplicati). NON procedere col subset risolto.
    client = MagicMock()
    client.query_oid_by_key.return_value = ({"A": 11}, ["batch 2: timeout"])
    svc = ArcGISFeatureWriteService(client)
    features = [{"attributes": {"KEY": "A"}}, {"attributes": {"KEY": "B"}}]

    with pytest.raises(ValueError, match="OBJECTID"):
        svc.resolve_objectids_for_update(
            _conn_info(), "https://example.test/FeatureServer/0", features, "KEY",
        )


def test_resolve_objectids_proceeds_when_no_query_errors():
    client = MagicMock()
    client.query_oid_by_key.return_value = ({"A": 11, "B": 12}, [])
    svc = ArcGISFeatureWriteService(client)
    features = [{"attributes": {"KEY": "A"}}, {"attributes": {"KEY": "B"}}]

    resolved = svc.resolve_objectids_for_update(
        _conn_info(), "https://example.test/FeatureServer/0", features, "KEY",
    )
    assert [f["attributes"]["OBJECTID"] for f in resolved] == [11, 12]
