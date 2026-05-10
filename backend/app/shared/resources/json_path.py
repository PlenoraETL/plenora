"""JSON path extraction helpers for API response mapping."""

from __future__ import annotations

import importlib

import json
from typing import Any


def geojson_to_wkt(geom: dict) -> str:
    """Convert a GeoJSON geometry dict to WKT using shapely when available."""
    try:
        # Lazy via importlib: engine boundary guard
        # (BE-GO-READY-ARCH-057-W1) forbids static `shapely` imports in
        # `app.shared.resources.*`. shapely is optional here (graceful
        # fallback to JSON), so we resolve it dynamically.

        shape = importlib.import_module("shapely.geometry").shape
        return shape(geom).wkt
    except Exception:
        return json.dumps(geom, ensure_ascii=False)


_DOT_PLACEHOLDER = "\x01"


def _tokenize_path(path: str) -> list[str]:
    """Tokenize a JSON path while preserving dots inside bracket selectors."""
    out_chars: list[str] = []
    depth = 0
    for ch in path:
        if ch == "[":
            depth += 1
            out_chars.append(ch)
        elif ch == "]":
            depth -= 1
            out_chars.append(ch)
        elif ch == "." and depth > 0:
            out_chars.append(_DOT_PLACEHOLDER)
        else:
            out_chars.append(ch)
    protected = "".join(out_chars)
    parts = protected.replace("]", "").replace("[", ".").split(".")
    return [p.replace(_DOT_PLACEHOLDER, ".") for p in parts]


def extract_json_value(data: Any, path: str) -> Any:
    """Extract a value from nested JSON/dict/list data using dotted paths."""
    if not path:
        return data

    parts = _tokenize_path(path)
    current = data
    i = 0
    n = len(parts)

    while i < n:
        part = parts[i]
        if not part:
            i += 1
            continue
        if current is None:
            return None

        if part == "*":
            if isinstance(current, list) and len(current) > 0:
                current = current[0]
            elif isinstance(current, list):
                return None
            i += 1
        elif "=" in part:
            if not isinstance(current, list):
                return None
            key, _, expected = part.partition("=")
            key = key.strip()
            expected = expected.strip()
            nested = "." in key
            found = None
            for item in current:
                if not isinstance(item, dict):
                    continue
                val = extract_json_value(item, key) if nested else item.get(key)
                if val is None:
                    continue
                if str(val) == expected:
                    found = item
                    break
                try:
                    if float(val) == float(expected):
                        found = item
                        break
                except (ValueError, TypeError):
                    pass
            current = found
            i += 1
        elif part.isdigit():
            idx = int(part)
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
            i += 1
        elif isinstance(current, dict):
            matched = False
            for j in range(n, i, -1):
                joined = ".".join(parts[i:j])
                if joined in current:
                    current = current[joined]
                    i = j
                    matched = True
                    break
            if not matched:
                return None
        else:
            return None

    if isinstance(current, dict) and current.get("type") in [
        "Point",
        "Polygon",
        "MultiPolygon",
        "LineString",
        "MultiLineString",
        "MultiPoint",
        "GeometryCollection",
    ]:
        return geojson_to_wkt(current)

    return current
