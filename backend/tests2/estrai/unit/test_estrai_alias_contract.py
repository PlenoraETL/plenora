"""Self-join di Estrai: alias univoci per costruzione (06/11/2026).

REGOLA (``app.core.estrai.query_aliases``): l'alias effettivo è
``effective_alias(node)`` (alias, altrimenti nome tabella); prima della
generazione ``with_unique_aliases`` dà a ogni nodo successivo con un alias già
preso l'alias automatico ``{tabella}_{n}`` (n = 2, 3, ... saltando quelli
presi o dichiarati), su copie. FROM/JOIN scrivono ``tabella alias``; ON,
WHERE, SELECT usano l'alias; le colonne omonime seguono la regola di sempre
delle colonne in uscita (``ID``, ``ID_2``) e /schemas coincide con il SQL.

ENUMERAZIONE: nessun ``*node.alias or`` in ``app/core/estrai`` fuori da
``query_aliases``; le tabelle della query si ottengono solo da
``QueryGenerator.query_tables`` (SQL e /schemas). La parità dei canvas senza
tabelle ripetute è in ``tests2/contracts/test_filter_groups_parity.py``
(golden congelato su HEAD e05f8fea).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.estrai.query_aliases import effective_alias, with_unique_aliases
from app.core.estrai.query_generator import QueryGenerator
from app.core.projects.canvas_models import Edge, Node
from app.core.projects.schema_execution_sources import sql_projection

pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]

_APP = Path(__file__).resolve().parents[3] / "app"
_COLUMNS = [{"name": "ID", "type": "int"}, {"name": "PARENT_ID", "type": "int"}, {"name": "NAME", "type": "str"}]


def _src(node_id: str, alias: str | None = None, table: str = "EMP", **extra) -> Node:
    return Node(id=node_id, type="source", position={"x": 0, "y": 0}, table_name=table, alias=alias,
                columns=_COLUMNS, **extra)


def _result(**extra) -> Node:
    return Node(id="r", type="result", position={"x": 0, "y": 0}, **extra)


def _join(source: str, target: str, **extra) -> Edge:
    return Edge(id=f"j-{source}-{target}", source=source, target=target, join_type="inner",
                source_column="PARENT_ID", target_column="ID", **extra)


def _out(source: str) -> Edge:
    return Edge(id=f"o-{source}", source=source, target="r", join_type="output")


def _generate(nodes, edges, db_type="postgres"):
    out = QueryGenerator(db_type).generate_sql(nodes, edges, "r")
    names = [col["name"] for col in sql_projection(nodes, edges, nodes[-1], db_type)]
    return out, names


def test_self_join_without_aliases_gets_automatic_alias_and_distinct_columns():
    a = _src("a", filters=[{"column": "NAME", "operator": "=", "value": "x"}])
    b = _src("b", filters=[{"column": "NAME", "operator": "IS NULL", "value": ""}])
    out, names = _generate([a, b, _result()], [_join("a", "b"), _out("b")])
    assert out["sql"] == (
        "SELECT EMP.ID, EMP.PARENT_ID, EMP.NAME, EMP_2.ID, EMP_2.PARENT_ID, EMP_2.NAME\n"
        "FROM EMP\n"
        "INNER JOIN EMP EMP_2 ON EMP.PARENT_ID = EMP_2.ID\n"
        "WHERE EMP.NAME = :p_0 AND EMP_2.NAME IS NULL"
    )
    assert names == ["id", "parent_id", "name", "id_2", "parent_id_2", "name_2"]
    assert out["tables_used"] == ["EMP", "EMP"]
    # Copie di lavoro: il canvas non cambia.
    assert a.alias is None and b.alias is None


def test_self_join_with_distinct_aliases_keeps_them():
    out, names = _generate([_src("a", "capo"), _src("b", "dip"), _result()], [_join("a", "b"), _out("b")], "mssql")
    assert out["sql"] == (
        "SELECT capo.ID, capo.PARENT_ID, capo.NAME, dip.ID, dip.PARENT_ID, dip.NAME\n"
        "FROM dbo.EMP capo\n"
        "INNER JOIN dbo.EMP dip ON capo.PARENT_ID = dip.ID"
    )
    assert names == ["ID", "PARENT_ID", "NAME", "ID_2", "PARENT_ID_2", "NAME_2"]


def test_repeated_alias_and_declared_automatic_name_are_skipped():
    # b ripete l'alias di a; c dichiara gia' EMP_2: b riceve EMP_3.
    nodes = [_src("a", "e"), _src("b", "E"), _src("c", "EMP_2"), _result()]
    edges = [_join("a", "b"), _join("b", "c"), _out("c")]
    out, names = _generate(nodes, edges)
    assert "FROM EMP e\nINNER JOIN EMP EMP_3 ON e.PARENT_ID = EMP_3.ID\nINNER JOIN EMP EMP_2 ON EMP_3.PARENT_ID = EMP_2.ID" in out["sql"]
    assert names == ["id", "parent_id", "name", "id_2", "parent_id_2", "name_2", "id_3", "parent_id_3", "name_3"]


def test_alias_equal_to_another_table_name_is_renamed():
    nodes = [_src("a", "CITY", table="EMP"), _src("b", None, table="CITY"), _result()]
    out, _ = _generate(nodes, [_join("a", "b"), _out("b")])
    assert "FROM EMP CITY\nINNER JOIN CITY CITY_2 ON CITY.PARENT_ID = CITY_2.ID" in out["sql"]


def test_self_join_group_order_and_on_filters_use_the_alias():
    result = _result(group_by_enabled=True, group_by_columns=["EMP_2.NAME"],
                     aggregations=[{"column": "EMP.ID", "function": "COUNT", "alias": "n"}],
                     order_by_enabled=True, order_by_columns=[{"column": "EMP_2.NAME", "direction": "ASC"}])
    edge = _join("a", "b", on_filters=[{"column": "NAME", "operator": "=", "value": "y"}])
    out, names = _generate([_src("a"), _src("b"), result], [edge, _out("b")])
    assert out["sql"] == (
        "SELECT EMP_2.NAME, COUNT(EMP.ID) AS n\n"
        "FROM EMP\n"
        "INNER JOIN EMP EMP_2 ON EMP.PARENT_ID = EMP_2.ID AND EMP_2.NAME = 'y'\n"
        "GROUP BY EMP_2.NAME\n"
        "ORDER BY EMP_2.NAME ASC"
    )
    assert names == ["name", "n"]


def test_cross_and_spatial_self_join():
    cross = Edge(id="x", source="a", target="b", join_type="cross")
    out, _ = _generate([_src("a"), _src("b"), _result()], [cross, _out("b")])
    assert "FROM EMP\nCROSS JOIN EMP EMP_2" in out["sql"]
    spatial = Edge(id="s", source="a", target="b", join_type="inner", is_spatial=True,
                   spatial_predicate="ST_Within", source_geom_column="geom", target_geom_column="geom")
    out, _ = _generate([_src("a"), _src("b"), _result()], [spatial, _out("b")])
    assert "INNER JOIN EMP EMP_2 ON " in out["sql"] and "EMP.geom" in out["sql"] and "EMP_2.geom" in out["sql"]


def test_automatic_alias_base_is_a_plain_identifier():
    nodes = with_unique_aliases([_src("a", table="dbo.my-tab"), _src("b", table="dbo.my-tab"), _src("c", table="1x"), _src("d", table="1x")])
    assert [effective_alias(n) for n in nodes] == ["dbo.my-tab", "my_tab_2", "1x", "t_1x_2"]


def test_without_repeated_aliases_the_same_nodes_are_returned():
    nodes = [_src("a", "x"), _src("b", None, table="OTHER"), _src("c", None)]
    assert all(left is right for left, right in zip(with_unique_aliases(nodes), nodes))


# ---------------------------------------------------------------- enumerazione


def _sources(root: Path):
    for path in sorted(root.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def test_effective_alias_is_the_only_alias_fallback_in_estrai():
    pattern = re.compile(r"\b\w*node\.alias\s+or\b")
    offenders = [f"{path.name}:{n}" for path, text in _sources(_APP / "core" / "estrai")
                 if path.name != "query_aliases.py"
                 for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
    assert offenders == []


def test_query_tables_is_the_only_table_sequence():
    """SQL e /schemas prendono le tabelle solo da ``query_tables`` (alias univoci)."""
    pattern = re.compile(r"\._(trova_tabelle_connesse|riordina_per_dipendenze_join)\(")
    offenders = []
    for path, text in _sources(_APP):
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_APP).as_posix()}:{n}")
    body = (_APP / "core" / "estrai" / "query_generator.py").read_text(encoding="utf-8")
    start = body.index("    def query_tables(")
    end = body.index("\n    def ", start + 1)
    lines = body[:start].count("\n") + 1, body[:end].count("\n") + 1
    assert all(o.startswith("core/estrai/query_generator.py:") and lines[0] <= int(o.rsplit(":", 1)[1]) <= lines[1]
               for o in offenders), offenders
    assert len(offenders) == 2
