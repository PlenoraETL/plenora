"""Contract: il roundtrip CanvasState non deve perdere campi espliciti."""

from __future__ import annotations

from app.core.projects.canvas_models import CanvasState


def _dump_canvas(payload: dict) -> dict:
    return CanvasState(**payload).model_dump(
        exclude_none=False,
        exclude_unset=False,
    )


def _assert_roundtrip_stable(payload: dict) -> dict:
    dumped = _dump_canvas(payload)
    reparsed = _dump_canvas(dumped)
    assert reparsed == dumped
    return dumped


def _estrai_canvas_full() -> dict:
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "source",
                "position": {"x": 100, "y": 100},
                "table_name": "sales",
                "alias": "src",
                "columns": [
                    {
                        "name": "id",
                        "alias": "sale_id",
                        "visible": True,
                        "type": "int",
                    },
                    {
                        "name": "city",
                        "alias": "city_name",
                        "visible": True,
                        "type": "str",
                    },
                    {
                        "name": "score",
                        "alias": None,
                        "visible": False,
                        "type": "real",
                    },
                ],
                "filters": [{"column": "score", "operator": ">", "value": "10"}],
                "calculated_columns": [
                    {"name": "score_x2", "expression": "score * 2", "type": "real"}
                ],
                "connection_id": "conn-estrai-1",
                "data": {"sourceKind": "table"},
                "config": {"preview_limit": 50},
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 380, "y": 100},
                "fileName": "estrai_full",
                "format": "csv",
                "distinct": True,
                "limit": 500,
                "geomColumn": "geom",
                "group_by_enabled": True,
                "group_by_columns": ["city"],
                "aggregations": [
                    {"column": "id", "function": "COUNT", "alias": "rows_count"}
                ],
                "order_by_enabled": True,
                "order_by_columns": [{"column": "city", "direction": "ASC"}],
                "having_enabled": True,
                "having_conditions": [
                    {
                        "function": "COUNT",
                        "column": "id",
                        "operator": ">=",
                        "value": "1",
                    }
                ],
                "columns": [],
                "data": {"exportMode": "manual"},
                "config": {"compression": "none"},
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "src-1",
                "target": "result-1",
                "join_type": "output",
                "source_column": "",
                "target_column": "",
                "join_conditions": [],
                "on_filters": [],
                "is_spatial": False,
                "source_geom_column": None,
                "target_geom_column": None,
                "spatial_distance": None,
            }
        ],
    }


def _manipola_canvas_full() -> dict:
    return {
        "nodes": [
            {
                "id": "fs-1",
                "type": "file_source",
                "position": {"x": 80, "y": 100},
                "file_source_id": "file-source-1",
                "columns": [],
                "data": {"origin": "upload"},
                "config": {"delimiter": ","},
            },
            {
                "id": "tr-1",
                "type": "transform",
                "position": {"x": 320, "y": 100},
                "collapsed": True,
                "transform_op": "filter",
                "transform_category": "base",
                "config": {
                    "column": "score",
                    "operator": ">",
                    "value": "15",
                },
                "data": {
                    "config": {
                        "column": "score",
                        "operator": ">",
                        "value": "15",
                    },
                    "ui": {"open": False},
                },
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 560, "y": 100},
                "fileName": "manipola_filter",
                "format": "csv",
                "columns": [],
                "data": {"resultKind": "filtered"},
                "config": {"line_ending": "lf"},
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "fs-1",
                "target": "tr-1",
                "join_type": "left",
                "source_column": "",
                "target_column": "",
                "join_conditions": [],
                "on_filters": [],
                "is_spatial": False,
            },
            {
                "id": "edge-2",
                "source": "tr-1",
                "target": "result-1",
                "join_type": "output",
                "source_column": "",
                "target_column": "",
                "join_conditions": [],
                "on_filters": [],
                "is_spatial": False,
            },
        ],
    }


