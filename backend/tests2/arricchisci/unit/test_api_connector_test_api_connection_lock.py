"""
Lock tests per ApiConnectionService.test_api_connection.

Scopo: congelare il contratto osservabile di `test_api_connection`
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
                "app.core.arricchisci.api_connection_management."
                "get_shared_session",
                return_value=session,
            ),
            # `api_connection_management` fa `from ... import
            # validate_external_url`, quindi il binding e' locale al modulo
            # consumer: patch dell'origine non basta.
            ssrf=_p(
                "app.core.arricchisci.api_connection_management."
                "validate_external_url",
                return_value=None,
            ),
            arcgis_token=_p(
                "app.core.arricchisci.api_connection_management."
                "get_arcgis_token",
                return_value="ag-tok-xyz",
            ),
            prep_headers=_po("_prepare_headers", return_value={"X": "y"}),
        )
        yield mocks


# Section 1: validation + config retrieval


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


# Section 2: params merge + template processing


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


# Section 3: path param substitution


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


# Section 4: auth injection (api_key, arcgis_token)


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


# Section 5: SSRF + HTTP call


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


# Section 6: response JSON parsing + fallback


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


# Section 7: exception handlers


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

    def test_unknown_exception_returns_safe_message(self, tac_env):
        """Safety-net contract ramo Exception: eccezioni inattese
        (RuntimeError, TypeError, bug interno) diventano un messaggio
        safe generico nella response FE. Previous behavior, `str(e)` leakava
        internals nel JSON visibile al form di setup -- oggi il
        dettaglio resta solo nel log.
        """
        tac_env.session.get.side_effect = RuntimeError("internal boom")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }
        result = tac_env.svc.test_api_connection(config=cfg, test_params={})
        assert result["success"] is False
        assert result["error"] == "Errore interno durante il test della connessione"
        assert "internal boom" not in result["error"], (
            "str(original_exception) non deve comparire nella response FE"
        )

    def test_unknown_exception_logs_with_exc_info_true(self, tac_env):
        """Safety-net contract: `logger.error(..., exc_info=True)` e'
        stato aggiunto in questo commit (Previous behavior il ramo Exception
        non aveva alcun log: il dettaglio tecnico si perdeva
        silenziosamente). Lock sul contratto di osservabilita':
        senza `exc_info=True` la diagnostica dei bug interni
        sarebbe impossibile.

        current contract B3-B.2a — behavior contract vs source-location contract
        ====================================================================
        Pre-B3-B.2a il test patchava
        `patch("app.core.arricchisci.api_connector.logger")` per
        intercettare il `logger.error([GESTITO]...)` emesso dal ramo
        Exception fail-safe di `test_api_connection`. Patch
        source-location-bound: spostando il metodo in mixin/sotto-modulo
        (come previsto da B3-B.2b `ApiConnectionManagementMixin`), il
        `logger` letto dal mixin e' un altro StructuredLogger wrapper
        instance — il rebind di `api_connector.logger` non lo affecta
        → mock non chiamato → test rosso anche se il ramo Exception
        emette il log correttamente.

        Post-B3-B.2a: aggancio un `logging.Handler` custom al logger
        stdlib canonico `"app.core.arricchisci.api_connector"` per nome.
        Lo `StructuredLogger` wrapper di `app/utils/logger.py` delega a
        `_logger.log(level, msg, ...)` sul logger stdlib omonimo, quindi
        qualunque chiamata su `logger.error(...)` (anche da un futuro
        modulo figlio estratto) viene catturata via handler — purche'
        il wrapper sia ottenuto via `get_logger("app.core.arricchisci.api_connector")`.
        Indipendente dalla posizione fisica. Stesso pattern modernizzato
        in `test_unsafe_append_gate_contract.py` (B2.5a).

        Anti-regressione preservata: se un futuro fix spostasse il
        `logger.error` su un logger ad-hoc con nome diverso (es.
        `logging.getLogger(__name__)` con `__name__ != "...api_connector"`),
        il record NON arriverebbe al collector handler → test fallisce
        come voluto (loggers con nomi diversi sono separati: la
        sibling-propagation non esiste, solo verso parent → root).

        Contract preservato bit-per-bit:
          1. ramo Exception emette `logger.error(...)` (almeno 1 record);
          2. record di livello ERROR (`levelno == logging.ERROR`);
          3. emit con `exc_info=True` per preservare il traceback;
          4. il messaggio contiene `"[GESTITO]"` marker (anti-regressione
             aggiuntiva: assicura che il fail-safe usi il marker
             distintivo invece di un altro qualunque error log).
        """
        import logging

        tac_env.session.get.side_effect = RuntimeError("stacktrace needed")
        cfg = {
            "endpoint": {"url": "https://api.test/q", "method": "GET"},
            "auth": {},
        }

        # Cattura ERROR sul logger canonico per nome (vedi docstring
        # per razionale current contract). Imposto setLevel(ERROR) per
        # filtrare emit di altri livelli + `exc_info=True` viene
        # registrato nel `LogRecord.exc_info` field.
        captured_records: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):  # noqa: D401
                captured_records.append(record)

        stdlib_logger = logging.getLogger("app.core.arricchisci.api_connector")
        handler = _Collector()
        handler.setLevel(logging.ERROR)
        prev_level = stdlib_logger.level
        stdlib_logger.addHandler(handler)
        stdlib_logger.setLevel(logging.ERROR)
        try:
            tac_env.svc.test_api_connection(config=cfg, test_params={})
        finally:
            stdlib_logger.removeHandler(handler)
            stdlib_logger.setLevel(prev_level)

        # Almeno 1 record ERROR sul logger canonico
        error_records = [r for r in captured_records if r.levelno == logging.ERROR]
        assert error_records, (
            f"Atteso almeno 1 record ERROR sul logger canonico "
            f"'app.core.arricchisci.api_connector' nel ramo Exception "
            f"fail-safe. Tutti i record catturati: "
            f"{[(r.levelname, r.getMessage()) for r in captured_records]!r}"
        )

        # Filtra il record specifico del fail-safe via marker [GESTITO]
        # (anti-regressione: assicura che il fail-safe usi il marker
        # distintivo invece di un altro qualunque error log che potrebbe
        # essere emesso nel flow).
        gestito_records = [
            r for r in error_records if "[GESTITO]" in r.getMessage()
        ]
        assert gestito_records, (
            f"Atteso ERROR record con marker '[GESTITO]' (fail-safe ramo "
            f"Exception). ERROR ricevuti: "
            f"{[r.getMessage() for r in error_records]!r}"
        )

        # exc_info=True popola `LogRecord.exc_info` con la tupla
        # `(exc_type, exc_value, tb)` — nel nostro caso il RuntimeError
        # iniettato. Anti-regressione: senza `exc_info=True` la
        # diagnostica del traceback sarebbe persa.
        target_record = gestito_records[0]
        assert target_record.exc_info is not None, (
            "logger.error deve essere chiamato con exc_info=True "
            "per preservare il traceback nel log; "
            f"LogRecord.exc_info e' None su record: {target_record.getMessage()!r}"
        )
        # `exc_info` e' una tupla (exc_type, exc_value, tb) quando
        # exc_info=True viene serializzato dal logging stdlib.
        assert isinstance(target_record.exc_info, tuple) and len(target_record.exc_info) == 3, (
            f"LogRecord.exc_info atteso tuple di 3 elementi (type, value, tb); "
            f"ottenuto: {target_record.exc_info!r}"
        )
        # Il RuntimeError iniettato deve essere preservato come exc_value
        exc_type, exc_value, _tb = target_record.exc_info
        assert exc_type is RuntimeError, (
            f"exc_type atteso RuntimeError; ottenuto: {exc_type!r}"
        )
        assert "stacktrace needed" in str(exc_value), (
            f"exc_value deve preservare il messaggio originale "
            f"'stacktrace needed'; ottenuto: {exc_value!r}"
        )
