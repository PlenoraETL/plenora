"""Smoke contract - STRUCT-001 Seam D-G: estrazione create_multi_layer_from_upload.

Verifica le invarianti minime del refactor:

  1. layers_config empty/None -> ValueError, NO side effect.
  2. MIME block -> ValueError, NO parse/dispatch/db_save.
  3. XLSX multi-sheet -> N FileSource, sheet valorizzato, layer None.
  4. GPKG multi-layer -> N FileSource, layer valorizzato, sheet None,
     CRS detection corretta.
  5. ZIP unwrap .gpkg -> parse usa extracted path; format passa da
     'zip' a 'gpkg'.
  6. BadZipFile -> ValueError("ZIP non valido"), NO db_save.
  7. ZipSlipError -> ValueError("path traversal"), NO db_save.
  8. Parse failure per-layer: il loop CONTINUA sui successivi
     (best-effort PER-LAYER); il layer fallito ottiene
     columns=[]/row_count=0.
  9. Dispatch failure: propaga immediato (loop interrotto), MA i
     layer precedenti restano persistiti su DB
     (atomicita' batch NON garantita - documentazione esplicita).
 10. group_id None -> default_group propagato a TUTTI i N FileSource
     (DTO + DB save).
 11. safe layer name sanitization: spazi/dash -> underscore.
 12. Wrapper FileSourceService.create_multi_layer_from_upload delega
     coi 16 collaborator del service, incluso cleanup_file=os.unlink
     (asimmetria preservata).

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
    fake_dto = MagicMock()
    fake_dto.model_dump = MagicMock(return_value={"k": "v"})

    # generate_file_id deterministico: prefix con il name
    def gen_id(name):
        return f"file_{name}"

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
        "db_save_file_source": MagicMock(),
        "build_file_source": MagicMock(side_effect=lambda **kw: dict(kw)),
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

    # Nessun side effect (neanche temp file scritto, perche' check pre-MIME)
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["load_by_format"].assert_not_called()


# =============================================================================
# 2. MIME block prima di parse/dispatch/db
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

    # Nessun layer processato
    collaborators["load_by_format"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    # Logger warning con contesto
    collaborators["logger"].warning.assert_called_once()
    msg = collaborators["logger"].warning.call_args[0][0]
    assert "create_multi_layer_from_upload bloccato (MIME)" in msg


# =============================================================================
# 3. XLSX multi-sheet -> N FileSource, sheet valorizzato
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

    # 2 FileSource creati
    assert len(out) == 2
    # BE-HARDEN-001C E1: dispatch UNA volta pre-loop (shared blob);
    # db_save N volte (uno per sheet).
    assert collaborators["dispatch_blob_storage"].call_count == 1
    assert collaborators["db_save_file_source"].call_count == 2
    # build_file_source: per XLSX -> sheet valorizzato, layer None
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[0]["sheet"] == "Sheet1"
    assert builds[0]["layer"] is None
    assert builds[1]["sheet"] == "Sheet2"
    assert builds[1]["layer"] is None
    # load_by_format: per XLSX -> layer arg = None, sheet arg = valore
    load_calls = collaborators["load_by_format"].call_args_list
    assert load_calls[0].args[2] is None  # layer
    assert load_calls[0].args[3] == "Sheet1"  # sheet
    assert load_calls[1].args[2] is None
    assert load_calls[1].args[3] == "Sheet2"


# =============================================================================
# 4. GPKG multi-layer -> N FileSource, layer valorizzato + CRS detection
# =============================================================================


def test_gpkg_multi_layer_creates_n_file_sources_with_layer_and_crs(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    # Mock gdf con CRS EPSG:4326
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
    # build_file_source: per GPKG -> layer valorizzato, sheet None
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert [b["layer"] for b in builds] == ["roads", "buildings", "rivers"]
    assert all(b["sheet"] is None for b in builds)
    # CRS propagato a tutti
    assert all(b["crs"] == "EPSG:4326" for b in builds)
    # load_by_format: layer arg valorizzato, sheet arg None
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

    # detect_format ritorna 'zip' inizialmente, poi 'gpkg' post-unwrap
    collaborators["detect_format"].side_effect = ["zip", "gpkg"]

    # Path inner reale: il modulo crea zip_tmp_dir tramite tempfile.mkdtemp
    # nel temp_dir patched (tmp_path). Per simulare iterdir post-extract,
    # creiamo il file .gpkg direttamente nella zip_tmp_dir attesa.
    # Approccio piu' semplice: pre-crea un file gpkg + monkey-patch
    # safe_extractall per non fare nulla, e mockear rglob ritornando il file.
    # Strategia: lascia che mkdtemp crei la dir, quindi popola via
    # safe_extractall side_effect.

    inner_gpkg = tmp_path / "_seam_inner.gpkg"
    inner_gpkg.write_bytes(b"inner gpkg bytes")

    def fake_extract(z, dest):
        # Sposta inner_gpkg dentro la dest
        target = Path(dest) / "inner.gpkg"
        target.write_bytes(b"inner gpkg bytes")

    collaborators["safe_extractall"].side_effect = fake_extract

    # Necessario: il modulo apre zipfile.ZipFile(tmp_path) PRIMA di
    # safe_extractall. Per non sbattere su BadZipFile, scrivo un mini ZIP
    # vuoto valido come contenuto del file_content.
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

    # safe_extractall chiamato (ZIP unwrap path)
    collaborators["safe_extractall"].assert_called_once()
    # load_by_format chiamato col path estratto (.gpkg) NON con il .zip
    load_args = collaborators["load_by_format"].call_args.args
    assert load_args[0].name == "inner.gpkg"
    # build_file_source riceve format='gpkg' (post unwrap)
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gpkg"


# =============================================================================
# 6. BadZipFile -> ValueError, no db_save
# =============================================================================


def test_bad_zip_file_raises_value_error_no_db_save(collaborators, tmp_path):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "zip"
    # File NON-ZIP camuffato da .zip -> BadZipFile sollevato dal modulo
    bad_bytes = b"this is not a real zip file at all"

    with pytest.raises(ValueError, match="ZIP non valido"):
        create_multi_layer_from_upload(
            io.BytesIO(bad_bytes), "fake.zip", "Bad",
            layers_config=[{"layer": "L1"}],
            **collaborators,
        )

    collaborators["db_save_file_source"].assert_not_called()
    collaborators["dispatch_blob_storage"].assert_not_called()


# =============================================================================
# 7. ZipSlipError -> ValueError, no db_save
# =============================================================================


def test_zip_slip_error_raises_value_error_no_db_save(collaborators):
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "zip"
    # safe_extractall solleva l'errore (custom ZipSlip)
    collaborators["safe_extractall"].side_effect = RuntimeError("/etc/passwd traversal")
    # collaborators["zip_slip_error"] e' RuntimeError (vedi fixture)

    # ZIP valido per superare il BadZipFile check
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("placeholder.txt", b"hi")

    with pytest.raises(ValueError, match="path traversal"):
        create_multi_layer_from_upload(
            io.BytesIO(buf.getvalue()), "evil.zip", "Evil",
            layers_config=[{"layer": "L1"}],
            **collaborators,
        )

    collaborators["db_save_file_source"].assert_not_called()


# =============================================================================
# 8. Parse failure per-layer: loop continua
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

    # 3 FileSource creati nonostante il fail del 2#
    assert len(out) == 3
    # Logger warning per il layer fallito
    warning_calls = [
        c.args[0] for c in collaborators["logger"].warning.call_args_list
        if "Errore inferenza colonne layer 'broken'" in c.args[0]
    ]
    assert len(warning_calls) == 1
    # Il 2# FileSource ha columns=[], row_count=0
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[1]["columns"] == []
    assert builds[1]["row_count"] == 0
    # Tutti i 3 db_save chiamati
    assert collaborators["db_save_file_source"].call_count == 3


# =============================================================================
# 9. db_save failure: propaga, atomicita' parziale residua (post-E1)
# =============================================================================


def test_db_save_failure_propagates_with_partial_atomicity(collaborators):
    """Post BE-HARDEN-001C E1: dispatch e' UNA volta pre-loop (atomico).
    L'unica fonte residua di partial-batch e' db_save fail. Se db_save
    fallisce al 2# layer -> propaga; il 1# gia' persistito, il 2# NO,
    il 3# NON viene processato. Atomicita' batch resta NON garantita per
    db_save (E3 affrontera' la transazione)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    # 1# db_save OK, 2# solleva
    collaborators["db_save_file_source"].side_effect = [
        None,
        RuntimeError("DB lock timeout on layer 2"),
    ]

    with pytest.raises(RuntimeError, match="DB lock timeout on layer 2"):
        create_multi_layer_from_upload(
            io.BytesIO(b"fake gpkg"), "data.gpkg", "Multi",
            layers_config=[
                {"layer": "L1"},
                {"layer": "L2"},
                {"layer": "L3"},
            ],
            group_id="g1",
            **collaborators,
        )

    # ATOMICITA' PARZIALE residua: 1# db_save persistito, 2# fallito,
    # 3# NON tentato.
    assert collaborators["db_save_file_source"].call_count == 2
    # E1: dispatch chiamato 1 volta (pre-loop), invariato anche su fail
    # del db_save.
    assert collaborators["dispatch_blob_storage"].call_count == 1
    # Cleanup eseguito comunque (finally)
    collaborators["cleanup_file"].assert_called()


# =============================================================================
# 10. group_id None -> default_group propagato a tutti
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

    # Tutti i build + db_save: group_id=default_grp
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert all(b["group_id"] == "default_grp" for b in builds)
    db_calls = [c.kwargs for c in collaborators["db_save_file_source"].call_args_list]
    assert all(c["group_id"] == "default_grp" for c in db_calls)


# =============================================================================
# 11. safe layer name sanitization
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

    # file_source_name = "Pack_<safe_layer>"; spazi e dash -> underscore
    builds = [c.kwargs for c in collaborators["build_file_source"].call_args_list]
    assert builds[0]["name"] == "Pack_my_layer_name"
    assert builds[1]["name"] == "Pack_another_one_with_spaces"
    # generate_file_id chiamato col file_source_name sanitized
    fid_calls = [c.args[0] for c in collaborators["generate_file_id"].call_args_list]
    assert fid_calls == ["Pack_my_layer_name", "Pack_another_one_with_spaces"]


# =============================================================================
# 12. Wrapper FileSourceService delega coi 16 collaborator
# =============================================================================


def test_file_service_wrapper_delegates_with_cleanup_file_os_unlink():
    """`FileSourceService.create_multi_layer_from_upload` thin wrapper:
    invoca file_multi_layer_upload con i 16 collaborator del service.
    Verifica che cleanup_file sia os.unlink (asimmetria preservata)."""
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
    # Verifica i 16 collaborator del service
    assert kwargs["generate_file_id"] is svc._generate_file_id
    assert kwargs["time_now"] is svc._time.now
    assert kwargs["detect_format"] is svc._detect_format
    assert kwargs["load_by_format"] is svc._load_by_format
    assert kwargs["column_builder_from_df"] is svc._column_builder.from_dataframe
    assert kwargs["dispatch_blob_storage"] is svc._dispatch_blob_storage
    assert callable(kwargs["db_save_file_source"])
    assert callable(kwargs["build_file_source"])
    # Cleanup ASIMMETRICO preservato:
    # cleanup_file = os.unlink (NON rmtree_safe)
    # cleanup_dir = rmtree_safe (NON os.unlink)
    assert kwargs["cleanup_file"] is os.unlink
    assert kwargs["cleanup_dir"] is not os.unlink  # diverso!
    assert callable(kwargs["safe_extractall"])
    assert isinstance(kwargs["zip_slip_error"], type)
    assert isinstance(kwargs["temp_dir"], (str, Path))
    assert isinstance(kwargs["default_group"], str)
    assert kwargs["logger"] is svc._logger


# =============================================================================
# 13. MULTI-GDB-001 fix: GDB directory in ZIP -> raw_data resta lo ZIP
# =============================================================================


def test_zip_unwrap_gdb_directory_uses_original_zip_blob(
    collaborators, tmp_path,
):
    """MULTI-GDB-001 fix: per GDB-in-ZIP il path estratto e' una directory
    `.gdb` (non un file). Pre-fix: `tmp_path.read_bytes()` su directory
    sollevava IsADirectoryError. Post-fix: per format='gdb' conserviamo
    il ZIP originale come blob (analogo a create_from_upload Seam D-F).

    Verifica:
      - dispatch_blob_storage riceve i bytes dello ZIP originale, NON quelli
        di un singolo file dentro la dir;
      - nessun IsADirectoryError viene propagato;
      - load_by_format chiamato col path della directory .gdb (per geopandas);
      - format='gdb' propagato al DTO.
    """
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    # detect_format: prima 'zip' (originale), poi 'gdb' (post unwrap)
    collaborators["detect_format"].side_effect = ["zip", "gdb"]

    # Simula safe_extractall che crea una directory .gdb dentro zip_tmp_dir.
    # safe_extractall callback: scrive UNA dir .gdb (non un file).
    def fake_extract(z, dest):
        gdb_dir = Path(dest) / "geodatabase.gdb"
        gdb_dir.mkdir()
        # Aggiungi un file dummy dentro (simula contenuto FileGDB reale)
        (gdb_dir / "a00000001.gdbtable").write_bytes(b"gdb internal")

    collaborators["safe_extractall"].side_effect = fake_extract

    # Costruiamo un mini-ZIP valido (placeholder: il contenuto reale viene
    # scritto da safe_extractall sopra; il ZIP serve solo a non triggerare
    # BadZipFile durante l'open).
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("placeholder.txt", b"placeholder content")
    zip_bytes = buf.getvalue()

    # NB: NON deve sollevare IsADirectoryError.
    out = create_multi_layer_from_upload(
        io.BytesIO(zip_bytes), "geodatabase.zip", "Geo",
        layers_config=[{"layer": "feature_class_1"}],
        group_id="g1",
        **collaborators,
    )

    # 1 FileSource creato (no exception)
    assert len(out) == 1

    # MULTI-GDB-001: dispatch_blob_storage riceve gli STESSI bytes del ZIP
    # originale, NON i bytes di un file dentro la dir .gdb.
    dispatch_args = collaborators["dispatch_blob_storage"].call_args.args
    # signature: (file_id, raw_data, filename)
    assert dispatch_args[1] == zip_bytes, (
        "MULTI-GDB-001 fix: per GDB il blob salvato deve essere il ZIP "
        "originale, non un file dentro la directory .gdb"
    )

    # load_by_format chiamato col path della directory .gdb (geopandas
    # legge la GDB tramite path della directory, non bytes).
    load_args = collaborators["load_by_format"].call_args.args
    assert load_args[0].name == "geodatabase.gdb"
    assert load_args[0].is_dir(), "Path passato a load_by_format deve essere la dir .gdb"

    # format='gdb' propagato al DTO
    build_kwargs = collaborators["build_file_source"].call_args.kwargs
    assert build_kwargs["format"] == "gdb"
    # Per GDB: layer valorizzato, sheet None
    assert build_kwargs["layer"] == "feature_class_1"
    assert build_kwargs["sheet"] is None
    # filename aggiornato a "geodatabase.gdb" post-unwrap
    assert build_kwargs["original_filename"] == "geodatabase.gdb"
    # file_size = lunghezza dello ZIP originale (non del file interno)
    assert build_kwargs["file_size"] == len(zip_bytes)


# =============================================================================
# 14. BE-HARDEN-001A: partial_batch_failure observability
# =============================================================================
#
# Solo log strutturato. NESSUN cambio semantico (atomicita' batch resta
# best-effort: MULTI-ATOMIC-001 resta aperto). Verifichiamo i 5 contratti
# richiesti dallo scope:
#   - dispatch fail al layer 2/3 -> log emesso
#   - db_save fail al layer 2/3 -> log emesso
#   - full success -> nessun log
#   - pre-loop failure (MIME) -> nessun log
#   - failure al primo layer (created_count=0) -> nessun log


def _warning_messages(logger_mock) -> List[str]:
    """Helper: estrae stringhe dei warning emessi (solo arg posizionale 0)."""
    return [c.args[0] for c in logger_mock.warning.call_args_list]


def _has_partial_batch_failure_log(logger_mock) -> bool:
    return any(
        "event=partial_batch_failure" in m for m in _warning_messages(logger_mock)
    )


def test_partial_batch_failure_log_on_db_save_failure_at_layer_two_of_three(
    collaborators,
):
    """Layer 1 OK, Layer 2 db_save fail (created_count=1, expected=3) ->
    log partial_batch_failure emesso con failed_phase=db_save."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    collaborators["db_save_file_source"].side_effect = [
        None,  # layer 1 OK
        RuntimeError("DB lock timeout"),
    ]

    with pytest.raises(RuntimeError, match="DB lock timeout"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"}],
            group_id="g1",
            **collaborators,
        )

    msgs = _warning_messages(collaborators["logger"])
    pbf = next(m for m in msgs if "event=partial_batch_failure" in m)
    assert "expected_count=3" in pbf
    assert "created_count=1" in pbf
    assert "failed_layer='L2'" in pbf
    assert "failed_phase=db_save" in pbf


def test_no_partial_batch_failure_log_on_full_success(collaborators):
    """Tutti i N layer OK -> nessun log partial_batch_failure."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"

    out = create_multi_layer_from_upload(
        io.BytesIO(b"x"), "data.gpkg", "Multi",
        layers_config=[{"layer": "L1"}, {"layer": "L2"}],
        group_id="g1",
        **collaborators,
    )

    assert len(out) == 2
    assert not _has_partial_batch_failure_log(collaborators["logger"])


def test_no_partial_batch_failure_log_on_mime_block(collaborators):
    """MIME block pre-loop -> created_count=0, NO log partial_batch_failure
    (e' failure pre-batch, non parziale)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload
    from app.utils.mime_validator import MimeMismatchError

    collaborators["mime_validate"].side_effect = MimeMismatchError("executable")

    with pytest.raises(ValueError, match="executable"):
        create_multi_layer_from_upload(
            io.BytesIO(b"MZ exe"), "trojan.gpkg", "Trojan",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
            **collaborators,
        )

    # MIME warning loggato MA NON il partial_batch_failure
    assert not _has_partial_batch_failure_log(collaborators["logger"])


def test_no_partial_batch_failure_log_on_first_db_save_failure(collaborators):
    """Failure al PRIMO db_save prima di created.append (created_count=0):
    NO log partial_batch_failure (la condizione `0 < created_count < expected`
    non e' soddisfatta).

    Post BE-HARDEN-001C E1: la sola fonte residua di in-loop fail e'
    db_save (dispatch e' pre-loop atomico, vedi test
    `test_dispatch_failure_before_loop_no_db_save_no_partial_log`)."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    # db_save fail SUBITO sul primo layer
    collaborators["db_save_file_source"].side_effect = RuntimeError(
        "DB lock on layer 1"
    )

    with pytest.raises(RuntimeError, match="DB lock on layer 1"):
        create_multi_layer_from_upload(
            io.BytesIO(b"x"), "data.gpkg", "Multi",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}, {"layer": "L3"}],
            group_id="g1",
            **collaborators,
        )

    # Nessun layer persistito -> NO log partial (failure pre-first-save).
    assert not _has_partial_batch_failure_log(collaborators["logger"])


