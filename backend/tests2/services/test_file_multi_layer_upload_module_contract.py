"""Smoke contract - STRUCT-001 Seam D-G + BE-HARDEN-001C E1+E3.

Verifica le invarianti minime del refactor multi-layer upload:

  1. layers_config empty/None -> ValueError, NO side effect.
  2. MIME block -> ValueError, NO parse/dispatch/save_batch.
  3. XLSX multi-sheet -> N FileSource, sheet valorizzato, layer None.
  4. GPKG multi-layer -> N FileSource, layer valorizzato, sheet None,
     CRS detection corretta.
  5. ZIP unwrap .gpkg -> parse usa extracted path; format passa da
     'zip' a 'gpkg'.
  6. BadZipFile -> ValueError("ZIP non valido"), NO save_batch.
  7. ZipSlipError -> ValueError("path traversal"), NO save_batch.
  8. Parse failure per-layer: il loop CONTINUA sui successivi
     (best-effort PER-LAYER, soft-preserve E3 decision); il layer
     fallito ottiene columns=[]/row_count=0.
  9. group_id None -> default_group propagato a TUTTI i N FileSource
     (DTO + batch row).
 10. safe layer name sanitization: spazi/dash -> underscore.
 11. Wrapper FileSourceService.create_multi_layer_from_upload delega
     coi 17 collaborator del service (E3: db_save_file_sources_batch
     + delete_blob), incluso cleanup_file=os.unlink.
 12. MULTI-GDB-001 fix: GDB-in-ZIP usa raw_data del ZIP originale.
 13. BE-HARDEN-001C E1: 1 dispatch_blob_storage per batch con
     storage_key_id=batch_id; N row condividono storage_key.
 14. BE-HARDEN-001C E3: 1 save_batch per N row in 1 transazione DB
     all-or-nothing; rollback -> cleanup compensativo del shared
     storage_key (best-effort); cleanup failure -> structured log.

Mock dei collaborator: nessun fiona/geopandas/DB reale. tempfile reale
su pytest tmp_path.
"""

from __future__ import annotations

import io
import os
import zipfile as _zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# =============================================================================
# Fixtures
# =============================================================================


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
    """Set base di collaborator. I test sostituiscono i singoli mock."""

    def gen_id(name):
        return f"file_{name}"

    # build_file_source factory: ritorna oggetto con attributo `id`
    # accessibile (e layer/sheet via getattr) -> compatibile col logger
    # post-batch che fa `fs.id` e `getattr(fs, 'layer', None)`.
    def _build_fs(**kw):
        return type("FS", (), kw)()

    return {
        "generate_file_id": MagicMock(side_effect=gen_id),
        "time_now": MagicMock(return_value=datetime(2026, 5, 2, tzinfo=timezone.utc)),
        "detect_format": MagicMock(return_value="gpkg"),
        "load_by_format": MagicMock(
            return_value=(pd.DataFrame({"id": [1, 2]}), None)
        ),
        "column_builder_from_df": MagicMock(
            return_value=[type("C", (), {"name": "id", "type": "int"})()]
        ),
        "dispatch_blob_storage": MagicMock(
            return_value={"data": b"raw", "storage_key": None, "size_bytes": 3}
        ),
        "db_save_file_sources_batch": MagicMock(),
        "delete_blob": MagicMock(),
        "build_file_source": MagicMock(side_effect=_build_fs),
        "cleanup_dir": MagicMock(),
        "cleanup_file": MagicMock(),
        "safe_extractall": MagicMock(),
        "zip_slip_error": RuntimeError,
        "temp_dir": str(tmp_path),
        "default_group": "default_grp",
        "logger": fake_logger,
        "mime_validate": MagicMock(return_value=None),
    }


# =============================================================================
# 1. layers_config empty/None -> ValueError, no side effect
# =============================================================================


@pytest.mark.parametrize("empty_value", [None, []])
def test_empty_layers_config_raises_value_error(collaborators, empty_value):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    with pytest.raises(ValueError, match="layers_config non puo essere vuota"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=empty_value,
            **collaborators,
        )

    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()
    collaborators["load_by_format"].assert_not_called()


# =============================================================================
# 2. MIME block prima di parse/dispatch/save_batch
# =============================================================================


