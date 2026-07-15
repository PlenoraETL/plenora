"""Smoke contract for `create_multi_layer_from_upload` (RAM-only, Phase4)."""

from __future__ import annotations

import io
import zipfile as _zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]
THREE_LAYERS = [{"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"}]


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
    """Collaborator RAM-only — niente piu' load_by_format / safe_extractall."""

    def gen_id(name):
        return f"file_{name}"

    def _build_fs(**kw):
        return type("FS", (), kw)()

    return {
        "generate_file_id": MagicMock(side_effect=gen_id),
        "time_now": MagicMock(return_value=datetime(2026, 5, 2, tzinfo=timezone.utc)),
        "detect_format": MagicMock(return_value="gpkg"),
        "column_builder_from_df": MagicMock(
            return_value=[type("C", (), {"name": "id", "type": "int"})()]
        ),
        "dispatch_blob_storage": MagicMock(
            return_value={"data": b"raw", "storage_key": None, "size_bytes": 3}
        ),
        "db_save_file_sources_batch": MagicMock(),
        "delete_blob": MagicMock(),
        "build_file_source": MagicMock(side_effect=_build_fs),
        "default_group": "default_grp",
        "logger": fake_logger,
        "mime_validate": MagicMock(return_value=None),
    }


def _create_multi(
    collaborators,
    content=b"x",
    filename="data.gpkg",
    name="Multi",
    *,
    layers_config=None,
    group_id="g1",
    load_return=(pd.DataFrame({"id": [1, 2]}), None),
    load_side_effect=None,
):
    from app.core.files.multi_layer_upload import create_multi_layer_from_upload

    patcher = patch(
        "app.core.dataframes.load_dispatch.load_by_format_stream",
    )
    mock_lbf = patcher.start()
    if load_side_effect is not None:
        mock_lbf.side_effect = load_side_effect
    else:
        mock_lbf.return_value = load_return
    try:
        result = create_multi_layer_from_upload(
            io.BytesIO(content), filename, name,
            layers_config=THREE_LAYERS if layers_config is None else layers_config,
            group_id=group_id,
            **collaborators,
        )
        return result, mock_lbf
    finally:
        patcher.stop()


def _builds(collaborators):
    return [c.kwargs for c in collaborators["build_file_source"].call_args_list]


def _batch_rows(collaborators):
    return collaborators["db_save_file_sources_batch"].call_args.args[0]


def _zip_bytes(inner_name="placeholder.txt", content=b"hi"):
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr(inner_name, content)
    return buf.getvalue()


def _service_with_multi_layer_collaborators():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._generate_file_id = MagicMock(return_value="file_x")
    svc._detect_format = MagicMock(return_value="gpkg")
    svc._time = MagicMock()
    svc._time.now = MagicMock(return_value=datetime.now(timezone.utc))
    svc._column_builder = MagicMock()
    svc._column_builder.from_dataframe = MagicMock(return_value=[])
    svc._dispatch_blob_storage = MagicMock(
        return_value={"data": b"x", "storage_key": None, "size_bytes": 1}
    )
    svc._logger = MagicMock()
    return svc


# 1. layers_config empty/None -> ValueError, no side effect


@pytest.mark.parametrize("empty_value", [None, []])
def test_empty_layers_config_raises_value_error(collaborators, empty_value):
    from app.core.files.multi_layer_upload import create_multi_layer_from_upload

    with pytest.raises(ValueError, match="layers_config non puo essere vuota"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=empty_value,
            **collaborators,
        )

    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()


# 2. MIME block prima di parse/dispatch/save_batch


def test_mime_block_short_circuits_before_any_layer_processing(collaborators):
    from app.utils.mime_validator import MimeMismatchError

    collaborators["mime_validate"].side_effect = MimeMismatchError("executable")

    with pytest.raises(ValueError, match="executable"):
        _create_multi(
            collaborators, b"MZ\x90\x00 fake exe", "trojan.gpkg", "Trojan",
            layers_config=THREE_LAYERS[:2],
        )

    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_multi_layer_from_upload bloccato (MIME)" in msg


