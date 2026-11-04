"""
Test per app/core/resources/column_types.py - adapter fra list[{name,type}] e dict{name:type}

+ dispatch table canvas-type <-> pandas-dtype <-> canonical (fase C).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.core.dataframes.schema import (
    canvas_type_to_pandas_dtype,
    series_to_canvas_type,
)
from app.core.resources.column_types import (
    CANONICAL_BASE_TYPES,
    TYPE_ALIASES,
    dict_to_metadata,
    is_canonical_type,
    lookup_canvas_column_type,
    metadata_to_dict,
    normalize_canvas_type,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared]


# Tombstone: il facade compat `app/shared/resources/column_types.py` e'
# stato rimosso (BE-GO-READY-ARCH-057-W1: gli engine subpackage in
# `app.shared.*` non possono importare `app.core.*`). I consumer ora
# importano direttamente da `app.core.resources.column_types`.
def test_shared_column_types_facade_stays_removed():
    shared_module = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "shared"
        / "resources"
        / "column_types.py"
    )
    assert not shared_module.exists()


# metadata_to_dict


def test_metadata_to_dict_basic():
    rows = [
        {"name": "id", "type": "int"},
        {"name": "nome", "type": "str"},
    ]
    assert metadata_to_dict(rows) == {"id": "int", "nome": "str"}


def test_metadata_to_dict_empty_or_none():
    assert metadata_to_dict(None) == {}
    assert metadata_to_dict([]) == {}


def test_metadata_to_dict_preserves_order():
    rows = [
        {"name": "z", "type": "int"},
        {"name": "a", "type": "str"},
        {"name": "m", "type": "date"},
    ]
    keys = list(metadata_to_dict(rows).keys())
    assert keys == ["z", "a", "m"]


def test_metadata_to_dict_skips_malformed_rows():
    rows = [
        {"name": "ok", "type": "int"},
        {"name": "", "type": "int"},            # nome vuoto
        {"name": "nope", "type": ""},           # tipo vuoto
        {"name": "nope", "type": None},         # tipo None
        {"type": "int"},                         # manca name
        {"name": "x"},                           # manca type
        "not a dict",                             # non-mapping
        None,
    ]
    assert metadata_to_dict(rows) == {"ok": "int"}


def test_metadata_to_dict_last_wins_on_duplicate_name():
    rows = [
        {"name": "id", "type": "int"},
        {"name": "id", "type": "str"},  # duplicato — last wins (dict behavior)
    ]
    assert metadata_to_dict(rows) == {"id": "str"}


# dict_to_metadata


def test_dict_to_metadata_basic():
    result = dict_to_metadata({"id": "int", "nome": "str"})
    assert result == [
        {"name": "id", "type": "int"},
        {"name": "nome", "type": "str"},
    ]


def test_dict_to_metadata_empty_or_none():
    assert dict_to_metadata(None) == []
    assert dict_to_metadata({}) == []


def test_dict_to_metadata_skips_empty_name_or_type():
    result = dict_to_metadata({"ok": "int", "": "str", "x": ""})
    assert result == [{"name": "ok", "type": "int"}]


def test_dict_to_metadata_preserves_insertion_order():
    d = {"z": "int", "a": "str"}
    result = dict_to_metadata(d)
    assert [r["name"] for r in result] == ["z", "a"]


# Roundtrip


def test_roundtrip_list_dict_list_is_identity():
    original = [
        {"name": "id", "type": "int"},
        {"name": "geom", "type": "geometry:Point:4326"},
        {"name": "nome", "type": "str"},
    ]
    assert dict_to_metadata(metadata_to_dict(original)) == original


def test_roundtrip_dict_list_dict_is_identity():
    original = {"id": "int", "nome": "str", "geom": "geometry:Point:4326"}
    assert metadata_to_dict(dict_to_metadata(original)) == original


# is_canonical_type


def test_is_canonical_type_base_types():
    for t in CANONICAL_BASE_TYPES:
        assert is_canonical_type(t)


def test_is_canonical_type_geometry_with_suffix():
    assert is_canonical_type("geometry:Point:4326")
    assert is_canonical_type("geometry:MultiPolygon:3857")


def test_is_canonical_type_rejects_unknown():
    assert not is_canonical_type("float")      # alias non-canonico
    assert not is_canonical_type("INT")        # case-sensitive
    assert not is_canonical_type("")
    assert not is_canonical_type(None)  # type: ignore[arg-type]
    assert not is_canonical_type(123)   # type: ignore[arg-type]


# normalize_canvas_type (fase C)


@pytest.mark.parametrize("input_,expected", [
    # Canonici puri
    ("int", "int"),
    ("real", "real"),
    ("str", "str"),
    ("bool", "bool"),
    ("date", "date"),
    ("geometry", "geometry"),
    # Case-insensitive sul base_type
    ("INT", "int"),
    ("Real", "real"),
    ("GEOMETRY", "geometry"),
    # Alias
    ("integer", "int"),
    ("float", "real"),
    ("double", "real"),
    ("boolean", "bool"),
    ("INTEGER", "int"),
    ("Float", "real"),
    # Geometry con suffissi
    ("geometry:Point", "geometry"),
    ("geometry:Point:4326", "geometry"),
    ("geometry:MultiPolygon:3857", "geometry"),
    # Empty/unknown/invalid
    ("", ""),
    ("unknown", ""),
    ("xyz", ""),
    (None, ""),
    (123, ""),
])
def test_normalize_canvas_type_matrix(input_, expected):
    assert normalize_canvas_type(input_) == expected


def test_type_aliases_map_coherent_with_canonical():
    """Ogni valore di TYPE_ALIASES deve essere un tipo canonico base."""
    for alias, canonical in TYPE_ALIASES.items():
        assert canonical in CANONICAL_BASE_TYPES, (
            f"Alias {alias} -> {canonical} non e' canonico"
        )


# canvas_type_to_pandas_dtype (fase C; accesso preferito via dataframes.schema)


@pytest.mark.parametrize("canvas_type,expected_dtype", [
    # Canonici
    ("int", "int64"),
    ("real", "float64"),
    ("bool", "bool"),
    ("date", "datetime64[ns]"),
    ("str", "str"),
    ("geometry", "object"),
    # Alias -> stesso dtype
    ("integer", "int64"),
    ("float", "float64"),
    ("double", "float64"),
    ("boolean", "bool"),
    # Geometry con suffisso -> object
    ("geometry:Point:4326", "object"),
    # Unknown / empty -> object (safe default)
    ("", "object"),
    ("unknown", "object"),
    (None, "object"),
])
def test_canvas_type_to_pandas_dtype_matrix(canvas_type, expected_dtype):
    assert canvas_type_to_pandas_dtype(canvas_type) == expected_dtype


def test_canvas_type_to_pandas_dtype_is_instantiable():
    """Ogni dtype restituito deve essere un dtype pandas valido."""
    for canvas in ["int", "real", "bool", "date", "str", "geometry", "integer"]:
        dtype = canvas_type_to_pandas_dtype(canvas)
        # Non deve esplodere
        pd.Series([], dtype=dtype)


def test_resource_column_types_has_no_dataframe_compat_helpers():
    import app.core.resources.column_types as column_types

    assert not hasattr(column_types, "canvas_type_to_pandas_dtype")
    assert not hasattr(column_types, "pandas_series_to_canvas_type")
    assert "app.core.dataframes" not in Path(column_types.__file__).read_text(
        encoding="utf-8"
    )


# lookup_canvas_column_type


def test_lookup_canvas_column_type_prefers_exact_match():
    mapping = {"Name": "str", "NAME": "int"}
    assert lookup_canvas_column_type("Name", mapping) == "str"


def test_lookup_canvas_column_type_falls_back_to_case_insensitive_match():
    mapping = {"TOTAL_PRICE": "real"}
    assert lookup_canvas_column_type("total_price", mapping) == "real"


def test_lookup_canvas_column_type_checks_alternate_names_before_case_fold():
    mapping = {"total_price": "real", "TOTAL_PRICE": "str"}
    assert lookup_canvas_column_type("deduped_total_price", mapping, "TOTAL_PRICE") == "str"


def test_lookup_canvas_column_type_returns_none_for_unknown_column():
    assert lookup_canvas_column_type("missing", {"id": "int"}) is None


# series_to_canvas_type (fase C — include N11 heuristic)


def test_pandas_series_int_is_int():
    assert series_to_canvas_type(pd.Series([1, 2], dtype="int64")) == "int"


def test_pandas_series_uint_is_int():
    """uint* -> int (dtype_str contiene 'int')."""
    assert series_to_canvas_type(pd.Series([1, 2], dtype="uint16")) == "int"


def test_pandas_series_float_with_fractions_is_real():
    assert series_to_canvas_type(pd.Series([1.5, 2.7], dtype=float)) == "real"


def test_pandas_series_float_all_int_is_int_n11():
    assert series_to_canvas_type(pd.Series([1.0, 2.0], dtype=float)) == "int"


def test_pandas_series_float_with_nan_rest_int_is_int_n11():
    assert series_to_canvas_type(pd.Series([1.0, np.nan, 3.0], dtype=float)) == "int"


def test_pandas_series_float_all_nan_is_real():
    assert series_to_canvas_type(pd.Series([np.nan, np.nan], dtype=float)) == "real"


def test_pandas_series_bool_is_bool():
    assert series_to_canvas_type(pd.Series([True, False], dtype=bool)) == "bool"


def test_pandas_series_datetime_is_date():
    s = pd.Series(pd.to_datetime(["2026-01-01"]))
    assert series_to_canvas_type(s) == "date"


def test_pandas_series_object_strings_is_str():
    assert series_to_canvas_type(pd.Series(["a", "b"], dtype=object)) == "str"


def test_pandas_series_object_numeric_strings_is_real():
    assert series_to_canvas_type(pd.Series(["1.5", "2.7"], dtype=object)) == "real"


def test_pandas_series_empty_object_is_str():
    assert series_to_canvas_type(pd.Series([], dtype=object)) == "str"


def test_pandas_series_nullable_Int64_is_int():
    assert series_to_canvas_type(pd.Series([1, pd.NA, 3], dtype="Int64")) == "int"
