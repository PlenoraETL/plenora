"""Smoke contract - STRUCT-001 Seam D-F: estrazione create_from_upload.

Verifica le invarianti minime del refactor:

  1. Happy path tabular (CSV): parse + columns + dispatch + db_save +
     cleanup temp.
  2. Happy path geospatial (GPKG): list_layers per single-layer auto-pick,
     load_by_format con layer, CRS detection da gdf.crs.
  3. MIME block (se mime_validate passato e fallisce): ValueError +
     logger.warning + parse/dispatch/db_save NON chiamati + cleanup
     temp comunque eseguito.
  4. Parse failure: load_by_format solleva -> logger.warning, columns=[],
     row_count=0, MA dispatch e db_save SONO comunque chiamati
     (best-effort: il file viene salvato anche senza columns).
  5. Dispatch failure (orphan guard): dispatch_blob_storage solleva ->
     propaga, db_save NON chiamato, cleanup temp eseguito.
  6. DB save failure: propaga al caller, cleanup temp eseguito.
  7. group_id None -> default_group propagato a DTO + DB save.
  8. Wrapper FileSourceService.create_from_upload delega coi 17
     collaborator del service.

Mock dei collaborator: nessun fiona/geopandas/DB reale. tempfile reale
su pytest tmp_path (verifica cleanup deterministica).
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixtures


@pytest.fixture
def fake_logger():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.debug = MagicMock()
    log.error = MagicMock()
    return log


@pytest.fixture
def collaborators(fake_logger, tmp_path):
    """Set base di collaborator. I test sostituiscono i singoli mock
    necessari per esercitare branch specifici."""
    fake_dto = MagicMock()
    fake_dto.model_dump = MagicMock(return_value={"k": "v"})
    return {
        "generate_file_id": MagicMock(return_value="file_abc"),
        "time_now": MagicMock(return_value=datetime(2026, 5, 2, tzinfo=timezone.utc)),
        "detect_format": MagicMock(return_value="csv"),
        "list_layers": MagicMock(return_value=[]),
        "list_sheets": MagicMock(return_value=[]),
        "load_by_format": MagicMock(
            return_value=(pd.DataFrame({"id": [1, 2, 3]}), None)
        ),
        "column_builder_from_df": MagicMock(
            return_value=[type("C", (), {"name": "id", "type": "int"})()]
        ),
        "dispatch_blob_storage": MagicMock(
            return_value={"data": b"raw", "storage_key": None, "size_bytes": 3}
        ),
        "db_save_file_source": MagicMock(),
        "build_file_source": MagicMock(return_value=fake_dto),
        "cleanup": MagicMock(),
        "safe_extractall": MagicMock(),
        "zip_slip_error": RuntimeError,  # placeholder type per zip slip
        "temp_dir": str(tmp_path),
        "default_group": "default_grp",
        "logger": fake_logger,
    }


# 1. Happy path tabular CSV


def test_happy_path_csv_calls_parse_dispatch_db_save_and_cleanup(collaborators):
    from app.services.file_upload import create_from_upload

    out = create_from_upload(
        io.BytesIO(b"id\n1\n2\n3\n"), "data.csv", "MyData",
        group_id="grp1",
        **collaborators,
    )

    # Pipeline chiamata
    collaborators["detect_format"].assert_called_once_with("data.csv")
    collaborators["load_by_format"].assert_called_once()
    collaborators["column_builder_from_df"].assert_called_once()
    collaborators["dispatch_blob_storage"].assert_called_once()
    collaborators["db_save_file_source"].assert_called_once()
    # DB kwargs corretti (blob_kwargs propagato 1:1)
    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["file_id"] == "file_abc"
    assert db_kwargs["group_id"] == "grp1"
    assert db_kwargs["fmt"] == "csv"
    assert db_kwargs["data"] == b"raw"
    assert db_kwargs["storage_key"] is None
    assert db_kwargs["size_bytes"] == 3
    # Cleanup tmp file (non zip_tmp_dir su csv)
    assert collaborators["cleanup"].call_count >= 1
    # DTO ritornato
    assert out is collaborators["build_file_source"].return_value
    collaborators["logger"].info.assert_called_once()


# 2. Happy path geospatial: GPKG single-layer auto-pick + CRS detection


def test_happy_path_gpkg_single_layer_autopicks_and_detects_crs(collaborators):
    from app.services.file_upload import create_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    collaborators["list_layers"].return_value = ["only_layer"]

    # Costruisci un GeoDataFrame-like con .crs.to_epsg() = 4326
    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=4326)
    fake_df = pd.DataFrame({"id": [1]})
    collaborators["load_by_format"].return_value = (fake_df, fake_gdf)

    create_from_upload(
        io.BytesIO(b"fake gpkg"), "data.gpkg", "Geo",
        group_id="g1",
        **collaborators,
    )

    # list_layers chiamato + auto-pick del singolo layer
    collaborators["list_layers"].assert_called_once()
    load_args = collaborators["load_by_format"].call_args.args
    # signature: (path, format, layer, sheet) -> layer == "only_layer"
    assert load_args[2] == "only_layer"
    # CRS = "EPSG:4326" propagato al DTO via build_file_source kwargs
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["crs"] == "EPSG:4326"


def test_geo_crs_fallback_to_authority_when_no_epsg(collaborators):
    """Se gdf.crs.to_epsg() ritorna None, fallback a to_authority()."""
    from app.services.file_upload import create_from_upload

    collaborators["detect_format"].return_value = "geojson"
    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=None)
    fake_gdf.crs.to_authority = MagicMock(return_value=("ESRI", "102100"))
    collaborators["load_by_format"].return_value = (pd.DataFrame({"a": [1]}), fake_gdf)

    create_from_upload(
        io.BytesIO(b"x"), "data.geojson", "Geo",
        group_id="g1",
        **collaborators,
    )

    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["crs"] == "ESRI:102100"


# 3. MIME block prima di parse/dispatch/DB


def test_mime_block_short_circuits_before_parse_dispatch_db(collaborators):
    """mime_validate solleva MimeMismatchError -> ValueError, no side effect
    persistente (parse/dispatch/db_save NON chiamati). Cleanup temp ESEGUITO."""
    from app.services.file_upload import create_from_upload
    from app.utils.mime_validator import MimeMismatchError

    mime = MagicMock(side_effect=MimeMismatchError("executable detected"))

    with pytest.raises(ValueError, match="executable detected"):
        create_from_upload(
            io.BytesIO(b"MZ\x90\x00 fake"),
            "trojan.csv", "Trojan",
            group_id="g1",
            mime_validate=mime,
            **collaborators,
        )

    # PRE-side-effect: parse / dispatch / db_save NON chiamati
    collaborators["load_by_format"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    # Logger warning con contesto
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_from_upload bloccato (MIME)" in msg
    # Cleanup tmp temp comunque chiamato (best-effort)
    collaborators["cleanup"].assert_called()


# 4. Parse failure: best-effort, NON aborta il flusso DB save


def test_parse_failure_continues_with_empty_columns_and_saves_to_db(collaborators):
    """load_by_format solleva -> logger.warning, columns=[], row_count=0,
    MA dispatch e db_save SONO comunque chiamati (preservato byte-per-byte).
    Cleanup temp esegue."""
    from app.services.file_upload import create_from_upload

    collaborators["load_by_format"].side_effect = RuntimeError("parser exploded")

    out = create_from_upload(
        io.BytesIO(b"data"), "data.csv", "BadFile",
        group_id="g1",
        **collaborators,
    )

    # Logger warning per il parse failure
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "Errore inferenza colonne" in msg

    # DB save CHIAMATO comunque (best-effort save anche senza columns)
    collaborators["db_save_file_source"].assert_called_once()
    # column_builder_from_df NON chiamato (load fallisce prima)
    collaborators["column_builder_from_df"].assert_not_called()
    # DTO costruito con columns=[] e row_count=0
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["columns"] == []
    assert build_kwargs["row_count"] == 0
    assert out is collaborators["build_file_source"].return_value
    # Cleanup esegue
    collaborators["cleanup"].assert_called()


# 5. Dispatch failure (orphan guard)


def test_dispatch_failure_propagates_no_db_save(collaborators):
    """Orphan guard: dispatch_blob_storage solleva -> exception propaga,
    db_save_file_source NON chiamato, cleanup temp eseguito."""
    from app.services.file_upload import create_from_upload

    collaborators["dispatch_blob_storage"].side_effect = RuntimeError("S3 down")

    with pytest.raises(RuntimeError, match="S3 down"):
        create_from_upload(
            io.BytesIO(b"data"), "data.csv", "X",
            group_id="g1",
            **collaborators,
        )

    # Build/parse OK PRIMA del dispatch
    collaborators["dispatch_blob_storage"].assert_called_once()
    # DB save NON chiamato (orphan guard)
    collaborators["db_save_file_source"].assert_not_called()
    # Cleanup esegue (finally)
    collaborators["cleanup"].assert_called()


# 6. DB save failure propaga


def test_db_save_failure_propagates_with_cleanup(collaborators):
    from app.services.file_upload import create_from_upload

    collaborators["db_save_file_source"].side_effect = RuntimeError("DB write fail")

    with pytest.raises(RuntimeError, match="DB write fail"):
        create_from_upload(
            io.BytesIO(b"data"), "data.csv", "X",
            group_id="g1",
            **collaborators,
        )

    # Dispatch chiamato PRIMA del fail
    collaborators["dispatch_blob_storage"].assert_called_once()
    # Cleanup esegue (finally)
    collaborators["cleanup"].assert_called()


# 7. group_id None -> default_group propagato


def test_default_group_used_when_group_id_is_none(collaborators):
    from app.services.file_upload import create_from_upload

    create_from_upload(
        io.BytesIO(b"data"), "data.csv", "X",
        group_id=None,
        **collaborators,
    )

    # DTO + DB save: stesso group_id (default_grp)
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["group_id"] == "default_grp"
    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["group_id"] == "default_grp"


# 8. Wrapper FileSourceService.create_from_upload delega


def test_file_service_wrapper_delegates_to_module():
    """`FileSourceService.create_from_upload` thin wrapper: invoca
    file_upload.create_from_upload con i 17 collaborator del service."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._detect_format = MagicMock(return_value="csv")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc.list_layers = MagicMock(return_value=[])
    svc.list_sheets = MagicMock(return_value=[])
    svc._load_by_format = MagicMock(return_value=(pd.DataFrame({"a": [1]}), None))
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock(return_value=[])
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()

    fake_dto = MagicMock()
    with patch(
        "app.services.file_upload.create_from_upload",
        return_value=fake_dto,
    ) as mock_impl:
        out = svc.create_from_upload(
            io.BytesIO(b"x"), "doc.csv", "Doc",
            description="d", layer=None, sheet=None,
            selected_columns=None, srid=None, group_id="g1",
        )

    assert out is fake_dto
    mock_impl.assert_called_once()
    kwargs = mock_impl.call_args.kwargs
    # Verifica che i 17 collaborator siano passati col binding corretto
    assert kwargs["generate_file_id"] is svc._generate_file_id
    assert kwargs["time_now"] is svc._time.now
    assert kwargs["detect_format"] is svc._detect_format
    assert kwargs["list_layers"] is svc.list_layers
    assert kwargs["list_sheets"] is svc.list_sheets
    assert kwargs["load_by_format"] is svc._load_by_format
    assert kwargs["column_builder_from_df"] is svc._column_builder.from_dataframe
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    assert callable(kwargs["db_save_file_source"])
    assert callable(kwargs["build_file_source"])
    assert callable(kwargs["cleanup"])
    assert callable(kwargs["safe_extractall"])
    assert isinstance(kwargs["zip_slip_error"], type)
    # temp_dir: il service passa settings.TEMP_DIR che puo' essere
    # str o Path (entrambi accettati da tempfile.NamedTemporaryFile).
    assert isinstance(kwargs["temp_dir"], (str, Path))
    assert isinstance(kwargs["default_group"], str)
    assert kwargs["logger"] is svc._logger
