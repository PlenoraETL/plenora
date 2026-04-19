"""Lock tests per ApiConnectionService._execute_rest_file.

Scopo: congelare il contratto osservabile di `_execute_rest_file` prima
del refactor C2 (estrazione helpers dalla funzione da 284 righe).

Collaborator mockati:
  - self._prepare_headers
  - self._validate_outbound_url
  - self._resolve_file_source_path
  - self._resolve_file_source_id_path
  - requests.post (via il modulo `requests` importato locally dentro la funzione)
  - tempfile.mkdtemp → Path del tmp_exec_dir
  - `open` per lettura input file / scrittura output file

I test verificano input/output della funzione (success dict o error dict)
e l'invariante "cleanup tmp_exec_dir su error, no cleanup su success".
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


# =============================================================================
# Builders
# =============================================================================


def _api_config(
    url="https://svc.example.test/process",
    method="POST",
    file_fields=None,
    static_fields=None,
    timeout=600,
    response_format="binary_file",
    output_extension=".zip",
    auth=None,
):
    return {
        "endpoint": {"url": url, "method": method},
        "auth": auth or {},
        "request": {
            "file_fields": file_fields or [],
            "static_fields": static_fields or [],
            "timeout": timeout,
        },
        "response": {
            "format": response_format,
            "output_extension": output_extension,
        },
    }


def _mock_http_response(status_code=200, body_chunks=None, json_body=None, text=""):
    """Finto response di requests.post (stream=True)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text

    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("not json")

    chunks = body_chunks if body_chunks is not None else [b""]
    resp.iter_content.return_value = iter(chunks)
    resp.close = MagicMock()
    return resp


# =============================================================================
# Fixture
# =============================================================================


@pytest.fixture
def rf_env(api_service, tmp_path):
    """Fixture per _execute_rest_file: patcha collaborator esterni e
    filesystem (tempfile.mkdtemp → tmp_path/exec_dir).
    """
    exec_dir = tmp_path / "exec_dir"
    exec_dir.mkdir()

    # Creo un file di input fittizio cosi' open('rb') funziona
    input_file = tmp_path / "input.dxf"
    input_file.write_bytes(b"dxf-content")

    stack = ExitStack()

    def _p(target, **kwargs):
        return stack.enter_context(patch(target, **kwargs))

    def _po(attr, **kwargs):
        return stack.enter_context(patch.object(api_service, attr, **kwargs))

    with stack:
        mocks = SimpleNamespace(
            svc=api_service,
            exec_dir=exec_dir,
            input_file=input_file,
            prepare_headers=_po("_prepare_headers", return_value={"Authorization": "Bearer x"}),
            ssrf=_po("_validate_outbound_url", return_value=None),  # None = OK
            resolve_path=_po("_resolve_file_source_path", return_value=str(input_file)),
            resolve_id_path=_po("_resolve_file_source_id_path"),
            mkdtemp=_p("tempfile.mkdtemp", return_value=str(exec_dir)),
            requests_post=_p("requests.post"),
        )
        # Default response: 200, bytes validi
        mocks.requests_post.return_value = _mock_http_response(
            status_code=200, body_chunks=[b"output-bytes"]
        )
        yield mocks


# =============================================================================
# Section 1: validazione config (endpoint, method, format, SSRF)
# =============================================================================


class TestExecuteRestFileConfigValidation:
    def test_missing_endpoint_url_returns_error(self, rf_env):
        cfg = _api_config(url=None)
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "Endpoint URL mancante" in result["error"]

    def test_non_post_method_returns_error(self, rf_env):
        cfg = _api_config(method="GET")
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "non supportato" in result["error"]
        assert "GET" in result["error"]

    def test_ssrf_block_returns_error(self, rf_env):
        rf_env.ssrf.return_value = "IP privato 10.0.0.1"
        cfg = _api_config(url="http://10.0.0.1/process")
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "URL non consentito" in result["error"]
        assert "IP privato" in result["error"]

    def test_unsupported_response_format_returns_error(self, rf_env):
        cfg = _api_config(response_format="json_array")
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "json_array" in result["error"]

    def test_auth_preparation_failure_returns_error(self, rf_env):
        rf_env.prepare_headers.side_effect = RuntimeError("oauth token expired")
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "Preparazione auth fallita" in result["error"]
        assert "oauth token expired" in result["error"]


# =============================================================================
# Section 2: file fields (node_input, extra_input, required vs optional)
# =============================================================================


