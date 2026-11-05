"""Tipi previsti del Lookup = tipi dell'esecuzione (Fase 5, T6, residuo BA1).

Matrice 5 tipi di colonna x 6 mappe x 4 «Valori non mappati» x 2 uscite x
{righe, vuoto} = 480 casi (quella del rapporto A3, sezione F3, dove 380 casi
divergevano). Per ogni caso:

- previsione certa (stato dei tipi ``certain``) -> tipo previsto == tipo eseguito;
- previsione incerta -> lo stato dei tipi del nodo e' ``indeterminate`` e il
  tipo eseguito e' tra le alternative dichiarate dal piano
  (``manipola/lookup_type_plan``).
"""

from __future__ import annotations

import itertools
import logging
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.manipola.lookup_type_plan import _plan, full_mapping_type_plan, lookup_type_plan, representative_plan
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.transforms.analysis import op_lookup
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.schema_types import ColumnSchema

COLUMNS = {
    "int": [1, 2, 3, 4], "real": [1.5, 2.0, 3.5, 4.0], "str": ["a", "b", "c", "d"],
    "bool": [True, False, True, False], "date": list(pd.to_datetime(["2024-01-05"] * 4)),
}
_ALL = list(dict.fromkeys(["a", "b", "c", "d", "1", "2", "3", "4", "1.5", "3.5", "True", "False", "2024-01-05"]))
MAPPINGS = {
    "testo": [{"from": "a", "to": "X"}, {"from": "1", "to": "Y"}, {"from": "True", "to": "Z"}],
    "numeri": [{"from": "a", "to": "10"}, {"from": "1", "to": "20"}, {"from": "True", "to": "30"}],
    "tutte_testo": [{"from": v, "to": "X"} for v in _ALL],
    "tutte_numeri": [{"from": v, "to": "7"} for v in _ALL],
    "nessuna": [{"from": "zzz", "to": "X"}],
    "vuota": [],
}
UNMATCHED = [None, "keep", "null", "value"]
OUTPUTS = [None, "nuova"]

MATRIX = list(itertools.product(COLUMNS, MAPPINGS, UNMATCHED, OUTPUTS, [False, True]))


def test_matrix_size():
    assert len(MATRIX) == 5 * 6 * 4 * 2 * 2 == 480


@pytest.mark.parametrize("col_type,mapping,unmatched,output,empty", MATRIX)
def test_lookup_type_matches_execution(col_type, mapping, unmatched, output, empty):
    frame = pd.DataFrame({"c": COLUMNS[col_type]})
    if empty:
        frame = frame.iloc[:0]
    config = {"column": "c", "mapping": MAPPINGS[mapping], "unmatched": unmatched,
              "default": "0" if unmatched == "value" else None}
    if output:
        config["output_column"] = output
    schemas = [[ColumnSchema(**col, id=f"0:{i}", source_name=col["name"])
                for i, col in enumerate(build_columns_with_types(frame, {}))]]
    service = SchemaInferenceService(None)
    node = SimpleNamespace(id="t", config=config, data={}, transform_op="lookup")
    inferred = {c.name: c.type for c in service._infer_transform_schema(node, schemas, {})}
    executed = get_executor().execute_with_params("lookup", [frame], config)
    actual = {c["name"]: c["type"] for c in build_columns_with_types(executed, {})}
    assert set(inferred) == set(actual)
    target = output or "c"
    plan = lookup_type_plan(schemas[0][0].type, config)
    status = service._types_status_cache.get("t", ("certain", ""))[0]
    if status == "certain":
        assert inferred == actual
    else:
        assert plan.certain is False
        assert actual[target] in plan.alternatives
        assert {k: v for k, v in inferred.items() if k != target} == {k: v for k, v in actual.items() if k != target}


def test_prediction_is_certain_where_values_do_not_matter():
    certain = [
        lookup_type_plan(t, {"column": "c", "mapping": MAPPINGS[m], "unmatched": u,
                             "default": "0" if u == "value" else None}).certain
        for t, m, u in itertools.product(["int", "real", "str", "bool", "date"], MAPPINGS, UNMATCHED)
    ]
    # La grande maggioranza dei casi e' certa; solo la conversione numerica
    # dell'uscita dipende dai valori (intero o decimale).
    assert sum(certain) >= len(certain) * 3 // 4


# ── Fase 5, T5+T6, correzione dopo la conferma: costo della previsione ──────
# REGOLA: la previsione dipende dai TIPI dei valori della mappa, non dal loro
# numero. Il piano gira su una mappa ridotta rappresentativa
# (``representative_plan``) con lo STESSO esito del piano sulla mappa intera.