# 3. XLSX multi-sheet -> N FileSource, sheet valorizzato (E3: 1 save_batch)


def test_xlsx_multi_sheet_creates_n_file_sources_with_sheet_field(collaborators):
    collaborators["detect_format"].return_value = "xlsx"

    out, mock_lbf = _create_multi(
        collaborators, b"fake xlsx", "data.xlsx", "Workbook",
        layers_config=[{"sheet": "Sheet1"}, {"sheet": "Sheet2"}],
    )

    assert len(out) == 2
    assert collaborators["dispatch_blob_storage"].call_count == 1
    assert collaborators["db_save_file_sources_batch"].call_count == 1
    rows_arg = _batch_rows(collaborators)
    assert len(rows_arg) == 2
    builds = _builds(collaborators)
    assert builds[0]["sheet"] == "Sheet1"
    assert builds[0]["layer"] is None
    assert builds[1]["sheet"] == "Sheet2"
    assert builds[1]["layer"] is None
    load_calls = mock_lbf.call_args_list
    assert load_calls[0].kwargs["layer"] is None
    assert load_calls[0].kwargs["sheet"] == "Sheet1"
    assert load_calls[1].kwargs["layer"] is None
    assert load_calls[1].kwargs["sheet"] == "Sheet2"


# 4. GPKG multi-layer -> N FileSource, layer valorizzato + CRS detection


def test_gpkg_multi_layer_creates_n_file_sources_with_layer_and_crs(collaborators):
    collaborators["detect_format"].return_value = "gpkg"

    fake_gdf = MagicMock()
    fake_gdf.crs = MagicMock()
    fake_gdf.crs.to_epsg = MagicMock(return_value=4326)

    out, mock_lbf = _create_multi(
        collaborators, b"fake gpkg", "data.gpkg", "Geo",
        layers_config=[
            {"layer": "roads"},
            {"layer": "buildings"},
            {"layer": "rivers"},
        ],
        load_return=(pd.DataFrame({"id": [1]}), fake_gdf),
    )

    assert len(out) == 3
    builds = _builds(collaborators)
    assert [b["layer"] for b in builds] == ["roads", "buildings", "rivers"]
    assert all(b["sheet"] is None for b in builds)
    assert all(b["crs"] == "EPSG:4326" for b in builds)
    load_calls = mock_lbf.call_args_list
    assert [c.kwargs["layer"] for c in load_calls] == [
        "roads", "buildings", "rivers",
    ]
    assert all(c.kwargs["sheet"] is None for c in load_calls)


# 5. ZIP unwrap .gpkg -> parse usa inner bytes


def test_zip_unwrap_finds_gpkg_and_uses_inner_bytes(collaborators):
    collaborators["detect_format"].side_effect = ["zip", "gpkg"]

    inner_gpkg_bytes = b"inner gpkg payload"
    zip_payload = _zip_bytes("inner.gpkg", inner_gpkg_bytes)

    _create_multi(
        collaborators, zip_payload, "bundle.zip", "Pack",
        layers_config=[{"layer": "L1"}],
    )

    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gpkg"
    assert build_kwargs["original_filename"] == "inner.gpkg"


def test_zip_unwrap_supports_multi_sheet_xlsx(collaborators):
    collaborators["detect_format"].side_effect = ["zip", "xlsx"]
    inner_xlsx_bytes = b"inner xlsx payload"
    zip_payload = _zip_bytes("workbook.xlsx", inner_xlsx_bytes)

    out, mock_lbf = _create_multi(
        collaborators,
        zip_payload,
        "bundle.zip",
        "Workbook",
        layers_config=[{"sheet": "Summary"}],
    )

    assert len(out) == 1
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "xlsx"
    assert build_kwargs["sheet"] == "Summary"
    assert build_kwargs["layer"] is None
    assert mock_lbf.call_args.kwargs == {"layer": None, "sheet": "Summary"}


# 6. BadZipFile -> ValueError, no save_batch


