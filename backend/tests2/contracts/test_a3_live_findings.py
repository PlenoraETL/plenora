"""A3, controllo dal vivo (SC_scheda_manipola sul server): due difetti trovati
sul canvas reale e chiusi per costruzione.

1. Tipi dello schema -> dtype dell'esecuzione a vuoto: ``datetime`` (e gli
   altri tipi dell'inferenza fuori dal vocabolario minimo) diventava
   ``object``, e Valida rifiutava una Formula ``ct_date_due.dt.year * 10000
   + ...`` che l'esecuzione esegue. Regola: famiglia del tipo con la stessa
   tabella dell'inferenza (``type_families.json``) -> dtype.
2. Selezione dell'Output per nome con due colonne omonime (dopo una Rinomina
   ``name``/``city_id`` -> ``Comune``): l'esecuzione (``df[[nomi]]``) le
   prende entrambe e il salvataggio le scrive ``Comune``/``Comune_2``; la
   previsione ne teneva una. Regola: ``expand_column_plan``.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.dataframes.output_columns import apply_output_columns
from app.core.dataframes.schema import canvas_type_to_pandas_dtype, series_to_canvas_type
from app.core.dataframes.tabular import make_unique_column_names
from app.core.manipola.node_validator import ManipolaNodeValidator
from app.core.manipola.plugin_executor import get_executor
from app.core.projects.canvas_models import CanvasState, Node
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.schema_column_plans import TYPE_FAMILIES, type_family
from app.shared.execution.schema_types import InferredSchema

pytestmark = [pytest.mark.unit, pytest.mark.contract]

# date e datetime sono la stessa famiglia per pandas (datetime64).
_SAME = {"date": "datetime"}


@pytest.mark.parametrize("canvas_type", sorted(TYPE_FAMILIES["aliases"]))
def test_every_inference_type_maps_to_a_dtype_of_its_family(canvas_type):
    dtype = canvas_type_to_pandas_dtype(canvas_type)
    assert dtype != "object", f"{canvas_type}: nessun dtype (diventerebbe object)"
    produced = type_family(series_to_canvas_type(pd.Series([], dtype=dtype)))
    expected = type_family(canvas_type)
    assert _SAME.get(produced, produced) == _SAME.get(expected, expected), (canvas_type, dtype, produced)


@pytest.mark.parametrize("kind", ["datetime", "timestamp", "date"])
def test_formula_dt_accessor_on_datetime_schema_is_valid(kind):
    formula = "ct_date_due.dt.year * 10000 + ct_date_due.dt.month * 100 + ct_date_due.dt.day"
    config = {"formula": formula, "new_column": "chiave_data"}
    rows = pd.DataFrame({"ct_date_due": pd.to_datetime(["2024-01-02", None])})
    get_executor().execute_with_params("formula", [rows], config)  # l'esecuzione la accetta
    upstream = InferredSchema(columns=[{"name": "ct_date_due", "type": kind}])
    node = Node.model_validate({"id": "f", "type": "transform", "position": {"x": 0, "y": 0},
                                "transform_op": "formula", "config": config})
    issues = ManipolaNodeValidator().validate_node(node, upstream=upstream, inputs=[upstream])
    assert [i.message for i in issues if i.severity == "error"] == []


@pytest.mark.parametrize("selection", [
    [{"name": "Comune", "keep": True}],
    [{"name": "id", "keep": True}, {"name": "Comune", "keep": True, "label": "Città"}],
    [{"name": "Comune", "keep": False}],
])
def test_output_selection_with_duplicate_names_predicts_the_saved_file(selection):
    frame = pd.DataFrame({"id": [1], "name": ["Roma"], "city_id": [7]})
    rename = {"renames": [{"old_name": "name", "new_name": "Comune"}, {"old_name": "city_id", "new_name": "Comune"}]}
    renamed = get_executor().execute_with_params("rename", [frame], rename)
    selected = apply_output_columns(renamed, selection)
    saved, _ = make_unique_column_names(list(selected.columns))

    canvas = CanvasState.model_validate({"nodes": [
        {"id": "s", "type": "file_source", "position": {"x": 0, "y": 0}, "data": {"columns": [
            {"name": "id", "type": "int", "id": "c-id"}, {"name": "name", "type": "str", "id": "c-name"},
            {"name": "city_id", "type": "int", "id": "c-city"}]}},
        {"id": "t", "type": "transform", "position": {"x": 0, "y": 1}, "transform_op": "rename", "config": rename},
        {"id": "r", "type": "result", "position": {"x": 0, "y": 2}, "output_columns": selection},
    ], "edges": [{"id": "e1", "source": "s", "target": "t"}, {"id": "e2", "source": "t", "target": "r"}]})
    service = SchemaInferenceService(SimpleNamespace(
        get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(project_type="manipola", default_connection_id=None),
    ))
    predicted = [col["name"] for col in service.infer_node_schema_typed("p", "r").columns]
    assert predicted == saved



@pytest.mark.parametrize("formula,expected", [
    ("utente.str.slice(1, 4) + '_IS'", "112_IS"),
    ("'IS_' + utente.str.slice(1, 4)", "IS_112"),
])
def test_formula_text_concat_valid_on_empty_frames(formula, expected):
    """Collaudo dal vivo (Utente che esegue): su zero righe pandas non deduce il
    tipo e la Formula che unisce testo falliva solo in Valida. Regola: un
    rifiuto che sparisce su UNA riga di valori rappresentativi dello schema e'
    un artefatto delle colonne vuote, la configurazione e' valida."""
    config = {"formula": formula, "new_column": "istituto"}
    rows = pd.DataFrame({"utente": ["E1121"]})
    assert get_executor().execute_with_params("formula", [rows], config)["istituto"].tolist() == [expected]
    upstream = InferredSchema(columns=[{"name": "utente", "type": "string"}])
    node = Node.model_validate({"id": "f", "type": "transform", "position": {"x": 0, "y": 0},
                                "transform_op": "formula", "config": config})
    issues = ManipolaNodeValidator().validate_node(node, upstream=upstream, inputs=[upstream])
    assert [i.message for i in issues if i.severity == "error"] == []


