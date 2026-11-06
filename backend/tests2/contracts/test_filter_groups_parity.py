"""Gruppi E/O dei filtri (06/11/2026): parità con il codice di prima + regola nuova.

REGOLA (``app.shared.execution.condition_groups``): ``group`` facoltativo,
intero >= 0 (assente = 0); stesso gruppo in AND, gruppi diversi in OR, gruppi
in ordine di prima apparizione. Una sola funzione per Estrai (WHERE) e
Manipola (Filtra).

- PARITÀ: il corpus ``filter_groups_corpus`` (ogni operatore Estrai e
  Manipola, ogni dialetto, funzioni data, errori) produce SQL, parametri,
  colonne e DataFrame del golden congelato su HEAD e05f8fea, anche con
  ``group`` esplicito uguale per tutte le condizioni.
- ENUMERAZIONE: ogni operatore accettato dal modello ``Filter`` e da
  ``prepare_filter`` è nel corpus; il corpus usa davvero ogni operatore.
- GRUPPI: SQL atteso e righe attese con più gruppi; gruppi vuoti ignorati;
  ``group`` non valido rifiutato con lo stesso messaggio ovunque.
- NA (B-K1, ``dataframes.tabular.selection_mask``): con colonne nullable
  (Int64/Float64/string/boolean con ``<NA>``) ogni operatore, in forma
  singola, in un gruppo e in più gruppi, e il Condizionale danno l'esito
  congelato da e05f8fea (``filter_nullable_golden.json``): un valore mancante
  rende la condizione falsa, mai un'eccezione.
"""
from __future__ import annotations

import copy
import json
import typing

import pandas as pd
import pytest
from pydantic import ValidationError

import filter_groups_corpus as corpus
import generate_filter_groups_golden as generator
from app.core.eseguibili.binding_capabilities import bindable_targets
from app.core.estrai.query_generator import QueryGenerator
from app.core.manipola.node_validator import ManipolaNodeValidator
from app.core.dataframes.tabular import selection_mask
from app.core.manipola.transforms import filtering
from app.core.manipola.transforms.filtering import FILTER_OPERATORS, op_filter
from app.core.projects.canvas_models import Edge, Filter, Node
from app.shared.execution.condition_groups import condition_group, condition_groups
from app.shared.execution.schema_types import InferredSchema

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]

GOLDEN = json.loads(generator.GOLDEN_PATH.read_text(encoding="utf-8"))
NULLABLE_GOLDEN = json.loads(generator.NULLABLE_GOLDEN_PATH.read_text(encoding="utf-8"))
_NULLABLE_CASES = {case["id"]: case for case in corpus.manipola_nullable_cases()}
_NULLABLE_CONDITIONAL_CASES = {case["id"]: case for case in corpus.manipola_nullable_conditional_cases()}
_ESTRAI_CASES = {case["id"]: case for case in corpus.estrai_filter_cases()}
_CANVAS_CASES = {f"{case['db_type']}/{case['id']}": case for case in corpus.estrai_canvas_cases()}
_MANIPOLA_CASES = {case["id"]: case for case in corpus.manipola_cases()}


# ---------------------------------------------------------------- enumerazione


def test_corpus_and_golden_have_the_same_cases():
    assert set(GOLDEN["estrai_filters"]) == set(_ESTRAI_CASES)
    assert set(GOLDEN["estrai_canvas"]) == set(_CANVAS_CASES)
    assert set(GOLDEN["manipola"]) == set(_MANIPOLA_CASES)


def test_estrai_operators_are_the_filter_model_operators_and_all_in_corpus():
    model_ops = set(typing.get_args(Filter.model_fields["operator"].annotation))
    assert set(corpus.ESTRAI_OPERATORS) == model_ops
    used = {item["operator"] for case in _ESTRAI_CASES.values() for item in case["filters"]}
    assert model_ops <= used, sorted(model_ops - used)
    # Ogni operatore produce almeno un SQL riuscito nel golden (non solo errori).
    ok = {item["operator"] for cid, case in _ESTRAI_CASES.items() if "sql" in GOLDEN["estrai_filters"][cid]
          for item in case["filters"]}
    assert model_ops <= ok, sorted(model_ops - ok)


def test_every_date_function_and_dialect_is_in_corpus():
    values = {(case["db_type"], item["value"]) for case in _ESTRAI_CASES.values() for item in case["filters"]}
    for db_type in corpus.ESTRAI_DB_TYPES:
        for function in corpus.ESTRAI_DATE_FUNCTIONS:
            assert (db_type, function) in values


