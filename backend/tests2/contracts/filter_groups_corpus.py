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


# ---------------------------------------------------------------- colonne nullable (B-K1)
#
# Regola dei NA nelle maschere (06/11/2026, ``dataframes.tabular.selection_mask``):
# una condizione su un valore mancante di una colonna nullable è FALSA, come
# ``df[mask]`` di e05f8fea. Attese in ``filter_nullable_golden.json``, congelate
# eseguendo il Filtra/Condizionale di e05f8fea (``generate_filter_groups_golden
# --nullable``): forma singola e un gruppo = ``op_filter`` di allora; più gruppi
# = unione, nell'ordine originale, delle righe di ogni gruppo filtrato a catena
# con ``op_filter`` di allora (la semantica dichiarata dei gruppi O).

NULLABLE_DTYPES = {"i": "Int64", "f": "Float64", "t": "string", "b": "boolean"}


def manipola_nullable_frame() -> pd.DataFrame:
    """Una colonna per dtype nullable, ognuna con valori mancanti (``<NA>``)."""
    return pd.DataFrame({
        "i": pd.array([1, None, 3, 4, None, 6, 3], dtype="Int64"),
        "f": pd.array([1.5, None, 2.5, 3.0, None, 10.0, 2.5], dtype="Float64"),
        "t": pd.array(["alfa", None, "Beta", "gamma", None, "alfa", "delta"], dtype="string"),
        "b": pd.array([True, None, False, True, None, False, True], dtype="boolean"),
    }, index=[20, 21, 22, 23, 24, 25, 26])


# Per colonna: un valore per ogni operatore (``None`` dove l'operatore non lo legge).
_NULLABLE_VALUES: dict[str, dict[str, Any]] = {
    "i": {"==": 3, "!=": "3", ">": "2", ">=": 4, "<": "4", "<=": 3, "contains": "1", "startswith": "3",
          "endswith": "4", "isnull": None, "notnull": None, "in": "1,3", "not_in": [1, 4]},
    "f": {"==": 2.5, "!=": "2.5", ">": 2, ">=": "2.5", "<": "3", "<=": 10, "contains": ".5", "startswith": "1",
          "endswith": "0", "isnull": None, "notnull": None, "in": "1.5,10", "not_in": [2.5]},
    "t": {"==": "alfa", "!=": "alfa", ">": "b", ">=": "Beta", "<": "b", "<=": "beta", "contains": "AL",
          "startswith": "a", "endswith": "a", "isnull": None, "notnull": None, "in": "alfa,gamma",
          "not_in": ["alfa", None]},
    "b": {"==": True, "!=": True, ">": False, ">=": True, "<": True, "<=": False, "contains": "True",
          "startswith": "F", "endswith": "e", "isnull": None, "notnull": None, "in": "1", "not_in": [True]},
}

# Secondo gruppo dei casi a più gruppi, scelto su un'altra colonna con NA.
_OTHER_GROUP = {"i": ("t", "==", "delta"), "f": ("b", "==", False), "t": ("i", ">", "5"), "b": ("f", "<", 2)}


def manipola_nullable_cases() -> list[dict[str, Any]]:
    """Ogni operatore × ogni colonna nullable: forma singola, un gruppo, più gruppi."""
    cases = []
    for column, values in _NULLABLE_VALUES.items():
        for operator in MANIPOLA_OPERATORS:
            value = values[operator]
            condition = {"column": column, "operator": operator, "value": value}
            other_column, other_operator, other_value = _OTHER_GROUP[column]
            other = {"column": other_column, "operator": other_operator, "value": other_value}
            cases.append({"id": f"single/{column}/{operator}",
                          "params": {"column": column, "operator": operator, "value": value}})
            cases.append({"id": f"group1/{column}/{operator}", "params": {"conditions": [condition]}})
            # Un gruppo con due condizioni in E: la seconda vede le righe rimaste.
            cases.append({"id": f"group1and/{column}/{operator}", "params": {"conditions": [other, condition]}})
            cases.append({"id": f"groups2/{column}/{operator}", "params": {"conditions": [
                {**condition, "group": 0}, {**other, "group": 1},
            ]}})
            cases.append({"id": f"groups3/{column}/{operator}", "params": {"conditions": [
                {"column": "t", "operator": "isnull", "value": None, "group": 2},
                {**condition, "group": 0},
                {"column": "b", "operator": "==", "value": True, "group": 1},
                {**other, "group": 0},
            ]}})
    return cases


# Condizionale (CASE WHEN) sulle stesse colonne: ogni operatore di
# ``_evaluate_condition`` su ogni colonna nullable.
CONDITIONAL_OPERATORS = (
    "==", "!=", ">", ">=", "<", "<=", "contains", "startswith", "endswith", "isnull", "notnull", "between",
)


def manipola_nullable_conditional_cases() -> list[dict[str, Any]]:
    cases = []
    for column, values in _NULLABLE_VALUES.items():
        for operator in CONDITIONAL_OPERATORS:
            value = "1,3" if operator == "between" else values[operator]
            cases.append({"id": f"conditional/{column}/{operator}", "params": {
                "column": column, "default_value": "N", "output_column": "esito",
                "conditions": [{"operator": operator, "value": value, "result": "S"},
                               {"operator": "isnull", "value": None, "result": "vuoto"}]}})
    return cases
