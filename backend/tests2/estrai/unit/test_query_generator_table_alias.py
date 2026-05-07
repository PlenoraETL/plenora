"""
Regression guard: QueryGenerator deve produrre alias tabella SENZA `AS`.

Oracle (ORA-03048) vieta `FROM tabella AS alias` — accetta solo
"""

from __future__ import annotations

import re

import pytest

from app.core.estrai.query_generator import QueryGenerator
from app.shared.models import Column, Edge, Node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
    pytest.mark.regression,
]


def _simple_canvas(table_name: str = "MY_TABLE", alias: str = "src"):
    source = Node(
        id="src-1",
        type="source",
        position={"x": 0, "y": 0},
        table_name=table_name,
        alias=alias,
        columns=[
            Column(name="ID", type="int", visible=True),
            Column(name="CITY", type="str", visible=True),
        ],
        filters=[],
    )
    result = Node(
        id="result-1",
        type="result",
        position={"x": 200, "y": 0},
        fileName="out",
        format="csv",
    )
    edge = Edge(id="e-1", source="src-1", target="result-1", join_type="output")
    return [source, result], [edge]


def _join_canvas():
    """Canvas con INNER JOIN tra due tabelle per coprire _genera_join."""
    a = Node(
        id="a-1", type="source",
        position={"x": 0, "y": 0},
        table_name="ORDERS", alias="o",
        columns=[Column(name="ID", type="int", visible=True),
                 Column(name="CUSTOMER_ID", type="int", visible=True)],
        filters=[],
    )
    b = Node(
        id="b-1", type="source",
        position={"x": 200, "y": 0},
        table_name="CUSTOMERS", alias="c",
        columns=[Column(name="ID", type="int", visible=True),
                 Column(name="NAME", type="str", visible=True)],
        filters=[],
    )
    result = Node(
        id="result-1", type="result",
        position={"x": 400, "y": 0},
        fileName="out", format="csv",
    )
    join_edge = Edge(
        id="e-join", source="a-1", target="b-1",
        join_type="inner", source_column="CUSTOMER_ID", target_column="ID",
    )
    out_edge = Edge(id="e-out", source="b-1", target="result-1", join_type="output")
    return [a, b, result], [join_edge, out_edge]


_AS_AFTER_TABLE = re.compile(
    r"\b(FROM|JOIN)\s+[\w\".]+\s+AS\s+\w+",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "db_type", ["oracle", "postgres", "mysql", "mssql", "sqlite"],
)
def test_generate_sql_table_alias_has_no_as_keyword(db_type):
    nodes, edges = _simple_canvas()
    qg = QueryGenerator(db_type=db_type)
    out = qg.generate_sql(nodes, edges, "result-1", db_type=db_type)

    sql = out["sql"]
    match = _AS_AFTER_TABLE.search(sql)
    assert match is None, (
        f"[{db_type}] SQL contiene `AS` su alias tabella — Oracle rifiuta "
        f"con ORA-03048. Match: {match.group(0) if match else ''}\n"
        f"SQL:\n{sql}"
    )


@pytest.mark.parametrize(
    "db_type", ["oracle", "postgres", "mysql", "mssql", "sqlite"],
)
def test_generate_sql_join_alias_has_no_as_keyword(db_type):
    nodes, edges = _join_canvas()
    qg = QueryGenerator(db_type=db_type)
    out = qg.generate_sql(nodes, edges, "result-1", db_type=db_type)

    sql = out["sql"]
    match = _AS_AFTER_TABLE.search(sql)
    assert match is None, (
        f"[{db_type}] JOIN contiene `AS` su alias tabella. "
        f"Match: {match.group(0) if match else ''}\nSQL:\n{sql}"
    )


def test_generate_sql_oracle_simple_has_alias_without_as():
    """Smoke test specifico Oracle: l'alias c'e', ma non il keyword AS."""
    nodes, edges = _simple_canvas(table_name="ORA_SRC", alias="src")
    qg = QueryGenerator(db_type="oracle")
    out = qg.generate_sql(nodes, edges, "result-1", db_type="oracle")

    sql = out["sql"].upper()
    assert "FROM ORA_SRC SRC" in sql, (
        f"L'alias deve essere presente come space-separated. SQL:\n{out['sql']}"
    )
    assert "FROM ORA_SRC AS SRC" not in sql