def test_no_partial_batch_failure_log_on_bad_zip_file(collaborators):
    """BadZipFile pre-loop -> NO log partial_batch_failure."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "zip"
    bad_bytes = b"this is not a zip"

    with pytest.raises(ValueError, match="ZIP non valido"):
        create_multi_layer_from_upload(
            io.BytesIO(bad_bytes), "fake.zip", "Bad",
            layers_config=[{"layer": "L1"}, {"layer": "L2"}],
            **collaborators,
        )

    assert not _has_partial_batch_failure_log(collaborators["logger"])


# =============================================================================
# 15. BE-HARDEN-001C E1: shared blob dispatch (1 dispatch, N db_save)
# =============================================================================
#
# Verifica che multi-layer upload faccia 1 sola chiamata a
# dispatch_blob_storage (PRE-LOOP) con `storage_key_id=batch_id`, e che
# le N db_save_file_source ricevano gli stessi `shared_blob_kwargs`
# (storage_key condiviso in object_storage path; data condiviso in BYTEA
# path). MULTI-BLOB-001 si chiude con questi contratti.


def test_multi_layer_uses_single_dispatch_with_batch_id_storage_key(collaborators):
    """3 layer -> dispatch_blob_storage chiamato 1 volta PRIMA del loop.
    Argomenti: file_id e storage_key_id sono entrambi un `batch_*`
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

    # 1 sola chiamata a dispatch
    assert collaborators["dispatch_blob_storage"].call_count == 1
    # db_save chiamato N volte (1 per layer)
    assert collaborators["db_save_file_source"].call_count == 3

    # Argomenti: file_id e storage_key_id == lo stesso batch_id opaque
    call = collaborators["dispatch_blob_storage"].call_args
    file_id_arg = call.args[0]
    storage_key_id_kwarg = call.kwargs.get("storage_key_id")
    assert file_id_arg.startswith("batch_"), (
        f"E1 expected opaque batch_id, got {file_id_arg!r}"
    )
    assert storage_key_id_kwarg == file_id_arg, (
        "E1: storage_key_id must equal the batch_id used as positional file_id"
    )
    # batch_id NON deve coincidere con i file_id dei layer (sono `file_<name>`
    # via fixture generate_file_id, mentre batch_id e' `batch_<hex>`)
    assert not file_id_arg.startswith("file_")


