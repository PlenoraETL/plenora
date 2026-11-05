"""GROUP BY / ORDER BY / aggregazioni / HAVING con ``tabella.colonna`` (Fase 5, T6, R3.16).

Con due tabelle che hanno la stessa colonna (``comuni.id``, ``province.id``) il
pannello dell'Output offre una voce per tabella con il riferimento qualificato;
il generatore lo risolve nella colonna di QUELLA tabella. I nomi semplici
restano risolti come prima (stesso SQL dei progetti salvati).
"""

from __future__ import annotations

import pytest

from app.core.estrai.query_generator import QueryGenerator
from app.core.projects.canvas_models import CanvasState

pytestmark = [pytest.mark.unit, pytest.mark.estrai, pytest.mark.contract]


def _canvas(result_extra):
    def table(node_id, name, cols):
        return {"id": node_id, "type": "source", "position": {"x": 0, "y": 0}, "table_name": name,
                "columns": [{"name": c, "type": "int", "visible": True} for c in cols]}
    return CanvasState(**{
        "nodes": [table("c", "comuni", ["id", "pop"]), table("p", "province", ["id", "area"]),
                  {"id": "out", "type": "result", "position": {"x": 0, "y": 0}, **result_extra}],
        "edges": [{"id": "j", "source": "p", "target": "c", "join_type": "left", "source_column": "id", "target_column": "id"},
                  {"id": "o", "source": "c", "target": "out", "join_type": "output"}],
    })


def _sql(result_extra):
    canvas = _canvas(result_extra)
    return QueryGenerator().generate_sql(canvas.nodes, canvas.edges, "out", db_type="postgres")["sql"]


def test_qualified_references_pick_the_table():
    sql = _sql({
        "group_by_enabled": True, "group_by_columns": ["comuni.id", "province.id"],
        "aggregations": [{"column": "comuni.pop", "function": "SUM", "alias": "tot"}],
        "order_by_enabled": True, "order_by_columns": [{"column": "province.id", "direction": "DESC"}],
        "having_enabled": True, "having_conditions": [{"function": "COUNT", "column": "province.id", "operator": ">", "value": "1"}],
    })
    assert "SELECT comuni.id, province.id, SUM(comuni.pop) AS tot" in sql
    assert "GROUP BY comuni.id, province.id" in sql
    assert "HAVING COUNT(province.id) > :p_0" in sql
    assert "ORDER BY province.id DESC" in sql


def test_plain_names_keep_the_previous_resolution():
    sql = _sql({"group_by_enabled": True, "group_by_columns": ["id"],
                "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}]})
    # Regola di sempre (table_map): l'ultima tabella con quel nome.
    assert "GROUP BY comuni.id" in sql or "GROUP BY province.id" in sql
    assert sql == _sql({"group_by_enabled": True, "group_by_columns": ["id"],
                        "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}]})


def test_qualified_reference_to_hidden_or_unknown_column_is_not_resolved():
    with pytest.raises(ValueError):
        _sql({"group_by_enabled": True, "group_by_columns": ["comuni.area"],
              "aggregations": [{"column": "*", "function": "COUNT", "alias": "n"}]})
