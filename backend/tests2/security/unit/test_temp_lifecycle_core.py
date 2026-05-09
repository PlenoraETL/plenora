"""
Core-level test per Refactor #4 Step C: temp file lifecycle.

Verifica che i call site identificati nell'audit non leakino
"""

from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Fix 2: imdf/loader.extract_imdf_zip cleanup tmp on extraction error


def test_extract_imdf_zip_cleans_up_on_zip_error(tmp_path):
    """Zip corrotto/illeggibile dopo mkdtemp â†’ tmp dir non deve restare
    sul disco. Pre-fix l'eccezione di safe_extractall lasciava la dir
    creata in TEMP."""
    from app.core.imdf.loader import extract_imdf_zip

    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip file at all")

    import tempfile as _tf
    temp_root = tmp_path / "temp-root"
    temp_root.mkdir()

    def _mkdtemp(prefix: str = "") -> str:
        path = temp_root / f"{prefix}owned_zip_error"
        path.mkdir()
        return str(path)

    # Isola il test dal temp globale di Windows: il contratto resta
    # "extract_imdf_zip pulisce la tempdir che crea".
    before = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}

    with patch.object(_tf, "mkdtemp", side_effect=_mkdtemp), pytest.raises(Exception):
        extract_imdf_zip(bad_zip)

    after = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}
    new_dirs = after - before
    assert not new_dirs, (
        f"extract_imdf_zip deve cleanup tmp su error, ma sono rimaste: {new_dirs}"
    )


def test_extract_imdf_zip_cleans_up_on_invalid_imdf_content(tmp_path):
    """Zip valido ma contenuto non IMDF (manca .gdb e manifest.json)
    â†’ ValueError dopo l'estrazione, tmp dir deve essere pulita."""
    import zipfile
    from app.core.imdf.loader import extract_imdf_zip

    not_imdf_zip = tmp_path / "fake.zip"
    with zipfile.ZipFile(not_imdf_zip, "w") as zf:
        zf.writestr("readme.txt", "this is not imdf")

    import tempfile as _tf
    temp_root = tmp_path / "temp-root"
    temp_root.mkdir()

    def _mkdtemp(prefix: str = "") -> str:
        path = temp_root / f"{prefix}owned_invalid_content"
        path.mkdir()
        return str(path)

    before = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}

    with patch.object(_tf, "mkdtemp", side_effect=_mkdtemp), pytest.raises(
        ValueError, match="non sembra un IMDF"
    ):
        extract_imdf_zip(not_imdf_zip)

    after = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}
    new_dirs = after - before
    assert not new_dirs, (
        f"extract_imdf_zip deve cleanup tmp su validazione fallita, "
        f"ma sono rimaste: {new_dirs}"
    )


def test_extract_imdf_zip_does_not_cleanup_caller_provided_dest(tmp_path):
    """Sanity: se il caller fornisce dest_dir esplicito, NON dobbiamo
    cleanarlo su errore (la responsabilita' e' del caller)."""
    from app.core.imdf.loader import extract_imdf_zip

    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip")

    caller_dest = tmp_path / "caller_owned"
    caller_dest.mkdir()
    (caller_dest / "marker.txt").write_text("caller wrote this", encoding="utf-8")

    with pytest.raises(Exception):
        extract_imdf_zip(bad_zip, dest_dir=caller_dest)

    # caller_dest deve esistere ancora â€” non l'abbiamo cleanata
    assert caller_dest.exists()
    assert (caller_dest / "marker.txt").exists()


# Fix 3: api_connector._rest_file _cleanup_tmp on error path


# Fix bonus: file_service._temp_file_for branch SHP/GDB cleanup


def test_temp_file_for_cleans_up_tmp_dir_when_zip_extraction_fails(tmp_path):
    """Pre-fix: il branch SHP/GDB di `_temp_file_for` faceva mkdtemp +
    try/except che logga errore senza cleanup. Una qualsiasi exception
    di safe_extractall lasciava la tmp dir su disco. Ora cleanup
    garantito su entrambi i path errore (exception + nessun shp/gdb
    trovato)."""
    from app.core.files.service import FileSourceService
    from app.config import settings

    svc = FileSourceService.__new__(FileSourceService)
    # Mock minimo: logger e dipendenze richieste
    import logging
    svc._logger = logging.getLogger(__name__)

    # Patch _get_blob_from_db / get_file_source per restituire un blob
    # non-zip (fallisce safe_extractall) con format="shp"
    fake_meta = {"format": "shp", "original_filename": "test.shp"}
    fake_blob = b"not a zip at all"

    temp_root = Path(settings.TEMP_DIR)
    before = {d.name for d in temp_root.iterdir() if d.is_dir()}

    # N1: _temp_file_for ora chiama _assert_visible. Bypass con sentinel
    # superadmin per isolare il test sul lifecycle temp â€” il guard
    # cross-tenant e' testato altrove.
    from app.core.files.service import FileSourceService
    with patch.object(svc, "_get_blob_from_db", return_value=fake_blob), \
         patch("app.core.files.db.get_file_source", return_value=fake_meta), \
         patch.object(svc, "_assert_visible", return_value=None):
        result = svc._temp_file_for(
            "fs-1", expected_group_id=FileSourceService.SUPERADMIN_BYPASS,
        )

    assert result is None, "blob non-zip deve causare return None"

    after = {d.name for d in temp_root.iterdir() if d.is_dir()}
    new_dirs = after - before
    assert not new_dirs, (
        f"_temp_file_for deve cleanup tmp_dir su zip extraction error, "
        f"ma sono rimaste: {new_dirs}"
    )


