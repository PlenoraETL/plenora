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


def test_upsert_delete_failure_does_not_readd_undeleted_keys() -> None:
    # HIGH (master #1): se il delete del match FALLISCE, le chiavi non cancellate
    # NON vanno re-inserite, altrimenti riga vecchia + nuova = DUPLICATO.
    client = _client()
    client.delete_features.return_value = {"success": False, "deleted_count": 0, "errors": ["boom"]}
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", "B"]})

    result = publish_existing_layer_mode(
        client, conn, "https://example.test/FeatureServer/0",
        df, mode="upsert", match_col="KEY", target_srid=4326,
    )

    # nessuna feature ri-aggiunta → niente duplicati
    client.add_features.assert_not_called()
    assert result.added_count == 0
    assert result.skipped_count == 2
    assert any("duplicat" in e.lower() for e in result.errors)


def test_upsert_delete_exception_does_not_readd_undeleted_keys() -> None:
    client = _client()
    client.delete_features.side_effect = RuntimeError("deleteFeatures down")
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": ["A", "B"]})

    result = publish_existing_layer_mode(
        client, conn, "https://example.test/FeatureServer/0",
        df, mode="upsert", match_col="KEY", target_srid=4326,
    )

    client.add_features.assert_not_called()
    assert result.added_count == 0
    assert result.skipped_count == 2


def test_upsert_partial_delete_failure_excludes_only_failed_keys() -> None:
    # 201 chiavi → 2 batch (200 + 1). Il 1° batch fallisce, il 2° riesce:
    # solo la/e chiave/i del batch riuscito vengono (ri)aggiunte. No duplicati.
    client = _client()
    client.delete_features.side_effect = [
        {"success": False, "deleted_count": 0, "errors": ["batch1 fail"]},
        {"success": True, "deleted_count": 1, "errors": []},
    ]
    client.add_features.return_value = {"added_count": 1, "errors": []}
    conn = SimpleNamespace()
    df = pd.DataFrame({"KEY": [f"K{i}" for i in range(201)]})

    result = publish_existing_layer_mode(
        client, conn, "https://example.test/FeatureServer/0",
        df, mode="upsert", match_col="KEY", target_srid=4326,
    )

    client.add_features.assert_called_once()
    added_features = client.add_features.call_args.args[2]
    assert len(added_features) == 1  # solo il batch cancellato con successo
    assert result.skipped_count == 200


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