MIXED = {
    "misti": [{"from": 1, "to": "X"}, {"from": "1", "to": 5}, {"from": True, "to": 2.5}, {"from": None, "to": "n"}],
    "stesso_testo": [{"from": "1", "to": "X"}, {"from": "2", "to": "Y"}, {"from": 1, "to": 5}],
    "intero_enorme": [{"from": "a", "to": 2 ** 70}, {"from": "b", "to": 3}],
    "numeri_e_testo": [{"from": str(i), "to": (i if i % 3 else f"{i}.5")} for i in range(60)] + [{"from": "x", "to": "abc"}],
    "chiavi_booleane": [{"from": 1, "to": "uno"}, {"from": 0, "to": "zero"}, {"from": "True", "to": 9}],
    "dizionario": {str(i): f"v{i}" for i in range(40)},
    "valori_composti": [{"from": "a", "to": [1, 2]}, {"from": "b", "to": {"k": 1}}],
    "date": [{"from": "2024-01-05", "to": "A"}, {"from": "2024-01-05 10:30:00", "to": 3}],
    "non_valida": "boh",
    "vuota_testo": "",
}
PARITY = list(itertools.product(
    ["int", "real", "str", "bool", "date", "datetime", "integer"],
    {**MAPPINGS, **MIXED}, UNMATCHED, OUTPUTS,
))


@pytest.mark.parametrize("col_type,mapping,unmatched,output", PARITY)
def test_reduced_plan_equals_full_mapping_plan(col_type, mapping, unmatched, output):
    config = {"column": "c", "mapping": {**MAPPINGS, **MIXED}[mapping], "unmatched": unmatched,
              "default": "0" if unmatched == "value" else None}
    if output:
        config["output_column"] = output
    assert lookup_type_plan(col_type, config) == full_mapping_type_plan(col_type, config)


def test_reduced_mapping_is_small_for_a_large_map():
    config = {"mapping": [{"from": str(i), "to": f"v{i}"} for i in range(50_000)], "unmatched": "null"}
    plan = representative_plan("int", config)
    assert len(plan["mapping"]) <= 3


def test_large_map_prediction_under_100ms():
    config = {"mapping": {str(i): f"v{i}" for i in range(50_000)}, "unmatched": "keep", "default": None}
    best = float("inf")
    for _ in range(3):
        _plan.cache_clear()
        start = time.perf_counter()
        lookup_type_plan("int", config)
        best = min(best, time.perf_counter() - start)
    assert best < 0.1, f"{best * 1000:.0f} ms"


def test_prediction_logs_nothing_at_info():
    probe_logger = logging.getLogger(op_lookup.__module__)
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level=logging.INFO)
    probe_logger.addHandler(handler)
    try:
        _plan.cache_clear()
        lookup_type_plan("int", {"mapping": MAPPINGS["numeri"], "unmatched": "null"})
        assert records == []
        # L'esecuzione vera continua a scrivere le sue righe INFO.
        op_lookup(pd.DataFrame({"c": ["a"]}), column="c", mapping={"a": "X"})
        assert [r.levelno for r in records] == [logging.INFO]
    finally:
        probe_logger.removeHandler(handler)


# ── Fase 5, T5+T6, seconda correzione ──────────────────────────────────────
# REGOLA: un ingresso di prova che non si costruisce rende il tipo INCERTO,
# mai un'eccezione (prima: OverflowError da typed_probe_frame con una
# chiave-testo oltre int64 su una colonna intera). E una chiave che la colonna
# non puo' contenere (intero oltre int64 su int/Int64) non e' un candidato:
# non corrisponde mai, come nell'esecuzione.

@pytest.mark.parametrize("col_type", ["int", "Int64", "integer", "bigint"])
@pytest.mark.parametrize("mapping", [
    {"12345678901234567890": "a"},
    {"12345678901234567890": "1", "2": "3"},
    {"1": "x", "12345678901234567890": None},
    [{"from": 12345678901234567890, "to": 2.0}, {"from": "0", "to": 2}],
])
@pytest.mark.parametrize("unmatched", ["keep", "null", ""])
def test_key_beyond_int64_never_raises_and_matches_execution(col_type, mapping, unmatched):
    _plan.cache_clear()
    config = {"mapping": mapping, "unmatched": unmatched}
    plan = lookup_type_plan(col_type, config)
    assert isinstance(plan.type, str) and plan.type
    if plan.certain:
        # Tipo certo: e' quello dell'esecuzione su dati veri della colonna.
        for values in ([2, None], [0, 7], []):
            frame = pd.DataFrame({"c": pd.Series(values, dtype="float64" if None in values else "int64")})
            out = op_lookup(frame, column="c", **config)
            got = next(c["type"] for c in build_columns_with_types(out, {}) if c["name"] == "c")
            assert got == plan.type, (values, got, plan)


def test_probe_that_cannot_be_built_is_uncertain_not_an_error(monkeypatch):
    from app.core.manipola import lookup_type_plan as module

    real = module.typed_probe_frame

    def failing(column, column_type, values):
        if any(v is None for v in values):
            raise OverflowError("prova non costruibile")
        return real(column, column_type, values)

    monkeypatch.setattr(module, "typed_probe_frame", failing)
    _plan.cache_clear()
    try:
        plan = lookup_type_plan("int", {"mapping": {"1": "a"}, "unmatched": "keep"})
    finally:
        _plan.cache_clear()
    assert plan.certain is False


def test_mapping_order_is_execution_order():
    """L'ordine di inserimento della mappa e' quello dell'esecuzione (dichiarato)."""
    plan = representative_plan("str", {"mapping": {"b": "1", "a": "x"}, "unmatched": "keep"})
    assert [pair["from"] for pair in plan["mapping"]] == ["b", "a"]
