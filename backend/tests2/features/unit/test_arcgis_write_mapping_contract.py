"""Contracts for ArcGIS write column mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.connections.arcgis_write_mapping import ArcGISWriteMappingContract
from app.core.features.features_service import FeaturesService


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


def test_legacy_features_mapping_path_reexports_neutral_contract() -> None:
    from app.core.connections.arcgis_write_mapping import (
        ArcGISWriteMappingContract as LegacyContract,
    )

    assert LegacyContract is ArcGISWriteMappingContract


def test_write_mapping_contract_renames_only_at_boundary_and_keeps_geometry() -> None:
    df = pd.DataFrame(
        [
            {
                "city_name": "Torino",
                "source_id": "T-1",
                "unused": "drop-me",
                "geometry": "POINT (7.68 45.07)",
            }
        ]
    )
    contract = ArcGISWriteMappingContract.from_raw(
        [
            {"source": "city_name", "dest": "CITY"},
            {"source_column": "source_id", "dest_column": "SRC_ID"},
        ]
    )

    mapped = contract.apply(df, geometry_column="geometry", required_match_column="SRC_ID")

    assert list(mapped.df.columns) == ["CITY", "SRC_ID", "geometry"]
    assert mapped.geometry_column == "geometry"
    assert list(df.columns) == ["city_name", "source_id", "unused", "geometry"]


def test_write_mapping_contract_rejects_missing_source_column() -> None:
    df = pd.DataFrame([{"city_name": "Torino"}])
    contract = ArcGISWriteMappingContract.from_raw(
        [{"source": "missing", "dest": "CITY"}]
    )

    with pytest.raises(ValueError, match="colonne sorgente mancanti: missing"):
        contract.apply(df, geometry_column="geometry")


def test_write_features_from_df_applies_mapping_before_feature_serialization() -> None:
    captured_columns: list[str] = []

    def _to_features(df: pd.DataFrame, geometry_column: str, operation: str) -> list[dict]:
        captured_columns.extend(list(df.columns))
        return [{"attributes": {"CITY": df.iloc[0]["CITY"]}}]

    client = SimpleNamespace(
        add_dataframe_features=MagicMock(
            side_effect=lambda conn, layer_url, df, **kwargs: (
                _to_features(df, kwargs["geometry_column"], kwargs["operation"])
                and {"success": True, "added_count": 1, "errors": []}
            )
        ),
        add_features=MagicMock(
            return_value={"success": True, "added_count": 1, "errors": []}
        ),
    )
    service = FeaturesService(client=client)
    service.get_connection = MagicMock(return_value=object())
    service._to_connection_info = MagicMock(return_value=SimpleNamespace())
    df = pd.DataFrame([{"city_name": "Torino", "unused": "drop"}])

    result = service.write_features_from_df(
        connection_id="conn-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=df,
        column_mappings=[{"source": "city_name", "dest": "CITY"}],
    )

    assert result["success"] is True
    assert captured_columns == ["CITY"]
    assert list(df.columns) == ["city_name", "unused"]


def test_file_and_dataframe_writes_delegate_to_same_provider_write_core() -> None:
    df = pd.DataFrame([{"city_name": "Torino"}])
    conn_info = SimpleNamespace()
    core_result = {"success": True, "message": "ok", "count": 1, "errors": []}
    service = FeaturesService(client=SimpleNamespace())
    service.get_connection = MagicMock(return_value=object())
    service._to_connection_info = MagicMock(return_value=conn_info)
    service._resolve_write_connection = MagicMock(return_value=conn_info)
    service._load_dataframe = MagicMock(return_value=df)
    service._write_features_df_core = MagicMock(return_value=core_result)

    raw_mappings = [{"source": "city_name", "dest": "CITY"}]

    file_result = service.write_features(
        connection_id="conn-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        input_path="/tmp/result.parquet",
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="CITY",
        column_mappings=raw_mappings,
    )
    dataframe_result = service.write_features_from_df(
        connection_id="conn-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=df,
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="CITY",
        column_mappings=raw_mappings,
    )

    assert file_result == core_result
    assert dataframe_result == core_result
    first_call, second_call = service._write_features_df_core.call_args_list
    assert first_call.kwargs == second_call.kwargs
    assert first_call.kwargs["column_mappings"] is raw_mappings


def test_create_service_from_df_applies_mapping_before_schema_creation() -> None:
    captured_columns: list[str] = []

    def _create_service(
        conn: object,
        service_name: str,
        layer_name: str,
        df: pd.DataFrame,
        geometry_column: str,
        folder_id: str,
    ) -> dict:
        captured_columns.extend(list(df.columns))
        return {
            "success": True,
            "service_url": "https://example.test/FeatureServer",
            "layer_url": "https://example.test/FeatureServer/0",
            "item_id": "item-1",
            "feature_count": 1,
            "errors": [],
        }

    client = SimpleNamespace(create_service=MagicMock(side_effect=_create_service))
    service = FeaturesService(client=client)
    service.get_connection = MagicMock(return_value=object())
    service._to_connection_info = MagicMock(return_value=SimpleNamespace())
    df = pd.DataFrame([{"city_name": "Torino", "unused": "drop"}])

    result = service.create_service_from_df(
        connection_id="conn-1",
        service_name="DestService",
        layer_name="DestLayer",
        df=df,
        column_mappings=ArcGISWriteMappingContract.from_raw(
            [{"source": "city_name", "dest": "CITY"}]
        ),
    )

    assert result["success"] is True
    assert captured_columns == ["CITY"]
    assert list(df.columns) == ["city_name", "unused"]
