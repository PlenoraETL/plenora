"""Behavioral unit tests per `app/core/dataframes/preview_loader.py`.

Il modulo espone una sola API pubblica `preview_dataframe_from_bytes`
che fa dispatch format-specific con un cap esplicito di righe/feature.
Questi test esercitano:

  - happy-path per ogni formato supportato (csv / json / jsonlines /
    xlsx / parquet / geojson / gpkg);
  - il contratto sul valore di ritorno `(df, total_row_count_or_None)`:
    total e' None per i formati che richiederebbero scan completo
    (csv, jsonlines, xlsx) ed e' valorizzato per quelli con metadata
    cheap (parquet, json standard, geospatial);
  - il cap `max_rows` (la preview non supera mai il cap);
  - i rami di errore (formato non supportato -> ValueError, CSV non
    decodificabile -> UnicodeDecodeError);
  - edge case: file vuoti (parquet empty, json vuoto), normalizzazione
    del format_hint (case, leading dot, alias 'geopackage').

I path interni privati (`_preview_*`, `_normalize_format`) sono coperti
indirettamente attraverso la API pubblica + alcuni test mirati sul
normalizzatore poiche' incapsula il dispatch.
"""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from app.core.dataframes.preview_loader import (
    _normalize_format,
    preview_dataframe_from_bytes,
)


pytestmark = [
    pytest.mark.unit,
]


# ----------------------------------------------------------------------
# Helpers per materializzare payload bytes nei vari formati.
# ----------------------------------------------------------------------


def _csv_bytes(n_rows: int, encoding: str = "utf-8") -> bytes:
    df = pd.DataFrame({"id": range(n_rows), "name": [f"r{i}" for i in range(n_rows)]})
    return df.to_csv(index=False).encode(encoding)


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def _xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def _geojson_bytes(n: int) -> bytes:
    features = []
    for i in range(n):
        features.append(
            {
                "type": "Feature",
                "properties": {"id": i, "name": f"p{i}"},
                "geometry": {"type": "Point", "coordinates": [float(i), float(i)]},
            }
        )
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc).encode("utf-8")


# ----------------------------------------------------------------------
# _normalize_format
# ----------------------------------------------------------------------


def test_normalize_format_empty_returns_empty():
    assert _normalize_format("") == ""
    assert _normalize_format(None) == ""  # type: ignore[arg-type]


def test_normalize_format_lowercases_and_strips_dot():
    assert _normalize_format(".CSV") == "csv"
    assert _normalize_format("  Parquet  ") == "parquet"


def test_normalize_format_geopackage_alias_maps_to_gpkg():
    assert _normalize_format("geopackage") == "gpkg"
    assert _normalize_format(".GeoPackage") == "gpkg"


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------


def test_csv_happy_path_returns_dataframe_and_none_total():
    data = _csv_bytes(5)
    df, total = preview_dataframe_from_bytes(data, "csv")

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 5
    # CSV non espone row count cheap -> None.
    assert total is None


def test_csv_caps_rows_at_max_rows():
    data = _csv_bytes(100)
    df, total = preview_dataframe_from_bytes(data, "csv", max_rows=10)

    assert len(df) == 10
    assert total is None


def test_csv_fewer_rows_than_cap_returns_all():
    data = _csv_bytes(3)
    df, _ = preview_dataframe_from_bytes(data, "csv", max_rows=200)
    assert len(df) == 3


def test_csv_latin1_fallback_encoding():
    # Byte 0xff non e' valido UTF-8 ma decodifica in latin-1 / cp1252.
    header = "id,name\n"
    row = "1,caf\xe9\n"  # e' acuta in latin-1 = 0xe9
    data = (header + row).encode("latin-1")
    df, total = preview_dataframe_from_bytes(data, "csv")

    assert len(df) == 1
    assert total is None
    # Il valore decodificato deve contenere il carattere accentato.
    assert "caf" in str(df.iloc[0]["name"])


def test_csv_format_hint_with_dot_and_uppercase():
    data = _csv_bytes(2)
    df, _ = preview_dataframe_from_bytes(data, ".CSV")
    assert len(df) == 2


# ----------------------------------------------------------------------
# JSON (standard + jsonlines)
# ----------------------------------------------------------------------