def test_bad_zip_file_raises_value_error_no_save_batch(collaborators):
    collaborators["detect_format"].return_value = "zip"

    with pytest.raises(ValueError, match="ZIP non valido"):
        _create_multi(
            collaborators, b"this is not a real zip file at all", "fake.zip", "Bad",
            layers_config=[{"layer": "L1"}],
        )

    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()


# 7. ZIP senza file riconosciuti -> ValueError


def test_zip_with_unsupported_content_raises_value_error(collaborators):
    collaborators["detect_format"].side_effect = ["zip"]
    zip_payload = _zip_bytes("readme.txt", b"no gpkg here")

    with pytest.raises(ValueError, match="ZIP non riconosciuto"):
        _create_multi(
            collaborators, zip_payload, "junk.zip", "Junk",
            layers_config=[{"layer": "L1"}],
        )

    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()


# 8. Parse failure per-layer: batch abortito e shared blob ripulito


def test_parse_failure_per_layer_aborts_batch_and_cleans_blob(collaborators):
    collaborators["detect_format"].return_value = "gpkg"
    collaborators["dispatch_blob_storage"].return_value = {
        "data": None,
        "storage_key": "files/batch/blob",
        "size_bytes": 3,
    }

    with pytest.raises(ValueError, match="foglio/layer 'broken'"):
        _create_multi(
            collaborators, b"fake gpkg", "data.gpkg", "Multi",
            layers_config=[
                {"layer": "ok1"},
                {"layer": "broken"},
                {"layer": "ok3"},
            ],
            load_side_effect=[
                (pd.DataFrame({"a": [1]}), None),
                RuntimeError("parser exploded layer 2"),
                (pd.DataFrame({"a": [2, 3]}), None),
            ],
        )

    warning_calls = [
        c.args[0] for c in collaborators["logger"].warning.call_args_list
        if "Errore inferenza colonne layer 'broken'" in c.args[0]
    ]
    assert len(warning_calls) == 1
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_called_once_with("files/batch/blob")


# 9. group_id None -> default_group propagato a tutti


def test_default_group_used_when_group_id_is_none(collaborators):
    collaborators["detect_format"].return_value = "gpkg"

    _create_multi(
        collaborators,
        layers_config=THREE_LAYERS[:2],
        group_id=None,
    )

    builds = _builds(collaborators)
    assert all(b["group_id"] == "default_grp" for b in builds)
    assert all(r["group_id"] == "default_grp" for r in _batch_rows(collaborators))


# 10. safe layer name sanitization


def test_safe_layer_name_sanitization_replaces_spaces_and_dashes(collaborators):
    collaborators["detect_format"].return_value = "gpkg"

    _create_multi(
        collaborators, name="Pack",
        layers_config=[
            {"layer": "my layer-name"},
            {"layer": "another-one with spaces"},
        ],
    )

    builds = _builds(collaborators)
    assert builds[0]["name"] == "Pack_my_layer_name"
    assert builds[1]["name"] == "Pack_another_one_with_spaces"
    fid_calls = [
        c.args[0] for c in collaborators["generate_file_id"].call_args_list
    ]
    assert fid_calls == ["Pack_my_layer_name", "Pack_another_one_with_spaces"]


# 11. FileSourceService delega coi collaborator core (RAM-only)


