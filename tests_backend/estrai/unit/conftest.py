import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")
sys.path.insert(0, str(Path(__file__).parent))  # aggiunge unit/ per trovare helpers.py

from pathlib import Path
from typing import List, Optional, Dict
import pytest
from app.shared.models import Node, Edge, Column, Filter, Position

_POS = Position(x=0, y=0)


def make_source(node_id, table_name, columns, alias=None, filters=None, calculated_columns=None):
    cols = [Column(name=c["name"], type=c.get("type","str"), visible=c.get("visible",True), alias=c.get("alias")) for c in columns]
    filts = [Filter(column=f["column"], operator=f["operator"], value=f.get("value","")) for f in (filters or [])]
    return Node(id=node_id, type="source", position=_POS, table_name=table_name, alias=alias, columns=cols, filters=filts, calculated_columns=calculated_columns or [])


def make_result(node_id="result", filename="output", fmt="csv", limit=None, distinct=False,
                group_by_enabled=False, group_by_columns=None, aggregations=None, geom_column=None):
    return Node(id=node_id, type="result", position=_POS, fileName=filename, format=fmt,
                limit=limit, distinct=distinct, group_by_enabled=group_by_enabled,
                group_by_columns=group_by_columns or [], aggregations=aggregations or [],
                geomColumn=geom_column)


def make_edge(edge_id, source, target, join_type="inner", source_col="", target_col="",
              is_spatial=False, spatial_predicate=None, source_geom=None, target_geom=None,
              distance=None, join_conditions=None):
    return Edge(id=edge_id, source=source, target=target, join_type=join_type,
                source_column=source_col, target_column=target_col, is_spatial=is_spatial,
                spatial_predicate=spatial_predicate, source_geom_column=source_geom,
                target_geom_column=target_geom, spatial_distance=distance,
                join_conditions=join_conditions)


def output_edge(source, result_id="result"):
    return make_edge("edge-out", source, result_id, join_type="output")
