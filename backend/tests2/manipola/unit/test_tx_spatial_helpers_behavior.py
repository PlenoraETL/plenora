"""
Behavioral coverage per `spatial_helpers.py`.

Esercita ogni funzione del modulo (incluse le helper "private" usate dal
cluster spatial) sui rami significativi: geometria attiva mancante/rinominata,
calcolo zona UTM, risoluzione CRS metrico/output, mapping lon/lat -> UTM EPSG
e buffer per-riga in UTM locale.

Nessun mock: input pandas/geopandas reali e minimi.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from app.core.manipola.transforms.spatial_helpers import (
    _buffer_per_row_local_utm,
    _ensure_active_geometry,
    _resolve_metric_crs,
    _resolve_output_geom_crs,
    _utm_epsg_for_lonlat,
    get_utm_crs_for_geometry,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# _ensure_active_geometry
# ---------------------------------------------------------------------------


def test_ensure_active_geometry_noop_when_active_present():
    gdf = gpd.GeoDataFrame(
        {"v": [1, 2]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    out = _ensure_active_geometry(gdf)
    assert out.geometry.name == "geometry"
    assert "geometry" in out.columns


def test_ensure_active_geometry_recovers_after_rename():
    """Simula il caso post-sjoin: la geometria attiva e' stata rinominata.

    Costruiamo un GeoDataFrame con colonna geometry attiva 'geometry_right'
    e nessuna colonna 'geometry'. La funzione deve trovare la colonna geometry
    superstite e impostarla come attiva.
    """
    gdf = gpd.GeoDataFrame(
        {"v": [10, 20]},
        geometry=gpd.GeoSeries([Point(0, 0), Point(2, 2)], crs="EPSG:4326"),
    )
    # Rinomina la colonna geometry: l'attributo .geometry punta ancora al
    # vecchio nome non piu' presente fra le colonne.
    renamed = gdf.rename(columns={"geometry": "geometry_right"})
    out = _ensure_active_geometry(renamed)
    # La geometria attiva deve essere una colonna esistente.
    assert out.geometry.name in out.columns
    assert out.geometry.name == "geometry_right"


def test_ensure_active_geometry_prefers_geometry_column():
    """Con piu' colonne geometry, 'geometry' e' preferita."""
    base = gpd.GeoSeries([Point(0, 0), Point(1, 1)], crs="EPSG:4326")
    other = gpd.GeoSeries([Point(5, 5), Point(6, 6)], crs="EPSG:4326")
    gdf = gpd.GeoDataFrame({"v": [1, 2]}, geometry=base, crs="EPSG:4326")
    gdf["geom_extra"] = other
    # Forza la perdita del riferimento attivo rinominando temporaneamente.
    gdf = gdf.set_geometry("geom_extra")
    # Ora la geometria attiva e' 'geom_extra' ma esiste ancora 'geometry'.
    # Rinominiamo per simulare attiva mancante mantenendo entrambe le colonne.
    gdf2 = gpd.GeoDataFrame(
        {"v": [1, 2], "geometry": base, "geom_extra": other},
    )
    # Nessuna geometria attiva impostata correttamente -> discovery.
    out = _ensure_active_geometry(gdf2)
    assert out.geometry.name == "geometry"


# ---------------------------------------------------------------------------
# get_utm_crs_for_geometry
# ---------------------------------------------------------------------------


def test_get_utm_empty_returns_default():
    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    assert get_utm_crs_for_geometry(gdf) == "EPSG:32632"


def test_get_utm_northern_hemisphere_zone():
    # Bologna ~ lon 11.3 -> zona 32, lat positiva -> 326xx
    gdf = gpd.GeoDataFrame(geometry=[Point(11.34, 44.49)], crs="EPSG:4326")
    assert get_utm_crs_for_geometry(gdf) == "EPSG:32632"


def test_get_utm_southern_hemisphere_zone():
    # lon ~ -58 (Buenos Aires), lat negativa -> 327xx
    gdf = gpd.GeoDataFrame(geometry=[Point(-58.38, -34.6)], crs="EPSG:4326")
    epsg = get_utm_crs_for_geometry(gdf)
    assert epsg.startswith("EPSG:327")


def test_get_utm_handles_failure_path_returns_default():
    """Geometrie None inducono il ramo except -> default EPSG:32632."""
    gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([None]), crs="EPSG:4326")
    assert get_utm_crs_for_geometry(gdf) == "EPSG:32632"


# ---------------------------------------------------------------------------
# _resolve_metric_crs
# ---------------------------------------------------------------------------


def test_resolve_metric_crs_explicit_string_wins():
    gdf = gpd.GeoDataFrame(geometry=[Point(11, 44)], crs="EPSG:4326")
    assert _resolve_metric_crs(gdf, "  EPSG:3857  ") == "EPSG:3857"


def test_resolve_metric_crs_empty_string_falls_through_to_auto_utm():
    gdf = gpd.GeoDataFrame(geometry=[Point(11.34, 44.49)], crs="EPSG:4326")
    # stringa vuota/whitespace -> non considerata -> auto UTM su CRS geografico
    assert _resolve_metric_crs(gdf, "   ") == "EPSG:32632"


def test_resolve_metric_crs_auto_utm_when_geographic():
    gdf = gpd.GeoDataFrame(geometry=[Point(11.34, 44.49)], crs="EPSG:4326")
    assert _resolve_metric_crs(gdf, None) == "EPSG:32632"


def test_resolve_metric_crs_none_when_already_metric():
    gdf = gpd.GeoDataFrame(geometry=[Point(500000, 4900000)], crs="EPSG:32632")
    assert _resolve_metric_crs(gdf, None) is None


def test_resolve_metric_crs_none_when_crs_missing():
    gdf = gpd.GeoDataFrame(geometry=[Point(1, 1)])  # crs None
    assert gdf.crs is None
    assert _resolve_metric_crs(gdf, None) is None


# ---------------------------------------------------------------------------
# _utm_epsg_for_lonlat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (11.34, 44.49, "EPSG:32632"),   # Italia, nord
        (-58.38, -34.6, "EPSG:32721"),  # Argentina, sud
        (0.0, 51.5, "EPSG:32631"),      # Londra, nord
    ],
)
def test_utm_epsg_for_lonlat_nominal(lon, lat, expected):
    assert _utm_epsg_for_lonlat(lon, lat) == expected


def test_utm_epsg_for_lonlat_clamps_below_zone_1():
    # lon = -181 -> zone calcolata < 1 -> clamp a 1
    assert _utm_epsg_for_lonlat(-181.0, 10.0) == "EPSG:32601"


def test_utm_epsg_for_lonlat_clamps_above_zone_60():
    # lon = 181 -> zone > 60 -> clamp a 60
    assert _utm_epsg_for_lonlat(181.0, 10.0) == "EPSG:32660"


def test_utm_epsg_for_lonlat_equator_is_northern():
    # lat == 0 -> ramo lat >= 0 -> 326xx
    assert _utm_epsg_for_lonlat(11.0, 0.0).startswith("EPSG:326")


# ---------------------------------------------------------------------------
# _resolve_output_geom_crs
# ---------------------------------------------------------------------------


def test_resolve_output_geom_same_returns_original():
    assert _resolve_output_geom_crs("EPSG:4326", "EPSG:32632", "same") == "EPSG:4326"


def test_resolve_output_geom_none_returns_original():
    assert _resolve_output_geom_crs("EPSG:4326", "EPSG:32632", None) == "EPSG:4326"


def test_resolve_output_geom_metric_uses_metric_used():
    assert _resolve_output_geom_crs("EPSG:4326", "EPSG:32632", "metric") == "EPSG:32632"


def test_resolve_output_geom_metric_falls_back_to_original():
    # metric_used None -> fallback original
    assert _resolve_output_geom_crs("EPSG:4326", None, "metric") == "EPSG:4326"


def test_resolve_output_geom_explicit_epsg_passthrough():
    assert _resolve_output_geom_crs("EPSG:4326", "EPSG:32632", "EPSG:3857") == "EPSG:3857"


# ---------------------------------------------------------------------------
# _buffer_per_row_local_utm
# ---------------------------------------------------------------------------


def test_buffer_per_row_returns_polygons_and_preserves_crs_index():
    pts = gpd.GeoSeries(
        [Point(11.34, 44.49), Point(11.40, 44.55)],
        crs="EPSG:4326",
        index=[7, 9],
    )
    out = _buffer_per_row_local_utm(pts, distance=50.0, cap_style_int=1)

    # Stesso CRS sorgente, stesso indice, geometrie ora poligonali (buffer).
    assert str(out.crs) == "EPSG:4326"
    assert list(out.index) == [7, 9]
    for geom in out:
        assert geom is not None
        assert geom.geom_type in ("Polygon", "MultiPolygon")
        assert not geom.is_empty


def test_buffer_per_row_keeps_none_for_empty_and_missing():
    pts = gpd.GeoSeries(
        [Point(11.34, 44.49), None, Polygon()],
        crs="EPSG:4326",
    )
    out = _buffer_per_row_local_utm(pts, distance=10.0, cap_style_int=1)
    # Geometria valida -> buffer; None ed empty restano None.
    assert out.iloc[0] is not None and out.iloc[0].geom_type == "Polygon"
    assert out.iloc[1] is None
    assert out.iloc[2] is None


def test_buffer_per_row_skips_polar_singularity(caplog):
    # lat >= 89 -> punto saltato (UTM degenera al polo) -> resta None
    pts = gpd.GeoSeries([Point(0.0, 89.5)], crs="EPSG:4326")
    with caplog.at_level(
        logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"
    ):
        out = _buffer_per_row_local_utm(pts, distance=10.0, cap_style_int=1)
    assert out.iloc[0] is None


def test_buffer_per_row_defaults_src_crs_to_4326_when_absent():
    # GeoSeries senza CRS: src_crs default = EPSG:4326, output rispetta default.
    pts = gpd.GeoSeries([Point(11.34, 44.49)])
    assert pts.crs is None
    out = _buffer_per_row_local_utm(pts, distance=25.0, cap_style_int=1)
    assert str(out.crs) == "EPSG:4326"
    assert out.iloc[0] is not None and out.iloc[0].geom_type == "Polygon"


def test_buffer_per_row_groups_distinct_utm_zones():
    """Punti in zone UTM diverse vengono raggruppati e bufferati entrambi."""
    pts = gpd.GeoSeries(
        [Point(11.34, 44.49), Point(2.35, 48.85)],  # Bologna (z32) e Parigi (z31)
        crs="EPSG:4326",
    )
    out = _buffer_per_row_local_utm(pts, distance=100.0, cap_style_int=1)
    assert len(out) == 2
    for geom in out:
        assert geom is not None and geom.geom_type == "Polygon"
