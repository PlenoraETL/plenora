"""Guard: la policy IMDF delega al writer ArcGIS condiviso (no mechanics)."""

from __future__ import annotations

import inspect

import pytest

from app.core.imdf import arcgis_uploader


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_imdf_renderer_path_delegates_to_shared_writer() -> None:
    """La policy IMDF applica i renderer via il writer condiviso, non chiamando
    il client ArcGIS direttamente."""
    source = inspect.getsource(arcgis_uploader.upload_imdf_layers)

    assert "feature_writer.apply_layer_renderers" in source
    assert "client.update_layer_renderer" not in source
    assert "client.update_renderer" not in source


def test_imdf_uploader_delegates_multilayer_to_shared_writer() -> None:
    """I loop multi-layer (create/existing) sono nel writer condiviso; la policy
    IMDF non chiama piu' i primitivi ArcGIS direttamente."""
    source = inspect.getsource(arcgis_uploader.upload_imdf_layers)

    assert "feature_writer.create_service_with_layers" in source
    assert "feature_writer.write_layers_to_existing_service" in source
    assert "client.add_layer_to_service" not in source
    assert "client.create_service" not in source


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
