"""
Contract test — op_geo_reproject source_crs safety (B2 Phase 4).

Background:
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.contract,
    pytest.mark.security,
]


# Fixtures: una geometria semplice, riusata in piu' configurazioni di CRS.


@pytest.fixture
def wkt_dataframe():
    """Plain pandas DataFrame con geometrie WKT (no EWKB, no SRID).
    Rappresenta il caso reale che ha originato il bug: importazione da CSV
    senza metadati CRS.
    """
    return pd.DataFrame({
        "id": [1, 2, 3],
        "geometry": [
            "POINT (11.34 44.49)",   # Bologna circa, in lat/lon
            "POINT (12.49 41.90)",   # Roma circa
            "POINT (9.19 45.46)",    # Milano circa
        ],
    })


@pytest.fixture
def gdf_with_crs():
    """GeoDataFrame con CRS gia' impostato (caso "CRS gia' presente")."""
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(11.34, 44.49), Point(12.49, 41.90)],
        crs="EPSG:4326",
    )


@pytest.fixture
def gdf_without_crs():
    """GeoDataFrame con geometrie ma SENZA CRS impostato.
    Rappresenta il vettore d'attacco principale: gpd.GeoDataFrame(...)
    costruito a mano senza specificare crs=, oppure CRS perso lungo la
    pipeline.
    """
    return gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(500000, 4900000), Point(700000, 5000000)],
        crs=None,
    )


# 1. source_crs mancante + nessun CRS recuperabile -> ValueError fail-loud


def test_reproject_raises_when_source_crs_missing_and_gdf_has_no_crs(
    gdf_without_crs,
):
    """Caso canonico del bug: GeoDataFrame senza CRS, source_crs non
    fornito. Pre-B2 venivano assunti EPSG:4326 silenziosamente; post-B2
    deve sollevare ValueError chiaro."""
    from app.core.manipola.transforms.spatial import op_geo_reproject

    with pytest.raises(ValueError) as exc:
        op_geo_reproject(
            gdf_without_crs,
            geom_column="geometry",
            target_crs="EPSG:3857",
            source_crs=None,
        )
    msg = str(exc.value).lower()
    # Il messaggio deve indicare chiaramente la causa al chiamante umano.
    assert "source_crs" in msg
    assert "crs" in msg


def test_reproject_raises_when_source_crs_missing_on_plain_wkt_dataframe(
    wkt_dataframe,
):
    """Vettore d'attacco realistico: plain DataFrame con WKT (CSV import)
    senza metadati. Niente SRID EWKB, niente CRS, niente source_crs ->
    fail-loud invece di assumere EPSG:4326."""
    from app.core.manipola.transforms.spatial import op_geo_reproject

    with pytest.raises(ValueError) as exc:
        op_geo_reproject(
            wkt_dataframe,
            geom_column="geometry",
            target_crs="EPSG:3857",
            source_crs=None,
        )
    assert "source_crs" in str(exc.value).lower()


# 2. source_crs esplicito -> riproiezione ok


def test_reproject_works_with_explicit_source_crs_on_gdf_without_crs(
    gdf_without_crs,
):
    """Workaround documentato: l'utente specifica source_crs esplicito.
    Deve funzionare e produrre un GeoDataFrame nel target_crs richiesto."""
    from app.core.manipola.transforms.spatial import op_geo_reproject

    # gdf_without_crs ha coordinate metriche tipo UTM 32N (Italia centrale).
    result = op_geo_reproject(
        gdf_without_crs,
        geom_column="geometry",
        target_crs="EPSG:4326",
        source_crs="EPSG:32632",
    )
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs is not None
    assert "4326" in str(result.crs)
    # Le coordinate devono essere in lat/lon (gradi), non piu' in metri.
    # Sanity: longitudine intorno a 5-15, latitudine intorno a 40-50.
    xs = result.geometry.x.tolist()
    ys = result.geometry.y.tolist()
    assert all(-180 <= x <= 180 for x in xs)
    assert all(-90 <= y <= 90 for y in ys)


def test_reproject_works_with_explicit_source_crs_on_plain_dataframe(
    wkt_dataframe,
):
    """Stesso vettore (CSV import senza CRS), risolto specificando
    source_crs come parametro: deve funzionare."""
    from app.core.manipola.transforms.spatial import op_geo_reproject

    result = op_geo_reproject(
        wkt_dataframe,
        geom_column="geometry",
        target_crs="EPSG:3857",
        source_crs="EPSG:4326",
    )
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs is not None
    assert "3857" in str(result.crs)


# 3. CRS gia' presente sul GeoDataFrame -> riproiezione ok (no source_crs)


def test_reproject_works_when_gdf_has_crs_and_source_crs_omitted(
    gdf_with_crs,
):
    """Caso felice: il GeoDataFrame porta gia' un CRS valido (EPSG:4326).
    L'utente non deve essere costretto a ripeterlo via source_crs."""
    from app.core.manipola.transforms.spatial import op_geo_reproject

    result = op_geo_reproject(
        gdf_with_crs,
        geom_column="geometry",
        target_crs="EPSG:3857",
        source_crs=None,
    )
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs is not None
    assert "3857" in str(result.crs)
    # Sanity: dopo la riproiezione a Web Mercator i valori sono in metri
    # (ordini di grandezza milioni), non piu' in gradi.
    xs = result.geometry.x.abs().tolist()
    assert all(x > 1000 for x in xs)