def test_temp_file_for_cleans_up_tmp_dir_when_no_shp_inside_valid_zip(tmp_path):
    """Caso edge: zip valido ma senza .shp/.gdb dentro â†’ return None
    e cleanup. Pre-fix: il fall-through al `return None` lasciava la
    tmp_dir creata."""
    import zipfile as _zipfile
    import io
    from app.core.files.service import FileSourceService
    from app.config import settings

    svc = FileSourceService.__new__(FileSourceService)
    import logging
    svc._logger = logging.getLogger(__name__)

    # Costruiamo un zip valido che NON contiene shp/gdb
    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    fake_blob = buf.getvalue()

    fake_meta = {"format": "shp", "original_filename": "test.shp"}

    temp_root = Path(settings.TEMP_DIR)
    before = {d.name for d in temp_root.iterdir() if d.is_dir()}

    # N1: stesso bypass del test sopra
    from app.core.files.service import FileSourceService
    with patch.object(svc, "_get_blob_from_db", return_value=fake_blob), \
         patch("app.core.files.db.get_file_source", return_value=fake_meta), \
         patch.object(svc, "_assert_visible", return_value=None):
        result = svc._temp_file_for(
            "fs-1", expected_group_id=FileSourceService.SUPERADMIN_BYPASS,
        )

    assert result is None, "zip senza .shp deve causare return None"

    after = {d.name for d in temp_root.iterdir() if d.is_dir()}
    new_dirs = after - before
    assert not new_dirs, (
        f"_temp_file_for deve cleanup tmp_dir quando zip non contiene "
        f".shp/.gdb, ma sono rimaste: {new_dirs}"
    )


def test_rest_file_cleans_up_tmp_dir_on_http_error_path():
    """Pre-fix: errore HTTP / response vuota / download_timed_out
    ritornavano dict di errore senza cleanup tmp_exec_dir â†’ leak.
    Ora cleanup garantito da finally + _cleanup_tmp flag.

    Il test verifica end-to-end che NON resti una plenora_exec_* in TEMP
    dopo un'invocazione che fallisce."""
    import tempfile as _tf
    from unittest.mock import MagicMock
    from app.core.arricchisci.api_connector import ApiConnectionService

    svc = ApiConnectionService.__new__(ApiConnectionService)
    svc._current_group_id = "grp-1"

    api_config = {
        "endpoint": {"url": "https://example.test/process", "method": "POST"},
        "auth": {},
        "request": {"file_fields": [], "static_fields": [], "timeout": 30},
        "response": {"format": "binary_file", "output_extension": ".zip"},
    }
    node_data = {}
    nodes = []
    edges = []
    node_id = "n-1"

    temp_root = Path(_tf.gettempdir())
    before = {d.name for d in temp_root.iterdir() if d.name.startswith("plenora_exec_")}

    # Mock requests.post per simulare un 500 dal server remoto
    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.text = "internal error"
    fake_response.json.side_effect = Exception("not json")
    fake_response.iter_content.return_value = iter([])

    # B3-B.5a modernization: `requests.post` patchato sul source canonico
    # invece dell'alias `app.core.arricchisci.api_connector.requests.post`.
    # `_shared_prepare_headers` resta source-bound al modulo api_connector
    # perche' `_prepare_headers` (3-line shim) resta in core (vedi B3-B.3
    # mini-scan: AUTH/UTILITY non estratto).
    with patch("app.core.arricchisci.api_connector._shared_prepare_headers", return_value={}), \
         patch("requests.post", return_value=fake_response):
        result = svc._execute_rest_file(node_data, api_config, nodes, edges, node_id, "proj-1")

    assert result["success"] is False

    after = {d.name for d in temp_root.iterdir() if d.name.startswith("plenora_exec_")}
    new_dirs = after - before
    assert not new_dirs, (
        f"_rest_file deve cleanup tmp_exec_dir su errore HTTP, "
        f"ma sono rimaste: {new_dirs}"
    )
