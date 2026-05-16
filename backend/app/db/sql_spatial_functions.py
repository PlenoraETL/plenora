"""
Spatial SQL function translators for ``sql_functions``.

The mixin keeps geometric dialect logic outside the main registry/API module
while depending only on translator methods supplied by ``SqlFunctionTranslator``.
"""

from typing import Any

from app.db.sql_types import SqlParamMap, SqlTranslationMap


def _safe_number(value, kind: str = "float"):
    """Validate a numeric value before interpolating it into SQL."""
    if value is None:
        raise ValueError("_safe_number: valore None")
    try:
        if kind == "int":
            numeric = float(value)
            if numeric != int(numeric):
                raise ValueError(
                    f"_safe_number: valore '{value}' non e' intero"
                )
            return int(numeric)
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"_safe_number: valore '{value}' non numerico (kind={kind}): {e}"
        )


class SqlSpatialFunctionMixin:
    """Translator methods for spatial/geometric functions."""

    db_type: Any

    def _spatial_translation_for(
        self,
        translations: SqlTranslationMap,
        function_name: str,
    ) -> str:
        db_value = self.db_type.value
        if db_value in translations:
            return translations[db_value]
        raise ValueError(f"{function_name} non supportato per {db_value}")

    def _translate_centroid(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Centroide - centro geometrico della geometria."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_Centroid({col})",
            "mssql": f"{col}.STCentroid()",
            "mysql": f"ST_Centroid({col})",
            "oracle": f"SDO_GEOM.SDO_CENTROID({col}, 0.005)",
            "sqlite": f"Centroid({col})",
        }
        return self._spatial_translation_for(translations, "CENTROID")

    def _translate_buffer(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Buffer - area di distanza attorno alla geometria."""
        col = self._qualify_column(params["column"], table_alias)
        distance = _safe_number(params["distance"], "float")

        translations = {
            "postgres": f"ST_Buffer({col}, {distance})",
            "mssql": f"{col}.STBuffer({distance})",
            "mysql": f"ST_Buffer({col}, {distance})",
            "oracle": f"SDO_GEOM.SDO_BUFFER({col}, {distance}, 0.005)",
            "sqlite": f"Buffer({col}, {distance})",
        }
        return self._spatial_translation_for(translations, "BUFFER")

    def _translate_transform(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Riproiezione - trasforma la geometria in un altro CRS."""
        col = self._qualify_column(params["column"], table_alias)
        srid = _safe_number(params["srid"], "int")

        translations = {
            "postgres": f"ST_Transform({col}, {srid})",
            "mssql": f"{col}.STTransform({srid})",
            "mysql": f"ST_Transform({col}, {srid})",
            "oracle": f"SDO_CS.TRANSFORM({col}, {srid})",
            "sqlite": f"Transform({col}, {srid})",
        }
        return self._spatial_translation_for(translations, "TRANSFORM")

    def _translate_area(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Area - superficie del poligono."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_Area({col})",
            "mssql": f"{col}.STArea()",
            "mysql": f"ST_Area({col})",
            "oracle": f"SDO_GEOM.SDO_AREA({col}, 0.005)",
            "sqlite": f"Area({col})",
        }
        return self._spatial_translation_for(translations, "AREA")

    def _translate_length(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Lunghezza - lunghezza della linea."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_Length({col})",
            "mssql": f"{col}.STLength()",
            "mysql": f"ST_Length({col})",
            "oracle": f"SDO_GEOM.SDO_LENGTH({col}, 0.005)",
            "sqlite": f"GLength({col})",
        }
        return self._spatial_translation_for(translations, "LENGTH")

    def _translate_perimeter(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Perimetro - lunghezza del contorno del poligono."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_Perimeter({col})",
            "mssql": f"{col}.STBoundary().STLength()",
            "mysql": f"ST_Length(ST_ExteriorRing({col}))",
            "oracle": f"SDO_GEOM.SDO_LENGTH({col}, 0.005)",
            "sqlite": f"Perimeter({col})",
        }
        return self._spatial_translation_for(translations, "PERIMETER")

    def _translate_simplify(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Semplificazione - riduce i vertici della geometria."""
        col = self._qualify_column(params["column"], table_alias)
        tolerance = _safe_number(params["tolerance"], "float")

        translations = {
            "postgres": f"ST_Simplify({col}, {tolerance})",
            "mssql": f"{col}.Reduce({tolerance})",
            "mysql": f"ST_Simplify({col}, {tolerance})",
            "oracle": f"SDO_UTIL.SIMPLIFY({col}, {tolerance})",
            "sqlite": f"Simplify({col}, {tolerance})",
        }
        return self._spatial_translation_for(translations, "SIMPLIFY")

    def _translate_convex_hull(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Inviluppo convesso - poligono convesso minimo che contiene la geometria."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_ConvexHull({col})",
            "mssql": f"{col}.STConvexHull()",
            "mysql": f"ST_ConvexHull({col})",
            "oracle": f"SDO_GEOM.SDO_CONVEXHULL({col}, 0.005)",
            "sqlite": f"ConvexHull({col})",
        }
        return self._spatial_translation_for(translations, "CONVEX_HULL")

    def _translate_envelope(self, params: SqlParamMap, table_alias: str = None) -> str:
        """Bounding Box - rettangolo di delimitazione minimo (MBR)."""
        col = self._qualify_column(params["column"], table_alias)

        translations = {
            "postgres": f"ST_Envelope({col})",
            "mssql": f"{col}.STEnvelope()",
            "mysql": f"ST_Envelope({col})",
            "oracle": f"SDO_GEOM.SDO_MBR({col})",
            "sqlite": f"Envelope({col})",
        }
        return self._spatial_translation_for(translations, "ENVELOPE")
