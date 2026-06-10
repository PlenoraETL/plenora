"""
Behavioral coverage per `app.core.dataframes.geometry_parsing`.

Complementare a `test_geometry_parsing_contract.py` (che copre i bug-cluster
WKT/GeoJSON mixed e gli architectural guard): qui esercitiamo l'happy-path
di OGNI funzione pubblica + i rami significativi non ancora coperti:

  - looks_like_wkb_hex: lunghezza, prefisso, charset
  - parse_geometry: GeoJSON Feature/FeatureCollection, WKB bytes/memoryview,
    WKB hex, EWKB, NaN/None/"None"/"" guard, fallback finale
  - parse_geometry_series: bytes/memoryview fast path, WKB hex per-row path,
    fast-path-fallback, dtype/index
  - parse_wkb_hex: standard hex, EWKB diretto, invalido → None
  - extract_srid_from_ewkb: SRID valido, SRID 0 (guard >0), non-EWKB, troppo
    corto, non-hex
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from shapely import wkb
from shapely.geometry import LineString, Point, Polygon

from app.core.dataframes.geometry_parsing import (
    extract_srid_from_ewkb,
    looks_like_wkb_hex,
    parse_geometry,
    parse_geometry_series,
    parse_wkb_hex,
)


pytestmark = [pytest.mark.unit]


# ----------------------------------------------------------------------
# Sample fixtures (geometrie deterministiche)
# ----------------------------------------------------------------------

POINT = Point(1, 2)
POINT_WKB_HEX = POINT.wkb_hex  # standard WKB, little-endian, no SRID
# EWKB hex con SRID 4326 (flag SRID = byte "20" in posizione 8:10)
EWKB_4326 = wkb.dumps(POINT, hex=True, srid=4326)


# ----------------------------------------------------------------------
# looks_like_wkb_hex
# ----------------------------------------------------------------------

class TestLooksLikeWkbHex:
    def test_standard_wkb_hex_is_recognized(self):
        assert looks_like_wkb_hex(POINT_WKB_HEX) is True

    def test_ewkb_hex_is_recognized(self):
        assert looks_like_wkb_hex(EWKB_4326) is True

    def test_too_short_is_rejected(self):
        # < 20 chars → subito False
        assert looks_like_wkb_hex("0101000000") is False

    def test_not_starting_with_zero_is_rejected(self):
        # stesso payload ma primo char != "0"
        mutated = "1" + POINT_WKB_HEX[1:]
        assert looks_like_wkb_hex(mutated) is False

    def test_non_hex_chars_in_prefix_rejected(self):
        # 20 char, parte con "0", ma contiene caratteri non-hex (Z) nei
        # primi 20 → False perche' il controllo charset e' su value[:20]
        bad = "0" + "Z" * 21
        assert looks_like_wkb_hex(bad) is False

    def test_mixed_case_hex_accepted(self):
        # il charset accetta sia maiuscole che minuscole
        assert looks_like_wkb_hex("0" + "aAbBcCdDeEfF" + "012345678") is True


# ----------------------------------------------------------------------
# parse_geometry — guard su valori mancanti
# ----------------------------------------------------------------------

class TestParseGeometryMissingGuards:
    def test_none(self):
        assert parse_geometry(None) is None

    def test_nan_float(self):
        assert parse_geometry(float("nan")) is None

    def test_numpy_nan(self):
        assert parse_geometry(np.nan) is None

    def test_empty_string(self):
        assert parse_geometry("") is None

    def test_literal_none_string(self):
        assert parse_geometry("None") is None

    def test_whitespace_only_returns_none(self):
        # str(value).strip() vuoto → None
        assert parse_geometry("   ") is None


# ----------------------------------------------------------------------
# parse_geometry — shapely passthrough
# ----------------------------------------------------------------------

class TestParseGeometryShapelyPassthrough:
    def test_returns_same_object_identity(self):
        assert parse_geometry(POINT) is POINT

    def test_polygon_passthrough(self):
        poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])
        assert parse_geometry(poly) is poly


# ----------------------------------------------------------------------
# parse_geometry — WKT
# ----------------------------------------------------------------------

class TestParseGeometryWkt:
    def test_point(self):
        out = parse_geometry("POINT (1 2)")
        assert out is not None and out.equals(POINT)

    def test_linestring(self):
        out = parse_geometry("LINESTRING (0 0, 1 1)")
        assert out is not None and out.equals(LineString([(0, 0), (1, 1)]))

    def test_lowercase_prefix_uppercased_for_match(self):
        # value_upper.startswith(prefix): il prefisso lowercase entra nel
        # ramo WKT comunque
        out = parse_geometry("point (3 4)")
        assert out is not None and out.equals(Point(3, 4))

    def test_invalid_wkt_falls_through_to_none(self):
        # prefisso valido ma corpo rotto → wkt.loads solleva → nessun
        # altro ramo lo cattura → None
        assert parse_geometry("POLYGON (broken") is None


# ----------------------------------------------------------------------
# parse_geometry — GeoJSON
# ----------------------------------------------------------------------

class TestParseGeometryGeoJson:
    def test_geometry_object(self):
        out = parse_geometry('{"type":"Point","coordinates":[5,6]}')
        assert out is not None and out.equals(Point(5, 6))

    def test_feature_with_geometry(self):
        gj = '{"type":"Feature","properties":{},"geometry":{"type":"Point","coordinates":[7,8]}}'
        out = parse_geometry(gj)
        assert out is not None and out.equals(Point(7, 8))

    def test_feature_without_geometry_returns_none(self):
        gj = '{"type":"Feature","properties":{},"geometry":null}'
        assert parse_geometry(gj) is None

    def test_feature_collection_uses_first_feature(self):
        gj = (
            '{"type":"FeatureCollection","features":['
            '{"type":"Feature","geometry":{"type":"Point","coordinates":[9,10]}}'
            ']}'
        )
        out = parse_geometry(gj)
        assert out is not None and out.equals(Point(9, 10))

    def test_empty_feature_collection_returns_none(self):
        gj = '{"type":"FeatureCollection","features":[]}'
        assert parse_geometry(gj) is None

    def test_malformed_json_brace_falls_through(self):
        # inizia con "{" ma JSON rotto → except → prosegue → non WKT/WKB
        # → None
        assert parse_geometry('{not valid json') is None

    def test_json_dict_without_type_key_falls_through(self):
        # parse ok ma manca "type" → non entra in nessun ramo shape() →
        # cade fino a None
        assert parse_geometry('{"foo": 1}') is None


# ----------------------------------------------------------------------
# parse_geometry — WKB (bytes / memoryview / hex / EWKB)
# ----------------------------------------------------------------------

class TestParseGeometryWkb:
    def test_wkb_bytes(self):
        out = parse_geometry(POINT.wkb)
        assert out is not None and out.equals(POINT)

    def test_wkb_memoryview(self):
        out = parse_geometry(memoryview(POINT.wkb))
        assert out is not None and out.equals(POINT)

    def test_wkb_hex_string(self):
        out = parse_geometry(POINT_WKB_HEX)
        assert out is not None and out.equals(POINT)

    def test_ewkb_hex_string_with_srid(self):
        out = parse_geometry(EWKB_4326)
        assert out is not None and out.equals(POINT)

    def test_unrecognized_string_returns_none(self):
        assert parse_geometry("not-a-geometry-at-all") is None


# ----------------------------------------------------------------------
# parse_wkb_hex
# ----------------------------------------------------------------------

class TestParseWkbHex:
    def test_standard_wkb_hex(self):
        out = parse_wkb_hex(POINT_WKB_HEX)
        assert out is not None and out.equals(POINT)

    def test_ewkb_hex_parsed_by_first_branch(self):
        # shapely.wkb.loads gestisce EWKB direttamente → primo try riesce
        out = parse_wkb_hex(EWKB_4326)
        assert out is not None and out.equals(POINT)

    def test_invalid_hex_returns_none(self):
        assert parse_wkb_hex("00" * 15) is None

    def test_garbage_returns_none(self):
        assert parse_wkb_hex("zzzzzzzzzzzzzzzzzzzz") is None


# ----------------------------------------------------------------------
# extract_srid_from_ewkb
# ----------------------------------------------------------------------

class TestExtractSridFromEwkb:
    def test_valid_srid_4326(self):
        assert extract_srid_from_ewkb(EWKB_4326) == 4326

    def test_standard_wkb_without_srid_flag_returns_none(self):
        # byte 8:10 non e' "20" → nessun SRID
        assert extract_srid_from_ewkb(POINT_WKB_HEX) is None

    def test_srid_zero_is_treated_as_absent(self):
        # flag SRID presente ma valore 0 → guard `srid > 0` → None
        val = "0101000020" + "00000000" + "000000000000F03F0000000000000040"
        assert extract_srid_from_ewkb(val) is None

    def test_non_wkb_hex_returns_none(self):
        assert extract_srid_from_ewkb("this is clearly not wkb hex data") is None

    def test_too_short_returns_none(self):
        assert extract_srid_from_ewkb("0101000020") is None


# ----------------------------------------------------------------------
# parse_geometry_series — rami non coperti dal contract test
# ----------------------------------------------------------------------

class TestParseGeometrySeriesWkbBytesFastPath:
    def test_homogeneous_wkb_bytes(self):
        s = pd.Series([Point(1, 1).wkb, Point(2, 2).wkb])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(1, 1))
        assert out.iloc[1].equals(Point(2, 2))
        assert out.dtype == "object"

    def test_memoryview_path_converted_to_bytes(self):
        s = pd.Series([memoryview(Point(3, 3).wkb), memoryview(Point(4, 4).wkb)])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(3, 3))
        assert out.iloc[1].equals(Point(4, 4))

    def test_bytes_fast_path_fallback_on_invalid(self):
        # bytes invalidi → from_wkb(on_invalid="raise") solleva → fallback
        # per-riga via apply(parse_geometry) che ritorna None sull'invalido
        s = pd.Series([Point(5, 5).wkb, b"\x00\x01\x02bad"])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(5, 5))
        assert out.iloc[1] is None


class TestParseGeometrySeriesWkbHexPerRow:
    def test_homogeneous_wkb_hex(self):
        s = pd.Series([Point(1, 2).wkb_hex, Point(3, 4).wkb_hex])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(1, 2))
        assert out.iloc[1].equals(Point(3, 4))

    def test_ewkb_hex_series_with_srid(self):
        s = pd.Series([EWKB_4326, EWKB_4326])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(POINT)
        assert out.iloc[1].equals(POINT)


class TestParseGeometrySeriesWktFastPath:
    def test_homogeneous_wkt_vectorized(self):
        s = pd.Series(["POINT (0 0)", "POINT (1 1)", "POINT (2 2)"])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(0, 0))
        assert out.iloc[2].equals(Point(2, 2))
        assert out.dtype == "object"

    def test_wkt_with_embedded_empty_string_treated_as_none(self):
        # il map della fast path normalizza "" → None prima di from_wkt
        s = pd.Series(["POINT (0 0)", "", "POINT (2 2)"])
        out = parse_geometry_series(s)
        assert out.iloc[0].equals(Point(0, 0))
        assert out.iloc[1] is None
        assert out.iloc[2].equals(Point(2, 2))


class TestParseGeometrySeriesUnrecognizedStrings:
    def test_plain_strings_fall_back_to_per_row_none(self):
        # niente prefisso WKT, non bytes, non WKB hex → ultimo ramo:
        # series.apply(parse_geometry) → tutti None
        s = pd.Series(["foo", "bar", "baz"])
        out = parse_geometry_series(s)
        assert all(v is None for v in out)
        assert len(out) == 3

    def test_first_null_sample_skipped_dropna(self):
        # dropna salta i None iniziali; il sample reale e' il primo
        # non-null (WKT) → fast path WKT
        s = pd.Series([None, "POINT (8 9)", None])
        out = parse_geometry_series(s)
        assert out.iloc[0] is None
        assert out.iloc[1].equals(Point(8, 9))
        assert out.iloc[2] is None


def test_module_level_constants_present():
    # smoke sui contratti pubblici del modulo
    from app.core.dataframes import geometry_parsing as gp

    assert "geometry" in gp.GEOMETRY_COLUMN_NAMES
    assert "POINT" in gp.WKT_PREFIXES
    assert "GEOMETRYCOLLECTION" in gp.WKT_PREFIXES
    assert not math.isnan(len(gp.WKT_PREFIXES))
