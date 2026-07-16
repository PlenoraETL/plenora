from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


def _temporal_gdf():
    geopandas = pytest.importorskip("geopandas")
    pytest.importorskip("pyogrio")
    from shapely.geometry import Point

    return geopandas.GeoDataFrame(
        {
            "when_dt": [
                datetime(
                    2024, 2, 29, 12, 0,
                    tzinfo=timezone(timedelta(hours=2)),
                ),
                datetime(1899, 12, 31),
            ],
            "when_date": ["29/02/2024", "31/12/1899"],
        },
        geometry=[Point(9, 45), Point(10, 46)],
        crs="EPSG:4326",
    ).rename_geometry("shape")


@pytest.mark.parametrize("formato", ["gpkg", "geojson"])
def test_geospatial_temporal_roundtrip_preserves_dates_and_utc(formato):
    from app.core.dataframes.geospatial_io import geospatial_dataframe_to_bytes
    from app.core.io.geospatial import read_vector_bytes

    raw = geospatial_dataframe_to_bytes(
        _temporal_gdf(),
        formato,
        "shape",
        "EPSG:4326",
        column_types={"when_date": "date"},
    )
    back = read_vector_bytes(raw)

    assert list(back.columns) == ["when_dt", "when_date", "geometry"]
    assert back.loc[0, "when_dt"] == pd.Timestamp("2024-02-29 10:00:00")
    assert back.loc[0, "when_date"] == pd.Timestamp("2024-02-29")
    assert back.loc[1, "when_dt"] == pd.Timestamp("1899-12-31")
    assert back.loc[1, "when_date"] == pd.Timestamp("1899-12-31")
    if formato == "geojson":
        assert json.loads(raw)["type"] == "FeatureCollection"


def test_shapefile_temporal_roundtrip_uses_iso_text_and_valid_zip():
    from app.core.dataframes.geospatial_io import geospatial_dataframe_to_bytes
    from app.core.io.geospatial import read_vector_bytes

    raw = geospatial_dataframe_to_bytes(
        _temporal_gdf(),
        "shp",
        "shape",
        "EPSG:4326",
        column_types={"when_date": "date"},
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        suffixes = {name.rsplit(".", 1)[-1] for name in archive.namelist()}
    assert {"shp", "shx", "dbf", "prj"} <= suffixes

    back = read_vector_bytes(raw)
    assert list(back.columns) == ["when_dt", "when_date", "geometry"]
    assert back["when_dt"].tolist() == [
        "2024-02-29 10:00:00",
        "1899-12-31 00:00:00",
    ]
    assert back["when_date"].tolist() == ["2024-02-29", "1899-12-31"]


def test_geospatial_declared_temporal_invalid_value_fails_closed():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    from app.core.dataframes.geospatial_io import geospatial_dataframe_to_bytes

    gdf = geopandas.GeoDataFrame(
        {"when": ["29/02/2024", "not-a-date"]},
        geometry=[Point(9, 45), Point(10, 46)],
        crs="EPSG:4326",
    )
    with pytest.raises(ValueError, match="valori non validi"):
        geospatial_dataframe_to_bytes(
            gdf,
            "gpkg",
            "geometry",
            "EPSG:4326",
            column_types={"when": "date"},
        )


def test_geopackage_alias_is_real_gpkg_and_gdb_write_is_rejected():
    from app.core.dataframes.frame_io import save_dataframe_to_bytes

    gdf = _temporal_gdf()
    raw = save_dataframe_to_bytes(
        gdf,
        "geopackage",
        geom_column="shape",
        column_types={"when_date": "date"},
    )
    assert raw.startswith(b"SQLite format 3")
    with pytest.raises(ValueError, match="solo in lettura"):
        save_dataframe_to_bytes(gdf, "gdb", geom_column="shape")


def test_pipeline_result_detects_and_reports_physical_geometry_name(monkeypatch):
    import app.core.results.streaming_blob_writer as streaming_blob_writer
    from app.core.results.pipeline_result import save_pipeline_result_outcome

    saved_calls = []

    def _save(**kwargs):
        saved_calls.append(kwargs)
        return "result-1"

    monkeypatch.setattr(
        streaming_blob_writer,
        "save_dataframe_to_result_blob_streaming",
        _save,
    )
    outcome = save_pipeline_result_outcome(
        project_id="p1",
        df=_temporal_gdf(),
        output_format="gpkg",
        filename_stem="map",
    )

    assert saved_calls[0]["geom_column"] == "shape"
    assert outcome.output_columns == ["when_dt", "when_date", "geometry"]
