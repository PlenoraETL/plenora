"""
Contract test — guard su dataset che attraversano piu' zone UTM.

`get_utm_crs_for_geometry()` sceglie una singola zona UTM basata sul
"""

from __future__ import annotations

import logging
import geopandas as gpd
import pytest
from shapely.geometry import Point


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


@pytest.fixture
def geo_caplog(caplog):
    """caplog non cattura `app.utils.geo` perche' il logger custom ha
    propagate=False. Aggancia l'handler di caplog direttamente al target.
    """
    target = logging.getLogger("app.core.manipola.transforms.spatial_helpers")
    target.addHandler(caplog.handler)
    original_level = target.level
    target.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(original_level)


def _gdf_from_lon_range(lon_min: float, lon_max: float, lat: float = 44.0):
    """Crea GeoDataFrame WGS84 con 2 punti che definiscono il bbox."""
    pts = [Point(lon_min, lat), Point(lon_max, lat)]
    return gpd.GeoDataFrame(geometry=pts, crs="EPSG:4326")


# 1. Caso nominale — nessun warning


@pytest.mark.parametrize("lon_min,lon_max", [
    (11.30, 11.35),   # Bologna urbano: 0.05 gradi
    (9.00, 11.00),    # Lombardia: 2 gradi
    (7.00, 10.00),    # Italia NW: 3 gradi esatti (soglia inclusiva)
])
def test_utm_guard_silent_for_compact_datasets(lon_min, lon_max, geo_caplog):
    from app.core.manipola.transforms.spatial_helpers import get_utm_crs_for_geometry

    caplog = geo_caplog
    gdf =_gdf_from_lon_range(lon_min, lon_max)
    with caplog.at_level(logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"):
        epsg = get_utm_crs_for_geometry(gdf)

    assert epsg.startswith("EPSG:326") or epsg.startswith("EPSG:327")
    guard_records = [r for r in caplog.records if "UTM zone guard" in r.message]
    assert not guard_records, f"Warning inatteso: {[r.message for r in guard_records]}"


# 2. Bbox moderato (> 3 gradi) — warning standard


@pytest.mark.parametrize("lon_min,lon_max", [
    (7.00, 11.00),    # Italia NW: 4 gradi
    (6.00, 12.00),    # Italia W-E: 6 gradi esatti (dentro soglia standard)
])
def test_utm_guard_warns_for_multizone_moderate(lon_min, lon_max, geo_caplog):
    from app.core.manipola.transforms.spatial_helpers import get_utm_crs_for_geometry

    caplog = geo_caplog
    gdf =_gdf_from_lon_range(lon_min, lon_max)
    with caplog.at_level(logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"):
        epsg = get_utm_crs_for_geometry(gdf)

    assert epsg.startswith("EPSG:326") or epsg.startswith("EPSG:327")
    guard_records = [r for r in caplog.records if "UTM zone guard" in r.message]
    assert len(guard_records) == 1, (
        f"Atteso 1 warning 'UTM zone guard', trovati {len(guard_records)}"
    )
    assert "CRITICAL" not in guard_records[0].message
    assert guard_records[0].levelno == logging.WARNING


# 3. Bbox molto ampio (> 6 gradi) — warning critico


@pytest.mark.parametrize("lon_min,lon_max", [
    (7.00, 14.00),    # Italia completa: 7 gradi
    (-10.00, 25.00),  # Europa occidentale: 35 gradi
])
def test_utm_guard_warns_critical_for_multizone_wide(lon_min, lon_max, geo_caplog):
    from app.core.manipola.transforms.spatial_helpers import get_utm_crs_for_geometry

    caplog = geo_caplog
    gdf =_gdf_from_lon_range(lon_min, lon_max)
    with caplog.at_level(logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"):
        epsg = get_utm_crs_for_geometry(gdf)

    assert epsg.startswith("EPSG:326") or epsg.startswith("EPSG:327")
    guard_records = [r for r in caplog.records if "UTM zone guard" in r.message]
    assert len(guard_records) == 1, (
        f"Atteso 1 warning 'UTM zone guard CRITICAL', trovati {len(guard_records)}"
    )
    assert "CRITICAL" in guard_records[0].message


# 4. Regression: casi patologici esistenti continuano a funzionare


def test_utm_guard_empty_gdf_returns_default_no_warning(caplog):
    from app.core.manipola.transforms.spatial_helpers import get_utm_crs_for_geometry

    gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    with caplog.at_level(logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"):
        epsg = get_utm_crs_for_geometry(gdf)

    assert epsg == "EPSG:32632"
    guard_records = [r for r in caplog.records if "UTM zone guard" in r.message]
    assert not guard_records


def test_utm_guard_single_point_no_warning(caplog):
    from app.core.manipola.transforms.spatial_helpers import get_utm_crs_for_geometry

    gdf = gpd.GeoDataFrame(geometry=[Point(11.34, 44.49)], crs="EPSG:4326")
    with caplog.at_level(logging.WARNING, logger="app.core.manipola.transforms.spatial_helpers"):
        epsg = get_utm_crs_for_geometry(gdf)

    assert epsg == "EPSG:32632"
    guard_records = [r for r in caplog.records if "UTM zone guard" in r.message]
    assert not guard_records
