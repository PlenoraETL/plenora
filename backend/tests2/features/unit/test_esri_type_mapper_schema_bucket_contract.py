"""
Contract test BE-IMDF-003 — schema-bucket type mappers di `EsriTypeMapper`.

Verifica i due helper consolidati:
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.shared.connections.esri_types import EsriTypeMapper


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent.parent / "app"


# 1. map_esri_field_to_schema_bucket: esri-type → schema bucket


@pytest.mark.parametrize(
    "esri_type, expected",
    [
        # string family
        ("esriFieldTypeString", "string"),
        ("esriFieldTypeGUID", "string"),
        # NB: `esriFieldTypeGlobalID` -> "other" (NON "string"). Substring
        # match `"guid" in "globalid"` e' False. Behavior bit-per-bit
        # invariato dal legacy `validator._coarse_type_from_esri`.
        # `EsriTypeMapper.map_field_type` (Plenora-internal scheme) lo
        # mappa a "str" via exact-match dict; gli schemi sono distinti.
        ("esriFieldTypeGlobalID", "other"),
        ("esriFieldTypeXML", "string"),
        # int family
        ("esriFieldTypeInteger", "int"),
        ("esriFieldTypeSmallInteger", "int"),
        ("esriFieldTypeOID", "int"),
        # float family
        ("esriFieldTypeDouble", "float"),
        ("esriFieldTypeSingle", "float"),
        ("esriFieldTypeFloat", "float"),  # NOTE: legacy alias
        # date
        ("esriFieldTypeDate", "date"),
        # geometry
        ("esriFieldTypeGeometry", "geometry"),
        ("esriFieldTypeShape", "geometry"),  # NOTE: legacy alias
        # other
        ("esriFieldTypeBlob", "other"),
        ("esriFieldTypeRaster", "other"),
        ("", "other"),
    ],
)
def test_map_esri_field_to_schema_bucket_known(esri_type, expected):
    assert EsriTypeMapper.map_esri_field_to_schema_bucket(esri_type) == expected


def test_map_esri_field_to_schema_bucket_none_returns_other():
    """`(name or "").lower()` rende None → "" → "other"."""
    assert EsriTypeMapper.map_esri_field_to_schema_bucket(None) == "other"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "esri_type, expected",
    [
        ("esrifieldtypestring", "string"),
        ("ESRIFIELDTYPEINTEGER", "int"),
        ("EsriFieldTypeDouble", "float"),
        ("string", "string"),  # substring match anywhere
        ("xxx_oid", "int"),
        ("Custom_Date_Field", "date"),
    ],
)
def test_map_esri_field_to_schema_bucket_case_insensitive_substring(esri_type, expected):
    assert EsriTypeMapper.map_esri_field_to_schema_bucket(esri_type) == expected


# 2. map_pandas_dtype_to_schema_bucket: pandas/numpy dtype → schema bucket


@pytest.mark.parametrize(
    "dtype_str, expected",
    [
        # string-like dtypes
        ("object", "string"),
        ("string", "string"),
        ("str", "string"),
        ("string[python]", "string"),
        ("string[pyarrow]", "string"),
        # int variants
        ("int8", "int"),
        ("int16", "int"),
        ("int32", "int"),
        ("int64", "int"),
        ("Int64", "int"),
        ("uint32", "int"),
        # float variants
        ("float16", "float"),
        ("float32", "float"),
        ("float64", "float"),
        ("double", "float"),
        # datetime
        ("datetime64[ns]", "date"),
        ("datetime64[ns, UTC]", "date"),
        ("date", "date"),
        # geometry (geopandas)
        ("geometry", "geometry"),
        # other
        ("category", "other"),
        ("bool", "other"),
        ("complex128", "other"),
        ("", "other"),
    ],
)
def test_map_pandas_dtype_to_schema_bucket_string_inputs(dtype_str, expected):
    assert EsriTypeMapper.map_pandas_dtype_to_schema_bucket(dtype_str) == expected


def test_map_pandas_dtype_to_schema_bucket_real_pandas_dtypes():
    import pandas as pd

    # Series concrete con vari dtype
    cases = [
        (pd.Series([1, 2, 3]).dtype, "int"),               # int64
        (pd.Series([1.0, 2.0]).dtype, "float"),            # float64
        (pd.Series(["a", "b"]).dtype, "string"),           # object
        (pd.Series(pd.date_range("2024-01-01", periods=2)).dtype, "date"),
    ]
    for dtype, expected in cases:
        assert EsriTypeMapper.map_pandas_dtype_to_schema_bucket(dtype) == expected, (
            f"dtype={dtype!r} -> atteso {expected}"
        )


def test_map_pandas_dtype_to_schema_bucket_geopandas_geometry():
    """Geometry dtype (str representation) deve mappare a "geometry"."""
    # Qualunque dtype che contiene "geometry" → bucket geometry
    assert EsriTypeMapper.map_pandas_dtype_to_schema_bucket("geometry") == "geometry"


def test_map_pandas_dtype_to_schema_bucket_none_returns_other():
    """`str(None).lower()` = `"none"`, non in alcun bucket → "other"."""
    assert EsriTypeMapper.map_pandas_dtype_to_schema_bucket(None) == "other"  # type: ignore[arg-type]


# 3. Schema separation: map_field_type (Plenora) vs schema bucket


@pytest.mark.parametrize(
    "esri_type, plenora_internal, coarse",
    [
        ("esriFieldTypeString",  "str",      "string"),
        ("esriFieldTypeInteger", "int",      "int"),
        ("esriFieldTypeDouble",  "real",     "float"),
        ("esriFieldTypeSingle",  "real",     "float"),
        ("esriFieldTypeDate",    "date",     "date"),
        ("esriFieldTypeGUID",    "str",      "string"),
        ("esriFieldTypeGeometry","geometry", "geometry"),
    ],
)
def test_two_schemes_distinct(esri_type, plenora_internal, coarse):
    """`map_field_type` (Plenora-internal `str`/`real`) e
    `map_esri_field_to_schema_bucket` (`string`/`float`) sono **due
    convenzioni distinte**, entrambe per design."""
    assert EsriTypeMapper.map_field_type(esri_type) == plenora_internal
    assert EsriTypeMapper.map_esri_field_to_schema_bucket(esri_type) == coarse


# 4. Cross-bucket parity: bundle vs FS si confrontano nello stesso schema


def test_coarse_buckets_match_for_compat_check():
    """Bundle pandas int + FS Esri int → stesso bucket → schema match.
    Bundle pandas float + FS Esri double → stesso bucket → schema match."""
    import pandas as pd
    # pandas int → "int", esri Integer → "int"
    assert (
        EsriTypeMapper.map_pandas_dtype_to_schema_bucket(pd.Series([1]).dtype)
        == EsriTypeMapper.map_esri_field_to_schema_bucket("esriFieldTypeInteger")
        == "int"
    )
    # pandas float → "float", esri Double → "float"
    assert (
        EsriTypeMapper.map_pandas_dtype_to_schema_bucket(pd.Series([1.0]).dtype)
        == EsriTypeMapper.map_esri_field_to_schema_bucket("esriFieldTypeDouble")
        == "float"
    )
    # pandas object → "string", esri String → "string"
    assert (
        EsriTypeMapper.map_pandas_dtype_to_schema_bucket(pd.Series(["a"]).dtype)
        == EsriTypeMapper.map_esri_field_to_schema_bucket("esriFieldTypeString")
        == "string"
    )


def test_coarse_buckets_diverge_for_real_mismatches():
    """Bundle pandas int + FS Esri String → bucket diverso → mismatch."""
    import pandas as pd
    bundle_bucket = EsriTypeMapper.map_pandas_dtype_to_schema_bucket(pd.Series([1]).dtype)
    fs_bucket = EsriTypeMapper.map_esri_field_to_schema_bucket("esriFieldTypeString")
    assert bundle_bucket != fs_bucket
    assert bundle_bucket == "int"
    assert fs_bucket == "string"


# 5. Validator wrapper backward-compat (BE-IMDF-003 thin delegate)


def test_validator_coarse_from_esri_delegates():
    from app.core.imdf import validator
    assert validator._coarse_type_from_esri("esriFieldTypeString") == "string"
    assert validator._coarse_type_from_esri("esriFieldTypeInteger") == "int"
    assert validator._coarse_type_from_esri("esriFieldTypeDouble") == "float"
    assert validator._coarse_type_from_esri("esriFieldTypeDate") == "date"
    assert validator._coarse_type_from_esri("esriFieldTypeGeometry") == "geometry"
    assert validator._coarse_type_from_esri("esriFieldTypeBlob") == "other"
    assert validator._coarse_type_from_esri(None) == "other"  # type: ignore[arg-type]


def test_validator_coarse_from_dtype_delegates():
    import pandas as pd
    from app.core.imdf import validator
    assert validator._coarse_type_from_dtype(pd.Series([1]).dtype) == "int"
    assert validator._coarse_type_from_dtype(pd.Series([1.0]).dtype) == "float"
    assert validator._coarse_type_from_dtype(pd.Series(["a"]).dtype) == "string"
    assert validator._coarse_type_from_dtype("object") == "string"
    assert validator._coarse_type_from_dtype("category") == "other"


def test_validator_symbols_remain_module_level():
    """Per i test che monkeypatchano i simboli modulo-level (e per
    parita' col pre-IMDF-003), entrambi i simboli devono restare
    sotto `validator`."""
    from app.core.imdf import validator
    assert callable(validator._coarse_type_from_esri)
    assert callable(validator._coarse_type_from_dtype)


def test_esri_type_mapper_public_reexport_via_arcgis_client():
    """arcgis_client.py keeps a public re-export for existing imports."""
    from app.core.features.arcgis_client import EsriTypeMapper as FromClient
    from app.shared.connections.esri_types import EsriTypeMapper as FromShared

    assert FromClient is FromShared


# 6. Source-bound: il validator delega senza re-implementare il match


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_validator_uses_shared_mapper():
    src = _read(_BACKEND_APP / "core" / "imdf" / "validator.py")
    assert "EsriTypeMapper.map_esri_field_to_schema_bucket" in src
    assert "EsriTypeMapper.map_pandas_dtype_to_schema_bucket" in src
    # Niente piu' implementazione inline con i match substring
    assert '"string" in t or "guid" in t or "xml" in t' not in src
    assert '"datetime" in s or "date" in s' not in src
