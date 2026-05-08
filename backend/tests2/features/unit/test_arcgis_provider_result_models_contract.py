"""Contract tests for typed ArcGIS provider result compatibility helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.shared.connections.arcgis_provider_results import (
    DeleteFeatureServiceItemsResult,
    FolderEnsureResult,
    ItemDeleteResult,
    ItemListingResult,
    ItemSearchResult,
    RendererUpdateResult,
    ServiceNameAvailabilityResult,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_result_models_convert_to_existing_public_shapes() -> None:
    item = {
        "id": "item-1",
        "title": "MyFS",
        "type": "Feature Service",
        "ownerFolder": "folder-1",
    }

    assert FolderEnsureResult(
        folder_id="folder-1",
        title="Publish",
        created=False,
    ).to_compat_id() == "folder-1"
    assert ItemListingResult(items=(item,)).to_compat_list() == [item]
    assert ItemSearchResult(items=(item,)).to_compat_list() == [item]
    assert RendererUpdateResult(
        success=False,
        message="bad renderer",
    ).to_compat_dict() == {"success": False, "message": "bad renderer"}
    assert ItemDeleteResult(
        success=False,
        message="denied",
        item_id="item-1",
        response={"error": "denied"},
    ).to_compat_dict() == {
        "success": False,
        "message": "denied",
        "item_id": "item-1",
        "response": {"error": "denied"},
    }
    assert DeleteFeatureServiceItemsResult(
        attempted=2,
        succeeded=1,
    ).to_compat_tuple() == (2, 1)
    assert ServiceNameAvailabilityResult(
        available=True,
        error=None,
    ).to_compat_tuple() == (True, None)


def test_delete_feature_service_items_uses_typed_counter_result_internally() -> None:
    client = ArcGISClient(http_client=MagicMock())
    client.delete_item_permanently = MagicMock(  # type: ignore[method-assign]
        side_effect=[
            {"success": True, "message": "item deleted", "item_id": "item-1"},
            {"success": False, "message": "denied", "item_id": "item-2"},
        ]
    )
    conn = SimpleNamespace(server_url="https://org.maps.arcgis.com", username="publisher")

    typed = client._delete_feature_service_items_result(
        conn,
        [
            {"id": "item-1", "ownerFolder": "folder-1"},
            {"id": "", "ownerFolder": "missing-id"},
            {"id": "item-2", "ownerFolder": None},
        ],
        "MyFS",
    )

    assert typed == DeleteFeatureServiceItemsResult(attempted=2, succeeded=1)
    assert typed.to_compat_tuple() == (2, 1)
    assert client.delete_item_permanently.call_count == 2
