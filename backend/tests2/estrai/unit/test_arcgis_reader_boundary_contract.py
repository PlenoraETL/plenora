from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.estrai.executor import EstraiExecutor


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class FakeArcGISReader:
    def __init__(self) -> None:
        self.connection = SimpleNamespace(id="arcgis-1")
        self.query_calls: list[dict[str, object]] = []
        self.execute_calls: list[dict[str, object]] = []

    def get_connection(self, connection_id: str) -> object:
        assert connection_id == "arcgis-1"
        return self.connection

    def query_layer(
        self,
        connection_id: str,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: int | None = None,
        out_sr: int | None = None,
    ) -> tuple[list[tuple[object, ...]], list[str]]:
        self.query_calls.append(
            {
                "connection_id": connection_id,
                "layer_url": layer_url,
                "where": where,
                "out_fields": out_fields,
                "return_geometry": return_geometry,
                "max_features": max_features,
                "out_sr": out_sr,
            }
        )
        return [(1, "Room A"), (2, "Room B"), (3, "Room C")], ["OBJECTID", "name"]

    def execute_query(
        self,
        connection_id: str,
        layer_url: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: int | None = None,
        out_sr: int | None = None,
        geom_column: str = "geometry",
    ) -> dict[str, object]:
        self.execute_calls.append(
            {
                "connection_id": connection_id,
                "layer_url": layer_url,
                "project_id": project_id,
                "output_filename": output_filename,
                "output_format": output_format,
                "where": where,
                "out_fields": out_fields,
                "return_geometry": return_geometry,
                "max_features": max_features,
                "out_sr": out_sr,
                "geom_column": geom_column,
            }
        )
        return {
            "success": True,
            "message": "Query completata: 2 features estratte",
            "rows_count": 2,
            "columns": ["OBJECTID", "name"],
            "output_path": "output.csv",
            "timings": {},
        }


def _executor(reader: FakeArcGISReader) -> EstraiExecutor:
    executor = EstraiExecutor(
        project_service=MagicMock(),
        executor=MagicMock(),
        generator=MagicMock(),
        connection_service=MagicMock(),
        features_service=reader,
    )
    executor._salva_tipi_colonne_da_canvas = MagicMock()
    return executor


def _canvas() -> SimpleNamespace:
    feature_node = SimpleNamespace(
        type="feature_source",
        layer_url="https://example.test/FeatureServer/0",
        where="status = 'open'",
        columns=[
            {"name": "OBJECTID", "visible": True},
            {"name": "name", "visible": True},
            {"name": "geometry", "visible": False},
        ],
        geometry_type="esriGeometryPoint",
        data={},
    )
    result_node = SimpleNamespace(
        type="result",
        id="result-1",
        fileName="rooms",
        format="csv",
        limit=2,
        geomColumn=None,
    )
    return SimpleNamespace(nodes=[feature_node, result_node], edges=[])


def test_preview_feature_service_uses_arcgis_reader_boundary_and_keeps_response_shape():
    reader = FakeArcGISReader()
    executor = _executor(reader)

    response = executor._preview_feature_service(
        "project-1",
        _canvas(),
        _canvas().nodes[1],
        reader.connection,
        limit=2,
    )

    assert response == {
        "success": True,
        "columns": [
            {"name": "OBJECTID", "type": "str"},
            {"name": "name", "type": "str"},
        ],
        "rows": [{"OBJECTID": 1, "name": "Room A"}, {"OBJECTID": 2, "name": "Room B"}],
        "total_count": 3,
        "preview_count": 2,
        "truncated": True,
        "query": "ArcGIS REST: https://example.test/FeatureServer/0 WHERE status = 'open'",
    }
    assert reader.query_calls == [
        {
            "connection_id": "arcgis-1",
            "layer_url": "https://example.test/FeatureServer/0",
            "where": "status = 'open'",
            "out_fields": "OBJECTID,name",
            "return_geometry": False,
            "max_features": 3,
            "out_sr": None,
        }
    ]


def test_execute_feature_service_uses_arcgis_reader_boundary_and_preserves_resource_result():
    reader = FakeArcGISReader()
    executor = _executor(reader)
    canvas = _canvas()

    response = executor._execute_feature_service(
        "project-1",
        canvas,
        canvas.nodes[1],
        reader.connection,
    )

    assert response["success"] is True
    assert response["rows_count"] == 2
    assert response["columns"] == ["OBJECTID", "name"]
    assert response["output_path"] == "output.csv"
    assert response["query"] == "ArcGIS REST: https://example.test/FeatureServer/0 WHERE status = 'open'"
    assert reader.execute_calls == [
        {
            "connection_id": "arcgis-1",
            "layer_url": "https://example.test/FeatureServer/0",
            "project_id": "project-1",
            "output_filename": "rooms",
            "output_format": "csv",
            "where": "status = 'open'",
            "out_fields": "OBJECTID,name",
            "return_geometry": False,
            "max_features": 2,
            "out_sr": None,
            "geom_column": "geometry",
        }
    ]