def test_mime_block_short_circuits_before_any_layer_processing(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload
    from app.utils.mime_validator import MimeMismatchError

    collaborators["mime_validate"].side_effect = MimeMismatchError("executable")

    with pytest.raises(ValueError, match="executable"):
        create_multi_layer_from_upload(
            io.BytesIO(b"MZ\x90\x00 fake exe"), "trojan.gpkg", "Trojan",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
            **collaborators,
        )

    collaborators["load_by_format"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_multi_layer_from_upload bloccato (MIME)" in msg


# =============================================================================
# 3. XLSX multi-sheet -> N FileSource, sheet valorizzato (E3: 1 save_batch)
# =============================================================================


def test_xlsx_multi_sheet_creates_n_file_sources_with_sheet_field(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "xlsx"

    out = create_multi_layer_from_upload(
        io.BytesIO(b"fake xlsx"), "data.xlsx", "Workbook",
        layers_config=[{"sheet": "Sheet1"}, {"sheet": "Sheet2"}],
        group_id="g1",
        **collaborators,
    )

    assert len(out) == 2
    # E1: dispatch chiamato UNA volta
    assert collaborators["dispatch_blob_storage"].call_count == 1
    # E3: save_batch chiamato UNA volta con N row
    assert collaborators["db_save_file_sources_batch"].call_count == 1
    rows_arg = collaborators["db_save_file_sources_batch"].call_args.args[0]
    assert len(rows_arg) == 2
    # build_file_source: per XLSX -> sheet valorizzato, layer None
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[0]["sheet"] == "Sheet1"
    assert builds[0]["layer"] is None
    assert builds[1]["sheet"] == "Sheet2"
    assert builds[1]["layer"] is None
    # load_by_format: per XLSX -> layer arg = None, sheet arg = valore
    load_calls = collaborators["load_by_format"].call_args_list
    assert load_calls[0].args[2] is None
    assert load_calls[0].args[3] == "Sheet1"
    assert load_calls[1].args[2] is None
    assert load_calls[1].args[3] == "Sheet2"


# =============================================================================
# 4. GPKG multi-layer -> N FileSource, layer valorizzato + CRS detection
# =============================================================================


def test_gpkg_multi_layer_creates_n_file_sources_with_layer_and_crs(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=4326)
    collaborators["load_by_format"].return_value = (
        pd.DataFrame({"id": [1]}), fake_gdf,
    )

    out = create_multi_layer_from_upload(
        io.BytesIO(b"fake gpkg"), "data.gpkg", "Geo",
        layers_config=[
            {"layer": "roads"},
            {"layer": "buildings"},
            {"layer": "rivers"},
        ],
        group_id="g1",
        **collaborators,
    )

    assert len(out) == 3
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert [b["layer"] for b in builds] == ["roads", "buildings", "rivers"]
    assert all(b["sheet"] is None for b in builds)
    assert all(b["crs"] == "EPSG:4326" for b in builds)
    load_calls = collaborators["load_by_format"].call_args_list
    assert [c.args[2] for c in load_calls] == ["roads", "buildings", "rivers"]
    assert all(c.args[3] is None for c in load_calls)


# =============================================================================
# 5. ZIP unwrap .gpkg -> parse usa extracted path
# =============================================================================


def test_zip_unwrap_finds_gpkg_and_uses_extracted_path(
    collaborators, tmp_path,
):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].side_effect = ["zip", "gpkg"]

    inner_gpkg = tmp_path / "_seam_inner.gpkg"
    inner_gpkg.write_bytes(b"inner gpkg bytes")

    def fake_extract(z, dest):
        target = Path(dest) / "inner.gpkg"
        target.write_bytes(b"inner gpkg bytes")

    collaborators["safe_extractall"].side_effect = fake_extract

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("placeholder.txt", b"hi")
    zip_bytes = buf.getvalue()

    create_multi_layer_from_upload(
        io.BytesIO(zip_bytes), "bundle.zip", "Pack",
        layers_config=[{"layer": "L1"}],
        group_id="g1",
        **collaborators,
    )

    collaborators["safe_extractall"].assert_called_once()
    load_args = collaborators["load_by_format"].call_args.args
    assert load_args[0].name == "inner.gpkg"
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gpkg"


# =============================================================================
# 6. BadZipFile -> ValueError, no save_batch
# =============================================================================


def test_bad_zip_file_raises_value_error_no_save_batch(collaborators, tmp_path):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "zip"
    bad_bytes = b"this is not a real zip file at all"

    with pytest.raises(ValueError, match="ZIP non valido"):
        create_multi_layer_from_upload(
            io.BytesIO(bad_bytes), "fake.zip", "Bad",
            layers_config=[{"layer": "L1"}],
            **collaborators,
        )

    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()


# =============================================================================
# 7. ZipSlipError -> ValueError, no save_batch
# =============================================================================


def test_zip_slip_error_raises_value_error_no_save_batch(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "zip"
    collaborators["safe_extractall"].side_effect = RuntimeError("/etc/passwd traversal")

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("placeholder.txt", b"hi")

    with pytest.raises(ValueError, match="path traversal"):
        create_multi_layer_from_upload(
            io.BytesIO(buf.getvalue()), "evil.zip", "Evil",
            layers_config=[{"layer": "L1"}],
            **collaborators,
        )

    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()


# =============================================================================
# 8. Parse failure per-layer: loop continua (soft preserve, E3 decision)
# =============================================================================


def test_parse_failure_per_layer_continues_with_empty_columns(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    # 1# layer parse OK, 2# fallisce, 3# parse OK
    collaborators["load_by_format"].side_effect = [
        (pd.DataFrame({"a": [1]}), None),
        RuntimeError("parser exploded layer 2"),
        (pd.DataFrame({"a": [2, 3]}), None),
    ]

    out = create_multi_layer_from_upload(
        io.BytesIO(b"fake gpkg"), "data.gpkg", "Multi",
        layers_config=[
            {"layer": "ok1"},
            {"layer": "broken"},
            {"layer": "ok3"},
        ],
        group_id="g1",
        **collaborators,
    )

    # 3 FileSource creati nonostante il fail del 2# (soft preserve)
    assert len(out) == 3
    warning_calls = [
        c.args[0] for c in collaborators["logger"].warning.call_args_list
        if "Errore inferenza colonne layer 'broken'" in c.args[0]
    ]
    assert len(warning_calls) == 1
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[1]["columns"] == []
    assert builds[1]["row_count"] == 0
    # E3: tutti i 3 row inviati nello stesso save_batch (1 sola call)
    assert collaborators["db_save_file_sources_batch"].call_count == 1
    rows_arg = collaborators["db_save_file_sources_batch"].call_args.args[0]
    assert len(rows_arg) == 3


# =============================================================================
# 9. group_id None -> default_group propagato a tutti
# =============================================================================


def test_default_group_used_when_group_id_is_none(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    create_multi_layer_from_upload(
        io.BytesIO(b"x"), "data.gpkg", "Multi",
        layers_config=[{"layer": "L1"}, {"layer": "L2"}],
        group_id=None,
        **collaborators,
    )

    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert all(b["group_id"] == "default_grp" for b in builds)
    rows_arg = collaborators["db_save_file_sources_batch"].call_args.args[0]
    assert all(r["group_id"] == "default_grp" for r in rows_arg)


# =============================================================================
# 10. safe layer name sanitization
# =============================================================================


def test_safe_layer_name_sanitization_replaces_spaces_and_dashes(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    create_multi_layer_from_upload(
        io.BytesIO(b"x"), "data.gpkg", "Pack",
        layers_config=[
            {"layer": "my layer-name"},
            {"layer": "another-one with spaces"},
        ],
        group_id="g1",
        **collaborators,
    )

    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[0]["name"] == "Pack_my_layer_name"
    assert builds[1]["name"] == "Pack_another_one_with_spaces"
    fid_calls = [c.args[0] for c in collaborators["generate_file_id"].call_args_list]
    assert fid_calls == ["Pack_my_layer_name", "Pack_another_one_with_spaces"]


# =============================================================================
# 11. Wrapper FileSourceService delega coi 17 collaborator (E3)
# =============================================================================


def test_file_service_wrapper_delegates_with_save_batch_and_delete_blob():
    """`FileSourceService.create_multi_layer_from_upload` thin wrapper:
    invoca file_multi_layer_upload con i 17 collaborator del service.
    Verifica E3: passa db_save_file_sources_batch + delete_blob; verifica
    cleanup_file=os.unlink (asimmetria preservata)."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._detect_format = MagicMock(return_value="gpkg")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc._load_by_format = MagicMock(return_value=(pd.DataFrame({"a": [1]}), None))
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock(return_value=[])
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()

    fake_result = [MagicMock()]
    with patch(
        "app.services.file_multi_layer_upload.create_multi_layer_from_upload",
        return_value=fake_result,
    ) as mock_impl:
        out = svc.create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Doc",
            description="d",
            layers_config=[{"layer": "L1"}],
            srid=4326,
            group_id="g1",
        )

    assert out is fake_result
    mock_impl.assert_called_once()
    kwargs = mock_impl.call_args.kwargs
    assert kwargs["generate_file_id"] is svc._generate_file_id
    assert kwargs["time_now"] is svc._time.now
    assert kwargs["detect_format"] is svc._detect_format
    assert kwargs["load_by_format"] is svc._load_by_format
    assert kwargs["column_builder_from_df"] is svc._column_builder.from_dataframe
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    # E3: nuovo callback batch + delete_blob
    assert callable(kwargs["db_save_file_sources_batch"])
    assert callable(kwargs["delete_blob"])
    # E3: legacy db_save_file_source NON deve essere passato (no zombie)
    assert "db_save_file_source" not in kwargs
    assert callable(kwargs["build_file_source"])
    assert kwargs["cleanup_file"] is os.unlink
    assert kwargs["cleanup_dir"] is not os.unlink
    assert callable(kwargs["safe_extractall"])
    assert isinstance(kwargs["zip_slip_error"], type)
    assert isinstance(kwargs["temp_dir"], (str, Path))
    assert isinstance(kwargs["default_group"], str)
    assert kwargs["logger"] is svc._logger


# =============================================================================
# 11b. BE-DELETE-001: _delete_blob short-circuit on object_storage_enabled()
# =============================================================================


def _capture_delete_blob_callback():
    """Helper: costruisce il service, intercetta `_impl` e ritorna il
    callback `delete_blob` realmente passato dal wrapper. Permette di
    testare la closure in isolamento senza eseguire il batch reale."""
    from app.services.file_service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._detect_format = MagicMock(return_value="gpkg")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc._load_by_format = MagicMock(return_value=(pd.DataFrame({"a": [1]}), None))
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock(return_value=[])
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()

    captured = {}
    with patch(
        "app.services.file_multi_layer_upload.create_multi_layer_from_upload",
        return_value=[MagicMock()],
    ) as mock_impl:
        svc.create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Doc",
            layers_config=[{"layer": "L1"}],
        )
        captured["delete_blob"] = mock_impl.call_args.kwargs["delete_blob"]
    return svc, captured["delete_blob"]


def test_delete_blob_short_circuits_when_object_storage_disabled():
    """BE-DELETE-001 + BE-GO-READY-STORAGE-ENGINE-002: se
    object_storage_enabled() ritorna False, il callback _delete_blob
    NON chiama get_runtime_object_storage e logga un debug strutturato
    `event=storage_compensating_delete_skipped` (non error). Edge case
    patologico (capability disabilitata tra dispatch e cleanup) reso
    esplicito."""
    svc, delete_blob = _capture_delete_blob_callback()

    with patch(
        "app.shared.cloud_backends.object_storage_enabled", return_value=False,
    ), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
    ) as mock_get_storage:
        delete_blob("file-sources/batch_x/data.gpkg")

    mock_get_storage.assert_not_called()
    svc._logger.debug.assert_called_once()
    args = svc._logger.debug.call_args[0]
    rendered = args[0] % args[1:]
    assert "event=storage_compensating_delete_skipped" in rendered
    assert "domain=file_source_multi_layer" in rendered
    assert "skip_reason=capability_disabled" in rendered
    assert "file-sources/batch_x/data.gpkg" in rendered
    # NON deve emettere warning/error (no falso orfano)
    svc._logger.warning.assert_not_called()
    svc._logger.error.assert_not_called()


def test_delete_blob_calls_storage_when_object_storage_enabled():
    """BE-DELETE-001: caso nominale (capability ON) -> chiama
    get_runtime_object_storage().delete(key) come prima del fix."""
    svc, delete_blob = _capture_delete_blob_callback()

    fake_storage = MagicMock()
    with patch(
        "app.shared.cloud_backends.object_storage_enabled", return_value=True,
    ), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        delete_blob("file-sources/batch_y/data.gpkg")

    fake_storage.delete.assert_called_once_with("file-sources/batch_y/data.gpkg")


# =============================================================================
# 12. MULTI-GDB-001 fix: GDB directory in ZIP -> raw_data resta lo ZIP
# =============================================================================


def test_zip_unwrap_gdb_directory_uses_original_zip_blob(
    collaborators, tmp_path,
):
    """MULTI-GDB-001 fix: per GDB-in-ZIP il path estratto e' una directory
    .gdb (non un file). Pre-fix: tmp_path.read_bytes() su directory
    sollevava IsADirectoryError. Post-fix: per format='gdb' conserviamo
    il ZIP originale come blob."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].side_effect = ["zip", "gdb"]

    def fake_extract(z, dest):
        gdb_dir = Path(dest) / "geodatabase.gdb"
        gdb_dir.mkdir()
        (gdb_dir / "a00000001.gdbtable").write_bytes(b"gdb internal")

    collaborators["safe_extractall"].side_effect = fake_extract

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("placeholder.txt", b"placeholder content")
    zip_bytes = buf.getvalue()

    out = create_multi_layer_from_upload(
        io.BytesIO(zip_bytes), "geodatabase.zip", "Geo",
        layers_config=[{"layer": "feature_class_1"}],
        group_id="g1",
        **collaborators,
    )

    assert len(out) == 1

    # Dispatch riceve gli STESSI bytes del ZIP originale, NON i bytes di
    # un file dentro la dir .gdb.
    dispatch_args = collaborators["dispatch_blob_storage"].call_args.args
    assert dispatch_args[1] == zip_bytes, (
        "MULTI-GDB-001 fix: per GDB il blob salvato deve essere il ZIP "
        "originale, non un file dentro la directory .gdb"
    )

    load_args = collaborators["load_by_format"].call_args.args
    assert load_args[0].name == "geodatabase.gdb"
    assert load_args[0].is_dir(), "Path passato a load_by_format deve essere la dir .gdb"

    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gdb"
    assert build_kwargs["layer"] == "feature_class_1"
    assert build_kwargs["sheet"] is None
    assert build_kwargs["original_filename"] == "geodatabase.gdb"
    assert build_kwargs["file_size"] == len(zip_bytes)


# =============================================================================
# 13. BE-HARDEN-001C E1: shared blob dispatch (1 dispatch, N row condivise)
# =============================================================================


def test_multi_layer_uses_single_dispatch_with_batch_id_storage_key(collaborators):
    """3 layer -> dispatch_blob_storage chiamato 1 volta PRIMA del batch
    save. Argomenti: file_id e storage_key_id sono entrambi un `batch_*`
    opaque id (non legati ad alcun file_id specifico del layer)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    create_multi_layer_from_upload(
        io.BytesIO(b"shared blob"), "data.gpkg", "Multi",
        layers_config=[
            {"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"},
        ],
        group_id="g1",
        **collaborators,
    )

    assert collaborators["dispatch_blob_storage"].call_count == 1
    assert collaborators["db_save_file_sources_batch"].call_count == 1

    call = collaborators["dispatch_blob_storage"].call_args
    file_id_arg = call.args[0]
    storage_key_id_kwarg = call.kwargs.get("storage_key_id")
    assert file_id_arg.startswith("batch_"), (
        f"E1 expected opaque batch_id, got {file_id_arg!r}"
    )
    assert storage_key_id_kwarg == file_id_arg
    assert not file_id_arg.startswith("file_")


def test_n_save_batch_rows_share_same_storage_key(collaborators):
    """Object_storage path: le N row del batch ricevono lo stesso storage_key
    e file_id distinti per ciascuna row."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    shared_kwargs = {
        "data": None,
        "storage_key": "file-sources/batch_xyz/data.gpkg",
        "size_bytes": 1024,
    }
    collaborators["dispatch_blob_storage"].return_value = shared_kwargs

    create_multi_layer_from_upload(
        io.BytesIO(b"big blob"), "data.gpkg", "Multi",
        layers_config=[
            {"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"},
        ],
        group_id="g1",
        **collaborators,
    )

    rows_arg = collaborators["db_save_file_sources_batch"].call_args.args[0]
    assert len(rows_arg) == 3
    storage_keys = {r["storage_key"] for r in rows_arg}
    assert storage_keys == {"file-sources/batch_xyz/data.gpkg"}
    datas = {r["data"] for r in rows_arg}
    assert datas == {None}
    sizes = {r["size_bytes"] for r in rows_arg}
    assert sizes == {1024}
    file_ids = [r["file_id"] for r in rows_arg]
    assert len(set(file_ids)) == 3


def test_dispatch_failure_before_loop_no_save_batch_no_cleanup(collaborators):
    """E1: dispatch fallisce PRE-LOOP -> 0 save_batch chiamato; nessun
    delete_blob (lo storage_key e' opaco al caller in caso di fail)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    collaborators["dispatch_blob_storage"].side_effect = RuntimeError(
        "S3 dispatch down"
    )

    with pytest.raises(RuntimeError, match="S3 dispatch down"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[
                {"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"},
            ],
            group_id="g1",
            **collaborators,
        )

    assert collaborators["dispatch_blob_storage"].call_count == 1
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    collaborators["cleanup_file"].assert_called()


# =============================================================================
# 14. BE-HARDEN-001C E3: batch save all-or-nothing + cleanup compensativo
# =============================================================================


def test_batch_save_called_once_with_n_rows_in_object_storage_mode(collaborators):
    """E3 happy path: 3 layer -> 1 sola chiamata a db_save_file_sources_batch
    con 3 row formate correttamente (file_id, group_id, name, original_filename,
    fmt, metadata, data, storage_key, size_bytes)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    create_multi_layer_from_upload(
        io.BytesIO(b"blob"), "data.gpkg", "Multi",
        layers_config=[
            {"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"},
        ],
        group_id="g1",
        **collaborators,
    )

    assert collaborators["db_save_file_sources_batch"].call_count == 1
    rows_arg = collaborators["db_save_file_sources_batch"].call_args.args[0]
    assert len(rows_arg) == 3
    required_keys = {
        "file_id", "group_id", "name", "original_filename", "fmt",
        "metadata", "data", "storage_key", "size_bytes",
    }
    for r in rows_arg:
        assert required_keys.issubset(r.keys()), (
            f"row mancante chiavi attese: row={r}"
        )


def test_batch_save_failure_triggers_compensating_blob_cleanup(collaborators):
    """E3 rollback: save_batch solleva (rollback DB atomico, 0 row).
    Se shared storage_key e' valorizzato -> delete_blob(shared_key) chiamato
    best-effort. La eccezione DB originale propaga inalterata."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    shared_kwargs = {
        "data": None,
        "storage_key": "file-sources/batch_abc/data.gpkg",
        "size_bytes": 2048,
    }
    collaborators["dispatch_blob_storage"].return_value = shared_kwargs
    collaborators["db_save_file_sources_batch"].side_effect = RuntimeError(
        "DB transaction rolled back"
    )

    with pytest.raises(RuntimeError, match="DB transaction rolled back"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[
                {"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"},
            ],
            group_id="g1",
            **collaborators,
        )

    # Cleanup compensativo eseguito col shared storage_key
    collaborators["delete_blob"].assert_called_once_with(
        "file-sources/batch_abc/data.gpkg",
    )
    # Save batch tentato 1 volta (e fallito atomicamente)
    assert collaborators["db_save_file_sources_batch"].call_count == 1


def test_batch_save_failure_in_bytea_mode_no_blob_cleanup_attempted(collaborators):
    """E3 rollback in BYTEA mode (storage_key=None): save_batch fallisce ->
    NESSUN delete_blob chiamato (non c'e' blob remoto da pulire). DB error
    propaga normalmente."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    # Default fixture e' gia' BYTEA: data=b"raw", storage_key=None
    collaborators["db_save_file_sources_batch"].side_effect = RuntimeError(
        "DB constraint violation"
    )

    with pytest.raises(RuntimeError, match="DB constraint violation"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
            group_id="g1",
            **collaborators,
        )

    collaborators["delete_blob"].assert_not_called()


def test_batch_save_failure_with_cleanup_failure_logs_orphan_event(collaborators):
    """E3 double-failure: save_batch fallisce + delete_blob a sua volta
    fallisce -> log strutturato `event=orphan_blob_cleanup_failed` emesso,
    eccezione DB ORIGINALE propaga (NON mascherata da cleanup error)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    shared_kwargs = {
        "data": None,
        "storage_key": "file-sources/batch_orphan/data.gpkg",
        "size_bytes": 4096,
    }
    collaborators["dispatch_blob_storage"].return_value = shared_kwargs
    collaborators["db_save_file_sources_batch"].side_effect = RuntimeError(
        "Original DB error"
    )
    collaborators["delete_blob"].side_effect = RuntimeError("S3 delete failed")

    with pytest.raises(RuntimeError, match="Original DB error"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
            group_id="g1",
            **collaborators,
        )

    # Log error strutturato emesso col contesto del batch_id e dell'errore
    error_calls = [c.args[0] for c in collaborators["logger"].error.call_args_list]
    orphan_logs = [m for m in error_calls if "event=orphan_blob_cleanup_failed" in m]
    assert len(orphan_logs) == 1
    msg = orphan_logs[0]
    assert "storage_key='file-sources/batch_orphan/data.gpkg'" in msg
    assert "batch_id=batch_" in msg
    assert "S3 delete failed" in msg
