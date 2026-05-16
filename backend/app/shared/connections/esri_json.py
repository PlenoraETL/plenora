"""
Esri JSON conversion helpers - provider-neutral shared module.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from app.shared.contracts.payloads import JsonObject, JsonRows
from app.utils.logger import get_logger

logger = get_logger(__name__)


class EsriJsonConverter:
    """
    Converte tra formato Esri JSON e GeoDataFrame/liste Python.

    Esri JSON features hanno struttura:
    {
        "attributes": {"FIELD1": "val1", "FIELD2": 42},
        "geometry": {"x": 11.0, "y": 44.0} // o rings/paths per polygon/line
    }

    La conversione a GeoDataFrame usa shapely per le geometrie.
    """

    @staticmethod
    def features_to_rows(
        features: JsonRows,
        fields: JsonRows,
        include_geometry: bool = True,
        geometry_type: str = "",
    ) -> Tuple[List[tuple], List[str]]:
        """
        Converte features Esri JSON in righe + colonne (formato execute_raw).

        Args:
            features: lista di features Esri JSON
            fields: lista definizioni campi dal layer schema
            include_geometry: se True, aggiunge colonna geometria WKT
            geometry_type: tipo geometria Esri per conversione corretta

        Returns:
            (righe, colonne) - stesso formato di QueryExecutor.execute_raw
        """
        if not features:
            return [], []

        # Nomi colonne attributi (escludi campo geometria Esri
        attr_columns = [f["name"] for f in fields if f.get("type") != "esriFieldTypeGeometry"]

        # Colonne output: attributi + eventuale geometry
        columns = list(attr_columns)
        if include_geometry:
            columns.append("geometry")

        rows = []
        for feature in features:
            attrs = feature.get("attributes", {})
            row = [attrs.get(col) for col in attr_columns]

            if include_geometry:
                geom = feature.get("geometry")
                wkt = EsriJsonConverter._geometry_to_wkt(geom, geometry_type) if geom else None
                row.append(wkt)

            rows.append(tuple(row))

        return rows, columns

    @staticmethod
    def _geometry_to_wkt(geom: JsonObject, geometry_type: str = "") -> Optional[str]:
        """
        Converte una geometria Esri JSON in WKT.

        Supporta:
        - Point: {"x": 11.0, "y": 44.0} -> POINT(11.0 44.0)
        - Multipoint: {"points": [[x,y], ...]} -> MULTIPOINT(...)
        - Polyline: {"paths": [[[x,y], ...], ...]} -> LINESTRING/MULTILINESTRING(...)
        - Polygon: {"rings": [[[x,y], ...], ...]} -> POLYGON/MULTIPOLYGON(...)
        """
        if geom is None:
            return None

        try:
            # Point
            if "x" in geom and "y" in geom:
                point_x, point_y = geom["x"], geom["y"]
                if point_x is None or point_y is None:
                    return None
                return f"POINT({point_x} {point_y})"

            # Multipoint
            if "points" in geom:
                points = geom["points"]
                if not points:
                    return None
                pts_wkt = ", ".join(f"{p[0]} {p[1]}" for p in points)
                return f"MULTIPOINT({pts_wkt})"

            # Polyline
            if "paths" in geom:
                paths = geom["paths"]
                if not paths:
                    return None
                if len(paths) == 1:
                    coords = ", ".join(f"{p[0]} {p[1]}" for p in paths[0])
                    return f"LINESTRING({coords})"

                lines = []
                for path in paths:
                    coords = ", ".join(f"{p[0]} {p[1]}" for p in path)
                    lines.append(f"({coords})")
                return f"MULTILINESTRING({', '.join(lines)})"

            # Polygon
            if "rings" in geom:
                rings = geom["rings"]
                if not rings:
                    return None
                # Esri: primo ring = esterno, successivi = buchi
                ring_wkts = []
                for ring in rings:
                    coords = ", ".join(f"{p[0]} {p[1]}" for p in ring)
                    ring_wkts.append(f"({coords})")
                return f"POLYGON({', '.join(ring_wkts)})"

            return None

        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"Errore conversione geometria a WKT: {e}")
            return None

__all__ = ["EsriJsonConverter"]