def test_manipola_operators_are_prepare_filter_operators_and_all_in_corpus():
    assert set(corpus.MANIPOLA_OPERATORS) == set(FILTER_OPERATORS)
    for form in ("single", "conditions1"):
        used = {(case["params"].get("operator") or case["params"].get("conditions", [{}])[0].get("operator"))
                for cid, case in _MANIPOLA_CASES.items() if cid.startswith(f"{form}/")}
        assert set(FILTER_OPERATORS) <= used, (form, sorted(set(FILTER_OPERATORS) - used))


# ---------------------------------------------------------------- parità


def _with_group(filters: list[dict], group: int | None) -> list[dict]:
    return [{**item, "group": group} for item in filters]


@pytest.mark.parametrize("group", [None, 0, 3], ids=["absent", "zero", "same"])
@pytest.mark.parametrize("case_id", sorted(_ESTRAI_CASES))
def test_estrai_where_equals_golden(case_id, group):
    case = copy.deepcopy(_ESTRAI_CASES[case_id])
    if group is not None:
        case["filters"] = _with_group(case["filters"], group)
    assert generator.estrai_filter_outcome(case) == GOLDEN["estrai_filters"][case_id]


@pytest.mark.parametrize("case_id", sorted(_CANVAS_CASES))
def test_estrai_canvas_without_repeated_tables_equals_golden(case_id):
    assert generator.estrai_canvas_outcome(_CANVAS_CASES[case_id]) == GOLDEN["estrai_canvas"][case_id]


@pytest.mark.parametrize("group", [None, 0, 2], ids=["absent", "zero", "same"])
@pytest.mark.parametrize("case_id", sorted(_MANIPOLA_CASES))
def test_manipola_filter_equals_golden(case_id, group):
    case = copy.deepcopy(_MANIPOLA_CASES[case_id])
    conditions = case["params"].get("conditions")
    if group is not None and isinstance(conditions, list) and all(isinstance(c, dict) for c in conditions):
        case["params"]["conditions"] = _with_group(conditions, group)
    assert generator.manipola_outcome(case) == GOLDEN["manipola"][case_id]


# ---------------------------------------------------------------- gruppi Estrai


def _estrai_sql(filters: list[dict], *, other_filters: list[dict] | None = None, db_type: str = "postgres") -> dict:
    nodes = [Node(**corpus._source("src", "EVENTS", "e", corpus._ESTRAI_COLUMNS, filters=filters)), Node(**corpus._result())]
    edges = [Edge(**corpus._out("src"))]
    if other_filters is not None:
        nodes.append(Node(**corpus._source("oth", "PEOPLE", "p", corpus._ESTRAI_COLUMNS, filters=other_filters)))
        edges.append(Edge(id="j", source="oth", target="src", join_type="inner", source_column="ID", target_column="ID"))
    return QueryGenerator(db_type=db_type).generate_sql(nodes, edges, "result-1")


def _where(out: dict) -> str:
    return out["sql"].split("\nWHERE ", 1)[1]


def test_estrai_groups_are_or_of_and_in_first_appearance_order():
    out = _estrai_sql([
        {"column": "ID", "operator": ">", "value": "1", "group": 2},
        {"column": "NAME", "operator": "LIKE", "value": "Ada", "group": 0},
        {"column": "ID", "operator": "<", "value": "9", "group": 2},
        {"column": "AMOUNT", "operator": "IS NULL", "value": "", "group": 0},
    ])
    assert _where(out) == "((e.ID > :p_0 AND e.ID < :p_1) OR (e.NAME LIKE :p_2 AND e.AMOUNT IS NULL))"
    assert out["params"] == {"p_0": 1, "p_1": 9, "p_2": "%Ada%"}


def test_estrai_nodes_stay_in_and_and_each_node_has_its_own_groups():
    out = _estrai_sql(
        [{"column": "ID", "operator": "=", "value": "1"}, {"column": "ID", "operator": "=", "value": "2", "group": 1}],
        other_filters=[{"column": "NAME", "operator": "IN", "value": "a,b", "group": 4},
                       {"column": "ID", "operator": "!=", "value": "3", "group": 4}],
    )
    assert _where(out) == "p.NAME IN (:p_0, :p_1) AND p.ID != :p_2 AND ((e.ID = :p_3) OR (e.ID = :p_4))"


def test_estrai_group_with_only_skipped_conditions_is_ignored():
    filters = [{"column": "ID", "operator": ">=", "value": "10"}, {"column": "", "operator": "=", "value": "x", "group": 1}]
    assert _where(_estrai_sql(filters)) == "e.ID >= :p_0"
    assert _estrai_sql(filters)["sql"] == _estrai_sql(filters[:1])["sql"]


