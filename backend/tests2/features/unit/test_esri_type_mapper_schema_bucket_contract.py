"""
Contract test BE-IMDF-003 — schema-bucket type mappers di `EsriTypeMapper`.

Verifica i due helper consolidati:
"""

from __future__ import annotations

import pytest

from app.shared.connections.esri_types import EsriTypeMapper


pytestmark = [pytest.mark.unit, pytest.mark.contract]


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


# 5. ESRI type mapping vive in shared connections, non nel client concreto


def test_esri_type_mapper_not_reexported_via_arcgis_client():
    """ESRI type mapping belongs in shared connections, not the concrete client."""
    import app.core.features.arcgis_client as arcgis_client
    from app.shared.connections.esri_types import EsriTypeMapper

    assert callable(EsriTypeMapper.map_field_type)
    assert not hasattr(arcgis_client, "EsriTypeMapper")
