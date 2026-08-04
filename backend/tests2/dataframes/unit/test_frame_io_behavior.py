"""Behavioral coverage for app/core/dataframes/frame_io.py.

Esercita gli helper di I/O DataFrame del modulo: dispatch per formato in
lettura (bytes/path/file), serializzazione `save_dataframe_to_bytes`,
coercion numerica JSON, normalizzazione format hint, fallback ArcGIS,
discovery dell'output piu' recente e gestione ZIP single-file.

I rami che richiedono servizi esterni reali (DB/HTTP) non esistono in questo
modulo: tutto e' file/bytes-bound e testabile a unita' con tmp_path + bytes
in-memory. I formati vector binari complessi (gpkg/shp/gdb directory) sono
coperti via geojson/zip e tramite il dispatch error-branch dove un vero
artifact binario richiederebbe fixture pesanti.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from app.core.dataframes.frame_io import (
    _coerce_json_dataframe_columns,
    _load_dataframe_from_bytes,
    _load_json_dataframe_from_text,
    _normalize_format_hint,

    find_latest_output_file,
    load_arcgis_feature_dataframe,
    load_dataframe_from_artifact,
    load_dataframe_from_file,
    save_dataframe_to_bytes,
)


pytestmark = [pytest.mark.unit]


# ----------------------------------------------------------------------
# _normalize_format_hint
# ----------------------------------------------------------------------


def test_normalize_format_hint_strips_dot_and_lowercases():
    assert _normalize_format_hint(".CSV") == "csv"
    assert _normalize_format_hint("  Parquet ") == "parquet"


def test_normalize_format_hint_aliases_geopackage_to_gpkg():
    assert _normalize_format_hint("GeoPackage") == "gpkg"
    assert _normalize_format_hint(".geopackage") == "gpkg"


def test_normalize_format_hint_none_and_empty_return_none():
    assert _normalize_format_hint(None) is None
    assert _normalize_format_hint("") is None
    assert _normalize_format_hint("   ") is None



# ----------------------------------------------------------------------
# _coerce_json_dataframe_columns
# ----------------------------------------------------------------------


def test_coerce_json_does_not_mutate_input():
    df = pd.DataFrame({"id": ["1", "2", "3"]})
    snapshot = df["id"].tolist()
    out = _coerce_json_dataframe_columns(df)
    # input invariato
    assert df["id"].tolist() == snapshot
    assert pd.api.types.is_object_dtype(df["id"]) or pd.api.types.is_string_dtype(df["id"])
    # output convertito
    assert "int" in str(out["id"].dtype).lower()


def test_coerce_json_skips_columns_containing_underscore():
    df = pd.DataFrame({"code": ["12_34", "56_78"]})
    out = _coerce_json_dataframe_columns(df)
    # underscore → non convertita a numerico
    assert out["code"].iloc[0] == "12_34"


def test_coerce_json_partial_numeric_string_column_stays_object():
    df = pd.DataFrame({"mix": ["1", "abc", "3"]})
    out = _coerce_json_dataframe_columns(df)
    # non tutti convertibili → ramo elif is_string → astype(object)
    assert pd.api.types.is_object_dtype(out["mix"])
    assert out["mix"].iloc[1] == "abc"


def test_coerce_json_leaves_already_numeric_columns_alone():
    df = pd.DataFrame({"n": [1, 2, 3], "s": ["a", "b", "c"]})
    out = _coerce_json_dataframe_columns(df)
    assert out["n"].tolist() == [1, 2, 3]
    assert out["s"].iloc[0] == "a"


# ----------------------------------------------------------------------
# _load_json_dataframe_from_text
# ----------------------------------------------------------------------


def test_load_json_from_text_builds_and_coerces():
    text = json.dumps([{"id": "1", "name": "a"}, {"id": "2", "name": "b"}])
    df = _load_json_dataframe_from_text(text)
    assert list(df.columns) == ["id", "name"]
    assert "int" in str(df["id"].dtype).lower()
    assert df["name"].tolist() == ["a", "b"]


# ----------------------------------------------------------------------
# _load_dataframe_from_bytes — dispatch per formato
# ----------------------------------------------------------------------


def test_load_bytes_csv_roundtrip():
    raw = b"a,b\n1,x\n2,y\n"
    df = _load_dataframe_from_bytes(raw, format_hint="csv")
    assert df["a"].tolist() == [1, 2]
    assert df["b"].tolist() == ["x", "y"]


def test_load_bytes_json_roundtrip():
    raw = json.dumps([{"a": 1, "b": "x"}]).encode("utf-8")
    df = _load_dataframe_from_bytes(raw, format_hint="json")
    assert df["a"].iloc[0] == 1
    assert df["b"].iloc[0] == "x"


def test_load_bytes_parquet_roundtrip_preserves_nullable_int():
    src = pd.DataFrame({"id": pd.array([1, None, 3], dtype="Int64")})
    buf = io.BytesIO()
    src.to_parquet(buf)
    df = _load_dataframe_from_bytes(buf.getvalue(), format_hint="parquet")
    assert str(df["id"].dtype) == "Int64"
    assert df["id"].iloc[1] is pd.NA


def test_load_bytes_xlsx_roundtrip():
    src = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    buf = io.BytesIO()
    src.to_excel(buf, index=False)
    df = _load_dataframe_from_bytes(buf.getvalue(), format_hint="xlsx")
    assert df["a"].tolist() == [1, 2]


def test_load_bytes_geojson_roundtrip():
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b"], "geometry": [Point(0, 0), Point(1, 1)]},
        geometry="geometry",
        crs="EPSG:4326",
    )
    raw = save_dataframe_to_bytes(gdf, format="geojson", geom_column="geometry")
    df = _load_dataframe_from_bytes(raw, format_hint="geojson")
    assert len(df) == 2
    assert "geometry" in df.columns


def test_load_bytes_unsupported_format_raises_valueerror():
    with pytest.raises(ValueError, match="non supportato"):
        _load_dataframe_from_bytes(b"data", format_hint="bogus")


# ----------------------------------------------------------------------
# load_dataframe_from_artifact
# ----------------------------------------------------------------------


def test_artifact_from_bytes_csv():
    df = load_dataframe_from_artifact(data=b"a,b\n1,2\n", format_hint="csv")
    assert df["a"].iloc[0] == 1
    assert df["b"].iloc[0] == 2


def test_artifact_no_path_no_data_raises():
    with pytest.raises(ValueError, match="senza path o data"):
        load_dataframe_from_artifact()


def test_artifact_path_csv_explicit_hint(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,x\n", encoding="utf-8")
    df = load_dataframe_from_artifact(path=p, format_hint="csv")
    assert df["a"].iloc[0] == 1


def test_artifact_path_json_explicit_hint(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps([{"id": "1"}]), encoding="utf-8")
    df = load_dataframe_from_artifact(path=p, format_hint="json")
    assert "int" in str(df["id"].dtype).lower()


def test_artifact_path_parquet_explicit_hint(tmp_path):
    src = pd.DataFrame({"id": pd.array([1, None], dtype="Int64")})
    p = tmp_path / "data.parquet"
    src.to_parquet(p)
    df = load_dataframe_from_artifact(path=p, format_hint="parquet")
    assert str(df["id"].dtype) == "Int64"


def test_artifact_path_xlsx_explicit_hint(tmp_path):
    src = pd.DataFrame({"a": [1, 2]})
    p = tmp_path / "data.xlsx"
    src.to_excel(p, index=False)
    df = load_dataframe_from_artifact(path=p, format_hint="xlsx")
    assert df["a"].tolist() == [1, 2]


def test_artifact_path_geojson_explicit_hint(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"name": ["a"], "geometry": [Point(0, 0)]},
        geometry="geometry",
        crs="EPSG:4326",
    )
    p = tmp_path / "data.geojson"
    gdf.to_file(p, driver="GeoJSON")
    df = load_dataframe_from_artifact(path=p, format_hint="geojson")
    assert "geometry" in df.columns


def test_artifact_path_no_hint_dispatches_by_extension(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a\n5\n", encoding="utf-8")
    df = load_dataframe_from_artifact(path=p)
    assert df["a"].iloc[0] == 5


# ----------------------------------------------------------------------
# load_dataframe_from_file — dispatch per estensione
# ----------------------------------------------------------------------


def test_file_missing_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError, match="File non trovato"):
        load_dataframe_from_file(tmp_path / "nope.csv")


def test_file_csv(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    df = load_dataframe_from_file(p)
    assert df["a"].tolist() == [1, 2]


def test_file_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps([{"id": "7"}]), encoding="utf-8")
    df = load_dataframe_from_file(p)
    assert "int" in str(df["id"].dtype).lower()


def test_file_parquet(tmp_path):
    src = pd.DataFrame({"id": pd.array([1, None, 3], dtype="Int64")})
    p = tmp_path / "x.parquet"
    src.to_parquet(p)
    df = load_dataframe_from_file(p)
    assert str(df["id"].dtype) == "Int64"


def test_file_xlsx(tmp_path):
    src = pd.DataFrame({"a": [1, 2]})
    p = tmp_path / "x.xlsx"
    src.to_excel(p, index=False)
    df = load_dataframe_from_file(p)
    assert df["a"].tolist() == [1, 2]


def test_file_geojson(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"g": [1], "geometry": [Point(2, 3)]}, geometry="geometry", crs="EPSG:4326"
    )
    p = tmp_path / "x.geojson"
    gdf.to_file(p, driver="GeoJSON")
    df = load_dataframe_from_file(p)
    assert "geometry" in df.columns


def test_file_unknown_extension_falls_back_to_csv(tmp_path):
    p = tmp_path / "x.dat"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    df = load_dataframe_from_file(p)
    assert df["a"].iloc[0] == 1


def test_file_zip_with_single_csv(tmp_path):
    csvp = tmp_path / "inner.csv"
    csvp.write_text("a,b\n10,20\n", encoding="utf-8")
    zp = tmp_path / "arc.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(csvp, "inner.csv")
    df = load_dataframe_from_file(zp)
    assert df["a"].iloc[0] == 10
    assert df["b"].iloc[0] == 20


def test_file_zip_unhandled_single_extension_raises(tmp_path):
    inner = tmp_path / "inner.bogus"
    inner.write_bytes(b"junk")
    zp = tmp_path / "arc.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(inner, "inner.bogus")
    with pytest.raises(ValueError, match="estensione non gestita"):
        load_dataframe_from_file(zp)


def test_file_zip_unrecognized_multifile_raises(tmp_path):
    f1 = tmp_path / "a.bogus"
    f2 = tmp_path / "b.bogus"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    zp = tmp_path / "arc.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(f1, "a.bogus")
        z.write(f2, "b.bogus")
    with pytest.raises(ValueError, match="Formato ZIP non riconosciuto"):
        load_dataframe_from_file(zp)


# ----------------------------------------------------------------------
# load_arcgis_feature_dataframe
# ----------------------------------------------------------------------


def test_arcgis_missing_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_arcgis_feature_dataframe(tmp_path / "missing.geojson")


def test_arcgis_csv_plain(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    df = load_arcgis_feature_dataframe(p)
    assert df["a"].iloc[0] == 1


def test_arcgis_csv_with_wkt_geometry_becomes_geodataframe(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("name,geometry\nfoo,POINT (1 2)\n", encoding="utf-8")
    df = load_arcgis_feature_dataframe(p)
    assert isinstance(df, gpd.GeoDataFrame)
    assert df.geometry.iloc[0].x == 1


def test_arcgis_geojson(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"n": [1], "geometry": [Point(0, 0)]}, geometry="geometry", crs="EPSG:4326"
    )
    p = tmp_path / "x.geojson"
    gdf.to_file(p, driver="GeoJSON")
    df = load_arcgis_feature_dataframe(p)
    assert "geometry" in df.columns


def test_arcgis_xlsx(tmp_path):
    src = pd.DataFrame({"a": [1, 2]})
    p = tmp_path / "x.xlsx"
    src.to_excel(p, index=False)
    df = load_arcgis_feature_dataframe(p)
    assert df["a"].tolist() == [1, 2]


def test_arcgis_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
    df = load_arcgis_feature_dataframe(p)
    assert df["a"].tolist() == [1, 2]


def test_arcgis_corrupt_unknown_binary_raises_valueerror(tmp_path):
    # suffix .bin sconosciuto, contenuto non vector → gpd.read_file fallisce,
    # nessun fallback CSV (non è .tsv/.txt) → ValueError esplicativo.
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02not-a-vector")
    with pytest.raises(ValueError, match="Formato file non supportato"):
        load_arcgis_feature_dataframe(p)


def test_arcgis_txt_fallback_to_csv(tmp_path):
    # suffix .txt: gpd.read_file fallisce ma il fallback CSV è ammesso.
    p = tmp_path / "x.txt"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    df = load_arcgis_feature_dataframe(p)
    assert df["a"].iloc[0] == 1


# ----------------------------------------------------------------------
# find_latest_output_file
# ----------------------------------------------------------------------


def test_find_latest_missing_dir_returns_none(tmp_path):
    assert find_latest_output_file(tmp_path / "nope") is None


def test_find_latest_empty_dir_returns_none(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert find_latest_output_file(d) is None


def test_find_latest_returns_newest_by_mtime(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    old = d / "old.txt"
    new = d / "new.txt"
    old.write_text("o", encoding="utf-8")
    new.write_text("n", encoding="utf-8")
    # forza mtime distinti
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    latest = find_latest_output_file(d)
    assert latest is not None
    assert latest.name == "new.txt"


def test_find_latest_ignores_subdirectories(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "sub").mkdir()
    f = d / "file.txt"
    f.write_text("x", encoding="utf-8")
    latest = find_latest_output_file(d)
    assert latest.name == "file.txt"


# ----------------------------------------------------------------------
# save_dataframe_to_bytes
# ----------------------------------------------------------------------


def test_save_csv_bytes_roundtrip():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    raw = save_dataframe_to_bytes(df, format="csv")
    assert isinstance(raw, (bytes, bytearray))
    back = _load_dataframe_from_bytes(bytes(raw), format_hint="csv")
    assert back["a"].tolist() == [1, 2]


def test_save_json_bytes_roundtrip():
    df = pd.DataFrame({"a": [1, 2]})
    raw = save_dataframe_to_bytes(df, format="json")
    back = _load_dataframe_from_bytes(bytes(raw), format_hint="json")
    assert back["a"].tolist() == [1, 2]


def test_save_parquet_bytes_roundtrip():
    df = pd.DataFrame({"a": [1, 2]})
    raw = save_dataframe_to_bytes(df, format="parquet")
    back = _load_dataframe_from_bytes(bytes(raw), format_hint="parquet")
    assert back["a"].tolist() == [1, 2]


def test_save_xlsx_bytes_roundtrip():
    df = pd.DataFrame({"a": [1, 2]})
    raw = save_dataframe_to_bytes(df, format="xlsx")
    back = _load_dataframe_from_bytes(bytes(raw), format_hint="xlsx")
    assert back["a"].tolist() == [1, 2]


def test_save_geojson_bytes_dispatches_geospatial():
    gdf = gpd.GeoDataFrame(
        {"n": [1], "geometry": [Point(0, 0)]}, geometry="geometry", crs="EPSG:4326"
    )
    raw = save_dataframe_to_bytes(gdf, format="geojson", geom_column="geometry")
    back = _load_dataframe_from_bytes(bytes(raw), format_hint="geojson")
    assert "geometry" in back.columns


def test_save_format_case_insensitive():
    df = pd.DataFrame({"a": [1]})
    raw = save_dataframe_to_bytes(df, format="CSV")
    assert b"a" in bytes(raw)


@pytest.mark.parametrize("format_hint", ["csv", "xlsx"])
def test_tabular_upload_preserves_identifier_and_na_like_text(format_hint):
    source = pd.DataFrame(
        {
            "code": ["00123"],
            "huge": ["999999999999999999999"],
            "label": ["NA"],
            "nullable": [None],
        }
    )
    if format_hint == "csv":
        raw = source.to_csv(index=False).encode("utf-8")
    else:
        buffer = io.BytesIO()
        source.to_excel(buffer, index=False)
        raw = buffer.getvalue()

    loaded = _load_dataframe_from_bytes(raw, format_hint=format_hint)

    assert loaded.loc[0, "code"] == "00123"
    assert loaded.loc[0, "huge"] == "999999999999999999999"
    assert loaded.loc[0, "label"] == "NA"
    assert pd.isna(loaded.loc[0, "nullable"])


def test_single_record_json_object_loads_as_one_row_without_identifier_loss():
    loaded = _load_json_dataframe_from_text(
        '{"code":"00123","huge":"999999999999999999999","label":"NULL"}'
    )

    assert loaded.to_dict(orient="records") == [
        {
            "code": "00123",
            "huge": "999999999999999999999",
            "label": "NULL",
        }
    ]


def test_json_export_is_strict_for_non_finite_numbers():
    raw = save_dataframe_to_bytes(
        pd.DataFrame({"value": [float("inf"), float("-inf"), float("nan")]}),
        format="json",
    )

    assert json.loads(bytes(raw)) == [
        {"value": "inf"},
        {"value": "-inf"},
        {"value": None},
    ]
    assert b"Infinity" not in bytes(raw)


def test_excel_upload_keeps_real_date_cells_as_dates():
    source = pd.DataFrame({"when": [datetime(2024, 2, 29, 12, 30)]})
    buffer = io.BytesIO()
    source.to_excel(buffer, index=False)

    loaded = _load_dataframe_from_bytes(buffer.getvalue(), format_hint="xlsx")

    assert pd.Timestamp(loaded.loc[0, "when"]) == pd.Timestamp(
        "2024-02-29T12:30:00"
    )
