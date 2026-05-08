"""
Contracts for the CARICA ArcGIS writer boundary.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from workers.tasks.carica_task import (
    ArcGISFeatureWriter,
    _execute_arcgis_write,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_arcgis_writer_protocol_exposes_dataframe_write_boundary() -> None:
    assert hasattr(ArcGISFeatureWriter, "create_service_from_df")
    assert hasattr(ArcGISFeatureWriter, "write_features_from_df")


def test_execute_arcgis_write_uses_generic_writer_boundary_for_existing_layer() -> None:
    task = SimpleNamespace(update_state=lambda **kwargs: None)
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

    with patch(
        "workers.tasks.carica_task._create_arcgis_feature_writer",
        return_value=writer,
    ) as create_writer:
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
            },
            project_id="proj-1",
            task_id="task-1",
        )

    create_writer.assert_called_once_with()
    writer.write_features_from_df.assert_called_once_with(
        connection_id="arc-1",
        layer_url="https://example.test/FeatureServer/0",
        operation="add",
        df=df,
        geometry_column="shape",
        source_srid=3003,
        batch_size=250,
        match_column="id",
    )
    assert result["success"] is True
    assert result["rows_written"] == 1
    assert result["rows_count"] == 1


def test_execute_arcgis_write_uses_generic_writer_boundary_for_service_create() -> None:
    task = SimpleNamespace(update_state=lambda **kwargs: None)
    df = pd.DataFrame([{"id": 1, "name": "A"}])
    writer = SimpleNamespace(
        create_service_from_df=MagicMock(
            return_value={
                "success": True,
                "feature_count": 1,
                "message": "Servizio creato",
                "service_url": "https://example.test/FeatureServer",
            }
        )
    )

    with patch(
        "workers.tasks.carica_task._create_arcgis_feature_writer",
        return_value=writer,
    ) as create_writer:
        result = _execute_arcgis_write(
            task=task,
            df=df,
            carica_node_data={
                "arcgis_connection_id": "arc-1",
                "arcgis_operation": "create_service",
                "arcgis_new_service_name": "DestService",
                "arcgis_new_layer_name": "DestLayer",
                "arcgis_folder_id": "folder-1",
                "geometry_column": "shape",
                "source_srid": 3003,
            },
            project_id="proj-1",
            task_id="task-1",
        )

    create_writer.assert_called_once_with()
    writer.create_service_from_df.assert_called_once_with(
        connection_id="arc-1",
        service_name="DestService",
        layer_name="DestLayer",
        df=df,
        geometry_column="shape",
        source_srid=3003,
        folder_id="folder-1",
    )
    assert result["success"] is True
    assert result["rows_written"] == 1
    assert result["service_url"] == "https://example.test/FeatureServer"
