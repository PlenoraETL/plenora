"""Corpus di parità per i gruppi E/O dei filtri e per gli alias di Estrai.

Solo INGRESSI: le attese stanno in ``filter_groups_golden.json``, congelate
eseguendo ``generate_filter_groups_golden.py`` sul codice PRIMA dei gruppi E/O
e del self-join (HEAD e05f8fea). Il golden non si rigenera con il codice nuovo:
è la prova che, senza la chiave ``group`` e senza tabelle ripetute, SQL,
parametri, colonne in uscita e DataFrame restano identici a prima.

Copertura dichiarata (i test di enumerazione la verificano):

- :data:`ESTRAI_OPERATORS` = gli operatori del modello ``Filter`` del canvas;
- :data:`ESTRAI_DATE_FUNCTIONS` = una funzione data per ramo di
  ``translate_date_function`` (più quelle riconosciute ma non traducibili);
- :data:`MANIPOLA_OPERATORS` = gli operatori di ``prepare_filter``;
- :data:`ESTRAI_DB_TYPES` = i dialetti di ``translate_date_function``.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

ESTRAI_DB_TYPES = ("postgres", "mssql", "oracle", "mysql", "sqlite")

ESTRAI_OPERATORS = (
    "=", "!=", ">", "<", ">=", "<=", "LIKE", "NOT LIKE", "IN", "NOT IN",
    "IS NULL", "IS NOT NULL", "BETWEEN",
)

ESTRAI_DATE_FUNCTIONS = (
    "NOW()", "GETDATE()", "GETUTCDATE()", "SYSDATETIME()",
    "DATEADD(month, 3, GETDATE())", "DATEADD(day, 7, GETDATE())",
    "ADD_MONTHS(SYSDATE, 2)", "DATE_ADD(NOW(), INTERVAL 4 MONTH)",
    "NOW() + INTERVAL '5 months'", "CURDATE()", "TRUNC(SYSDATE)",
    # Riconosciute come funzione ma non traducibili (errore congelato).
    "CURRENT_DATE", "SYSDATE", "EXTRACT(YEAR FROM x)",
)

MANIPOLA_OPERATORS = (
    "==", "!=", ">", ">=", "<", "<=", "contains", "startswith", "endswith",
    "isnull", "notnull", "in", "not_in",
)

_ESTRAI_COLUMNS = [
    {"name": "ID", "type": "int"},
    {"name": "NAME", "type": "str"},
    {"name": "AMOUNT", "type": "real"},
    {"name": "CREATED_AT", "type": "date"},
]

# Un filtro per riga: (colonna, operatore, valore). Ogni operatore compare
# con più forme di valore (tipi colonna, liste, numeri non convertibili).
_SINGLE_FILTERS: list[tuple[str, str, str]] = [
    ("ID", "=", "10"), ("ID", "=", "abc"), ("NAME", "=", "Ada"), ("AMOUNT", "=", "2.5"),
    ("AMOUNT", "=", "x"), ("CREATED_AT", "=", "2024-01-01"),
    ("ID", "!=", "3"), ("NAME", "!=", "Bob"),
    ("ID", ">", "1"), ("AMOUNT", ">", "1.5"), ("NAME", ">", "M"),
    ("ID", "<", "100"), ("AMOUNT", "<", "9"),
    ("ID", ">=", "10"), ("AMOUNT", ">=", "0"),
    ("ID", "<=", "7"), ("CREATED_AT", "<=", "2024-12-31"),
    ("NAME", "LIKE", "Ada"), ("ID", "LIKE", "1"),
    ("NAME", "NOT LIKE", "x%y"),
    ("ID", "IN", "1, 2, 003"), ("NAME", "IN", "a,b"), ("ID", "IN", "7"), ("NAME", "IN", "solo"),
    ("ID", "IN", ","), ("ID", "IN", ""),
    ("ID", "NOT IN", "4,5"), ("NAME", "NOT IN", "x"),
    ("ID", "IS NULL", ""), ("NAME", "IS NOT NULL", "ignored"),
    ("ID", "BETWEEN", "10 AND 20"), ("AMOUNT", "BETWEEN", "1.5 and 2.5"),
    ("CREATED_AT", "BETWEEN", "2024-01-01 AND 2024-02-01"), ("ID", "BETWEEN", "a AND b"),
    ("ID", "BETWEEN", "10"),
    # Filtri saltati (colonna vuota) e colonna non valida (errore).
    ("", "=", "1"), ("bad-name;", "=", "1"),
]
_SINGLE_FILTERS += [("CREATED_AT", op, fn) for fn in ESTRAI_DATE_FUNCTIONS for op in (">=", "<")]

# Più filtri sullo stesso nodo (oggi in AND, in ordine).
_MULTI_FILTERS: list[list[tuple[str, str, str]]] = [
    [("ID", ">=", "10"), ("NAME", "LIKE", "Ada"), ("ID", "IN", "1, 2, 003"), ("CREATED_AT", "<", "NOW()")],
    [("ID", "IS NULL", ""), ("", "=", "skip"), ("AMOUNT", "BETWEEN", "1 AND 2"), ("NAME", "NOT IN", "a,b")],
    [("ID", "=", "1"), ("ID", "=", "2"), ("ID", "=", "3")],
]


def estrai_filter_cases() -> list[dict[str, Any]]:
    """Casi WHERE: ogni filtro e ogni lista per ogni dialetto, con e senza alias."""
    cases = []
    lists = [[item] for item in _SINGLE_FILTERS] + _MULTI_FILTERS
    for db_type in ESTRAI_DB_TYPES:
        for index, filters in enumerate(lists):
            for alias in ("e", None):
                cases.append({
                    "id": f"{db_type}/{index}/{alias or 'noalias'}",
                    "db_type": db_type,
                    "alias": alias,
                    "filters": [{"column": c, "operator": o, "value": v} for c, o, v in filters],
                })
    return cases


def _source(node_id: str, table: str, alias: str | None, columns: list[dict], **extra: Any) -> dict:
    return {"id": node_id, "type": "source", "position": {"x": 0, "y": 0}, "table_name": table,
            "alias": alias, "columns": columns, **extra}


def _result(**extra: Any) -> dict:
    return {"id": "result-1", "type": "result", "position": {"x": 400, "y": 0},
            "fileName": "out", "format": "csv", **extra}


def _out(source: str) -> dict:
    return {"id": f"out-{source}", "source": source, "target": "result-1", "join_type": "output"}


_ORDERS = [{"name": "ID", "type": "int"}, {"name": "CUSTOMER_ID", "type": "int"}, {"name": "TOTAL", "type": "real"}]
_CUSTOMERS = [{"name": "ID", "type": "int"}, {"name": "NAME", "type": "str", "alias": "CUSTOMER"}]
_CITIES = [{"name": "ID", "type": "int"}, {"name": "geom", "type": "geometry:polygon"}]


def estrai_canvas_cases() -> list[dict[str, Any]]:
    """Canvas SENZA tabelle ripetute: FROM, JOIN, ON, SELECT, GROUP/ORDER/HAVING."""
    join_ab = {"id": "e-join", "source": "a", "target": "b", "join_type": "inner",
               "source_column": "CUSTOMER_ID", "target_column": "ID"}
    cases = [
        {"id": "single-noalias", "nodes": [_source("a", "ORDERS", None, _ORDERS,
                                                    filters=[{"column": "ID", "operator": ">", "value": "1"}]), _result()],
         "edges": [_out("a")]},
        {"id": "single-alias", "nodes": [_source("a", "ORDERS", "o", _ORDERS), _result(distinct=True, limit=5)],
         "edges": [_out("a")]},
        {"id": "join-aliased", "nodes": [_source("a", "ORDERS", "o", _ORDERS), _source("b", "CUSTOMERS", "c", _CUSTOMERS,
                                         filters=[{"column": "NAME", "operator": "LIKE", "value": "Ro"}]), _result()],
         "edges": [join_ab, _out("b")]},
        {"id": "join-noalias-left", "nodes": [_source("a", "ORDERS", None, _ORDERS), _source("b", "CUSTOMERS", None, _CUSTOMERS), _result()],
         "edges": [{**join_ab, "join_type": "left"}, _out("b")]},
        {"id": "join-reversed-conditions", "nodes": [_source("a", "ORDERS", "o", _ORDERS), _source("b", "CUSTOMERS", None, _CUSTOMERS), _result()],
         "edges": [{"id": "e-join", "source": "b", "target": "a", "join_type": "right",
                    "join_conditions": [{"source_column": "ID", "target_column": "CUSTOMER_ID", "source_func": "TRIM"}],
                    "on_filters": [{"column": "TOTAL", "operator": ">", "value": "5"},
                                   {"column": "TOTAL", "operator": "IN", "value": "1,2"}]}, _out("a")]},
        {"id": "cross", "nodes": [_source("a", "ORDERS", "o", _ORDERS), _source("b", "CUSTOMERS", "c", _CUSTOMERS), _result()],
         "edges": [{"id": "e-x", "source": "a", "target": "b", "join_type": "cross"}, _out("b")]},
        {"id": "cross-noalias", "nodes": [_source("a", "ORDERS", None, _ORDERS), _source("b", "CUSTOMERS", None, _CUSTOMERS), _result()],
         "edges": [{"id": "e-x", "source": "a", "target": "b", "join_type": "cross"}, _out("b")]},
        {"id": "spatial", "nodes": [_source("a", "PARCELS", "p", _CITIES), _source("b", "CITIES", None, _CITIES), _result()],
         "edges": [{"id": "e-s", "source": "a", "target": "b", "join_type": "inner", "is_spatial": True,
                    "spatial_predicate": "ST_Within", "source_geom_column": "geom", "target_geom_column": "geom"}, _out("b")]},
        {"id": "three-chain", "nodes": [_source("a", "ORDERS", "o", _ORDERS), _source("b", "CUSTOMERS", "c", _CUSTOMERS),
                                        _source("d", "CITIES", None, _CITIES,
                                                filters=[{"column": "ID", "operator": "IN", "value": "1,2"}]), _result()],
         "edges": [join_ab, {"id": "e-bd", "source": "b", "target": "d", "join_type": "left",
                             "source_column": "ID", "target_column": "ID"}, _out("d")]},
        {"id": "groupby-qualified", "nodes": [
            _source("a", "ORDERS", "o", _ORDERS), _source("b", "CUSTOMERS", "c", _CUSTOMERS),
            _result(group_by_enabled=True, group_by_columns=["c.ID", "CUSTOMER"],
                    aggregations=[{"column": "o.TOTAL", "function": "SUM", "alias": "tot"},
                                  {"column": "*", "function": "COUNT", "alias": "n"}],
                    order_by_enabled=True, order_by_columns=[{"column": "c.ID", "direction": "DESC"}, {"column": "tot"}],
                    having_enabled=True, having_conditions=[{"function": "SUM", "column": "o.TOTAL", "operator": ">", "value": "10"}])],
         "edges": [join_ab, _out("b")]},
        {"id": "calculated", "nodes": [_source("a", "ORDERS", "o", _ORDERS, calculated_columns=[
            {"name": "doppio", "is_manual": True, "sql": "o.TOTAL * 2 AS doppio"},
            {"name": "tre", "expression": "TOTAL + 3"}]), _result()],
         "edges": [_out("a")]},
    ]
    return [{**case, "db_type": db} for case in cases for db in ("postgres", "mssql", "oracle")]


def manipola_frame() -> pd.DataFrame:
    """Colonne di ogni famiglia: interi, reali con NaN, testo con None, misti."""
    return pd.DataFrame({
        "n": [1, 2, 3, 4, 5, 6],
        "x": [1.5, None, 2.5, 3.0, None, 10.0],
        "s": ["alfa", "Beta", None, "gamma", "alfa", "delta"],
        "o": ["a", 1, None, "b", 2.5, "c"],
    }, index=[10, 11, 12, 13, 14, 15])


_MANIPOLA_SINGLE: list[tuple[str, str, Any]] = [
    ("n", "==", 3), ("n", "==", "3"), ("n", "==", "3.0"), ("n", "==", "abc"), ("s", "==", "alfa"), ("x", "==", None),
    ("n", "!=", "2"), ("s", "!=", "alfa"),
    ("n", ">", "2"), ("x", ">", 2), ("s", ">", "b"), ("s", ">", 1), ("o", ">", "a"),
    ("n", ">=", "4"), ("x", ">=", "2.5"),
    ("n", "<", "3"), ("x", "<", "x"),
    ("n", "<=", 2), ("s", "<=", "beta"),
    ("s", "contains", "ALF"), ("o", "contains", "1"), ("s", "contains", "(a+)+"),
    ("s", "startswith", "a"), ("n", "startswith", "1"),
    ("s", "endswith", "a"), ("o", "endswith", ".5"),
    ("x", "isnull", None), ("s", "isnull", "ignored"),
    ("x", "notnull", None), ("o", "notnull", None),
    ("n", "in", "1, 3,5"), ("n", "in", [2, "4"]), ("s", "in", "alfa,gamma"), ("s", "in", ["Beta", None]),
    ("n", "in", "a,b"), ("n", "in", 3), ("x", "in", "1.5,10"),
    ("n", "not_in", "1,2"), ("s", "not_in", ["alfa"]), ("o", "not_in", "a,b"),
    # Errori: colonna assente, operatore fuori elenco.
    ("zz", "==", 1), ("n", "~", 1),
]

_MANIPOLA_MULTI: list[list[tuple[str, str, Any]]] = [
    [("n", ">", "1"), ("s", "contains", "a")],
    [("x", "notnull", None), ("n", "<", 6), ("s", "!=", "delta")],
    [("n", "in", "1,2,3,4"), ("o", "not_in", ["a"]), ("x", "isnull", None)],
    [("s", "startswith", "a"), ("s", "endswith", "x")],
    [("n", ">=", 2), ("o", ">", "a")],
    # Il confronto sulla colonna mista riesce SOLO sulle righe già ristrette
    # dalla condizione precedente (sull'intera colonna fallirebbe): l'AND di
    # un gruppo si valuta in sequenza sulle righe rimaste, come oggi.
    [("n", "<=", 1), ("o", ">", "A")],
    [("n", "==", 1), ("zz", "==", 1)],
    [],
]


def manipola_cases() -> list[dict[str, Any]]:
    """Forma singola (column/operator/value) e lista ``conditions``."""
    cases = []
    for index, (column, operator, value) in enumerate(_MANIPOLA_SINGLE):
        cases.append({"id": f"single/{index}", "params": {"column": column, "operator": operator, "value": value}})
        cases.append({"id": f"conditions1/{index}",
                      "params": {"conditions": [{"column": column, "operator": operator, "value": value}]}})
    for index, items in enumerate(_MANIPOLA_MULTI):
        cases.append({"id": f"conditions/{index}",
                      "params": {"conditions": [{"column": c, "operator": o, "value": v} for c, o, v in items]}})
    # Forme malformate di ``conditions``.
    cases.append({"id": "malformed/not-list", "params": {"conditions": {"column": "n"}}})
    cases.append({"id": "malformed/no-operator", "params": {"conditions": [{"column": "n"}]}})
    return cases
