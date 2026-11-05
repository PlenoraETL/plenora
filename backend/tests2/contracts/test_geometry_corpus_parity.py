"""Corpus delle geometrie delle anteprime (Fase 5, P7).

1. Il file condiviso col frontend e' aggiornato (stessi risultati di shapely).
2. Ogni forma di una geometria (WKT, EWKT, WKB ISO, EWKB, bytea, GeoJSON)
   si rilegge con shapely nella stessa geometria 2D: il corpus non contiene
   una forma che shapely leggerebbe diversamente.
3. I valori non leggibili sono rifiutati anche da shapely.
4. Il corpus copre ogni tipo, ogni dimensione (2D, Z, M, ZM), EMPTY e
   ogni forma.
"""

from __future__ import annotations

import json

import pytest
import shapely
from shapely.geometry import mapping

from tests2.contracts.generate_geometry_cases import CASES_PATH, build_cases

CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _read(kind: str, value: str):
    if kind == "geojson":
        return shapely.from_geojson(value)
    if kind in ("wkt", "wkt_canonico"):
        return shapely.from_wkt(value)
    if kind == "ewkt":
        return shapely.from_wkt(value.split(";", 1)[1])
    return shapely.from_wkb(value[2:] if value.startswith("\\x") else value)


def test_corpus_file_is_current():
    assert CASES == build_cases(), "rigenerare con python -m tests2.contracts.generate_geometry_cases"


@pytest.mark.parametrize("case", CASES["readable"], ids=lambda c: f"{c['kind']}:{c['source']}")
def test_every_form_reads_back_to_the_same_2d_geometry(case):
    geom = _read(case["kind"], case["value"])
    assert geom.geom_type == case["expected"]["type"]
    got = json.loads(json.dumps(mapping(shapely.force_2d(geom))))
    if case["kind"] == "geojson" and geom.geom_type == "Polygon" and geom.is_empty:
        got = {"type": "Polygon", "coordinates": []}  # shapely: [[]] da GeoJSON
    assert got == case["expected"]["geometry"]


@pytest.mark.parametrize("case", CASES["unreadable"], ids=lambda c: c["value"][:40])
def test_unreadable_values_are_rejected_by_shapely(case):
    with pytest.raises(Exception):
        geom = _read(case["kind"], case["value"])
        mapping(geom)


def test_corpus_covers_types_dimensions_and_forms():
    readable = CASES["readable"]
    sources = {c["source"] for c in readable}
    types = {c["expected"]["type"] for c in readable}
    assert types == {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString",
                     "MultiPolygon", "GeometryCollection"}
    for t in types:
        assert any(c["expected"]["type"] == t and shapely.from_wkt(c["source"]).is_empty
                   for c in readable), f"manca {t} EMPTY"
    dims = {(g.has_z, g.has_m) for g in map(shapely.from_wkt, sources)}
    assert dims == {(False, False), (True, False), (False, True), (True, True)}
    assert {c["kind"] for c in readable} == {
        "wkt", "wkt_canonico", "ewkt", "wkb_iso_le", "wkb_iso_be",
        "ewkb_le", "ewkb_be", "bytea", "geojson",
    }
    # MULTIPOINT senza parentesi interne.
    assert "MULTIPOINT (10 40, 40 30, 20 20, 30 10)" in sources
