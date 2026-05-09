"""Tombstone guard for retained IMDF ArcGIS domain adapters."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable

import pytest

from app.core.imdf import arcgis_uploader
from app.core.imdf.arcgis import service_discovery, sql_helpers


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _called_attributes(fn: Callable[..., object]) -> set[str]:
    tree = ast.parse(inspect.getsource(fn))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _body_shape(fn: Callable[..., object]) -> tuple[str, ...]:
    tree = ast.parse(inspect.getsource(fn))
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    return tuple(type(node).__name__ for node in func.body)


@pytest.mark.parametrize(
    ("fn", "allowed_calls", "body_shape"),
    [
        (
            service_discovery._list_user_folders,
            {"get", "list_user_folders", "strip", "lower", "warning"},
            ("Expr", "Try"),
        ),
        (service_discovery._ensure_folder, {"ensure_folder"}, ("Expr", "Return")),
        (
            service_discovery._is_service_name_available,
            {"is_service_name_available", "warning"},
            ("Expr", "Try"),
        ),
        (sql_helpers._query_oid_by_key, {"query_oid_by_key"}, ("Expr", "Return")),
    ],
)
def test_imdf_arcgis_domain_adapters_do_not_grow_provider_logic(
    fn: Callable[..., object],
    allowed_calls: set[str],
    body_shape: tuple[str, ...],
) -> None:
    """Retained adapters may shape IMDF results, but not regain ArcGIS mechanics."""
    source = inspect.getsource(fn)

    retired_marker = "KEEP" + "_COMPAT"
    assert retired_marker not in source
    assert _body_shape(fn) == body_shape
    assert _called_attributes(fn) <= allowed_calls


def test_imdf_renderer_path_uses_provider_layer_renderer_directly() -> None:
    source = inspect.getsource(arcgis_uploader.upload_imdf_layers)

    assert "client.update_layer_renderer" in source
    assert "client.update_renderer" not in source


def test_imdf_uploader_uses_provider_add_layer_directly() -> None:
    source = inspect.getsource(arcgis_uploader.upload_imdf_layers)

    assert "client.add_layer_to_service" in source
    assert "_publish_ops._add_layer_to_service" not in source


def test_imdf_uploader_private_helper_facade_stays_tombstoned() -> None:
    removed_facade_exports = {
        "_quote_sql_value",
        "_chunked",
        "_query_oid_by_key",
        "_list_user_folders",
        "_ensure_folder",
        "_is_service_name_available",
        "_enumerate_all_user_items",
        "_search_user_items",
        "_find_existing_service",
        "_try_cleanup_zombie_service",
        "_delete_existing_service",
        "_add_layer_to_service",
        "_publish_mode_existing",
    }

    leaked = sorted(
        name for name in removed_facade_exports if hasattr(arcgis_uploader, name)
    )

    assert leaked == []
