"""Tombstone: gpkg/shp cleanup hardening (Post-Phase5).

I test originali verificavano:
  - DATA_DIR/temp resta vuota dopo il salvataggio pipeline gpkg
  - il path passato a save_dataframe e' dentro TemporaryDirectory
    con prefix urby_gpkg_shp_
  - tmp dir pulita anche su upload failure

Post-Phase5 (vedi commit `pipeline_result -> bytes-only + SHP-zip-bytes`):
`save_pipeline_result_outcome` ora usa esclusivamente
`save_dataframe_to_bytes`, che dispatcha gpkg via pyogrio bytes e shp via
`app.core.io.geospatial.write_shapefile_zip_bytes` (TemporaryDirectory
incapsulata nel boundary). Nessun ramo path-based in pipeline_result.py
da hardenare — `save_dataframe`/`save_tabular_dataframe`/
`save_geospatial_dataframe` sono stati rimossi (dead code).

Anti-regression preservato tramite:
  - `test_in_memory_result_contract.test_save_dataframe_to_bytes_gpkg_and_shp_now_supported`
  - tombstone qui sotto, locks the removal.
"""

from __future__ import annotations

import pytest
from tests2.shared._ast_identifiers import all_identifiers


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_path_based_save_dataframe_removed_post_phase5():
    """`save_dataframe` (path-based) e funzioni cascade rimosse: i test
    di hardening sulla tmp dir gpkg/shp non hanno piu' un flow path-based
    da testare."""
    from app.core.dataframes import frame_io, geospatial_io

    for sym in (
        "save_dataframe",
        "save_tabular_dataframe",
    ):
        assert not hasattr(frame_io, sym), (
            f"frame_io.{sym} resta presente: refactor RAM-only incompleto."
        )
    for sym in (
        "save_geospatial_dataframe",
        "_salva_formato_geo",
        "_salva_shapefile_zippato",
        "_salva_geo_vuoto",
    ):
        assert not hasattr(geospatial_io, sym), (
            f"geospatial_io.{sym} resta presente: refactor RAM-only incompleto."
        )


def test_pipeline_result_dispatches_bytes_first_only():
    """`save_pipeline_result_outcome` deve invocare `save_dataframe_to_bytes`
    (lazy import dentro la funzione) — nessun import path-based residuo."""
    import inspect
    from app.core.results import pipeline_result

    src = inspect.getsource(pipeline_result.save_pipeline_result_outcome)
    # RAW: "save_dataframe_to_bytes" appare solo nella docstring della funzione
    # (non come identificatore codice) — all_identifiers() non lo vedrebbe.
    assert "save_dataframe_to_bytes" in src
    assert "save_dataframe" not in all_identifiers(src), (
        "save_pipeline_result_outcome usa ancora save_dataframe path-based"
    )
