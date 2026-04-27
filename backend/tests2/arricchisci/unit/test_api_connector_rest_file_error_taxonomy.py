"""Contract test F5 primo batch (2026-04-24) — `_execute_rest_file` error taxonomy.

Blinda: tutti i return di errore di `ApiConnectionService._execute_rest_file`
portano un campo `error_kind` ∈ {file_source_not_found, file_source_forbidden,
remote_processor_error, internal} + `user_error: bool`, non solo il messaggio
opaco unico `"Errore interno nel processore file remoto"`.

Motivazione storica: il caso reale osservato 2026-04-23 su
`test_imdf_20260413_034050_bee2e47a` / `api_enrich_1776085234269` aveva
un `file_source_id` referenziato nel canvas ma non esistente nel DB.
Il backend catturava via except Exception generico e propagava
"Errore interno nel processore file remoto" — indistinguibile da un
vero errore upstream IMDF. Dopo F5 primo batch, l'error_kind
`file_source_not_found` rende la diagnosi immediata.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.arricchisci.api_connector import ApiConnectionService


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture(autouse=True)
def _ensure_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost:5432/fake")
    yield


@pytest.fixture
def service(monkeypatch):
    svc = ApiConnectionService()
    # Imposta group_id per evitare che _assert_file_source_group_access
    # fallisca con "Contesto di sicurezza non determinabile"
    svc._current_group_id = "default"
    # Bypass SSRF guard per i test: usiamo hostname fittizi che non
    # risolverebbero. La logica SSRF e' testata altrove
    # (tests2/security/unit/test_validate_external_url_contract.py).
    monkeypatch.setattr(svc, "_validate_outbound_url", lambda url: None)
    return svc


@pytest.fixture
def minimal_api_config():
    return {
        "endpoint": {"url": "https://imdf.plenora.test/api/process", "method": "POST"},
        "auth": {"type": "none"},
        "request": {
            "body_type": "multipart",
            "file_fields": [
                {"name": "excel", "source": "extra_input", "required": True, "accept": ".xlsx"},
            ],
            "timeout": 60,
        },
        "response": {"format": "binary_file", "output_extension": ".zip"},
    }


# ─── error_kind: file_source_not_found ─────────────────────────────────────


def test_file_source_not_found_returns_structured_error_kind(service, minimal_api_config):
    """Bug reale 2026-04-23: file_source_id referenziato nel canvas non esiste
    nel DB. `_resolve_file_source_id_path` ritorna None (via row_meta None).
    Il build multipart deve returnare error_kind='file_source_not_found'."""
    node_data = {
        "extra_files": {
            "excel": {
                "file_source_id": "file_extra_excel_INEXISTENT_20260413_000000_deadbeef",
                "original_filename": "missing.xlsx",
            }
        },
    }

    # _resolve_file_source_id_path ritorna None → caso file_source_not_found
    with patch.object(service, "_resolve_file_source_id_path", return_value=None):
        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert result["success"] is False
    assert result["error_kind"] == "file_source_not_found"
    assert result["user_error"] is True
    assert "file_source" in result["error"].lower()
    # Anti-regression: NON deve più essere il messaggio opaco unico
    assert result["error"] != "Errore interno nel processore file remoto"


# ─── error_kind: file_source_forbidden ─────────────────────────────────────


def test_file_source_forbidden_returns_structured_error_kind(service, minimal_api_config):
    """Cross-tenant: `_assert_file_source_group_access` solleva ValueError
    con prefisso 'Accesso negato'. Il catch specifico in _execute_rest_file
    deve intercettarlo e returnare error_kind='file_source_forbidden'."""
    node_data = {
        "extra_files": {
            "excel": {
                "file_source_id": "file_other_group_20260101_000000_abcd1234",
                "original_filename": "file.xlsx",
            }
        },
    }

    def _raise_forbidden(fs_id):
        raise ValueError(
            f"Accesso negato: il file sorgente '{fs_id}' non appartiene al tuo gruppo"
        )

    with patch.object(service, "_resolve_file_source_id_path", side_effect=_raise_forbidden):
        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert result["success"] is False
    assert result["error_kind"] == "file_source_forbidden"
    assert result["user_error"] is True
    assert "Accesso negato" in result["error"]
    assert result["error"] != "Errore interno nel processore file remoto"


# ─── error_kind: remote_processor_error ────────────────────────────────────


def test_remote_processor_http_error_returns_structured_error_kind(
    service, minimal_api_config, tmp_path
):
    """Upstream IMDF ritorna HTTP 4xx/5xx: error_kind='remote_processor_error'.

    Usa un file reale su `tmp_path` per superare il build multipart (tempfile
    e Path sono importati in modo lazy nel service, patcharli e' fragile)."""
    fake_excel = tmp_path / "extra.xlsx"
    fake_excel.write_bytes(b"fake-xlsx-bytes-for-test")

    node_data = {
        "extra_files": {
            "excel": {"file_source_id": "fs-valid", "original_filename": "extra.xlsx"}
        },
    }

    fake_response = MagicMock()
    fake_response.status_code = 503

    # B3-B.5a modernization: patch su source canonico `requests.post`
    # invece dell'alias `app.core.arricchisci.api_connector.requests.post`.
    # `requests.post is requests.api.post` (re-export); il patch sulla
    # binding del modulo `requests` intercetta TUTTI i lookup
    # (api_connector caller corrente + futuri consumer estratti in mixin
    # via MRO). Pattern simmetrico a B3-B.4a (filter_visible_columns).
    with patch.object(service, "_resolve_file_source_id_path", return_value=str(fake_excel)), \
         patch("requests.post", return_value=fake_response), \
         patch.object(service, "_parse_http_error_body", return_value="Service Unavailable"):

        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert result["success"] is False
    assert result["error_kind"] == "remote_processor_error"
    assert result["user_error"] is True
    assert "503" in result["error"] or "HTTP" in result["error"]


def test_remote_processor_timeout_returns_structured_error_kind(
    service, minimal_api_config, tmp_path
):
    """Upstream timeout: error_kind='remote_processor_error', user_error=True."""
    fake_excel = tmp_path / "extra.xlsx"
    fake_excel.write_bytes(b"fake-xlsx-bytes-for-test")

    node_data = {
        "extra_files": {
            "excel": {"file_source_id": "fs-valid", "original_filename": "extra.xlsx"}
        },
    }

    # B3-B.5a modernization: vedi commento sopra.
    with patch.object(service, "_resolve_file_source_id_path", return_value=str(fake_excel)), \
         patch("requests.post", side_effect=requests.Timeout("upstream timeout")):

        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert result["success"] is False
    assert result["error_kind"] == "remote_processor_error"
    assert result["user_error"] is True
    assert "Timeout" in result["error"]


# ─── error_kind: internal ─────────────────────────────────────────────────


def test_internal_exception_returns_structured_error_kind(service, minimal_api_config):
    """Un'eccezione inattesa diversa da ValueError/requests cade nel safety-net
    finale: error_kind='internal', user_error=False."""
    node_data = {
        "extra_files": {
            "excel": {"file_source_id": "fs-valid", "original_filename": "x.xlsx"}
        },
    }

    # Forza un'eccezione inattesa nel mezzo del flusso
    with patch.object(
        service,
        "_resolve_file_source_id_path",
        side_effect=RuntimeError("qualche bug interno non previsto"),
    ):
        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert result["success"] is False
    assert result["error_kind"] == "internal"
    assert result["user_error"] is False
    # Il messaggio esposto NON deve leakare str(RuntimeError)
    assert "qualche bug interno" not in result["error"]
    # Il messaggio safe e' quello canonico
    assert result["error"] == "Errore interno nel processore file remoto"


# ─── Anti-regression strutturale ──────────────────────────────────────────


def test_all_error_returns_carry_error_kind_and_user_error_fields(service, minimal_api_config):
    """Ogni return di errore DEVE contenere i campi canonici. Blinda che
    non si introducano regressioni che tornano a return senza error_kind."""
    node_data = {
        "extra_files": {
            "excel": {"file_source_id": "fs-missing", "original_filename": "x.xlsx"}
        },
    }

    with patch.object(service, "_resolve_file_source_id_path", return_value=None):
        result = service._execute_rest_file(
            node_data=node_data,
            api_config=minimal_api_config,
            nodes=[],
            edges=[],
            node_id="api_enrich_test",
            project_id="proj_test",
            node_results=None,
        )

    assert "success" in result and result["success"] is False
    assert "error_kind" in result, "missing error_kind in error return dict"
    assert result["error_kind"] in {
        "file_source_not_found",
        "file_source_forbidden",
        "remote_processor_error",
        "internal",
    }
    assert "user_error" in result and isinstance(result["user_error"], bool)
    assert "error" in result and isinstance(result["error"], str) and result["error"]
