"""Smoke contract - STRUCT-001 Seam D-B: estrazione load_by_format.

Verifica le invarianti minime del refactor:

  1. Format tabular -> dispatch al loader.load_csv/load_excel/load_json,
     gdf=None.
  2. Format geo-spaziale -> dispatch a loader.load_geospatial + conversione
     della colonna `geometry` in WKT (gdf NON None).
  3. Format geo-spaziale senza colonna `geometry` -> nessuna conversione,
     ma gdf comunque ritornato.
  4. Format unsupported -> ValueError con il nome del format nel messaggio.
  5. Logger.info chiamato con il count di righe e colonne (osservabilita').
  6. Wrapper FileSourceService._load_by_format delega correttamente alla
     funzione comune con il loader e logger del service.

Mock dei loader (DataLoaderProtocol-like) per evitare geopandas/fiona/
parser reali. Nessun touch a load_dataframe/get_preview/get_layers_info.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_logger():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.debug = MagicMock()
    log.error = MagicMock()
    return log


@pytest.fixture
def fake_loader():
    """DataLoaderProtocol-like mock con i 4 metodi chiamati dal dispatch."""
    loader = MagicMock()
    loader.load_csv = MagicMock()
    loader.load_excel = MagicMock()
    loader.load_json = MagicMock()
    loader.load_geospatial = MagicMock()
    return loader


def test_csv_dispatches_to_loader_load_csv(fake_loader, fake_logger):
    from app.services.file_load_dispatch import load_by_format

    expected_df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    fake_loader.load_csv.return_value = expected_df

    df, gdf = load_by_format(
        fake_loader, Path("/tmp/x.csv"), "csv", logger=fake_logger,
    )

    assert df is expected_df
    assert gdf is None
    fake_loader.load_csv.assert_called_once_with(Path("/tmp/x.csv"))
    fake_loader.load_excel.assert_not_called()
    fake_loader.load_json.assert_not_called()
    fake_loader.load_geospatial.assert_not_called()
    # Logger chiamato col count
    fake_logger.info.assert_called_once()
    msg = fake_logger.info.call_args[0][0]
    assert "2 righe" in msg
    assert "2 colonne" in msg


def test_xlsx_dispatches_to_loader_load_excel_with_sheet(fake_loader, fake_logger):
    from app.services.file_load_dispatch import load_by_format

    expected_df = pd.DataFrame({"col": [1]})
    fake_loader.load_excel.return_value = expected_df

    df, gdf = load_by_format(
        fake_loader, Path("/tmp/x.xlsx"), "xlsx",
        sheet="Sheet2", logger=fake_logger,
    )

    assert df is expected_df
    assert gdf is None
    fake_loader.load_excel.assert_called_once_with(
        Path("/tmp/x.xlsx"), "Sheet2",
    )


def test_json_dispatches_to_loader_load_json(fake_loader, fake_logger):
    from app.services.file_load_dispatch import load_by_format

    expected_df = pd.DataFrame({"k": [1, 2, 3]})
    fake_loader.load_json.return_value = expected_df

    df, gdf = load_by_format(
        fake_loader, Path("/tmp/x.json"), "json", logger=fake_logger,
    )

    assert df is expected_df
    assert gdf is None
    fake_loader.load_json.assert_called_once_with(Path("/tmp/x.json"))


def _fake_geometry(wkt_value):
    """Mimica un oggetto shapely.geometry: ha attributo wkt o e' None."""
    g = MagicMock()
    g.wkt = wkt_value
    return g


@pytest.mark.parametrize("fmt", ["geojson", "gpkg", "shp", "gdb"])
def test_geospatial_dispatches_to_load_geospatial_and_converts_geometry_to_wkt(
    fake_loader, fake_logger, fmt,
):
    from app.services.file_load_dispatch import load_by_format

    # GeoDataFrame-like: e' un pd.DataFrame con colonna `geometry` di
    # oggetti geometry-like + attributo `geometry` (Series di geom).
    geom_a = _fake_geometry("POINT(1 2)")
    geom_b = _fake_geometry("POINT(3 4)")
    fake_gdf = pd.DataFrame({
        "id": [10, 20],
        "geometry": [geom_a, geom_b],
    })
    # `gdf.geometry.apply` e' la Series geometry - pandas la espone
    # automaticamente perche' e' una colonna chiamata 'geometry'.
    fake_loader.load_geospatial.return_value = fake_gdf

    df, gdf = load_by_format(
        fake_loader, Path(f"/tmp/x.{fmt}"), fmt,
        layer="layer1", logger=fake_logger,
    )

    fake_loader.load_geospatial.assert_called_once_with(
        Path(f"/tmp/x.{fmt}"), "layer1",
    )
    assert gdf is fake_gdf  # gdf returnato (geometry conversion in-place)
    # geometry colonna ora contiene le stringhe WKT, non gli oggetti.
    assert list(df["geometry"]) == ["POINT(1 2)", "POINT(3 4)"]


def test_geospatial_without_geometry_column_skips_wkt_conversion(
    fake_loader, fake_logger,
):
    from app.services.file_load_dispatch import load_by_format

    # Un layer senza geometria: gdf privo della colonna 'geometry'.
    fake_gdf = pd.DataFrame({"id": [1, 2]})
    fake_loader.load_geospatial.return_value = fake_gdf

    df, gdf = load_by_format(
        fake_loader, Path("/tmp/x.gpkg"), "gpkg",
        layer="non_geom", logger=fake_logger,
    )

    assert gdf is fake_gdf
    assert "geometry" not in df.columns
    assert list(df["id"]) == [1, 2]


def test_unsupported_format_raises_value_error(fake_loader, fake_logger):
    from app.services.file_load_dispatch import load_by_format

    with pytest.raises(ValueError) as exc_info:
        load_by_format(
            fake_loader, Path("/tmp/x.parquet"), "parquet",
            logger=fake_logger,
        )

    assert "parquet" in str(exc_info.value)
    # Nessun loader e' stato invocato sulla via fallita.
    fake_loader.load_csv.assert_not_called()
    fake_loader.load_excel.assert_not_called()
    fake_loader.load_json.assert_not_called()
    fake_loader.load_geospatial.assert_not_called()
    # Niente logger.info perche' raise prima.
    fake_logger.info.assert_not_called()


def test_file_source_service_wrapper_delegates_to_load_by_format():
    """`FileSourceService._load_by_format` deve essere un thin wrapper
    sulla funzione modulare. Spy via patch per evitare di costruire
    l'intero service tree (richiede DB/fs reali in __init__)."""
    from app.services.file_service import FileSourceService

    # Bypass __init__ e inietta i due collaborator usati dal wrapper.
    svc = FileSourceService.__new__(FileSourceService)
    svc._loader = MagicMock()
    svc._logger = MagicMock()

    expected_df = pd.DataFrame({"x": [1]})
    expected_gdf = None

    with patch(
        "app.services.file_load_dispatch.load_by_format",
        return_value=(expected_df, expected_gdf),
    ) as mock_dispatch:
        df, gdf = svc._load_by_format(
            Path("/tmp/file.csv"), "csv",
        )

    assert df is expected_df
    assert gdf is expected_gdf
    mock_dispatch.assert_called_once_with(
        svc._loader,
        Path("/tmp/file.csv"),
        "csv",
        None,
        None,
        logger=svc._logger,
    )