def _arricchisci_canvas_full() -> dict:
    return {
        "nodes": [
            {
                "id": "fs-1",
                "type": "file_source",
                "position": {"x": 100, "y": 100},
                "file_source_id": "file-source-2",
                "columns": [],
                "data": {"origin": "seed"},
                "config": {"encoding": "utf-8"},
            },
            {
                "id": "ae-1",
                "type": "api_enrich",
                "position": {"x": 300, "y": 100},
                "api_id": "api-1",
                "api_name": "HTTP Enricher",
                "param_mapping": {"city": "city"},
                "output_mapping": [{"path": "status", "column": "status"}],
                "static_parameters": {"country": "IT"},
                "enrich_type": "row",
                "api_type": "enricher",
                "connection_type": "rest_file",
                "request": {
                    "body_type": "multipart",
                    "timeout": 30,
                    "file_fields": [{"name": "file", "source": "node_input"}],
                    "static_fields": [{"name": "mode", "value": "echo"}],
                },
                "response": {
                    "format": "binary_file",
                    "output_extension": ".csv",
                },
                "extra_files": {
                    "template": {
                        "file_source_id": "file-extra-1",
                        "original_filename": "template.csv",
                        "size": 12,
                    }
                },
                "output_kind": "generic",
                "from_catalog": False,
                "batch": {"enabled": False},
                "continue_on_error": False,
                "skip_null_params": True,
                "max_errors": 3,
                "data": {"ui": {"tab": "mapping"}},
                "config": {"retry": 0},
                "columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 540, "y": 100},
                "fileName": "arricchisci_http",
                "format": "csv",
                "columns": [],
                "data": {"resultKind": "http"},
                "config": {"compression": "none"},
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "fs-1",
                "target": "ae-1",
                "join_type": "left",
                "source_column": "",
                "target_column": "",
                "join_conditions": [],
                "on_filters": [],
                "is_spatial": False,
            },
            {
                "id": "edge-2",
                "source": "ae-1",
                "target": "result-1",
                "join_type": "output",
                "source_column": "",
                "target_column": "",
                "join_conditions": [],
                "on_filters": [],
                "is_spatial": False,
            },
        ],
    }


def test_canvas_roundtrip_preserves_explicit_fields_estrai():
    dumped = _assert_roundtrip_stable(_estrai_canvas_full())

    src = dumped["nodes"][0]
    result = dumped["nodes"][1]
    edge = dumped["edges"][0]
    assert src["table_name"] == "sales"
    assert src["columns"][0]["alias"] == "sale_id"
    assert src["calculated_columns"][0]["name"] == "score_x2"
    assert result["group_by_columns"] == ["city"]
    assert result["aggregations"][0]["alias"] == "rows_count"
    assert result["having_conditions"][0]["operator"] == ">="
    assert edge["join_type"] == "output"


def test_canvas_roundtrip_preserves_explicit_fields_manipola():
    dumped = _assert_roundtrip_stable(_manipola_canvas_full())

    transform = dumped["nodes"][1]
    assert transform["type"] == "transform"
    assert transform["collapsed"] is True
    assert transform["transform_op"] == "filter"
    assert transform["config"]["operator"] == ">"
    assert transform["data"]["config"]["value"] == "15"
    assert dumped["edges"][0]["join_type"] == "left"
    assert dumped["edges"][1]["join_type"] == "output"


def test_canvas_roundtrip_preserves_explicit_fields_arricchisci():
    dumped = _assert_roundtrip_stable(_arricchisci_canvas_full())

    api_node = dumped["nodes"][1]
    assert api_node["type"] == "api_enrich"
    assert api_node["api_name"] == "HTTP Enricher"
    assert api_node["param_mapping"]["city"] == "city"
    assert api_node["request"]["timeout"] == 30
    assert api_node["request"]["file_fields"][0]["name"] == "file"
    assert api_node["response"]["format"] == "binary_file"
    assert api_node["extra_files"]["template"]["original_filename"] == "template.csv"
    assert api_node["batch"]["enabled"] is False
    assert dumped["edges"][1]["join_type"] == "output"
