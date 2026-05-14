"""Contracts for the neutral ArcGIS provider composition root."""

from __future__ import annotations

import ast

import pytest

from tests2.shared.esri_boundary_helpers import BACKEND_DIR, imported_names_in_file


pytestmark = [pytest.mark.unit, pytest.mark.contract]

ARCGIS_FACTORY = (
    BACKEND_DIR / "app" / "core" / "connections" / "arcgis_provider_factory.py"
)
HTTP_PROTOCOL_MODULE = "app.core.connections.arcgis_http_client_protocol"
CONCRETE_CLIENT_MODULE = "app.core.features.arcgis_client"


def test_arcgis_provider_factory_exports_neutral_http_protocol() -> None:
    from app.core.connections.arcgis_provider_factory import (
        ArcGISProviderHttpClientProtocol,
        __all__,
        create_arcgis_provider,
    )

    assert "ArcGISProviderHttpClientProtocol" in __all__
    assert "create_arcgis_provider" in __all__
    assert callable(ArcGISProviderHttpClientProtocol)
    assert callable(create_arcgis_provider)


def test_arcgis_provider_factory_keeps_concrete_import_inside_create_function() -> None:
    tree = ast.parse(ARCGIS_FACTORY.read_text(encoding="utf-8"))
    concrete_import_lines = {
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == CONCRETE_CLIENT_MODULE
        and any(alias.name == "ArcGISClient" for alias in node.names)
    }
    create_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_arcgis_provider"
    )
    create_body_lines = {
        child.lineno
        for statement in create_function.body
        for child in ast.walk(statement)
        if hasattr(child, "lineno")
    }

    assert concrete_import_lines
    assert concrete_import_lines <= create_body_lines


def test_arcgis_provider_factory_uses_connections_http_protocol_not_feature_type() -> None:
    imported = {
        (module, name) for module, name, _line in imported_names_in_file(ARCGIS_FACTORY)
    }

    assert (HTTP_PROTOCOL_MODULE, "ArcGISProviderHttpClientProtocol") in imported
    assert (CONCRETE_CLIENT_MODULE, "HttpClientProtocol") not in imported


def test_arcgis_provider_factory_passes_injected_http_client_to_concrete_provider() -> None:
    from app.core.connections.arcgis_provider_factory import create_arcgis_provider

    class FakeHttpClient:
        def get(self, url, params=None, timeout=30, headers=None):
            raise AssertionError("not called")

        def post(self, url, data=None, timeout=30, headers=None):
            raise AssertionError("not called")

    http_client = FakeHttpClient()
    provider = create_arcgis_provider(http_client=http_client)

    assert provider._http is http_client
