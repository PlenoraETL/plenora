"""Lookup: confronto scelto dal tipo della colonna (F3, seconda correzione).

Regola (shared/execution/lookup_mapping.py): ``lookup_match_as_text(dtype)``
e' l'UNICA scelta del confronto. Colonna di testo (object, str, string,
category, ogni dtype non numerico/booleano/data/durata) -> percorso storico,
chiavi originali, identico a HEAD 1c654091. Colonna numerica, booleana,
data/ora o durata -> chiavi e valori passano da ``lookup_value_text``.
POST /distinct restituisce sempre ``lookup_value_text``; il null
(``lookup_is_null``, unico) resta null e non corrisponde a nessuna chiave.

Guardie:
- tabella della forma unica (ogni riga del docstring del modulo, durate e
  date numpy comprese, riconosciute prima dei numeri);
- tabella dei dtype -> confronto;
- colonne di TESTO: esito identico a HEAD 1c654091 sul corpus
  ``lookup_text_corpus`` (fixture registrata sul riferimento), chiavi JSON
  numeriche/booleane/null e collisione 1/True comprese;
- ``lookup_is_numeric``: sui valori JSON decide come a HEAD;
- colonne numeriche/date/booleane/durate: le righe ora si applicano, per
  ogni «Valori non mappati»;
- giro completo distinct -> mappa -> esecuzione sulle colonne confrontate
  come testo e sulle colonne di solo testo;
- il null non corrisponde mai (nemmeno a "None", "nan", "NaT", "<NA>").
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.analysis import op_lookup
from app.shared.execution.lookup_mapping import (
    lookup_is_null, lookup_is_numeric, lookup_match_as_text, lookup_text_mapping, lookup_value_text,
)
from tests2.manipola.unit.lookup_text_corpus import FIXTURE_PATH, corpus, outcome

pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]

# Tabella della forma unica: ogni riga del docstring di lookup_mapping.py.
FORMS = [
    (None, None), (float("nan"), None), (np.nan, None), (pd.NaT, None), (pd.NA, None),
    ("abc", "abc"), ("", ""), ("  ", "  "), ("5.0", "5.0"),
    (True, "True"), (np.bool_(False), "False"),
    (5, "5"), (np.int64(5), "5"), (-3, "-3"),
    (5.0, "5"), (np.float64(5.0), "5"), (-0.0, "0"),
    (2.5, "2.5"), (0.1, "0.1"), (np.float32(2.5), "2.5"), (float("inf"), "inf"),
    (pd.Timestamp("2024-01-05"), "2024-01-05"), (dt.datetime(2024, 1, 5), "2024-01-05"),
    (pd.Timestamp("2024-01-05 10:30"), "2024-01-05 10:30:00"),
    (dt.datetime(2024, 1, 5, 10, 30), "2024-01-05 10:30:00"),
    (pd.Timestamp("2024-01-05", tz="UTC"), "2024-01-05 00:00:00+00:00"),
    (dt.date(2024, 1, 5), "2024-01-05"),
    # Valori temporali numpy/pandas: riconosciuti PRIMA dei numeri.
    (np.datetime64("NaT"), None), (np.timedelta64("NaT", "D"), None), (np.timedelta64("NaT"), None),
    (np.timedelta64(5, "D"), "5 days 00:00:00"), (np.timedelta64(90, "m"), "0 days 01:30:00"),
    (pd.Timedelta("5D"), "5 days 00:00:00"), (dt.timedelta(days=1, seconds=3), "1 days 00:00:03"),
    (np.datetime64("2024-01-05"), "2024-01-05"), (np.datetime64("2024-01-05T10:30"), "2024-01-05 10:30:00"),
]


@pytest.mark.parametrize("value,text", FORMS, ids=[repr(v) for v, _ in FORMS])
def test_one_text_form(value, text):
    assert lookup_value_text(value) == text


@pytest.mark.parametrize("value,text", FORMS, ids=[repr(v) for v, _ in FORMS])
def test_null_is_one_rule(value, text):
    # lookup_value_text e' None se e solo se lookup_is_null (anche distinct).
    assert lookup_is_null(value) is (text is None)


DTYPES = [
    ("object", False), ("str", False), ("string", False), ("category", False), ("period[D]", False),
    ("int64", True), ("uint8", True), ("Int64", True), ("float64", True), ("Float64", True),
    ("complex128", True), ("bool", True), ("boolean", True),
    ("datetime64[ns]", True), ("datetime64[ns, UTC]", True), ("timedelta64[ns]", True),
]


@pytest.mark.parametrize("dtype,as_text", DTYPES, ids=[d for d, _ in DTYPES])
def test_compare_rule_by_dtype(dtype, as_text):
    assert lookup_match_as_text(pd.Series([], dtype=dtype).dtype) is as_text


TEMPORALS = [np.timedelta64(5), np.timedelta64(5, "D"), np.timedelta64(-1, "s"), np.timedelta64(-1500, "ms"),
             np.timedelta64(1500, "ms"), np.timedelta64(3, "W"), np.timedelta64(7, "h"), np.timedelta64(1, "us"),
             np.timedelta64(1234567891, "ns"), pd.Timedelta("1D 2h 3min 4.000005006s"), pd.Timedelta("-3D"),
             dt.timedelta(days=-2, microseconds=7), dt.timedelta(0)]
DATETIMES = [np.datetime64("2024-01-05"), np.datetime64("2024-01"), np.datetime64("2024-01-05T10:30"),
             np.datetime64("2024-01-05T10:30:00.5"), np.datetime64("2024-01-05T10:30:00.000000005"),
             np.datetime64("2024-01-05T00:00:00.000001"), np.datetime64("1969-12-31T23:59:59")]


@pytest.mark.parametrize("value", TEMPORALS, ids=repr)
def test_timedelta_form_is_pandas_form(value):
    # Il modulo resta senza pandas: la forma calcolata e' quella di pandas.
    assert lookup_value_text(value) == str(pd.Timedelta(value))


@pytest.mark.parametrize("value", DATETIMES, ids=repr)
def test_numpy_datetime_form_is_timestamp_form(value):
    assert lookup_value_text(value) == lookup_value_text(pd.Timestamp(value))


def test_text_mapping_drops_null_keys_and_unifies_forms():
    assert lookup_text_mapping({"5": "a", None: "b", 7: "c", 2.0: "d"}) == {"5": "a", "7": "c", "2": "d"}


def test_lookup_is_numeric_uses_the_same_text():
    # Null (anche NaN) e vuoto non contano; 5.0 e "5" sono numeri.
    assert lookup_is_numeric({"a": 5.0, "b": float("nan"), "c": ""}, "null", None)
    assert lookup_is_numeric({"a": "1,5"}, "value", np.float64(2.0))
    assert not lookup_is_numeric({"a": "testo"}, "null", None)


JSON_VALUES = [None, "", " ", "a", "5", "5.0", "1,5", "nan", "inf", "True", 0, 5, -3, 5.0, 2.5, 0.1,
               float("nan"), float("inf"), 1e300, 2 ** 60, True, False]


def _head_is_numeric(mapping, unmatched, default):
    # Riferimento HEAD 1c654091: lookup_value_text era str(value).
    if unmatched in (None, "", "keep"):
        return False
    for value in list(mapping.values()) + ([default] if unmatched == "value" else []):
        if value is None or str(value).strip() == "":
            continue
        try:
            float(str(value).replace(",", "."))
        except (ValueError, TypeError):
            return False
    return True


@pytest.mark.parametrize("unmatched", [None, "", "keep", "null", "value"])
def test_lookup_is_numeric_same_as_head_on_json_values(unmatched):
    for first in JSON_VALUES:
        for second in JSON_VALUES:
            mapping = {"a": first}
            assert lookup_is_numeric(mapping, unmatched, second) == _head_is_numeric(mapping, unmatched, second), \
                (first, second)


# Colonne di testo: identico a HEAD -------------------------------------------

def test_text_columns_identical_to_head():
    head = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    entries = corpus()
    assert {key for key, *_ in entries} == set(head), "il corpus è quello registrato su HEAD"
    different = [key for key, frame, config in entries if outcome(frame, config) != head[key]]
    assert different == []


# Colonne numeriche, date, booleane: le righe ora si applicano ------------------

TYPED = {
    "int": (pd.Series([5, 6, 5]), {"5": "cinque"}, "cinque"),
    "float_integer": (pd.Series([5.0, np.nan, 5.0]), {"5": "cinque"}, "cinque"),
    "float_decimal": (pd.Series([2.5, 3.0, 2.5]), {"2.5": "due e mezzo"}, "due e mezzo"),
    "nullable_int": (pd.Series([5, None, 5], dtype="Int64"), {"5": "cinque"}, "cinque"),
    "date": (pd.Series(pd.to_datetime(["2024-01-05", None, "2024-01-05"])), {"2024-01-05": "epifania"}, "epifania"),
    "datetime": (pd.Series(pd.to_datetime(["2024-01-05 10:30", "2024-01-06 00:00", "2024-01-05 10:30"], format="%Y-%m-%d %H:%M")),
                 {"2024-01-05 10:30:00": "mattina"}, "mattina"),
    "bool": (pd.Series([True, False, True]), {"True": "sì"}, "sì"),
    "nullable_bool": (pd.Series([True, None, True], dtype="boolean"), {"True": "sì"}, "sì"),
    "nullable_float": (pd.Series([2.5, None, 2.5], dtype="Float64"), {"2.5": "x"}, "x"),
    "datetime_tz": (pd.Series(pd.to_datetime(["2024-01-05", None, "2024-01-05"]).tz_localize("UTC")),
                    {"2024-01-05 00:00:00+00:00": "utc"}, "utc"),
    "timedelta": (pd.Series(pd.to_timedelta(["5D", None, "5D"])), {"5 days 00:00:00": "cinque giorni"},
                  "cinque giorni"),
}


def test_object_column_keeps_head_path():
    # Colonna object (di testo): chiavi originali (map/isin di HEAD). 5
    # corrisponde a {"from": 5} e non a "5"; la chiave null segue map/isin.
    frame = pd.DataFrame({"c": pd.Series([5, "5", None, True], dtype=object)})
    mapping = {5: "num", None: "nul"}
    result = op_lookup(frame, "c", [{"from": k, "to": v} for k, v in mapping.items()], unmatched="null")
    expected = frame["c"].map(mapping).where(frame["c"].isin(mapping.keys()), None)
    assert result["c"].iloc[0] == "num" and pd.isna(result["c"].iloc[1])
    assert result["c"].equals(expected)



@pytest.mark.parametrize("kind", TYPED)
@pytest.mark.parametrize("unmatched", [None, "keep", "null", "value"])
@pytest.mark.parametrize("output_column", [None, "out"])
def test_typed_columns_rows_apply(kind, unmatched, output_column):
    series, mapping, target = TYPED[kind]
    frame = pd.DataFrame({"c": series})
    result = op_lookup(frame, "c", mapping, default="altro", unmatched=unmatched, output_column=output_column)
    out = result[output_column or "c"]
    assert out.iloc[0] == target and out.iloc[2] == target
    middle = out.iloc[1]
    if unmatched == "null":
        assert pd.isna(middle)
    elif unmatched == "value" or (unmatched is None):
        # Legacy con default: fillna(default) anche sul null originale.
        assert middle == "altro"
    else:
        original = frame["c"].iloc[1]
        assert (pd.isna(middle) and pd.isna(original)) or middle == original
    assert frame["c"].equals(series), "l'ingresso non cambia"


@pytest.mark.parametrize("null", [None, np.nan, pd.NaT])
def test_null_never_matches(null):
    frame = pd.DataFrame({"c": pd.Series([null, "a"], dtype=object)})
    mapping = {"None": "x", "nan": "x", "NaT": "x", "<NA>": "x", "": "x"}
    for unmatched in ("keep", "null"):
        result = op_lookup(frame, "c", mapping, unmatched=unmatched)
        assert pd.isna(result["c"].iloc[0])
        if unmatched == "keep":
            assert result["c"].iloc[1] == "a"
    # Legacy: il null resta il null originale.
    assert pd.isna(op_lookup(frame, "c", mapping)["c"].iloc[0])


# Giro completo: distinct -> mappa -> esecuzione ---------------------------------

def _distinct(monkeypatch, frame):
    from app.core.manipola.canvas_data_preparation import CanvasDataPreparation
    from app.core.projects import router
    from app.core.projects.canvas_models import Edge
    from app.shared.contracts.auth import CurrentUser
    nodes = [{"id": "s", "type": "file_source", "position": {"x": 0, "y": 0}, "data": {"columns": [{"name": "c", "type": "str"}]}},
             {"id": "n", "type": "transform", "position": {"x": 1, "y": 0}, "transform_op": "lookup", "config": {"column": "c"}}]
    body = router.DistinctRequest(canvas={"nodes": nodes, "edges": [Edge(id="e", source="s", target="n")]}, column="c")
    monkeypatch.setattr(router, "_project_service", SimpleNamespace(get_project_metadata=lambda _: SimpleNamespace(group_id="g")))
    monkeypatch.setattr(router, "assert_group_access", lambda *args: None)
    monkeypatch.setattr(router, "_prepare_canvas_for_owner", lambda _, canvas, *args, **kwargs: canvas)
    monkeypatch.setattr(CanvasDataPreparation, "input", lambda *args: (frame, False, None))
    return router.distinct_node_values("p", "n", body, CurrentUser(user_id="u", username="u", role="admin", group_id="g"))


ROUND_TRIP = {**{k: v[0] for k, v in TYPED.items()},
              "text": pd.Series(["a", None, "b", "a"], dtype="str"),
              "string": pd.Series(["a", None, " ", "a"], dtype="string"),
              "category": pd.Series(["a", None, "b"], dtype="category"),
              "object_text": pd.Series(["a", None, "  ", ""], dtype=object)}


def test_distinct_object_temporal_values(monkeypatch):
    # np.timedelta64 e' un intero per numpy: prima int(NaT) -> TypeError e
    # 5 giorni -> "5"; np.datetime64('NaT') non era null.
    column = pd.Series([np.timedelta64(5, "D"), np.timedelta64("NaT", "D"), np.datetime64("NaT"),
                        np.datetime64("2024-01-05"), None], dtype=object)
    reply = _distinct(monkeypatch, pd.DataFrame({"c": column}))
    assert reply["values"] == [
        {"value": "5 days 00:00:00", "is_null": False, "count": 1},
        {"value": None, "is_null": True, "count": 3},
        {"value": "2024-01-05", "is_null": False, "count": 1},
    ]


@pytest.mark.parametrize("kind", ROUND_TRIP)
def test_distinct_values_put_in_the_map_find_their_rows(monkeypatch, kind):
    frame = pd.DataFrame({"c": ROUND_TRIP[kind]})
    reply = _distinct(monkeypatch, frame)
    nulls = [v for v in reply["values"] if v["is_null"]]
    values = [v["value"] for v in reply["values"] if not v["is_null"]]
    # Il null resta null (mai "None"/"nan"), uno solo; gli altri sono la forma unica.
    assert all(v["value"] is None for v in nulls) and len(nulls) <= 1
    assert sum(v["count"] for v in nulls) == int(frame["c"].isna().sum())
    assert values == list(dict.fromkeys(lookup_value_text(v) for v in frame["c"] if lookup_value_text(v) is not None))
    mapping = [{"from": value, "to": f"#{i}"} for i, value in enumerate(values)]
    result = op_lookup(frame, "c", mapping, unmatched="null")
    for original, mapped in zip(frame["c"], result["c"]):
        if lookup_value_text(original) is None:
            assert pd.isna(mapped)
        else:
            assert mapped == f"#{values.index(lookup_value_text(original))}"


@pytest.mark.parametrize("value", [float("nan"), Decimal("NaN"), complex(float("nan"), 0), np.float32("nan"), None, pd.NA, pd.NaT, np.datetime64("NaT"), np.timedelta64("NaT")])
def test_every_nan_is_null(value):
    """Regola unica del null: anche Decimal('NaN') (numeric di Postgres) e il complesso NaN."""
    assert lookup_is_null(value) and lookup_value_text(value) is None


@pytest.mark.parametrize("value", [np.timedelta64(1, "M"), np.timedelta64(2, "Y")])
def test_month_year_durations_keep_numpy_text(value):
    assert lookup_value_text(value) == str(value)
