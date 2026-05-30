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


# Fix 2 (rimosso): `imdf/loader` interamente tombstonato col cutover a
# plenora.indoor.bundle — niente piu' extract_imdf_zip*/GDB, quindi nessun
# tmp-leak da estrazione, by-construction.


# Fix 3: api_connector._rest_file _cleanup_tmp on error path


# Post-Phase4 (RAM-only): _temp_file_for + branch SHP/GDB rimossi.
# Il leak su tmp_dir e' impossibile by-construction perche' il service
# non scrive mai blob su disco. Tombstone esplicito per anti-regressione.


def test_temp_file_for_tombstone_post_phase4():
    """`FileSourceService._temp_file_for` e' stato rimosso (Phase4 RAM-only).
    I consumer leggono il blob via `_get_blob_from_db` e poi delegano
    a parser bytes-first (load_by_format_bytes / list_vector_layers)."""
    from app.core.files.service import FileSourceService

    assert not hasattr(FileSourceService, "_temp_file_for"), (
        "FileSourceService._temp_file_for resta presente: refactor "
        "RAM-only incompleto. Rimuoverlo elimina by-construction il "
        "leak su tmp_dir nei branch SHP/GDB."
    )


def test_rest_file_cleans_up_tmp_dir_on_http_error_path():
    """Previous behavior: errore HTTP / response vuota / download_timed_out
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

    # B3-B.5a current contract: `requests.post` patchato sul source canonico
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
