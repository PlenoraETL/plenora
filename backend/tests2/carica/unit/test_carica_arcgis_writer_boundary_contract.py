"""Contracts for the CARICA ArcGIS writer boundary."""

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import app.core.carica.arcgis_write_boundary as arcgis_write_boundary
import app.core.carica.rest_write_boundary as rest_write_boundary
from app.core.carica.arcgis_write_boundary import (
    ArcGISFeatureWriteResult,
    CaricaArcGISWriteOutcome,
    CaricaArcGISWriteRequest,
    execute_carica_arcgis_write,
)
from app.core.carica.rest_write_boundary import RestBatchSummary
from app.core.connections.arcgis_writer_boundary import ArcGISFeatureWriter
from app.core.carica.dispatch import execute_arcgis_write


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_arcgis_writer_protocol_exposes_dataframe_write_boundary() -> None:
    assert hasattr(ArcGISFeatureWriter, "create_service_from_df")
    assert hasattr(ArcGISFeatureWriter, "write_features_from_df")


def test_write_boundaries_use_frozen_dataclass_result_contracts() -> None:
    assert is_dataclass(RestBatchSummary)
    assert is_dataclass(ArcGISFeatureWriteResult)

    rest_summary = RestBatchSummary(
        success=True,
        status_code=201,
        sent_count=1,
        error="",
    )
    arcgis_result = ArcGISFeatureWriteResult.from_mapping(
        {"feature_count": 1, "message": "done"}
    )

    with pytest.raises(FrozenInstanceError):
        setattr(rest_summary, "sent_count", 2)
    with pytest.raises(FrozenInstanceError):
        setattr(arcgis_result, "message", "changed")


def test_write_boundary_modules_do_not_keep_typeddict_contracts() -> None:
    module_paths = (
        Path(rest_write_boundary.__file__),
        Path(arcgis_write_boundary.__file__),
    )

    for module_path in module_paths:
        assert "TypedDict" not in module_path.read_text(encoding="utf-8")


def test_carica_arcgis_boundary_delegates_existing_layer_write_to_feature_writer() -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = SimpleNamespace(
        write_features_from_df=MagicMock(
            return_value={
                "success": True,
                "feature_count": 1,
                "message": "Aggiunte 1 features",
            }
        )
    )
    request = CaricaArcGISWriteRequest(
        project_id="proj-1",
        connection_id="arc-1",
        operation="add",
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="id",
        layer_url="https://example.test/FeatureServer/0",
        column_mappings=[{"source": "name", "dest": "NAME"}],
    )

    outcome = execute_carica_arcgis_write(
        df=df,
        request=request,
        feature_writer=writer,
    )

    writer.write_features_from_df.assert_called_once_with(
        connection_id="arc-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=df,
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="id",
        column_mappings=[{"source": "name", "dest": "NAME"}],
    )
    result = outcome.to_task_result()
    assert result["success"] is True
    assert result["rows_written"] == 1
    assert result["rows_inserted"] == 1
    assert result["rows_updated"] == 0
    assert result["rows_count"] == 1


def test_carica_arcgis_boundary_reports_update_counts_for_update_operation() -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = SimpleNamespace(
        write_features_from_df=MagicMock(
            return_value={
                "success": True,
                "feature_count": 1,
                "message": "Aggiornata 1 feature",
            }
        )
    )
    request = CaricaArcGISWriteRequest(
        project_id="proj-1",
        connection_id="arc-1",
        operation="update",
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="id",
        layer_url="https://example.test/FeatureServer/0",
        column_mappings=[],
    )

    result = execute_carica_arcgis_write(
        df=df,
        request=request,
        feature_writer=writer,
    ).to_task_result()

    assert result["success"] is True
    assert result["rows_written"] == 1
    assert result["rows_inserted"] == 0
    assert result["rows_updated"] == 1


def test_carica_arcgis_boundary_delegates_service_create_to_feature_writer() -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = SimpleNamespace(
        create_service_from_df=MagicMock(
            return_value={
                "success": True,
                "feature_count": 1,
                "message": "Servizio creato",
                "service_url": "https://example.test/FeatureServer",
                "layer_url": "https://example.test/FeatureServer/0",
                "item_id": "item-1",
                "errors": [],
            }
        )
    )
    request = CaricaArcGISWriteRequest(
        project_id="proj-1",
        connection_id="arc-1",
        operation="create_service",
        geometry_column="shape",
        source_srid=3003,
        batch_size=None,
        column_mappings=[{"source_column": "name", "dest_column": "NAME"}],
        service_name="DestService",
        layer_name="DestLayer",
        folder_id="folder-1",
    )

    outcome = execute_carica_arcgis_write(
        df=df,
        request=request,
        feature_writer=writer,
    )

    writer.create_service_from_df.assert_called_once_with(
        connection_id="arc-1",
        service_name="DestService",
        layer_name="DestLayer",
        df=df,
        geometry_column="shape",
        source_srid=3003,
        folder_id="folder-1",
        column_mappings=[{"source_column": "name", "dest_column": "NAME"}],
    )
    result = outcome.to_task_result()
    assert result["success"] is True
    assert result["rows_written"] == 1
    assert result["service_url"] == "https://example.test/FeatureServer"
    assert result["layer_url"] == "https://example.test/FeatureServer/0"
    assert result["item_id"] == "item-1"
    assert result["arcgis_result"] == {
        "service_url": "https://example.test/FeatureServer",
        "layer_url": "https://example.test/FeatureServer/0",
        "item_id": "item-1",
        "errors": [],
    }


def test_worker_delegates_arcgis_provider_write_to_carica_boundary() -> None:
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = object()
    outcome = CaricaArcGISWriteOutcome(
        status="completed",
        success=True,
        project_id="proj-1",
        rows_written=1,
        rows_inserted=1,
        rows_updated=0,
        total_rows=1,
        columns_count=2,
        message="done",
        layer_url="https://example.test/FeatureServer/0",
    )

    with patch(
        "app.core.carica.dispatch.create_default_arcgis_feature_writer",
        return_value=writer,
    ) as create_writer, patch(
        "app.core.carica.dispatch.execute_carica_arcgis_write",
        return_value=outcome,
    ) as execute_boundary:
        result = execute_arcgis_write(
            df=df,
            carica_node_data={
                "arcgis_connection_id": "arc-1",
                "arcgis_operation": "add",
                "arcgis_layer_url": "https://example.test/FeatureServer/0",
                "geometry_column": "shape",
                "source_srid": 3003,
                "batch_size": 250,
                "arcgis_match_column": "id",
                "column_mappings": [{"source": "name", "dest": "NAME"}],
            },
            project_id="proj-1",
            task_id="task-1",
            progress=lambda _p, _m: None,
        )

    create_writer.assert_called_once_with()
    execute_boundary.assert_called_once()
    _, kwargs = execute_boundary.call_args
    assert kwargs["df"] is df
    assert kwargs["feature_writer"] is writer
    request = kwargs["request"]
    assert request == CaricaArcGISWriteRequest(
        project_id="proj-1",
        connection_id="arc-1",
        operation="add",
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="id",
        layer_url="https://example.test/FeatureServer/0",
        column_mappings=[{"source": "name", "dest": "NAME"}],
    )
    assert result["success"] is True
    assert result["rows_written"] == 1
