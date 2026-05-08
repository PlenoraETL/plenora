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
        "ensure_folder",
        "list_user_items",
        "search_items",
        "is_service_name_available",
        "list_layers",
        "get_layer_schema",
        "query_features",
        "query_oid_by_key",
        "count_features_where",
        "create_service",
        "add_layer_to_service",
        "add_features",
        "update_features",
        "delete_features",
        "update_renderer",
        "update_layer_renderer",
        "delete_item_permanently",
        "delete_feature_service_items",
        "cleanup_zombie_feature_service",
    }

    for method_name in required_methods:
        assert hasattr(ArcGISProviderProtocol, method_name)


def test_arcgis_provider_capability_protocols_have_expected_method_sets() -> None:
    assert {
        "list_services",
        "list_folders",
        "ensure_folder",
        "list_user_items",
        "search_items",
        "is_service_name_available",
        "list_layers",
    } <= set(
        dir(ArcGISDiscoveryProviderProtocol)
    )
    assert {"get_layer_schema", "query_features", "count_features_where", "query_oid_by_key"} <= set(
        dir(ArcGISReadProviderProtocol)
    )
    assert {"add_features", "update_features", "delete_features"} <= set(
        dir(ArcGISWriteProviderProtocol)
    )
    assert {
        "create_service",
        "add_layer_to_service",
        "update_renderer",
        "update_layer_renderer",
        "delete_item_permanently",
        "delete_feature_service_items",
        "cleanup_zombie_feature_service",
    } <= set(
        dir(ArcGISServiceAdminProviderProtocol)
    )


def test_arcgis_provider_protocol_signature_shapes_match_client_boundary() -> None:
    expected_parameters = {
        "list_services": ["self", "conn", "folder", "_depth", "_max_depth"],
        "list_folders": ["self", "conn"],
        "ensure_folder": ["self", "conn", "folder_name"],
        "list_user_items": ["self", "conn"],
        "search_items": ["self", "conn", "query"],
        "is_service_name_available": ["self", "conn", "service_name"],
        "list_layers": ["self", "conn", "service_url"],
        "get_layer_schema": ["self", "conn", "layer_url"],
        "query_oid_by_key": ["self", "conn", "layer_url", "match_col", "values"],
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
        "update_layer_renderer": ["self", "conn", "layer_url", "drawing_info"],
        "delete_item_permanently": ["self", "conn", "item_id"],
        "delete_feature_service_items": ["self", "conn", "items", "service_name"],
        "cleanup_zombie_feature_service": ["self", "conn", "service_name"],
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


def test_arcgis_service_discovery_cluster_stays_split_from_client() -> None:
    import ast
    from pathlib import Path

    from app.core.features.arcgis_service_discovery import ArcGISServiceDiscoveryMixin

    extracted_methods = {
        "list_services",
        "_is_arcgis_online",
        "is_arcgis_online",
        "_list_services_online",
        "_list_user_content_page",
        "_list_user_items_in_folder",
        "_list_user_items_result",
        "list_user_items",
        "list_folders",
        "_list_folders_online",
        "_list_folders_server",
        "_ensure_folder_result",
        "ensure_folder",
        "_service_name_availability_result",
        "is_service_name_available",
        "_search_items_result",
        "search_items",
        "search_services_online",
        "_list_services_server",
        "_get_rest_services_url",
    }

    client_path = Path(inspect.getsourcefile(ArcGISClient) or "")
    tree = ast.parse(client_path.read_text(encoding="utf-8"))
    client_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ArcGISClient"
    )
    client_local_methods = {
        node.name for node in client_class.body if isinstance(node, ast.FunctionDef)
    }

    assert issubclass(ArcGISClient, ArcGISServiceDiscoveryMixin)
    assert not (client_local_methods & extracted_methods)
    assert extracted_methods <= set(ArcGISServiceDiscoveryMixin.__dict__)
