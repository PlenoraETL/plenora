"""
conftest.py per tests_v5/manipola/integration/
Contiene le funzioni builder inline — non dipende dal conftest globale.
"""
from typing import Dict, Any, List, Optional


def build_manipola_canvas(
    file_source_id, transform_op, transform_config,
    output_format="csv", output_filename="output",
    file_columns=None, second_file_source_id=None, second_file_columns=None,
):
    nodes = [{"id": "node-src-1", "type": "file_source", "position": {"x": 100, "y": 200},
               "file_source_id": file_source_id, "columns": file_columns or []}]
    edges = []
    if second_file_source_id:
        nodes.append({"id": "node-src-2", "type": "file_source", "position": {"x": 100, "y": 400},
                       "file_source_id": second_file_source_id, "columns": second_file_columns or []})
    nodes.append({"id": "node-transform", "type": "transform", "position": {"x": 400, "y": 300},
                   "transform_op": transform_op, "config": transform_config})
    nodes.append({"id": "node-result", "type": "result", "position": {"x": 700, "y": 300},
                   "fileName": output_filename, "format": output_format})
    edges.append({"id": "edge-src1-transform", "source": "node-src-1", "target": "node-transform"})
    if second_file_source_id:
        edges.append({"id": "edge-src2-transform", "source": "node-src-2", "target": "node-transform", "targetHandle": "input-1"})
    edges.append({"id": "edge-transform-result", "source": "node-transform", "target": "node-result"})
    return {"nodes": nodes, "edges": edges}


def build_estrai_file_canvas(file_source_id, output_format="csv", output_filename="output"):
    return {
        "nodes": [
            {"id": "node-file-src", "type": "file_source", "position": {"x": 100, "y": 200},
             "file_source_id": file_source_id, "columns": []},
            {"id": "node-result", "type": "result", "position": {"x": 500, "y": 200},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [{"id": "edge-src-result", "source": "node-file-src", "target": "node-result"}],
    }


def build_manipola_canvas_project_source(
    source_project_id, transform_op, transform_config,
    source_columns=None, output_format="csv", output_filename="output",
):
    return {
        "nodes": [
            {"id": "node-src", "type": "project_source", "position": {"x": 100, "y": 300},
             "source_project_id": source_project_id, "columns": source_columns or []},
            {"id": "node-transform", "type": "transform", "position": {"x": 400, "y": 300},
             "transform_op": transform_op, "config": transform_config},
            {"id": "node-result", "type": "result", "position": {"x": 700, "y": 300},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [
            {"id": "e1", "source": "node-src",       "target": "node-transform"},
            {"id": "e2", "source": "node-transform",  "target": "node-result"},
        ],
    }


def build_manipola_canvas_multi_source(
    file_source_ids, transform_op, transform_config,
    output_format="csv", output_filename="output",
):
    nodes, edges = [], []
    for i, fid in enumerate(file_source_ids):
        nodes.append({"id": f"node-src-{i}", "type": "file_source",
                       "position": {"x": 100, "y": 100 + i * 150},
                       "file_source_id": fid, "columns": []})
    nodes.append({"id": "node-multijoin", "type": "multi_join",
                   "position": {"x": 400, "y": 300},
                   "join_keys": transform_config.get("join_keys", []),
                   "multi_join_type": transform_config.get("join_type", "inner"),
                   "config": transform_config})
    for i in range(len(file_source_ids)):
        edges.append({"id": f"edge-src{i}-mj", "source": f"node-src-{i}",
                       "target": "node-multijoin", "targetHandle": f"input-{i}"})
    nodes.append({"id": "node-result", "type": "result", "position": {"x": 700, "y": 300},
                   "fileName": output_filename, "format": output_format})
    edges.append({"id": "edge-mj-result", "source": "node-multijoin", "target": "node-result"})
    return {"nodes": nodes, "edges": edges}


def build_manipola_canvas_sjoin(
    left_file_id, right_file_id, predicate="intersects", how="inner",
    output_format="csv", output_filename="sjoin_output",
    left_geom="geometry", right_geom="geometry",
):
    return {
        "nodes": [
            {"id": "node-src-left",  "type": "file_source", "position": {"x": 100, "y": 200},
             "file_source_id": left_file_id,  "columns": []},
            {"id": "node-src-right", "type": "file_source", "position": {"x": 100, "y": 400},
             "file_source_id": right_file_id, "columns": []},
            {"id": "node-transform", "type": "transform", "position": {"x": 400, "y": 300},
             "transform_op": "sjoin",
             "config": {"left_geom": left_geom, "right_geom": right_geom,
                        "predicate": predicate, "how": how}},
            {"id": "node-result", "type": "result", "position": {"x": 700, "y": 300},
             "fileName": output_filename, "format": output_format},
        ],
        "edges": [
            {"id": "e1", "source": "node-src-left",  "target": "node-transform"},
            {"id": "e2", "source": "node-src-right", "target": "node-transform", "targetHandle": "input-1"},
            {"id": "e3", "source": "node-transform", "target": "node-result"},
        ],
    }


def build_manipola_canvas_geo(
    file_source_id, geo_op, geo_config,
    output_format="gpkg", output_filename="geo_output",
    second_file_source_id=None,
):
    nodes = [{"id": "node-src-1", "type": "file_source", "position": {"x": 100, "y": 200},
               "file_source_id": file_source_id, "columns": []}]
    edges = []
    if second_file_source_id:
        nodes.append({"id": "node-src-2", "type": "file_source",
                       "position": {"x": 100, "y": 400},
                       "file_source_id": second_file_source_id, "columns": []})
    nodes.append({"id": "node-transform", "type": "transform", "position": {"x": 400, "y": 300},
                   "transform_op": geo_op, "config": geo_config})
    nodes.append({"id": "node-result", "type": "result", "position": {"x": 700, "y": 300},
                   "fileName": output_filename, "format": output_format})
    edges.append({"id": "e1", "source": "node-src-1", "target": "node-transform"})
    if second_file_source_id:
        edges.append({"id": "e2", "source": "node-src-2", "target": "node-transform", "targetHandle": "input-1"})
    edges.append({"id": "e3", "source": "node-transform", "target": "node-result"})
    return {"nodes": nodes, "edges": edges}
