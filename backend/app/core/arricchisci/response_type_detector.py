"""Typed response flattening/type detection for API enrichment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, MutableMapping


@dataclass
class ResponseTypeDetector:
    """Small collaborator for response row flattening and column type inference."""

    detected_column_types: MutableMapping[str, str]

    def detect_geometry_type(self, col_name: str, geom_type: str) -> None:
        """Map GeoJSON geometry type to Plenora's schema type."""
        geo_map = {
            "point": "geometry:point",
            "multipoint": "geometry:multipoint",
            "linestring": "geometry:line",
            "multilinestring": "geometry:line",
            "polygon": "geometry:polygon",
            "multipolygon": "geometry:multipolygon",
            "geometrycollection": "geometry",
        }
        self.detected_column_types[col_name] = geo_map.get(geom_type, "geometry")

    def infer_scalar_type(self, value: Any) -> str:
        """Infer a schema type from a scalar response value."""
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "real"
        if isinstance(value, dict) and value.get("type") in (
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
            "GeometryCollection",
        ):
            self.detect_geometry_type("_tmp", value.get("type", "").lower())
            return self.detected_column_types.pop("_tmp", "geometry")
        return "str"

    def flatten_dict(self, data: Dict) -> Dict:
        """Flatten one response row: scalars pass through, nested data becomes JSON."""
        result = {}
        for key, value in data.items():
            if isinstance(value, float) and value == int(value):
                result[key] = int(value)
            elif isinstance(value, (dict, list)):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = value
        return result