def test_estrai_single_non_zero_group_is_the_plain_and():
    filters = [{"column": "ID", "operator": ">", "value": "1"}, {"column": "NAME", "operator": "=", "value": "x"}]
    assert _estrai_sql(_with_group(filters, 7)) == _estrai_sql(filters)


def test_estrai_binding_index_is_the_flat_list_index():
    """``filters.N.value`` (binding runtime) indica la voce N della lista piatta."""
    filters = [{"column": "ID", "operator": "=", "value": "1", "group": 1},
               {"column": "ID", "operator": "IS NULL", "value": "", "group": 0},
               {"column": "NAME", "operator": "=", "value": "old", "group": 1}]
    node = corpus._source("src", "EVENTS", "e", corpus._ESTRAI_COLUMNS, filters=filters)
    paths = [target.path for target in bindable_targets(node)]
    assert paths == ["filters.0.value", "filters.2.value"]
    node["filters"][2]["value"] = "nuovo"  # scrittura della risoluzione a filters.2.value
    out = QueryGenerator().generate_sql([Node(**node), Node(**corpus._result())], [Edge(**corpus._out("src"))], "result-1")
    assert _where(out) == "((e.ID = :p_0 AND e.NAME = :p_1) OR (e.ID IS NULL))"
    assert out["params"] == {"p_0": 1, "p_1": "nuovo"}


def test_estrai_filter_model_keeps_group_through_round_trip():
    node = Node(**corpus._source("src", "EVENTS", None, [], filters=[{"column": "ID", "operator": "=", "value": "1", "group": 2}]))
    dumped = node.model_dump()["filters"][0]
    assert dumped["group"] == 2
    assert Node(**{**node.model_dump(), "filters": [dumped]}).filters[0].group == 2
    assert Node(**corpus._source("src", "EVENTS", None, [], filters=[{"column": "ID", "operator": "="}])).filters[0].group is None


# ---------------------------------------------------------------- gruppi Manipola


def _frame_rows(frame: pd.DataFrame) -> list[int]:
    assert list(frame.index) == list(range(len(frame)))
    return frame["n"].tolist()


def test_manipola_groups_are_or_of_and_in_original_row_order():
    conditions = [
        {"column": "n", "operator": ">=", "value": "5", "group": 1},
        {"column": "s", "operator": "==", "value": "alfa", "group": 0},
        {"column": "x", "operator": "notnull", "value": None, "group": 0},
        {"column": "s", "operator": "==", "value": "delta", "group": 1},
    ]
    # g1: n>=5 AND s==delta -> 6 ; g0: s==alfa AND x notnull -> 1. Ordine originale.
    assert _frame_rows(op_filter(corpus.manipola_frame(), conditions=conditions)) == [1, 6]


def test_manipola_overlapping_groups_keep_each_row_once():
    conditions = [{"column": "n", "operator": "<=", "value": 3}, {"column": "n", "operator": ">=", "value": 2, "group": 1}]
    result = op_filter(corpus.manipola_frame(), conditions=conditions)
    assert _frame_rows(result) == [1, 2, 3, 4, 5, 6]
    assert list(result.dtypes.astype(str)) == list(corpus.manipola_frame().dtypes.astype(str))


def test_manipola_group_and_is_sequential_inside_the_group():
    """Nel gruppo l'AND vede solo le righe rimaste (come il filtro a catena)."""
    conditions = [{"column": "n", "operator": "<=", "value": 1, "group": 0},
                  {"column": "o", "operator": ">", "value": "A", "group": 0},
                  {"column": "s", "operator": "==", "value": "delta", "group": 1}]
    assert _frame_rows(op_filter(corpus.manipola_frame(), conditions=conditions)) == [1, 6]


def test_manipola_error_in_any_group_is_the_same_error():
    conditions = [{"column": "n", "operator": "==", "value": 1}, {"column": "o", "operator": ">", "value": "a", "group": 1}]
    with pytest.raises(ValueError, match="operatore '>' non applicabile sulla colonna 'o'"):
        op_filter(corpus.manipola_frame(), conditions=conditions)


# ---------------------------------------------------------------- group non valido


_BAD_GROUPS = [-1, True, "1", 1.5, [0]]


@pytest.mark.parametrize("bad", _BAD_GROUPS, ids=repr)
def test_invalid_group_is_rejected_by_the_single_rule(bad):
    message = "'group' deve essere un numero intero maggiore o uguale a 0"
    with pytest.raises(ValueError, match=message):
        condition_groups([{"column": "a", "group": 0}, {"column": "b", "group": bad}])
    with pytest.raises(ValueError, match=message):
        op_filter(corpus.manipola_frame(), conditions=[{"column": "n", "operator": "notnull", "group": bad}])
    with pytest.raises(ValidationError):
        Filter(column="ID", operator="=", value="1", group=bad)