def test_file_source_service_multi_layer_delegates_with_ram_only_collaborators():
    """Service facade delega al modulo core con i collaborator RAM-only.
    Verifica che i collaborator legacy (load_by_format / safe_extractall /
    cleanup_*) NON siano piu' iniettati."""
    svc = _service_with_multi_layer_collaborators()
    fake_result = [MagicMock()]
    with patch(
        "app.core.files.multi_layer_upload.create_multi_layer_from_upload",
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
    assert kwargs["column_builder_from_df"] is svc._column_builder.from_dataframe
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    assert callable(kwargs["db_save_file_sources_batch"])
    assert callable(kwargs["delete_blob"])
    assert callable(kwargs["build_file_source"])
    assert isinstance(kwargs["default_group"], str)
    assert kwargs["logger"] is svc._logger
    # Collaborator legacy non piu' iniettati post-RAM-only.
    for legacy in (
        "load_by_format", "cleanup_dir", "cleanup_file",
        "safe_extractall", "zip_slip_error", "temp_dir",
        "db_save_file_source",
    ):
        assert legacy not in kwargs, (
            f"collaborator legacy {legacy} ancora iniettato dal service"
        )


# 11b. BE-DELETE-001: _delete_blob short-circuit on object_storage_enabled()


def _capture_delete_blob_callback():
    """Helper: costruisce il service, intercetta `_impl` e ritorna il
    callback `delete_blob` realmente passato dal facade."""
    svc = _service_with_multi_layer_collaborators()
    captured = {}
    with patch(
        "app.core.files.multi_layer_upload.create_multi_layer_from_upload",
        return_value=[MagicMock()],
    ) as mock_impl:
        svc.create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Doc",
            layers_config=[{"layer": "L1"}],
        )
        captured["delete_blob"] = mock_impl.call_args.kwargs["delete_blob"]
    return svc, captured["delete_blob"]


def test_delete_blob_short_circuits_when_object_storage_disabled():
    """BE-DELETE-001 + BE-GO-READY-STORAGE-ENGINE-002."""
    svc, delete_blob = _capture_delete_blob_callback()

    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=False,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
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
    svc._logger.warning.assert_not_called()
    svc._logger.error.assert_not_called()


def test_delete_blob_calls_storage_when_object_storage_enabled():
    """BE-DELETE-001 caso nominale (capability ON)."""
    svc, delete_blob = _capture_delete_blob_callback()

    fake_storage = MagicMock()
    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=True,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        delete_blob("file-sources/batch_y/data.gpkg")

    fake_storage.delete.assert_called_once_with("file-sources/batch_y/data.gpkg")


# 12. MULTI-GDB-001 fix: GDB directory in ZIP -> raw_data resta lo ZIP


def test_zip_unwrap_gdb_directory_uses_original_zip_blob(collaborators):
    """MULTI-GDB-001 fix RAM-only: per GDB-in-ZIP il blob salvato resta lo
    ZIP originale; pyogrio /vsizip legge la directory .gdb senza materializzare."""
    collaborators["detect_format"].side_effect = ["zip", "gdb"]

    # Costruiamo un ZIP che contiene una "directory" .gdb (entry con / dentro)
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("geodatabase.gdb/a00000001.gdbtable", b"gdb internal")
    zip_bytes = buf.getvalue()

    out, _mock_lbf = _create_multi(
        collaborators, zip_bytes, "geodatabase.zip", "Geo",
        layers_config=[{"layer": "feature_class_1"}],
    )

    assert len(out) == 1

    dispatch_args = collaborators["dispatch_blob_storage"].call_args.args
    dispatch_stream = dispatch_args[1]
    dispatch_stream.seek(0)
    assert dispatch_stream.read() == zip_bytes, (
        "MULTI-GDB-001 fix RAM-only: per GDB il blob salvato deve essere "
        "il ZIP originale (pyogrio legge /vsizip)"
    )
    assert (
        collaborators["dispatch_blob_storage"].call_args.kwargs["size_bytes"]
        == len(zip_bytes)
    )

    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gdb"
    assert build_kwargs["layer"] == "feature_class_1"
    assert build_kwargs["sheet"] is None
    assert build_kwargs["file_size"] == len(zip_bytes)


# 13. BE-HARDEN-001C E1: shared blob dispatch (1 dispatch, N row condivise)


def test_multi_layer_uses_single_dispatch_with_batch_id_storage_key(collaborators):
    """3 layer -> dispatch_blob_storage chiamato 1 volta PRIMA del batch
    save. Argomenti: file_id e storage_key_id sono entrambi un `batch_*`."""
    collaborators["detect_format"].return_value = "gpkg"

    _create_multi(collaborators, b"shared blob")

    assert collaborators["dispatch_blob_storage"].call_count == 1
    assert collaborators["db_save_file_sources_batch"].call_count == 1

    call = collaborators["dispatch_blob_storage"].call_args
    file_id_arg = call.args[0]
    storage_key_id_kwarg = call.kwargs.get("storage_key_id")
    assert file_id_arg.startswith("batch_")
    assert storage_key_id_kwarg == file_id_arg
    assert not file_id_arg.startswith("file_")


