"""Contract tests for the canonical ArcGIS Feature Provider protocols."""

from __future__ import annotations

import inspect

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.shared.connections.arcgis_provider import (
    ArcGISDiscoveryProviderProtocol,
    ArcGISProviderProtocol,
    ArcGISReadProviderProtocol,
    ArcGISServiceAdminProviderProtocol,
    ArcGISWriteProviderProtocol,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _parameter_names(protocol: type, method_name: str) -> list[str]:
    return list(inspect.signature(getattr(protocol, method_name)).parameters)


def test_arcgis_provider_protocol_exports_read_write_capabilities() -> None:
    required_methods = {
        "list_services",
        "list_folders",
        "list_layers",
        "get_layer_schema",
        "query_features",
        "count_features_where",
        "create_service",
        "add_layer_to_service",
        "add_features",
        "update_features",
        "delete_features",
        "update_renderer",
    }

    for method_name in required_methods:
        assert hasattr(ArcGISProviderProtocol, method_name)


def test_arcgis_provider_capability_protocols_have_expected_method_sets() -> None:
    assert {"list_services", "list_folders", "list_layers"} <= set(
        dir(ArcGISDiscoveryProviderProtocol)
    )
    assert {"get_layer_schema", "query_features", "count_features_where"} <= set(
        dir(ArcGISReadProviderProtocol)
    )
    assert {"add_features", "update_features", "delete_features"} <= set(
        dir(ArcGISWriteProviderProtocol)
    )
    assert {"create_service", "add_layer_to_service", "update_renderer"} <= set(
        dir(ArcGISServiceAdminProviderProtocol)
    )


def test_arcgis_provider_protocol_signature_shapes_match_client_boundary() -> None:
    expected_parameters = {
        "list_services": ["self", "conn", "folder", "_depth", "_max_depth"],
        "list_folders": ["self", "conn"],
        "list_layers": ["self", "conn", "service_url"],
        "get_layer_schema": ["self", "conn", "layer_url"],
        "query_features": [
            "self",
            "conn",
            "layer_url",
            "where",
            "out_fields",
            "return_geometry",
            "geometry_filter",
            "result_offset",
            "result_record_count",
            "order_by",
            "out_sr",
            "max_features",
        ],
        "add_features": ["self", "conn", "layer_url", "features", "batch_size"],
        "update_features": ["self", "conn", "layer_url", "features", "batch_size"],
        "delete_features": ["self", "conn", "layer_url", "object_ids", "where"],
        "create_service": [
            "self",
            "conn",
            "service_name",
            "layer_name",
            "df",
            "geometry_column",
            "srid",
            "folder_id",
        ],
        "add_layer_to_service": [
            "self",
            "conn",
            "service_url",
            "gdf",
            "layer_name",
            "layer_id",
            "srid",
        ],
        "update_renderer": ["self", "conn", "layer_url", "drawing_info"],
    }

    for method_name, parameter_names in expected_parameters.items():
        assert _parameter_names(ArcGISProviderProtocol, method_name) == parameter_names
        assert _parameter_names(ArcGISClient, method_name) == parameter_names


def test_arcgis_client_satisfies_each_provider_capability_protocol() -> None:
    client = ArcGISClient()

    assert isinstance(client, ArcGISDiscoveryProviderProtocol)
    assert isinstance(client, ArcGISReadProviderProtocol)
    assert isinstance(client, ArcGISWriteProviderProtocol)
    assert isinstance(client, ArcGISServiceAdminProviderProtocol)
    assert isinstance(client, ArcGISProviderProtocol)
