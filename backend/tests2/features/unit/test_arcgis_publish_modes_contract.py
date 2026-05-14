from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.connections.arcgis_publish_modes import publish_existing_layer_mode


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _client() -> MagicMock:
    client = MagicMock()
    client.dataframe_to_esri_features.side_effect = lambda df, *a, **kw: [
        {"attributes": {"KEY": value}} for value in df["KEY"].tolist()
    ]
    client.add_features.return_value = {"added_count": 2, "errors": []}
    client.update_features.return_value = {"updated_count": 2, "errors": []}
    client.delete_features.return_value = {"success": True, "deleted_count": 2, "errors": []}
    client.query_oid_by_key.return_value = ({"A": 11, "B": 12}, [])
    return client


def test_append_adds_features_without_match_lookup() -> None:
    client = _client()
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", "B"]})

    result = publish_existing_layer_mode(
        client,
        conn,
        "https://example.test/FeatureServer/0",
        df,
        mode="append",
        match_col=None,
        target_srid=4326,
    )

    assert result.added_count == 2
    assert result.updated_count == 0
    assert result.deleted_count == 0
    assert result.errors == []
    client.delete_features.assert_not_called()
    client.query_oid_by_key.assert_not_called()


def test_upsert_deletes_matching_keys_then_adds_features() -> None:
    client = _client()
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", "B"]})

    result = publish_existing_layer_mode(
        client,
        conn,
        "https://example.test/FeatureServer/0",
        df,
        mode="upsert",
        match_col="KEY",
        target_srid=4326,
    )

    assert result.deleted_count == 2
    assert result.added_count == 2
    assert result.errors == []
    client.delete_features.assert_called_once_with(
        conn,
        "https://example.test/FeatureServer/0",
        where="KEY IN ('A','B')",
    )
    client.add_features.assert_called_once()


def test_update_resolves_objectids_and_updates_without_add() -> None:
    client = _client()
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", "B"]})

    result = publish_existing_layer_mode(
        client,
        conn,
        "https://example.test/FeatureServer/0",
        df,
        mode="update",
        match_col="KEY",
        target_srid=4326,
    )

    assert result.updated_count == 2
    assert result.added_count == 0
    assert result.errors == []
    updated_features = client.update_features.call_args.args[2]
    assert [f["attributes"]["OBJECTID"] for f in updated_features] == [11, 12]
    client.add_features.assert_not_called()


def test_update_strict_failure_skips_layer_without_provider_writes() -> None:
    client = _client()
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", None]})

    result = publish_existing_layer_mode(
        client,
        conn,
        "https://example.test/FeatureServer/0",
        df,
        mode="update",
        match_col="KEY",
        target_srid=4326,
    )

    assert result.skipped_count == 2
    assert "NULL" in result.errors[0]
    client.add_features.assert_not_called()
    client.update_features.assert_not_called()
    client.delete_features.assert_not_called()
