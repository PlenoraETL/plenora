"""
Esri type mapping constants - provider-neutral shared module.

"""

from __future__ import annotations


class EsriTypeMapper:
    """Maps Esri field/geometry types to PLENORA canonical types and
    schema-comparison buckets.

    Analogous to ``TypeMapper`` in ``database_inspector.py``.

    Two distinct output schemes:

    ``map_field_type`` / ``map_geometry_type``
        PLENORA-internal types: ``int``, ``real``, ``str``, ``date``,
        ``geometry``, ``geometry:<subtype>``.

    ``map_esri_field_to_schema_bucket`` / ``map_pandas_dtype_to_schema_bucket``
        Coarse comparison buckets for schema compatibility checks:
        ``string``, ``int``, ``float``, ``date``, ``geometry``, ``other``.
        Both methods use the same bucket vocabulary so Esri fields and
        pandas dtypes can be compared apple-to-apple (BE-IMDF-003).
    """

    ESRI_TO_PLENORA = {
        "esriFieldTypeOID": "int",
        "esriFieldTypeInteger": "int",
        "esriFieldTypeSmallInteger": "int",
        "esriFieldTypeDouble": "real",
        "esriFieldTypeSingle": "real",
        "esriFieldTypeString": "str",
        "esriFieldTypeDate": "date",
        "esriFieldTypeGUID": "str",
        "esriFieldTypeGlobalID": "str",
        "esriFieldTypeGeometry": "geometry",
        "esriFieldTypeBlob": "str",
        "esriFieldTypeXML": "str",
    }

    ESRI_GEOM_TO_PLENORA = {
        "esriGeometryPoint": "geometry:point",
        "esriGeometryMultipoint": "geometry:point",
        "esriGeometryPolyline": "geometry:line",
        "esriGeometryPolygon": "geometry:polygon",
        "esriGeometryEnvelope": "geometry:polygon",
    }

    @classmethod
    def map_field_type(cls, esri_type: str) -> str:
        """Convert an Esri field type to a PLENORA-internal type.

        Bucket: ``int``/``real``/``str``/``date``/``geometry``.
        Default fallback: ``str``. **Different** from the schema-bucket
        scheme used by ``map_esri_field_to_schema_bucket``.
        """
        return cls.ESRI_TO_PLENORA.get(esri_type, "str")

    @classmethod
    def map_geometry_type(cls, esri_geom_type: str) -> str:
        """Convert an Esri geometry type to a PLENORA type with subtype."""
        return cls.ESRI_GEOM_TO_PLENORA.get(esri_geom_type, "geometry")

    @classmethod
    def map_esri_field_to_schema_bucket(cls, esri_type: str) -> str:
        """Map an Esri field type to a coarse schema-comparison bucket.

        Bucket: ``string``/``int``/``float``/``date``/``geometry``/``other``.
        Substring match, case-insensitive (preserves pre-IMDF-003 semantics
        of ``validator._coarse_type_from_esri``):

        - ``string``, ``guid``, ``xml`` -> "string"
        - ``integer``, ``oid`` -> "int"
        - ``double``, ``single``, ``float`` -> "float"
        - ``date`` -> "date"
        - ``geometry``, ``shape`` -> "geometry"
        - other / empty / None -> "other"

        BE-IMDF-003: this bucket scheme is **distinct** from
        ``map_field_type`` (Plenora-internal ``int``/``real``/``str``/...)
        and exists to compare Esri fields against pandas DataFrames via
        ``map_pandas_dtype_to_schema_bucket``.
        """
        t = (esri_type or "").lower()
        if "string" in t or "guid" in t or "xml" in t:
            return "string"
        if "integer" in t or "oid" in t:
            return "int"
        if "double" in t or "single" in t or "float" in t:
            return "float"
        if "date" in t:
            return "date"
        if "geometry" in t or "shape" in t:
            return "geometry"
        return "other"

    @classmethod
    def map_pandas_dtype_to_schema_bucket(cls, dtype: object) -> str:
        """Map a pandas/numpy dtype to a coarse schema-comparison bucket.

        Same bucket vocabulary as ``map_esri_field_to_schema_bucket`` so
        bundle (pandas) and Feature Service (Esri) fields compare cleanly.

        Substring match, case-insensitive on ``str(dtype).lower``:

        - ``str``, ``string``, ``object`` (exact) -> "string"
        - contains ``object`` or ``string`` -> "string"
        - contains ``int`` -> "int"
        - contains ``float`` or ``double`` -> "float"
        - contains ``datetime`` or ``date`` -> "date"
        - contains ``geometry`` -> "geometry"
        - other -> "other"

        Note: pandas 2.x uses ``'str'`` (StringDtype), pandas 1.x uses
        ``'object'``; both map to ``"string"``.

        The ``dtype`` parameter is typed as ``object`` to avoid importing
        pandas here - any object that str-converts to a recognisable
        dtype name works.
        """
        s = str(dtype).lower()
        if s in ("str", "string", "object"):
            return "string"
        if "object" in s or "string" in s:
            return "string"
        if "int" in s:
            return "int"
        if "float" in s or "double" in s:
            return "float"
        if "datetime" in s or "date" in s:
            return "date"
        if "geometry" in s:
            return "geometry"
        return "other"


__all__ = ["EsriTypeMapper"]