class TestExecuteRestFileFileFields:
    def test_required_node_input_missing_returns_error(self, rf_env):
        rf_env.resolve_path.return_value = None  # nessun file collegato
        cfg = _api_config(
            file_fields=[{"name": "file", "source": "node_input", "required": True}]
        )
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "'file'" in result["error"]
        assert "richiesto" in result["error"]

    def test_optional_node_input_missing_continues(self, rf_env):
        rf_env.resolve_path.return_value = None
        cfg = _api_config(
            file_fields=[{"name": "file", "source": "node_input", "required": False}],
            static_fields=[],
        )
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        # Non fa short-circuit, prosegue e chiama requests.post
        rf_env.requests_post.assert_called_once()
        assert result["success"] is True

    def test_required_extra_input_missing_returns_error(self, rf_env):
        cfg = _api_config(
            file_fields=[{"name": "excel", "source": "extra_input", "required": True}],
        )
        # node_data senza extra_files configurato
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "'excel'" in result["error"]
        assert "non configurato" in result["error"]

    def test_extra_input_file_source_not_resolvable_required(self, rf_env, tmp_path):
        rf_env.resolve_id_path.return_value = None  # fs non risolvibile
        cfg = _api_config(
            file_fields=[{"name": "excel", "source": "extra_input", "required": True}],
        )
        node_data = {"extra_files": {"excel": "fs-missing"}}
        result = rf_env.svc._execute_rest_file(
            node_data, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "non trovato" in result["error"] or "fs-missing" in result["error"]


# =============================================================================
# Section 3: HTTP error response parsing
# =============================================================================


class TestExecuteRestFileHttpErrors:
    def test_http_400_with_json_detail_extracts_detail(self, rf_env):
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=400,
            json_body={"detail": "invalid dxf schema"},
        )
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "HTTP 400" in result["error"]
        assert "invalid dxf schema" in result["error"]

    def test_http_500_with_text_body_fallback(self, rf_env):
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=500, text="upstream crashed"
        )
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "HTTP 500" in result["error"]
        assert "upstream crashed" in result["error"]


# =============================================================================
# Section 4: download response
# =============================================================================


class TestExecuteRestFileDownload:
    def test_empty_response_body_returns_error(self, rf_env):
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=200, body_chunks=[b""]
        )
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "vuota" in result["error"]

    def test_successful_download_returns_is_file_result(self, rf_env):
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=200,
            body_chunks=[b"chunk1-", b"chunk2"],
        )
        cfg = _api_config(output_extension=".zip")
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is True
        assert result["is_file_result"] is True
        assert str(result["output_path"]).endswith(".zip")
        assert result["metadata"]["content_length"] == len(b"chunk1-chunk2")
        assert result["metadata"]["status_code"] == 200
        assert "execution_time_ms" in result


# =============================================================================
# Section 5: exception handling + tmp cleanup
# =============================================================================


class TestExecuteRestFileExceptions:
    def test_requests_timeout_returns_error(self, rf_env):
        import requests as _req
        rf_env.requests_post.side_effect = _req.Timeout("read timeout")
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "Timeout" in result["error"]

    def test_requests_exception_returns_error(self, rf_env):
        import requests as _req
        rf_env.requests_post.side_effect = _req.ConnectionError("dns fail")
        cfg = _api_config()
        result = rf_env.svc._execute_rest_file(
            {}, cfg, [], [], "n1", "p1"
        )
        assert result["success"] is False
        assert "Errore HTTP" in result["error"]

    def test_tmp_exec_dir_cleanup_on_error_path(self, rf_env):
        """Su error path (es. empty response), la dir tmp_exec_dir deve
        essere rimossa dal finally. Senza cleanup, worker long-running
        accumulerebbero dirs plenora_exec_*."""
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=200, body_chunks=[b""]  # empty → error
        )
        cfg = _api_config()
        rf_env.svc._execute_rest_file({}, cfg, [], [], "n1", "p1")

        # exec_dir era tmp_path/exec_dir (vedi fixture); dopo l'error path
        # deve essere stato rimosso dal finally.
        assert not rf_env.exec_dir.exists(), (
            "tmp_exec_dir non ripulita dopo error path"
        )

    def test_tmp_exec_dir_preserved_on_success_path(self, rf_env):
        """Su success path, tmp_exec_dir NON deve essere rimossa perche'
        l'output_path restituito punta al suo interno e il caller e'
        responsabile del cleanup downstream."""
        rf_env.requests_post.return_value = _mock_http_response(
            status_code=200, body_chunks=[b"payload"]
        )
        cfg = _api_config()
        rf_env.svc._execute_rest_file({}, cfg, [], [], "n1", "p1")

        assert rf_env.exec_dir.exists(), (
            "tmp_exec_dir rimossa su success path (cleanup prematuro)"
        )
