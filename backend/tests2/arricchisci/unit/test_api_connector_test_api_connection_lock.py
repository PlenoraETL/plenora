"""Lock tests per ApiConnectionService.test_api_connection.

Scopo: congelare il contratto osservabile di `test_api_connection`
prima del refactor C5 (estrazione helpers + uso del condiviso
`_substitute_url_path_params`).
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _resp(status=200, json_body=None, text="", headers=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("not json")
    return r


@pytest.fixture
def tac_env(api_service):
    """Patcha collaborator esterni di test_api_connection."""
    stack = ExitStack()

    def _p(target, **kwargs):
        return stack.enter_context(patch(target, **kwargs))

    def _po(attr, **kwargs):
        return stack.enter_context(patch.object(api_service, attr, **kwargs))

    with stack:
        session = MagicMock()
        session.get = MagicMock(return_value=_resp(200, json_body={"ok": True}))
        session.post = MagicMock(return_value=_resp(200, json_body={"ok": True}))

        mocks = SimpleNamespace(
            svc=api_service,
            session=session,
            shared_session=_p(
                "app.shared.api_utils.get_shared_session", return_value=session
            ),
            ssrf=_p("app.shared.api_utils.validate_external_url", return_value=None),
            arcgis_token=_p(
                "app.shared.api_utils.get_arcgis_token", return_value="ag-tok-xyz"
            ),
            prep_headers=_po("_prepare_headers", return_value={"X": "y"}),
        )
        yield mocks


# =============================================================================
# Section 1: validation + config retrieval
# =============================================================================


class TestTestApiConnectionValidation:
    def test_no_api_id_no_config_returns_error(self, tac_env):
        result = tac_env.svc.test_api_connection()
        assert result["success"] is False
        assert "Nessuna configurazione" in result["error"]

    def test_api_id_not_found_returns_error(self, tac_env):
        with patch.object(
            tac_env.svc, "get_api_connection", return_value=None
        ):
            result = tac_env.svc.test_api_connection(api_id="missing")
        assert result["success"] is False
        assert "non trovata" in result["error"]

    def test_missing_url_in_config_returns_error(self, tac_env):
        cfg = {"endpoint": {"url": "", "method": "GET"}, "auth": {}}
        result = tac_env.svc.test_api_connection(config=cfg)
        assert result["success"] is False
        assert "URL non configurato" in result["error"]


# =============================================================================
# Section 2: params merge + template processing
# =============================================================================


class TestTestApiConnectionParams:
    def test_test_params_merged_with_default_values(self, tac_env):
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "parameters": [
                {"name": "f", "default_value": "json"},
                {"name": "format", "default_value": "geojson"},
            ],
        }
        tac_env.svc.test_api_connection(
            config=cfg, test_params={"format": "csv"}
        )

        kwargs = tac_env.session.get.call_args.kwargs
        params = kwargs.get("params", {})
        assert params.get("f") == "json"  # da default
        assert params.get("format") == "csv"  # test_params override

    def test_default_value_skipped_if_test_param_already_set(self, tac_env):
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
            "parameters": [{"name": "outFields", "default_value": "*"}],
        }
        tac_env.svc.test_api_connection(
            config=cfg, test_params={"outFields": "id,name"}
        )

        params = tac_env.session.get.call_args.kwargs.get("params", {})
        assert params.get("outFields") == "id,name"

    def test_template_variables_in_params_resolved(self, tac_env):
        """`{{NOW}}` deve essere risolto via process_static_fields."""
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        tac_env.svc.test_api_connection(
            config=cfg, test_params={"timestamp": "{{NOW}}"}
        )

        params = tac_env.session.get.call_args.kwargs.get("params", {})
        # `{{NOW}}` deve essere stato sostituito con qualcosa che NON contiene
        # piu il template
        assert "{{" not in params.get("timestamp", "")


# =============================================================================
# Section 3: path param substitution
# =============================================================================


class TestTestApiConnectionPathParams:
    def test_path_param_in_url_substituted(self, tac_env):
        cfg = {
            "endpoint": {
                "url": "https://api.test/locations/{lat},{lon}/data",
                "method": "GET",
            },
            "auth": {},
        }
        tac_env.svc.test_api_connection(
            config=cfg, test_params={"lat": "45.4", "lon": "9.2"}
        )

        url = tac_env.session.get.call_args[0][0]
        assert "45.4" in url
        assert "9.2" in url
        assert "{lat}" not in url
        # E NON nei query params
        params = tac_env.session.get.call_args.kwargs.get("params", {})
        assert "lat" not in params
        assert "lon" not in params


# =============================================================================
# Section 4: auth injection (api_key, arcgis_token)
# =============================================================================


class TestTestApiConnectionAuth:
    def test_api_key_in_query_appended(self, tac_env):
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {
                "type": "api_key",
                "location": "query",
                "key_name": "apikey",
                "key_value": "K123",
            },
        }
        tac_env.svc.test_api_connection(config=cfg, test_params={})
        params = tac_env.session.get.call_args.kwargs.get("params", {})
        assert params.get("apikey") == "K123"

    def test_arcgis_token_appended_when_auth_arcgis(self, tac_env):
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {"type": "arcgis_token", "token_url": "https://t/g"},
        }
        tac_env.svc.test_api_connection(config=cfg, test_params={})
        params = tac_env.session.get.call_args.kwargs.get("params", {})
        assert params.get("token") == "ag-tok-xyz"

    def test_arcgis_token_failure_returns_error(self, tac_env):
        tac_env.arcgis_token.return_value = None
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {"type": "arcgis_token"},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert "ArcGIS token" in result["error"]


# =============================================================================
# Section 5: SSRF + HTTP call
# =============================================================================


class TestTestApiConnectionHttp:
    def test_ssrf_blocked_returns_error(self, tac_env):
        # `validate_external_url` solleva ValueError con messaggio gia'
        # pre-formattato (es. "URL bloccato: ..."): il caller NON deve
        # wrappare di nuovo, altrimenti il prefisso risulta doppio.
        tac_env.ssrf.side_effect = ValueError(
            "URL bloccato: l'host risolve a un IP privato/interno (10.0.0.1)"
        )
        cfg = {
            "endpoint": {"url": "https://10.0.0.1/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        # Esattamente UN prefisso "URL bloccato:", non doppio
        assert result["error"].count("URL bloccato:") == 1
        assert "10.0.0.1" in result["error"]

    def test_post_method_uses_post(self, tac_env):
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "POST"},
            "auth": {},
        }
        tac_env.svc.test_api_connection(config=cfg, test_params={"x": 1})
        tac_env.session.post.assert_called_once()
        tac_env.session.get.assert_not_called()

    def test_http_error_returned_in_result_dict(self, tac_env):
        tac_env.session.get.return_value = _resp(500, text="server crashed")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert "HTTP 500" in result["error"]


# =============================================================================
# Section 6: response JSON parsing + fallback
# =============================================================================


class TestTestApiConnectionResponseParsing:
    def test_success_response_extracts_available_fields(self, tac_env):
        tac_env.session.get.return_value = _resp(
            200, json_body={"name": "Mario", "age": 42}
        )
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is True
        assert "available_fields" in result
        assert "raw_response" in result

    def test_text_plain_with_valid_json_fallback_parsed(self, tac_env):
        """Caso ArcGIS REST: ritorna JSON valido ma con
        Content-Type=text/plain. La fallback path deve parsarlo."""
        r = MagicMock()
        r.status_code = 200
        r.text = '{"items": [1, 2, 3]}'
        r.headers = {"Content-Type": "text/plain"}
        # `.json()` solleva (perche' Content-Type e' text/plain)
        r.json.side_effect = ValueError("not json")
        tac_env.session.get.return_value = r

        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is True
        assert result["raw_response"] == {"items": [1, 2, 3]}

    def test_unparseable_response_returns_error(self, tac_env):
        r = MagicMock()
        r.status_code = 200
        r.text = "<html>not json</html>"
        r.headers = {"Content-Type": "text/html"}
        r.json.side_effect = ValueError("not json")
        tac_env.session.get.return_value = r

        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert "non è JSON" in result["error"] or "non parseabile" in result["error"]


# =============================================================================
# Section 7: exception handlers
# =============================================================================


class TestTestApiConnectionExceptions:
    def test_timeout_returns_specific_error(self, tac_env):
        import requests as _req
        tac_env.session.get.side_effect = _req.exceptions.Timeout("read timeout")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert "Timeout" in result["error"]

    def test_connection_error_returns_specific_error(self, tac_env):
        import requests as _req
        tac_env.session.get.side_effect = _req.exceptions.ConnectionError("dns")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert "Errore di connessione" in result["error"]
