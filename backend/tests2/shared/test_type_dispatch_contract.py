"""
Contract test — dispatch dei tipi colonna (post-refactor C).

Dopo la centralizzazione del vocabolario in `app.core.resources.column_types`,
i helper pandas-specific passano dal boundary `app.core.dataframes.schema`.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests2.shared._ast_identifiers import all_identifiers


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _mapper():
    from app.core.carica.column_mapper import ColumnMapper
    return ColumnMapper(db_type="postgres")


# Site 2: _infer_type_from_canvas (canvas-type string -> canonical)


@pytest.mark.parametrize("canvas_type,expected", [
    # Tipi canonici base
    ("int", "int"),
    ("real", "real"),
    ("str", "str"),
    ("bool", "bool"),
    ("date", "date"),
    ("geometry", "geometry"),
    # Case-insensitive (base_type = split(":")[0].lower())
    ("INT", "int"),
    ("Real", "real"),
    ("GEOMETRY", "geometry"),
    # Geometry con suffissi
    ("geometry:Point", "geometry"),
    ("geometry:Point:4326", "geometry"),
    ("geometry:MultiPolygon:3857", "geometry"),
    # Unknown/empty
    ("", ""),
    ("unknown", ""),
    # Post-refactor C: alias unificati con carica/router (prima della
    # centralizzazione, questi tornavano "" qui e "int"/"real" in router).
    # La nuova semantica e' coerente: entrambi i dispatch accettano alias.
    ("float", "real"),
    ("integer", "int"),
    ("double", "real"),
    ("boolean", "bool"),
])
def test_infer_type_from_canvas_matrix(canvas_type, expected):
    """Freeze output per ogni input noto (matrice post-refactor C)."""
    m = _mapper()
    assert m._infer_type_from_canvas(canvas_type) == expected


# Site 3: _infer_type_from_pandas (pandas Series -> canonical)
# Copertura N11 gia' completa in test_n11_type_inference_loss_contract.
# Qui aggiungo solo boundary casi che il refactor potrebbe rompere.


def test_infer_type_from_pandas_int_dtype():
    assert _mapper()._infer_type_from_pandas(pd.Series([1, 2, 3], dtype="int64")) == "int"


def test_infer_type_from_pandas_uint_dtype():
    """uint8/uint16/... devono finire in 'int' (dtype_str contiene 'int')."""
    assert _mapper()._infer_type_from_pandas(pd.Series([1, 2], dtype="uint32")) == "int"


def test_infer_type_from_pandas_float_with_fractions_is_real():
    assert _mapper()._infer_type_from_pandas(pd.Series([1.5, 2.7], dtype=float)) == "real"


def test_infer_type_from_pandas_float_all_int_is_int_n11():
    assert _mapper()._infer_type_from_pandas(pd.Series([1.0, 2.0], dtype=float)) == "int"


def test_infer_type_from_pandas_float_with_nan_rest_int_is_int_n11():
    assert _mapper()._infer_type_from_pandas(pd.Series([1.0, np.nan, 3.0], dtype=float)) == "int"


def test_infer_type_from_pandas_float_all_nan_is_real():
    assert _mapper()._infer_type_from_pandas(pd.Series([np.nan, np.nan], dtype=float)) == "real"


def test_infer_type_from_pandas_bool_is_bool():
    assert _mapper()._infer_type_from_pandas(pd.Series([True, False], dtype=bool)) == "bool"


def test_infer_type_from_pandas_datetime_is_date():
    s = pd.Series(pd.to_datetime(["2026-01-01"]))
    assert _mapper()._infer_type_from_pandas(s) == "date"


def test_infer_type_from_pandas_object_with_strings_is_str():
    assert _mapper()._infer_type_from_pandas(pd.Series(["a", "b"], dtype=object)) == "str"


def test_infer_type_from_pandas_object_with_numeric_strings_is_real():
    assert _mapper()._infer_type_from_pandas(pd.Series(["1.5", "2.7"], dtype=object)) == "real"


def test_infer_type_from_pandas_empty_object_defaults_to_str():
    assert _mapper()._infer_type_from_pandas(pd.Series([], dtype=object)) == "str"


def test_infer_type_from_pandas_nullable_Int64_is_int():
    assert _mapper()._infer_type_from_pandas(pd.Series([1, pd.NA, 3], dtype="Int64")) == "int"


# Site 1: carica/router.py auto_mapping — alias map canvas-type -> pandas dtype
# La dispatch e' inline nel body dell'endpoint (if/elif chain, righe ~242-251).
# Uso AST sentinel per congelare le condizioni: se qualcuno cambia gli alias
# accettati o gli output dtype, questo test cattura la modifica.


def _extract_auto_mapping_body_source() -> str:
    """Estrae il source code RAW della funzione auto_mapping (preserva virgolette)."""
    from app.core.carica import router as carica_router
    src = inspect.getsource(carica_router)
    tree = ast.parse(src)
    src_lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "auto_mapping":
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(src_lines[start:end])
    raise AssertionError("auto_mapping not found in carica/router.py")


@pytest.mark.parametrize("alias,expected_dtype", [
    # Alias map post-refactor C: accesso preferito in app.core.dataframes.schema
    ("int",     "int64"),
    ("integer", "int64"),
    ("real",    "float64"),
    ("float",   "float64"),
    ("double",  "float64"),
    ("bool",    "bool"),
    ("boolean", "bool"),
    # Geometry (con/senza suffisso) e fallback su object
    ("geometry",             "object"),
    ("geometry:Point:4326",  "object"),
    ("str",                  "object"),
    ("",                     "object"),
    ("unknown",              "object"),
])
def test_canvas_type_to_pandas_dtype_behavior(alias, expected_dtype):
    """Behavior contract del dispatch canvas-type -> pandas-dtype.

    Post refactor C la dispatch pandas-specific vive in
    `app.core.dataframes.schema.canvas_type_to_pandas_dtype`.
    I test restano sul comportamento osservabile (input->output), non sull'impl.
    """
    from app.core.dataframes.schema import canvas_type_to_pandas_dtype
    assert canvas_type_to_pandas_dtype(alias) == expected_dtype


def test_auto_mapping_delegates_to_canonical_dispatch():
    """auto_mapping deve DELEGARE al dispatch canonico, non re-implementarlo.

    Sentinel anti-regressione: se qualcuno reintroduce un if/elif sui tipi
    dentro l'endpoint, il pattern originale `col_type in ("int", "integer")`
    riappare e questo test fallisce.
    """
    src = _extract_auto_mapping_body_source()
    assert "empty_dataframe_from_column_metadata" in all_identifiers(src), (
        "auto_mapping non usa il dataframe boundary. Refactor dataframe "
        "richiede delega a app.core.dataframes.schema, non dispatch inline."
    )
    from app.core.dataframes.schema import empty_dataframe_from_column_metadata

    helper_src = inspect.getsource(empty_dataframe_from_column_metadata)
    assert "canvas_type_to_pandas_dtype" in all_identifiers(helper_src), (
        "empty_dataframe_from_column_metadata non usa canvas_type_to_pandas_dtype. "
        "Il dispatch canonico deve restare la fonte unica."
    )
    # Guardia contro reintroduzione dell'if/elif storico
    # RAW: pattern multi-token (tuple di literal string), non identificatori singoli
    forbidden_patterns = [
        '"int", "integer"', "'int', 'integer'",
        '"real", "float", "double"', "'real', 'float', 'double'",
    ]
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"auto_mapping contiene '{pat}' — dispatch reintrodotto inline. "
            "Usa canvas_type_to_pandas_dtype."
        )


def test_empty_dataframe_from_column_metadata_preserves_declared_dtypes():
    from app.core.dataframes.schema import empty_dataframe_from_column_metadata

    df = empty_dataframe_from_column_metadata(
        [
            {"name": "id", "type": "integer"},
            {"name": "amount", "type": "double"},
            {"name": "enabled", "type": "boolean"},
            {"name": "created_at", "type": "date"},
            {"name": "geom", "type": "geometry:Point:4326"},
            {"name": "label", "type": "str"},
        ]
    )

    assert list(df.columns) == ["id", "amount", "enabled", "created_at", "geom", "label"]
    assert str(df["id"].dtype) == "int64"
    assert str(df["amount"].dtype) == "float64"
    assert str(df["enabled"].dtype) == "bool"
    assert str(df["created_at"].dtype) == "datetime64[ns]"
    assert str(df["geom"].dtype) == "object"
    assert str(df["label"].dtype) == "object"


# Geometry vocabulary (alias set condiviso fra ColumnMapper)


def test_geometry_urby_types_contains_expected_subtypes():
    """GEOMETRY_URBY_TYPES deve contenere tutti i sub-type supportati.
    Refactor C non deve scartarli silenziosamente."""
    from app.core.carica.column_mapper import GEOMETRY_URBY_TYPES
    expected = {
        "geometry", "geometry:point", "geometry:line", "geometry:polygon",
        "geometry:multipoint", "geometry:multiline", "geometry:multipolygon",
    }
    missing = expected - GEOMETRY_URBY_TYPES
    assert not missing, f"Sub-type geometry mancanti: {missing}"


# Coerenza fra dispatch: un input canonico va dove ci si aspetta


def test_dispatch_coherence_canonical_types_accepted_by_canvas_inference():
    """Ogni tipo canonico deve essere accettato da _infer_type_from_canvas.
    Refactor C: se C aggiunge vocabolario, assicurarsi che questi restino."""
    m = _mapper()
    for t in ["int", "real", "str", "bool", "date", "geometry"]:
        assert m._infer_type_from_canvas(t) == t


def test_dispatch_coherence_roundtrip_int_float_bool_date():
    """Pandas -> canonical -> Pandas: check che il tipo canonico ottenuto
    sia rimappabile a un dtype pandas consistente.

    Questa e' una proprieta' implicita che il refactor C deve preservare:
    serializzare/deserializzare via il vocabolario canonico non deve
    cambiare l'identita' del tipo logico.
    """
    m = _mapper()
    cases = [
        (pd.Series([1, 2], dtype="int64"),   "int"),
        (pd.Series([1.5, 2.5], dtype=float), "real"),
        (pd.Series([True, False], dtype=bool), "bool"),
        (pd.Series(pd.to_datetime(["2026-01-01"])), "date"),
        (pd.Series(["x", "y"], dtype=object), "str"),
    ]
    for series, expected in cases:
        assert m._infer_type_from_pandas(series) == expected
        # Round-trip: da canonical di nuovo a se stesso
        assert m._infer_type_from_canvas(expected) == expected