def test_n_save_batch_rows_share_same_storage_key(collaborators):
    """Object_storage path: N row del batch condividono storage_key,
    file_id distinti per ciascuna row."""
    collaborators["detect_format"].return_value = "gpkg"
    shared_kwargs = {
        "data": None,
        "storage_key": "file-sources/batch_xyz/data.gpkg",
        "size_bytes": 1024,
    }
    collaborators["dispatch_blob_storage"].return_value = shared_kwargs

    _create_multi(collaborators, b"big blob")

    rows_arg = _batch_rows(collaborators)
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
    """E1: dispatch fallisce PRE-LOOP -> 0 save_batch."""
    collaborators["detect_format"].return_value = "gpkg"
    collaborators["dispatch_blob_storage"].side_effect = RuntimeError(
        "S3 dispatch down"
    )

    with pytest.raises(RuntimeError, match="S3 dispatch down"):
        _create_multi(collaborators)

    assert collaborators["dispatch_blob_storage"].call_count == 1
    collaborators["db_save_file_sources_batch"].assert_not_called()
    collaborators["delete_blob"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()


# 14. BE-HARDEN-001C E3: batch save all-or-nothing + cleanup compensativo


def test_batch_save_called_once_with_n_rows_in_object_storage_mode(collaborators):
    """E3 happy path: 3 layer -> 1 sola chiamata a save_batch."""
    collaborators["detect_format"].return_value = "gpkg"

    _create_multi(collaborators, b"blob")

    assert collaborators["db_save_file_sources_batch"].call_count == 1
    rows_arg = _batch_rows(collaborators)
    assert len(rows_arg) == 3
    required_keys = {
        "file_id", "group_id", "name", "original_filename", "fmt",
        "metadata", "data", "storage_key", "size_bytes",
    }
    for r in rows_arg:
        assert required_keys.issubset(r.keys())


def test_batch_save_failure_triggers_compensating_blob_cleanup(collaborators):
    """E3 rollback: save_batch solleva (rollback DB atomico, 0 row).
    delete_blob(shared_key) best-effort. La eccezione DB propaga inalterata."""
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
        _create_multi(collaborators)

    collaborators["delete_blob"].assert_called_once_with(
        "file-sources/batch_abc/data.gpkg",
    )
    assert collaborators["db_save_file_sources_batch"].call_count == 1


def test_batch_save_failure_in_bytea_mode_no_blob_cleanup_attempted(collaborators):
    """E3 rollback in BYTEA mode (storage_key=None): NESSUN delete_blob."""
    collaborators["detect_format"].return_value = "gpkg"
    collaborators["db_save_file_sources_batch"].side_effect = RuntimeError(
        "DB constraint violation"
    )

    with pytest.raises(RuntimeError, match="DB constraint violation"):
        _create_multi(
            collaborators,
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
        )

    collaborators["delete_blob"].assert_not_called()


def test_batch_save_failure_with_cleanup_failure_logs_orphan_event(collaborators):
    """E3 double-failure: save_batch fallisce + delete_blob fallisce ->
    log strutturato `event=orphan_blob_cleanup_failed`, eccezione DB
    ORIGINALE propaga inalterata."""
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
        _create_multi(
            collaborators,
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
        )

    error_calls = [
        c.args[0] for c in collaborators["logger"].error.call_args_list
    ]
    orphan_logs = [m for m in error_calls if "event=orphan_blob_cleanup_failed" in m]
    assert len(orphan_logs) == 1
    msg = orphan_logs[0]
    assert "storage_key='file-sources/batch_orphan/data.gpkg'" in msg
    assert "batch_id=batch_" in msg
    assert "S3 delete failed" in msg
