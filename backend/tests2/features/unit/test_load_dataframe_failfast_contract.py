"""S3 (inventory 2.9): _load_dataframe non deve ripiegare silenziosamente su CSV
quando il file non e' ne' un formato geo riconoscibile ne' un CSV plausibile.

Prima del fix: un file con estensione non-CSV che geopandas non riesce a leggere
viene passato a pandas.read_csv, che spesso restituisce un DataFrame di spazzatura
senza alcun segnale di errore (tipo-mismatch mascherato).

Dopo il fix:
- estensione plausibile CSV-like (.tsv, .txt) → fallback CSV esplicito con log
- altrimenti → ValueError con messaggio chiaro che include l'errore geopandas
"""
from pathlib import Path

import pytest

from app.core.features.features_service import FeaturesService


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract, pytest.mark.regression]


def test_load_dataframe_raises_on_unknown_extension_with_non_geo_content(tmp_path: Path):
    bogus = tmp_path / "corrupted.gpkg_backup"
    bogus.write_bytes(b"\x00\x01\x02\x03 not a geojson, not a csv")

    with pytest.raises(ValueError) as excinfo:
        FeaturesService._load_dataframe(str(bogus))

    msg = str(excinfo.value)
    assert "corrupted.gpkg_backup" in msg
    assert ".gpkg_backup" in msg


def test_load_dataframe_raises_on_binary_payload_mislabeled(tmp_path: Path):
    binary_file = tmp_path / "payload.bin"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    with pytest.raises(ValueError):
        FeaturesService._load_dataframe(str(binary_file))


def test_load_dataframe_accepts_tsv_fallback_to_csv(tmp_path: Path):
    tsv_file = tmp_path / "data.tsv"
    tsv_file.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

    df = FeaturesService._load_dataframe(str(tsv_file))

    assert len(df) == 2
    assert list(df.columns) == ["a", "b", "c"]


def test_load_dataframe_accepts_txt_fallback_to_csv(tmp_path: Path):
    txt_file = tmp_path / "data.txt"
    txt_file.write_text("x,y\n10,20\n", encoding="utf-8")

    df = FeaturesService._load_dataframe(str(txt_file))

    assert len(df) == 1
    assert list(df.columns) == ["x", "y"]


def test_load_dataframe_still_reads_valid_geojson(tmp_path: Path):
    geojson_file = tmp_path / "points.geojson"
    geojson_file.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"id":1},'
        '"geometry":{"type":"Point","coordinates":[10.0,20.0]}}]}',
        encoding="utf-8",
    )

    df = FeaturesService._load_dataframe(str(geojson_file))

    assert len(df) == 1