def test_formula_unknown_column_still_rejected_after_one_row_retry():
    config = {"formula": "assente.str.slice(1, 4) + '_IS'", "new_column": "istituto"}
    upstream = InferredSchema(columns=[{"name": "utente", "type": "string"}])
    node = Node.model_validate({"id": "f", "type": "transform", "position": {"x": 0, "y": 0},
                                "transform_op": "formula", "config": config})
    issues = ManipolaNodeValidator().validate_node(node, upstream=upstream, inputs=[upstream])
    assert any(i.severity == "error" for i in issues)


@pytest.mark.parametrize("formula,output_type,expected", [
    ("utente.str.slice(1, 4) + '_IS'", None, "str"),     # collaudo dal vivo: prima "real"
    ("'IS_' + utente", None, "str"),
    ("n * 2", None, "int"),
    ("n / 2", None, "real"),
    ("n > 0", None, "bool"),
    ("utente + '_IS'", "real", "str"),                    # output_type non letto dall'esecuzione
    ("n * 2", "real", "int"),
])
def test_formula_predicted_type_matches_execution(formula, output_type, expected):
    """Regola: il tipo previsto di una colonna calcolata dalla Formula e' quello
    dell'esecuzione su una riga rappresentativa (manipola/formula_type_plan);
    output_type non e' letto dall'esecuzione e non decide."""
    from app.core.dataframes.schema import series_to_canvas_type
    from app.core.manipola.formula_type_plan import formula_column_types

    config = {"formula": formula, "new_column": "calc"}
    if output_type:
        config["output_type"] = output_type
    rows = pd.DataFrame({"utente": ["E1121"], "n": [3]})
    executed = series_to_canvas_type(get_executor().execute_with_params("formula", [rows], config)["calc"])
    assert formula_column_types([{"name": "utente", "type": "str"}, {"name": "n", "type": "int"}], config) == {"calc": executed}
    assert executed == expected
    canvas = CanvasState.model_validate({"nodes": [
        {"id": "s", "type": "file_source", "position": {"x": 0, "y": 0}, "data": {"columns": [
            {"name": "utente", "type": "str", "id": "c-u"}, {"name": "n", "type": "int", "id": "c-n"}]}},
        {"id": "f", "type": "transform", "position": {"x": 0, "y": 1}, "transform_op": "formula", "config": config},
    ], "edges": [{"id": "e1", "source": "s", "target": "f"}]})
    service = SchemaInferenceService(SimpleNamespace(
        get_project_canvas=lambda _: canvas,
        get_project_metadata=lambda _: SimpleNamespace(project_type="manipola", default_connection_id=None),
    ))
    predicted = {col["name"]: col["type"] for col in service.infer_node_schema_typed("p", "f").columns}
    assert predicted["calc"] == expected
