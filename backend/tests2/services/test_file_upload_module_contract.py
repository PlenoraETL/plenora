"""Smoke contract: estrazione create_from_upload (RAM-only, Phase4).

Verifica le invarianti del refactor RAM-only:
  - Nessun temp file su disco (no cleanup, no temp_dir, no safe_extractall).
  - Schema inference via `load_by_format_bytes` (bytes -> df/gdf).
  - Layer/sheet listing via `list_vector_layers` / `list_excel_sheets_from_bytes`.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
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
def collaborators(fake_logger):
    """Set base di collaborator RAM-only. I test sostituiscono i singoli
    mock necessari per esercitare branch specifici."""
    fake_dto = MagicMock()
    fake_dto.model_dump = MagicMock(return_value={"k": "v"})
    return {
        "generate_file_id": MagicMock(return_value="file_abc"),
        "time_now": MagicMock(return_value=datetime(2026, 5, 2, tzinfo=timezone.utc)),
        "detect_format": MagicMock(return_value="csv"),
        "column_builder_from_df": MagicMock(
            return_value=[type("C", (), {"name": "id", "type": "int"})()]
        ),
        "dispatch_blob_storage": MagicMock(
            return_value={"data": b"raw", "storage_key": None, "size_bytes": 3}
        ),
        "db_save_file_source": MagicMock(),
        "build_file_source": MagicMock(return_value=fake_dto),
        "default_group": "default_grp",
        "logger": fake_logger,
    }


# 1. Happy path tabular CSV — RAM-only, niente filesystem


def test_happy_path_csv_calls_parse_dispatch_db_save(collaborators):
    from app.core.files import upload as upload_mod

    with patch.object(
        upload_mod,
        "load_by_format_bytes",
        create=True,
    ):
        # Patch su load_dispatch (modulo origine) per intercettare la chiamata
        with patch(
            "app.core.dataframes.load_dispatch.load_by_format_bytes",
            return_value=(pd.DataFrame({"id": [1, 2, 3]}), None),
        ) as mock_lbf:
            out = upload_mod.create_from_upload(
                io.BytesIO(b"id\n1\n2\n3\n"), "data.csv", "MyData",
                group_id="grp1",
                **collaborators,
            )

    # Pipeline chiamata
    collaborators["detect_format"].assert_called_once_with("data.csv")
    mock_lbf.assert_called_once()
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
    # DTO ritornato
    assert out is collaborators["build_file_source"].return_value
    collaborators["logger"].info.assert_called_once()


# 2. Happy path geospatial: GPKG single-layer auto-pick + CRS detection


def test_happy_path_gpkg_single_layer_autopicks_and_detects_crs(collaborators):
    from app.core.files import upload as upload_mod

    collaborators["detect_format"].return_value = "gpkg"

    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=4326)
    fake_df = pd.DataFrame({"id": [1]})

    with patch(
        "app.core.io.geospatial.list_vector_layers",
        return_value=["only_layer"],
    ) as mock_list, patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(fake_df, fake_gdf),
    ) as mock_lbf:
        upload_mod.create_from_upload(
            io.BytesIO(b"fake gpkg"), "data.gpkg", "Geo",
            group_id="g1",
            **collaborators,
        )

    # list_vector_layers chiamato + auto-pick del singolo layer
    mock_list.assert_called_once()
    # load_by_format_bytes(layer="only_layer", ...)
    assert mock_lbf.call_args.kwargs["layer"] == "only_layer"
    # CRS = "EPSG:4326" propagato al DTO via build_file_source kwargs
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["crs"] == "EPSG:4326"


def test_geo_crs_fallback_to_authority_when_no_epsg(collaborators):
    """Se gdf.crs.to_epsg() ritorna None, fallback a to_authority()."""
    from app.core.files import upload as upload_mod

    collaborators["detect_format"].return_value = "geojson"
    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=None)
    fake_gdf.crs.to_authority = MagicMock(return_value=("ESRI", "102100"))

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"a": [1]}), fake_gdf),
    ):
        upload_mod.create_from_upload(
            io.BytesIO(b"x"), "data.geojson", "Geo",
            group_id="g1",
            **collaborators,
        )

    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["crs"] == "ESRI:102100"


# 3. MIME block prima di parse/dispatch/DB


def test_mime_block_short_circuits_before_parse_dispatch_db(collaborators):
    """mime_validate solleva MimeMismatchError -> ValueError, no side effect
    persistente (parse/dispatch/db_save NON chiamati)."""
    from app.core.files import upload as upload_mod
    from app.utils.mime_validator import MimeMismatchError

    mime = MagicMock(side_effect=MimeMismatchError("executable detected"))

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
    ) as mock_lbf:
        with pytest.raises(ValueError, match="executable detected"):
            upload_mod.create_from_upload(
                io.BytesIO(b"MZ\x90\x00 fake"),
                "trojan.csv", "Trojan",
                group_id="g1",
                mime_validate=mime,
                **collaborators,
            )

    # PRE-side-effect: parse / dispatch / db_save NON chiamati
    mock_lbf.assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    # Logger warning con contesto
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_from_upload bloccato (MIME)" in msg


# 4. Parse failure: best-effort, NON aborta il flusso DB save


def test_parse_failure_continues_with_empty_columns_and_saves_to_db(collaborators):
    """load_by_format_bytes solleva -> logger.warning, columns=[],
    row_count=0, MA dispatch e db_save SONO comunque chiamati."""
    from app.core.files import upload as upload_mod

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        side_effect=RuntimeError("parser exploded"),
    ):
        out = upload_mod.create_from_upload(
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


# 5. Dispatch failure (orphan guard)


def test_dispatch_failure_propagates_no_db_save(collaborators):
    """Orphan guard: dispatch_blob_storage solleva -> exception propaga,
    db_save_file_source NON chiamato."""
    from app.core.files import upload as upload_mod

    collaborators["dispatch_blob_storage"].side_effect = RuntimeError("S3 down")

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"id": [1]}), None),
    ):
        with pytest.raises(RuntimeError, match="S3 down"):
            upload_mod.create_from_upload(
                io.BytesIO(b"data"), "data.csv", "X",
                group_id="g1",
                **collaborators,
            )

    collaborators["dispatch_blob_storage"].assert_called_once()
    # DB save NON chiamato (orphan guard)
    collaborators["db_save_file_source"].assert_not_called()


# 6. DB save failure propaga


def test_db_save_failure_propagates(collaborators):
    from app.core.files import upload as upload_mod

    collaborators["db_save_file_source"].side_effect = RuntimeError("DB write fail")

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"id": [1]}), None),
    ):
        with pytest.raises(RuntimeError, match="DB write fail"):
            upload_mod.create_from_upload(
                io.BytesIO(b"data"), "data.csv", "X",
                group_id="g1",
                **collaborators,
            )

    # Dispatch chiamato PRIMA del fail
    collaborators["dispatch_blob_storage"].assert_called_once()


# 7. group_id None -> default_group propagato


def test_default_group_used_when_group_id_is_none(collaborators):
    from app.core.files import upload as upload_mod

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"id": [1]}), None),
    ):
        upload_mod.create_from_upload(
            io.BytesIO(b"data"), "data.csv", "X",
            group_id=None,
            **collaborators,
        )

    # DTO + DB save: stesso group_id (default_grp)
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["group_id"] == "default_grp"
    db_kwargs = collaborators["db_save_file_source"].call_args.kwargs
    assert db_kwargs["group_id"] == "default_grp"


# 8. FileSourceService.create_from_upload resta un facade core sottile


def test_file_source_service_create_from_upload_delegates_to_core_module():
    """`FileSourceService.create_from_upload` invoca
    app.core.files.upload.create_from_upload con i collaborator del service."""
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._detect_format = MagicMock(return_value="csv")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock(return_value=[])
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()

    fake_dto = MagicMock()
    with patch(
        "app.core.files.upload.create_from_upload",
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
    # Verifica che i collaborator RAM-only siano passati col binding corretto
    assert kwargs["generate_file_id"] is svc._generate_file_id
    assert kwargs["time_now"] is svc._time.now
    assert kwargs["detect_format"] is svc._detect_format
    assert kwargs["column_builder_from_df"] is svc._column_builder.from_dataframe
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    assert callable(kwargs["db_save_file_source"])
    assert callable(kwargs["build_file_source"])
    assert isinstance(kwargs["default_group"], str)
    assert kwargs["logger"] is svc._logger
    # Collaborator legacy NON piu' iniettati (verifica esplicita del cut)
    for legacy in (
        "list_layers", "list_sheets", "load_by_format",
        "cleanup", "safe_extractall", "zip_slip_error", "temp_dir",
    ):
        assert legacy not in kwargs, f"collaborator legacy {legacy} ancora iniettato"