def test_n_db_save_calls_share_same_storage_key(collaborators):
    """In object_storage path, le N db_save ricevono lo STESSO storage_key.
    Verifica MULTI-BLOB-001 fix: 1 blob fisico, N row che lo referenziano."""
    from app.services.file_multi_layer_upload import create_multi_layer_from_upload

    collaborators["detect_format"].return_value = "gpkg"
    # Simula object_storage mode: dispatch ritorna data=None + storage_key
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

    # Tutte le 3 db_save ricevono lo stesso storage_key, data, size_bytes
    db_calls = [c.kwargs for c in collaborators["db_save_file_source"].call_args_list]
    assert len(db_calls) == 3
    storage_keys = {c["storage_key"] for c in db_calls}
    assert storage_keys == {"file-sources/batch_xyz/data.gpkg"}, (
        f"E1: tutte le N row devono condividere lo storage_key, got {storage_keys}"
    )
    datas = {c["data"] for c in db_calls}
    assert datas == {None}
    sizes = {c["size_bytes"] for c in db_calls}
    assert sizes == {1024}
    # file_id PER-LAYER resta distinto (le N row sono entita' DB distinte
    # con shared blob)
    file_ids = [c["file_id"] for c in db_calls]
    assert len(set(file_ids)) == 3, (
        f"E1: file_id deve restare PER-LAYER (DB row distinte), got {file_ids}"
    )


def test_dispatch_failure_before_loop_no_db_save_no_partial_log(collaborators):
    """E1: se dispatch fallisce PRE-LOOP, 0 db_save chiamati e nessun
    log partial_batch_failure (created_count=0 -> condizione 0 < N <
    expected non soddisfatta). La eccezione propaga al caller."""
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

    # 1 dispatch (tentato e fallito), 0 db_save, 0 build_file_source
    assert collaborators["dispatch_blob_storage"].call_count == 1
    collaborators["db_save_file_source"].assert_not_called()
    collaborators["build_file_source"].assert_not_called()
    # Nessun log partial (created_count=0)
    assert not _has_partial_batch_failure_log(collaborators["logger"])
    # Cleanup eseguito (finally branch)
    collaborators["cleanup_file"].assert_called()
