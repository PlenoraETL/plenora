from __future__ import annotations

import pytest

from app.core.estrai.query_generator import QueryGenerator
from app.shared.models import Column, Edge, Filter, Node


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_generate_sql_filters_preserve_exact_where_sql_and_params():
    source = Node(
        id="src-1",
        type="source",
        position={"x": 0, "y": 0},
        table_name="EVENTS",
        alias="e",
        columns=[
            Column(name="ID", type="int", visible=True),
            Column(name="NAME", type="str", visible=True),
            Column(name="AMOUNT", type="real", visible=True),
            Column(name="CREATED_AT", type="date", visible=True),
        ],
        filters=[
            Filter(column="ID", operator=">=", value="10"),
            Filter(column="NAME", operator="LIKE", value="Ada"),
            Filter(column="ID", operator="IN", value="1, 2, 003"),
            Filter(column="CREATED_AT", operator="<", value="NOW()"),
        ],
    )
    result = Node(
        id="result-1",
        type="result",
        position={"x": 200, "y": 0},
        fileName="out",
        format="csv",
    )
    edge = Edge(id="e-out", source="src-1", target="result-1", join_type="output")

    out = QueryGenerator(db_type="postgres").generate_sql(
        [source, result],
        [edge],
        "result-1",
        db_type="postgres",
    )

    assert out["sql"] == (
        "SELECT e.ID, e.NAME, e.AMOUNT, e.CREATED_AT\n"
        "FROM EVENTS e\n"
        "WHERE e.ID >= :p_0 AND e.NAME LIKE :p_1 "
        "AND e.ID IN (:p_2, :p_3, :p_4) AND e.CREATED_AT < NOW()"
    )
    assert out["params"] == {
        "p_0": 10,
        "p_1": "%Ada%",
        "p_2": 1,
        "p_3": 2,
        "p_4": 3,
    }


def test_oracle_lob_filter_condition_still_wraps_column_with_to_char():
    qg = QueryGenerator(db_type="oracle")
    params = {}

    condition = qg._crea_condizione(
        table_alias="src",
        column="DESCRIPTION",
        operator="=",
        value="alpha",
        params=params,
        column_type="CLOB",
    )

    assert condition == "TO_CHAR(src.DESCRIPTION) = :p_0"
    assert params == {"p_0": "alpha"}