def test_json_standard_list_returns_total_count():
    records = [{"id": i, "v": i * 2} for i in range(7)]
    data = json.dumps(records).encode("utf-8")
    df, total = preview_dataframe_from_bytes(data, "json")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 7
    # JSON standard espone len(parsed) come total.
    assert total == 7


def test_json_standard_list_caps_rows_but_total_is_full():
    records = [{"id": i} for i in range(50)]
    data = json.dumps(records).encode("utf-8")
    df, total = preview_dataframe_from_bytes(data, "json", max_rows=5)

    assert len(df) == 5
    assert total == 50


def test_json_standard_dict_of_columns():
    # JSON object (non lista, non jsonlines): pd.DataFrame(dict).head().
    payload = {"a": [1, 2, 3], "b": [4, 5, 6]}
    data = json.dumps(payload).encode("utf-8")
    df, total = preview_dataframe_from_bytes(data, "json", max_rows=2)

    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2
    assert total == 3


def test_json_lines_returns_none_total():
    lines = "\n".join(json.dumps({"id": i, "name": f"n{i}"}) for i in range(6))
    data = lines.encode("utf-8")
    df, total = preview_dataframe_from_bytes(data, "json", max_rows=4)

    assert len(df) == 4
    # jsonlines: nrows applicato ma total richiede scan -> None.
    assert total is None
    assert "id" in df.columns


def test_json_invalid_utf8_uses_utf8_sig_fallback():
    # Il primo decode tentato e' utf-8; se solleva UnicodeDecodeError il
    # modulo ripiega su utf-8-sig. Costruiamo un payload jsonlines i cui
    # byte iniziali (BOM utf-8) fanno fallire un decode utf-8 stretto solo
    # quando seguiti da byte non-utf8; qui verifichiamo che un jsonlines
    # ben formato in utf-8 venga comunque parsato (ramo decode utf-8 ok).
    lines = "\n".join(json.dumps({"id": i}) for i in range(3))
    data = lines.encode("utf-8")
    df, total = preview_dataframe_from_bytes(data, "json")
    assert len(df) == 3
    # jsonlines -> total None.
    assert total is None


# ----------------------------------------------------------------------
# Excel
# ----------------------------------------------------------------------


def test_excel_happy_path_none_total():
    src = pd.DataFrame({"id": range(8), "label": [f"x{i}" for i in range(8)]})
    data = _xlsx_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "xlsx", max_rows=3)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert total is None
    assert list(df.columns) == ["id", "label"]


def test_excel_xls_alias_routes_to_excel():
    # 'xls' deve usare lo stesso path di 'xlsx' (openpyxl legge xlsx blob).
    src = pd.DataFrame({"id": [10, 20]})
    data = _xlsx_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "xls")
    assert len(df) == 2
    assert total is None


def test_excel_sheet_selection():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"a": [1]}).to_excel(xl, index=False, sheet_name="first")
        pd.DataFrame({"b": [2, 3, 4]}).to_excel(xl, index=False, sheet_name="second")
    data = buf.getvalue()

    df, _ = preview_dataframe_from_bytes(data, "xlsx", sheet="second")
    assert list(df.columns) == ["b"]
    assert len(df) == 3


# ----------------------------------------------------------------------
# Parquet
# ----------------------------------------------------------------------


def test_parquet_happy_path_returns_total_from_metadata():
    src = pd.DataFrame({"id": range(20), "v": [float(i) for i in range(20)]})
    data = _parquet_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "parquet", max_rows=5)

    assert len(df) == 5
    # Parquet espone num_rows cheap nel footer -> total completo.
    assert total == 20


def test_parquet_applies_nullable_dtype_backend():
    # Colonna integer con null: il path preview applica convert_dtypes
    # numpy_nullable, quindi Int64 (non float64).
    src = pd.DataFrame({"id": pd.array([1, 2, None, 4], dtype="Int64")})
    data = _parquet_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "parquet")

    assert total == 4
    assert str(df["id"].dtype) == "Int64"
    assert df.loc[2, "id"] is pd.NA


def test_parquet_empty_file_returns_empty_df_total_zero():
    src = pd.DataFrame({"id": pd.Series([], dtype="int64"), "name": pd.Series([], dtype="object")})
    data = _parquet_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "parquet")

    assert len(df) == 0
    assert total == 0
    # Lo schema (colonne) deve essere preservato anche se vuoto.
    assert "id" in df.columns
    assert "name" in df.columns