@pytest.mark.parametrize("bad", _BAD_GROUPS, ids=repr)
def test_manipola_validation_rejects_invalid_group_like_execution(bad):
    node = type("N", (), {"id": "f1", "type": "transform", "data": {}, "transform_op": "filter",
                          "config": {"conditions": [{"column": "n", "operator": "notnull", "group": bad}]}})()
    upstream = InferredSchema(columns=[{"name": "n", "type": "int"}], status="certain")
    issues = ManipolaNodeValidator().validate_node(node, upstream=upstream)
    assert [(issue.severity, issue.code) for issue in issues] == [("error", "preparation.invalid")]
    assert "'group'" in issues[0].message


def test_manipola_validation_accepts_groups():
    node = type("N", (), {"id": "f1", "type": "transform", "data": {}, "transform_op": "filter",
                          "config": {"conditions": [{"column": "n", "operator": "notnull", "group": 1},
                                                    {"column": "n", "operator": ">", "value": 1}]}})()
    upstream = InferredSchema(columns=[{"name": "n", "type": "int"}], status="certain")
    assert ManipolaNodeValidator().validate_node(node, upstream=upstream) == []


def test_condition_groups_shape():
    items = [{"k": 1}, {"k": 2, "group": 3}, {"k": 3, "group": None}, {"k": 4, "group": 3}]
    assert condition_groups(items) == [[items[0], items[2]], [items[1], items[3]]]
    assert condition_groups([]) == [] and condition_groups(None) == []
    assert condition_group(Filter(column="a", operator="=", group=5)) == 5


# ---------------------------------------------------------------- NA nelle maschere (B-K1)


def test_nullable_corpus_covers_every_operator_dtype_and_form():
    frame = corpus.manipola_nullable_frame()
    assert {col: str(dtype) for col, dtype in frame.dtypes.items()} == corpus.NULLABLE_DTYPES
    assert all(frame[col].isna().any() for col in frame.columns)
    assert set(_NULLABLE_CASES) == set(NULLABLE_GOLDEN["filter"])
    assert set(_NULLABLE_CONDITIONAL_CASES) == set(NULLABLE_GOLDEN["conditional"])
    for form in ("single", "group1", "group1and", "groups2", "groups3"):
        for column in corpus.NULLABLE_DTYPES:
            for operator in FILTER_OPERATORS:
                assert f"{form}/{column}/{operator}" in _NULLABLE_CASES
    for column in corpus.NULLABLE_DTYPES:
        for operator in corpus.CONDITIONAL_OPERATORS:
            assert f"conditional/{column}/{operator}" in _NULLABLE_CONDITIONAL_CASES
    # Il golden congela esiti riusciti: nessuno dei casi falliva con il codice di e05f8fea.
    assert not [cid for cid, out in NULLABLE_GOLDEN["filter"].items() if "error" in out]


@pytest.mark.parametrize("case_id", sorted(_NULLABLE_CASES))
def test_manipola_filter_with_nullable_columns_equals_e05f8fea(case_id):
    assert generator.manipola_nullable_outcome(filtering, _NULLABLE_CASES[case_id]) == NULLABLE_GOLDEN["filter"][case_id]


@pytest.mark.parametrize("case_id", sorted(_NULLABLE_CONDITIONAL_CASES))
def test_manipola_conditional_with_nullable_columns_equals_e05f8fea(case_id):
    outcome = generator.manipola_nullable_conditional_outcome(filtering, _NULLABLE_CONDITIONAL_CASES[case_id])
    assert outcome == NULLABLE_GOLDEN["conditional"][case_id]


def test_selection_mask_is_the_single_na_rule():
    nullable = pd.array([True, None, False], dtype="boolean")
    assert selection_mask(pd.Series(nullable)).tolist() == [True, False, False]
    assert selection_mask(pd.Series([True, None, float("nan")], dtype=object)).tolist() == [True, False, False]
    assert selection_mask(pd.Series([True, False])).dtype == bool


def test_filter_and_conditional_build_masks_only_through_selection_mask():
    """Guardia: nessuna conversione a bool scritta a mano nel modulo dei filtri."""
    import inspect
    source = inspect.getsource(filtering)
    assert "dtype=bool" not in source and "astype(bool)" not in source
    assert source.count("selection_mask(") >= 3
