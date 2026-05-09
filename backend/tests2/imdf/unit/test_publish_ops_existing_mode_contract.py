from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.imdf.arcgis.publish_ops import _publish_mode_existing


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_publish_mode_existing_preserves_legacy_layer_result_shape() -> None:
    client = MagicMock()
    client.list_layers.return_value = [
        {"name": "Localita", "id": 0, "url": "https://example.test/FeatureServer/0"},
    ]
    client.dataframe_to_esri_features.return_value = [{"attributes": {"SITE_ID": "A"}}]
    client.add_features.return_value = {"added_count": 1, "errors": []}
    result = {
        "success": False,
        "service_url": "https://example.test/FeatureServer",
        "service_item_id": "item-1",
        "folder_id": "",
        "layers": [],
        "renderers_applied": 0,
        "errors": [],
    }
    events = []

    returned = _publish_mode_existing(
        client=client,
        conn=SimpleNamespace(),
        service_url="https://example.test/FeatureServer",
        item_id="item-1",
        folder_id="",
        layers={"Localita": pd.DataFrame({"SITE_ID": ["A"]})},
        layers_present=["Localita"],
        mode="append",
        match_columns={"Localita": "SITE_ID"},
        target_srid=4326,
        result=result,
        emit=lambda *args: events.append(args),
        step=2,
        total_steps=3,
    )

    assert returned is result
    assert result["success"] is True
    assert result["layers"] == [{
        "id": 0,
        "name": "Localita",
        "url": "https://example.test/FeatureServer/0",
        "feature_count": 1,
        "deleted_count": 0,
        "updated_count": 0,
        "added_count": 1,
        "skipped_count": 0,
        "errors": [],
    }]
    assert events[-1] == ("done", 3, 3, "append completato")