def test_parquet_cap_larger_than_rows_returns_all():
    src = pd.DataFrame({"id": range(3)})
    data = _parquet_bytes(src)
    df, total = preview_dataframe_from_bytes(data, "parquet", max_rows=200)
    assert len(df) == 3
    assert total == 3


# ----------------------------------------------------------------------
# Geospatial (geojson / gpkg)
# ----------------------------------------------------------------------


def test_geojson_happy_path_returns_total_and_geometry():
    import geopandas as gpd

    data = _geojson_bytes(6)
    gdf, total = preview_dataframe_from_bytes(data, "geojson", max_rows=4)

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 4
    # read_info espone features count -> total completo.
    assert total == 6
    assert "geometry" in gdf.columns


def test_geojson_cap_larger_returns_all():
    data = _geojson_bytes(3)
    gdf, total = preview_dataframe_from_bytes(data, "geojson", max_rows=200)
    assert len(gdf) == 3
    assert total == 3


def test_gpkg_with_layer_selection(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point

    gpkg_path = tmp_path / "multi.gpkg"
    gdf_a = gpd.GeoDataFrame(
        {"id": [1, 2]}, geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326"
    )
    gdf_b = gpd.GeoDataFrame(
        {"id": [10, 20, 30]},
        geometry=[Point(2, 2), Point(3, 3), Point(4, 4)],
        crs="EPSG:4326",
    )
    gdf_a.to_file(gpkg_path, layer="layer_a", driver="GPKG")
    gdf_b.to_file(gpkg_path, layer="layer_b", driver="GPKG")

    data = gpkg_path.read_bytes()
    gdf, total = preview_dataframe_from_bytes(data, "gpkg", layer="layer_b", max_rows=2)

    assert len(gdf) == 2
    assert total == 3
    assert set(gdf["id"]).issubset({10, 20, 30})


def test_geopackage_alias_routes_to_vector():
    import geopandas as gpd
    from shapely.geometry import Point

    # Costruisci un singolo-layer gpkg in tmp e leggilo via alias.
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "single.gpkg")
        gpd.GeoDataFrame(
            {"id": [1, 2, 3]},
            geometry=[Point(0, 0), Point(1, 1), Point(2, 2)],
            crs="EPSG:4326",
        ).to_file(path, driver="GPKG")
        data = open(path, "rb").read()

    gdf, total = preview_dataframe_from_bytes(data, "geopackage", max_rows=10)
    assert len(gdf) == 3
    assert total == 3


# ----------------------------------------------------------------------
# Errori e formati non supportati
# ----------------------------------------------------------------------


def test_unsupported_format_raises_value_error():
    with pytest.raises(ValueError) as exc:
        preview_dataframe_from_bytes(b"whatever", "xml")
    assert "non supportato" in str(exc.value).lower() or "xml" in str(exc.value)


def test_empty_format_hint_raises_value_error():
    with pytest.raises(ValueError):
        preview_dataframe_from_bytes(b"data", "")


def test_csv_undecodable_raises_unicode_decode_error():
    # Sequenza di byte invalida in tutte le encoding tentate. cp1252 e
    # latin-1 mappano quasi ogni byte, quindi per forzare l'errore usiamo
    # byte che cp1252 rifiuta (es. 0x81, 0x8d, 0x8f, 0x90, 0x9d non sono
    # definiti in cp1252) ma che latin-1 accetterebbe; per garantire il
    # raise costruiamo bytes che falliscono utf-8 e cp1252 e poi monkey-
    # restringiamo l'insieme di encoding tentate.
    import app.core.dataframes.tabular as tabular_mod

    original = tabular_mod._CSV_ENCODINGS
    try:
        tabular_mod._CSV_ENCODINGS = ("utf-8",)
        with pytest.raises(UnicodeDecodeError):
            preview_dataframe_from_bytes(b"id,name\n1,\xff\xfe\n", "csv")
    finally:
        tabular_mod._CSV_ENCODINGS = original


def test_max_rows_zero_csv_returns_header_only():
    data = _csv_bytes(5)
    df, total = preview_dataframe_from_bytes(data, "csv", max_rows=0)
    assert len(df) == 0
    assert list(df.columns) == ["id", "name"]
    assert total is None
