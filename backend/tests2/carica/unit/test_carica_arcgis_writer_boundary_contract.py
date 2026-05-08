"""Contracts for the CARICA ArcGIS writer boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.arcgis_write_boundary import (
    CaricaArcGISWriteOutcome,
    CaricaArcGISWriteRequest,
    execute_carica_arcgis_write,
)
from app.core.features.arcgis_writer_boundary import ArcGISFeatureWriter
from workers.tasks.carica_task import _execute_arcgis_write


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_arcgis_writer_protocol_exposes_dataframe_write_boundary() -> None:
    assert hasattr(ArcGISFeatureWriter, "create_service_from_df")
    assert hasattr(ArcGISFeatureWriter, "write_features_from_df")


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
    assert result["rows_count"] == 1


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
    task = SimpleNamespace(update_state=lambda **kwargs: None)
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = object()
    outcome = CaricaArcGISWriteOutcome(
        status="completed",
        success=True,
        project_id="proj-1",
        rows_written=1,
        total_rows=1,
        columns_count=2,
        message="done",
        layer_url="https://example.test/FeatureServer/0",
    )

    with patch(
        "workers.tasks.carica_task.create_default_arcgis_feature_writer",
        return_value=writer,
    ) as create_writer, patch(
        "workers.tasks.carica_task.execute_carica_arcgis_write",
        return_value=outcome,
    ) as execute_boundary:
        result = _execute_arcgis_write(
            task=task,
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
