"""Core-level test per Refactor #4 Step C: temp file lifecycle.

Verifica che i 3 call site identificati nell'audit non leakino
tempfile su path errore:

1. `arricchisci/api_connector._rest_file` — `mkdtemp(prefix="plenora_exec_")`
   senza cleanup nel finally pre-fix; ora `_cleanup_tmp` flag + cleanup
   garantito su tutte le eccezioni del try block.

2. `imdf/loader.extract_imdf_zip` — `mkdtemp(prefix="imdf_")` senza
   cleanup se l'estrazione o l'indicizzazione (validazione "non sembra
   IMDF") falliscono. Ora try/except che cleanup la tmp dir creata,
   solo se `_we_own_tmp` (caller non ha passato dest_dir esplicito).

3. `artifacts/resolver._write_temp_blob` — `NamedTemporaryFile(delete=False)`
   senza cleanup su exception di `tf.write(blob)`. Ora unlink esplicito
   prima di re-raise.

Test core-level: chiamano direttamente le funzioni, niente HTTP,
niente CurrentUser.
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


# ---------------------------------------------------------------------------
# Fix 1: artifacts/resolver._write_temp_blob unlink su errore
# ---------------------------------------------------------------------------


def test_write_temp_blob_cleans_up_on_write_error():
    """Pre-fix: NamedTemporaryFile(delete=False) + write che esplode
    lasciava un file vuoto. Ora unlink esplicito prima di re-raise."""
    from app.core.artifacts.resolver import ArtifactResolver

    resolver = ArtifactResolver()

    captured_path = {}

    real_named_tempfile = __import__("tempfile").NamedTemporaryFile

    def _wrapped_tempfile(*args, **kwargs):
        tf = real_named_tempfile(*args, **kwargs)
        captured_path["name"] = tf.name
        # Sostituiamo write con uno che esplode
        original_write = tf.write

        def _boom(_blob):
            raise OSError("disk full simulato")

        tf.write = _boom
        return tf

    with patch("app.core.artifacts.resolver.tempfile.NamedTemporaryFile",
               side_effect=_wrapped_tempfile):
        with pytest.raises(OSError, match="disk full"):
            resolver._write_temp_blob(b"some-blob", "csv")

    # Il path deve essere stato creato MA poi rimosso dal cleanup
    assert "name" in captured_path
    assert not Path(captured_path["name"]).exists(), (
        "_write_temp_blob deve cleanup il tempfile su errore di write"
    )


def test_write_temp_blob_returns_path_on_success(tmp_path):
    """Sanity: il path success funziona ancora dopo il refactor."""
    from app.core.artifacts.resolver import ArtifactResolver

    resolver = ArtifactResolver()
    path = resolver._write_temp_blob(b"id\n1\n", "csv")
    try:
        assert path.exists()
        assert path.read_bytes() == b"id\n1\n"
        assert path.suffix == ".csv"
    finally:
        path.unlink(missing_ok=True)


def test_write_temp_blob_uses_settings_temp_dir(tmp_path, monkeypatch):
    """R#4 Step C cleanup: i tempfile dell'artifact resolver devono
    essere creati dentro settings.TEMP_DIR per coerenza con file_service
    e con il resto del backend, non nel temp di sistema. Pre-fix
    venivano sparpagliati nel default Python tempfile."""
    from app.core.artifacts.resolver import ArtifactResolver
    from types import SimpleNamespace

    custom_temp = tmp_path / "urby_temp"
    custom_temp.mkdir()

    fake_settings = SimpleNamespace(TEMP_DIR=custom_temp)
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "settings", fake_settings, raising=False)

    resolver = ArtifactResolver()
    path = resolver._write_temp_blob(b"id\n1\n", "csv")
    try:
        assert path.exists()
        # Il tempfile deve vivere DENTRO custom_temp, non nel system temp
        assert path.parent.resolve() == custom_temp.resolve(), (
            f"Atteso tempfile dentro {custom_temp}, trovato in {path.parent}"
        )
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fix 2: imdf/loader.extract_imdf_zip cleanup tmp on extraction error
# ---------------------------------------------------------------------------


def test_extract_imdf_zip_cleans_up_on_zip_error(tmp_path):
    """Zip corrotto/illeggibile dopo mkdtemp → tmp dir non deve restare
    sul disco. Pre-fix l'eccezione di safe_extractall lasciava la dir
    creata in TEMP."""
    from app.core.imdf.loader import extract_imdf_zip

    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip file at all")

    # Conto le subdir di TEMP_DIR prima del test (filtrate per prefix)
    import tempfile as _tf
    temp_root = Path(_tf.gettempdir())
    before = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}

    with pytest.raises(Exception):
        extract_imdf_zip(bad_zip)

    after = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}
    new_dirs = after - before
    assert not new_dirs, (
        f"extract_imdf_zip deve cleanup tmp su error, ma sono rimaste: {new_dirs}"
    )


def test_extract_imdf_zip_cleans_up_on_invalid_imdf_content(tmp_path):
    """Zip valido ma contenuto non IMDF (manca .gdb e manifest.json)
    → ValueError dopo l'estrazione, tmp dir deve essere pulita."""
    import zipfile
    from app.core.imdf.loader import extract_imdf_zip

    not_imdf_zip = tmp_path / "fake.zip"
    with zipfile.ZipFile(not_imdf_zip, "w") as zf:
        zf.writestr("readme.txt", "this is not imdf")

    import tempfile as _tf
    temp_root = Path(_tf.gettempdir())
    before = {d.name for d in temp_root.iterdir() if d.name.startswith("imdf_")}

    with pytest.raises(ValueError, match="non sembra un IMDF"):
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

    # caller_dest deve esistere ancora — non l'abbiamo cleanata
    assert caller_dest.exists()
    assert (caller_dest / "marker.txt").exists()


# ---------------------------------------------------------------------------
# Fix 3: api_connector._rest_file _cleanup_tmp on error path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fix bonus: file_service._temp_file_for branch SHP/GDB cleanup
# ---------------------------------------------------------------------------


def test_temp_file_for_cleans_up_tmp_dir_when_zip_extraction_fails(tmp_path):
    """Pre-fix: il branch SHP/GDB di `_temp_file_for` faceva mkdtemp +
    try/except che logga errore senza cleanup. Una qualsiasi exception
    di safe_extractall lasciava la tmp dir su disco. Ora cleanup
    garantito su entrambi i path errore (exception + nessun shp/gdb
    trovato)."""
    import tempfile as _tf
    from app.services.file_service import FileSourceService
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
    # superadmin per isolare il test sul lifecycle temp — il guard
    # cross-tenant e' testato altrove.
    from app.services.file_service import FileSourceService
    with patch.object(svc, "_get_blob_from_db", return_value=fake_blob), \
         patch("app.db.db_service.get_file_source", return_value=fake_meta), \
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
    """Caso edge: zip valido ma senza .shp/.gdb dentro → return None
    e cleanup. Pre-fix: il fall-through al `return None` lasciava la
    tmp_dir creata."""
    import tempfile as _tf
    import zipfile as _zipfile
    import io
    from app.services.file_service import FileSourceService
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
    from app.services.file_service import FileSourceService
    with patch.object(svc, "_get_blob_from_db", return_value=fake_blob), \
         patch("app.db.db_service.get_file_source", return_value=fake_meta), \
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
    ritornavano dict di errore senza cleanup tmp_exec_dir → leak.
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
