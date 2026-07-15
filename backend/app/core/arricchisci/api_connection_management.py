"""
Mixin API_CONNECTION_MANAGEMENT — CRUD lifecycle + test interactive

per `ApiConnectionService`.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from app.config import settings
from app.core.arricchisci._api_connection_helpers import (
    _decrypt_auth_in_config,
    _encrypt_auth_in_config,
)
from app.core.arricchisci.async_poller import AsyncPoller
from app.core.arricchisci.http_executor import (
    RequestSpec,
    allow_redirects_from_config,
    body_type_from_config,
    execute_http_request,
    raw_body_from_config,
    timeout_from_options,
)
from app.core.arricchisci.types import JsonObject, JsonObjectById, JsonRows
from app.core.arricchisci.success_evaluator import (
    evaluate_success,
    success_config_from_api_config,
)
from app.core.arricchisci.response_parser import parse_response_body
import app.core.arricchisci.db as db_svc
from app.shared.connections.oauth import get_arcgis_token
from app.utils.logger import get_logger
from app.core.artifacts.static_parameters import process_static_fields

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (incluso il test B3-B.2a-modernizzato che usa
# handler stdlib agganciato a `"app.core.arricchisci.api_connector"`
logger = get_logger("app.core.arricchisci.api_connector")

_SENSITIVE_AUTH_FIELDS = {
    "key_value",
    "value",
    "password",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
}


class ApiConnectionManagementMixin:
    """Mixin per `ApiConnectionService`: CRUD lifecycle + test
    interactive delle API connections persistite.
    """

    # CRUD CONNESSIONI API — storage PostgreSQL tramite db_service

    def _mask_credentials(self, config: JsonObject) -> JsonObject:
        """Oscura token/password/client_secret nella risposta API."""
        config = deepcopy(config)
        if 'auth' in config:
            if 'token' in config['auth']:
                config['auth']['token'] = '***'
            if 'password' in config['auth']:
                config['auth']['password'] = '***'
            if 'client_secret' in config['auth']:
                config['auth']['client_secret'] = '***'
            if 'key_value' in config['auth']:
                config['auth']['key_value'] = '***'
            if 'value' in config['auth']:
                config['auth']['value'] = '***'
        return config

    @staticmethod
    def _config_from_api_connection_row(row: db_svc.ApiConnectionRow) -> JsonObject:
        return deepcopy(row.config or {})

    @staticmethod
    def _normalize_auth_schema(config: JsonObject) -> JsonObject:
        """Map OpenAPI-style api_key fields to runtime auth fields."""
        auth = config.get("auth")
        if not isinstance(auth, dict) or auth.get("type") != "api_key":
            return config
        if "in" in auth and "location" not in auth:
            auth["location"] = auth["in"]
        if "name" in auth and "key_name" not in auth:
            auth["key_name"] = auth["name"]
        if "value" in auth and "key_value" not in auth:
            auth["key_value"] = auth["value"]
        if "key_value" in auth and "value" not in auth:
            auth["value"] = auth["key_value"]
        return config

    def _merge_auth_preserving_secrets(
        self,
        existing_auth: JsonObject,
        incoming_auth: Optional[JsonObject],
    ) -> JsonObject:
        if not isinstance(incoming_auth, dict):
            return dict(existing_auth or {})
        incoming = {
            k: v for k, v in incoming_auth.items()
            if v != "***"
            and not (k in _SENSITIVE_AUTH_FIELDS and v == "")
        }
        if "value" in incoming and "key_value" not in incoming:
            incoming["key_value"] = incoming["value"]
        if "key_value" in incoming and "value" not in incoming:
            incoming["value"] = incoming["key_value"]
        return {**(existing_auth or {}), **incoming}

    def _public_config_from_api_connection_row(
        self,
        row: db_svc.ApiConnectionRow,
        *,
        include_token: bool = False,
        strict: bool = False,
    ) -> JsonObject:
        config = self._config_from_api_connection_row(row)
        config["id"] = row.id or config.get("id")
        config["group_id"] = row.group_id
        config["created_by"] = row.created_by
        if include_token:
            return self._normalize_auth_schema(
                _decrypt_auth_in_config(config, strict=strict)
            )
        return self._mask_credentials(self._normalize_auth_schema(config))

    def list_api_connections(
        self,
        category: str = None,
        connection_type: str = None,
        group_id: str = None,
        resource_ids: set[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> JsonRows:
        """Lista tutte le API connections dal DB.

        group_id=None → no filter (used by superadmin and by allowed_groups path).
        """
        rows = db_svc.list_api_connection_rows(
            group_id,
            resource_ids=resource_ids,
            category=category,
            connection_type=connection_type,
            exclude_datasource=True,
            limit=limit,
            offset=offset,
        )
        connections = []
        for row in rows:
            config = self._config_from_api_connection_row(row)
            if 'connection_type' not in config:
                config['connection_type'] = 'manual'
            if config.get('category') == 'datasource':
                continue
            if category and config.get('category') != category:
                continue
            if connection_type and config.get('connection_type') != connection_type:
                continue
            # Inietta id, group_id, created_by dalle colonne DB dentro il dict
            # esposto (servono per ownership checks e can_edit nel router
            config["id"] = row.id or config.get("id")
            config["group_id"] = row.group_id
            config["created_by"] = row.created_by
            connections.append(self._mask_credentials(self._normalize_auth_schema(config)))
        connections.sort(
            key=lambda connection: connection.get('created_at', ''),
            reverse=True,
        )
        return connections

    def list_api_connections_decrypted(self, group_id: str = None) -> JsonObjectById:
        """Lista API connections con credenziali decifrate (per uso interno).

        Fa una sola query e decifra inline. Ritorna dict id -> config.
        """
        rows = db_svc.list_api_connection_rows(group_id)
        result = {}
        for row in rows:
            api_id = row.id
            if not api_id:
                continue
            config = self._config_from_api_connection_row(row)
            if not isinstance(config, dict):
                continue
            config["id"] = api_id
            config["group_id"] = row.group_id
            config["created_by"] = row.created_by
            config = self._normalize_auth_schema(_decrypt_auth_in_config(config))
            result[api_id] = config
        return result

    def get_api_connection(
        self,
        api_id: str,
        include_token: bool = False,
        *,
        strict: bool = False,
    ) -> Optional[JsonObject]:
        """Ottiene una API connection specifica dal DB.

        Se ``include_token=True`` decifra i campi auth sensibili (per uso
        interno: chiamate API, test connessione). Se ``False`` (default,
        per esposizione UI) i campi vengono mascherati e quindi non serve
        decifrare nulla.

        Se ``strict=True`` (valido solo con include_token=True), un campo
        cifrato il cui decrypt ritorna None solleva ``CredentialUnavailableError``
        invece di propagare None. Usato dal path test_api_connection per fallire
        esplicitamente invece di eseguire chiamate HTTP con auth invalida
        silenziosa.
        """
        row = db_svc.get_api_connection_row(api_id)
        if not row:
            return None
        return self._public_config_from_api_connection_row(
            row,
            include_token=include_token,
            strict=strict,
        )

    def create_api_connection(self, data: JsonObject) -> JsonObject:
        """Crea una nuova API connection nel DB."""
        api_id = f"api_{uuid.uuid4().hex[:12]}"
        config = {
            "id": api_id,
            "name": data.get("name", "Nuova API"),
            "description": data.get("description", ""),
            "api_type": data.get("api_type") or data.get("exec_type", "enricher"),
            "category": data.get("category", "custom"),
            "connection_type": data.get("connection_type", "manual"),
            "endpoint": {
                "url": data.get("url", ""),
                "method": data.get("method", "GET")
            },
            "auth": data.get("auth", {"type": "none"}),
            "custom_headers": data.get("custom_headers", {}),
            "parameters": data.get("parameters", []),
            "static_parameters": data.get("static_parameters", {}),
            "output_mapping": data.get("output_mapping", []),
            "schema_fields": data.get("schema_fields", []),
            "response_preset": data.get("response_preset", ""),
            "options": data.get("options", {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if data.get("response_config") is not None:
            config["response_config"] = data["response_config"]
        if data.get("pagination"):
            config["pagination"] = data["pagination"]
        if data.get("batch"):
            config["batch"] = data["batch"]
        if data.get("async") is not None:
            config["async"] = data["async"]
        # rest_file (Processore File): config multipart in/binary out
        if data.get("request") is not None:
            config["request"] = data["request"]
        if data.get("response") is not None:
            config["response"] = data["response"]
        self._normalize_auth_schema(config)
        _encrypt_auth_in_config(config)
        row = db_svc.save_api_connection_row(
            api_id=api_id,
            group_id=data.get('group_id', settings.DEFAULT_GROUP),
            config=config,
            created_by=data.get('created_by'),
        )
        logger.info(f"Creata API connection: {api_id}")
        # Restituisci la versione mascherata al client (mai esporre cifrato/chiaro al caller
        return self._public_config_from_api_connection_row(row, include_token=False)

    def update_api_connection(
        self,
        api_id: str,
        data: JsonObject,
    ) -> Optional[JsonObject]:
        """Aggiorna una API connection esistente nel DB.

        I campi auth sensibili sono cifrati at-rest. Il flusso è:
          1. Carica config dal DB → decifra auth (per poter fare merge sui valori reali)
          2. Applica le modifiche del client (i campi mascherati ``"***"`` vengono
             ignorati per non sovrascrivere i valori esistenti)
          3. Re-cifra auth → salva su DB
        """
        row = db_svc.get_api_connection_row(api_id)
        if not row:
            return None
        config = self._config_from_api_connection_row(row)
        # Decifra auth a partire dal record DB così il merge avviene su valori reali
        _decrypt_auth_in_config(config)

        updatable_fields = ['name', 'description', 'api_type', 'category', 'connection_type',
                            'parameters', 'static_parameters', 'output_mapping',
                            'schema_fields', 'custom_headers', 'response_preset', 'response_config',
                            'pagination', 'options', 'batch', 'async',
                            # rest_file (Processore File): config multipart in/binary out
                            'request', 'response']
        for field in updatable_fields:
            if field in data:
                config[field] = data[field]
        if "url" in data:
            config.setdefault("endpoint", {})["url"] = data["url"]
        if "method" in data:
            config.setdefault("endpoint", {})["method"] = data["method"]
        if "auth" in data:
            # Merge auth invece di sovrascrivere: i campi omessi dal
            # client (es. password mascherata non modificata) mantengono
            # il valore esistente nel DB. I campi inviati come "***"
            # dal client sono mascherati e vanno IGNORATI, altrimenti
            # sovrascriviamo il valore reale con la maschera. Stessa
            # logica per stringa vuota: il FE puo' inviare
            # `key_value: ""` se l'utente non ha toccato il campo
            # (input non controllato), e svuotarla equivarrebbe a
            # cancellare la credenziale → 401 al prossimo /test.
            # Filtriamo stringa vuota SOLO per i campi sensibili
            # (`key_value`, `password`, `client_secret`, `token`):
            # per gli altri (es. `key_name`, `location`) "" puo'
            # essere un reset legittimo.
            config["auth"] = self._merge_auth_preserving_secrets(
                config.get("auth", {}),
                data["auth"],
            )
            self._normalize_auth_schema(config)
        config["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._normalize_auth_schema(config)
        _encrypt_auth_in_config(config)
        updated_row = db_svc.update_api_connection_row(api_id, config=config)
        logger.info(f"Aggiornata API connection: {api_id}")
        # Restituisci la versione mascherata al client
        if updated_row is None:
            return None
        return self._public_config_from_api_connection_row(updated_row, include_token=False)

    def delete_api_connection(self, api_id: str) -> bool:
        """Elimina una API connection dal DB."""
        if not db_svc.get_api_connection_row(api_id):
            return False
        db_svc.delete_api_connection(api_id)
        logger.info(f"Eliminata API connection: {api_id}")
        return True

    # TEST CONNESSIONE API
    # Test singola chiamata con parsing risposta JSON per validazione
    # Estrae automaticamente i campi disponibili dalla risposta per
    # permettere la configurazione dell'output mapping nel frontend

    def _build_test_config_with_overrides(
        self,
        api_id: str,
        overrides: Optional[JsonObject],
    ) -> Optional[JsonObject]:
        config = self.get_api_connection(api_id, include_token=True, strict=True)
        if not config:
            return None

        data = overrides or {}
        for field in (
            "custom_headers",
            "static_parameters",
            "options",
            "request",
            "async",
            "success_when",
            "error_path",
        ):
            if field in data:
                config[field] = data[field]
        if "url" in data:
            config.setdefault("endpoint", {})["url"] = data["url"]
        if "method" in data:
            config.setdefault("endpoint", {})["method"] = data["method"]
        if "endpoint" in data and isinstance(data["endpoint"], dict):
            endpoint = data["endpoint"]
            if "url" in endpoint:
                config.setdefault("endpoint", {})["url"] = endpoint["url"]
            if "method" in endpoint:
                config.setdefault("endpoint", {})["method"] = endpoint["method"]
        if "auth" in data:
            config["auth"] = self._merge_auth_preserving_secrets(
                config.get("auth", {}),
                data["auth"],
            )
        return self._normalize_auth_schema(config)

    def test_api_connection_with_overrides(
        self,
        api_id: str,
        config_overrides: Optional[JsonObject] = None,
        test_params: Optional[JsonObject] = None,
    ) -> JsonObject:
        config = self._build_test_config_with_overrides(api_id, config_overrides)
        if not config:
            return {"success": False, "error": "API connection non trovata"}
        return self.test_api_connection(config=config, test_params=test_params)

    @staticmethod
    def _build_test_call_params(
        config: JsonObject,
        test_params: Optional[JsonObject],
    ) -> JsonObject:
        """Costruisce i parametri per `test_api_connection`.

        Merge di:
        - `test_params` (forniti dall'utente, hanno priorita')
        - `config['static_parameters']` (gli stessi usati in esecuzione)
        - `config['parameters']` con `default_value` (fixed params come
          `f=json`, `outFields=*`, ecc.) — riempiono solo i mancanti.

        Poi processa i template `{{NOW}}`, `{{NOW-2h}}` ecc. su valori
        string via `process_static_fields`.
        """
        raw_params = dict(config.get("static_parameters") or {})
        raw_params.update(test_params or {})
        for p in (config.get("parameters") or []):
            if not isinstance(p, dict):
                continue
            pname = p.get("name")
            if not pname or pname in raw_params:
                continue
            dval = p.get("default_value")
            if dval not in (None, ""):
                raw_params[pname] = dval

        params: JsonObject = {}
        for k, v in raw_params.items():
            if isinstance(v, str) and '{{' in v:
                fields = [{'name': k, 'value': v}]
                processed = process_static_fields(fields)
                params[k] = processed.get(k, v)
            else:
                params[k] = v
        return params

    @staticmethod
    def _inject_auth_query_params(
        auth: JsonObject,
        params: JsonObject,
    ) -> Optional[JsonObject]:
        """Inietta nei query params le credenziali in-query dell'auth.

        Modifica `params` in place. Ritorna `None` su success, oppure
        un error dict `{success: False, error: ...}` se l'auth richiede
        un token che non riesce a recuperare (es. ArcGIS token failure).

        Tipi gestiti:
        - `api_key` con `location='query'`: aggiunge `key_name=key_value`
        - `arcgis_token`: chiama `get_arcgis_token(auth)` e aggiunge
          `token=<tok>`. Su failure ritorna error dict.
        """
        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        if auth.get('type') == 'arcgis_token':
            arcgis_tok = get_arcgis_token(auth)
            if arcgis_tok:
                params['token'] = arcgis_tok
            else:
                return {
                    "success": False,
                    "error": (
                        "ArcGIS token: impossibile ottenere il token. "
                        "Verifica token_url, username e password."
                    ),
                }

        return None

    @staticmethod
    def _parse_test_response_body(
        response: requests.Response,
        api_config: Optional[JsonObject] = None,
    ) -> tuple[Any | None, str | None]:
        """Parsa il body di una `test_api_connection` response.

        Supporta JSON, CSV e XML via `response.format` o
        `response_config.format`. Per JSON mantiene il fallback text/plain
        usato da alcuni endpoint ArcGIS.
        """
        content_type = response.headers.get('Content-Type', '')
        try:
            return parse_response_body(response, api_config), None
        except (json.JSONDecodeError, ValueError):
            return None, (
                f"Risposta non parseabile (Content-Type: {content_type}). "
                "Verifica l'URL e i parametri."
            )

    def test_api_connection(
        self,
        api_id: str = None,
        config: Optional[JsonObject] = None,
        test_params: Optional[JsonObject] = None,
    ) -> JsonObject:
        """
        Testa una connessione API con parametri di prova.

        Esegue una singola chiamata HTTP e restituisce:
        - success: se la chiamata ha avuto successo (HTTP 200 + JSON valido)
        - available_fields: campi estratti dalla risposta (per configurare output mapping)
        - suggested_output_mapping: mapping iniziale selezionabile dal wizard
        - raw_response: risposta JSON completa (per ResponseConfigBuilder nel frontend)

        I parametri test vengono processati per risolvere variabili template
        ({{NOW}}, {{TODAY}}, etc.) prima della chiamata.
        """
        if api_id:
            config = self.get_api_connection(api_id, include_token=True, strict=True)
            if not config:
                return {"success": False, "error": "API connection non trovata"}

        if not config:
            return {"success": False, "error": "Nessuna configurazione fornita"}
        config = self._normalize_auth_schema(deepcopy(config))

        url = config.get("endpoint", {}).get("url", "")
        method = config.get("endpoint", {}).get("method", "GET").upper()
        auth = config.get("auth", {})
        custom_headers = config.get("custom_headers", {})
        options = config.get("options", {}) or {}
        request_config = config.get("request", {}) or {}
        async_config = config.get("async", {}) or {}
        success_config = success_config_from_api_config(config)

        if not url:
            return {"success": False, "error": "URL non configurato"}

        try:
            headers = self._prepare_headers(auth, custom_headers)

            params = self._build_test_call_params(config, test_params)
            url, params = self._substitute_url_path_params(url, params)

            auth_err = self._inject_auth_query_params(auth, params)
            if auth_err is not None:
                return auth_err

            request_spec = RequestSpec(
                url=url,
                method=method,
                params=params,
                json_body=params,
                raw_body=raw_body_from_config(request_config),
                body_type=body_type_from_config(request_config),
                headers=headers,
                timeout=timeout_from_options(options, default_read_timeout=30),
                allow_redirects=allow_redirects_from_config(
                    options,
                    request_config,
                ),
                context="test_api_connection",
            )

            if async_config.get("enabled"):
                poll_result = AsyncPoller(execute=execute_http_request).run(
                    request_spec,
                    async_config,
                    base_url=url,
                    max_polls=1,
                    allow_incomplete=True,
                )
                response = poll_result.response
                json_data = poll_result.data
                result = {
                    "success": True,
                    "async": True,
                    "completed": poll_result.completed,
                    "job_id": poll_result.job_id,
                    "async_status": poll_result.status,
                    "status_code": response.status_code,
                    "response_preview": (
                        response.text[:500] if response.text else ""
                    ),
                    "headers": dict(response.headers),
                    "response_data": json_data,
                    "raw_response": json_data,
                }
                if isinstance(json_data, (dict, list)):
                    available_fields = self._extract_available_fields(json_data)
                    result["available_fields"] = available_fields
                    result["suggested_output_mapping"] = (
                        self._build_suggested_output_mapping(available_fields)
                    )
                if not poll_result.completed:
                    return result

                evaluation = evaluate_success(json_data, success_config)
                if not evaluation.ok:
                    result["success"] = False
                    result["error"] = (
                        evaluation.error_message
                        or "Errore applicativo nella risposta API"
                    )
                return result

            response = execute_http_request(request_spec)

            result = {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "response_preview": response.text[:500] if response.text else "",
                "headers": dict(response.headers),
            }
            if response.status_code != 200:
                result["error"] = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200] if response.text else 'Nessuna risposta'}"
                )
                return result

            json_data, parse_error = self._parse_test_response_body(response, config)
            if json_data is not None:
                evaluation = evaluate_success(json_data, success_config)
                if not evaluation.ok:
                    result["success"] = False
                    result["error"] = (
                        evaluation.error_message
                        or "Errore applicativo nella risposta API"
                    )
                    result["response_data"] = json_data
                    result["raw_response"] = json_data
                    return result
                available_fields = self._extract_available_fields(json_data)
                result["available_fields"] = available_fields
                result["suggested_output_mapping"] = (
                    self._build_suggested_output_mapping(available_fields)
                )
                result["response_data"] = json_data  # Per debug
                result["raw_response"] = json_data  # Per ResponseConfigBuilder
            else:
                result["success"] = False
                result["error"] = parse_error or "Risposta vuota o non parseabile"

            return result

        except ValueError as request_err:
            return {"success": False, "error": str(request_err)}
        except TimeoutError as timeout_err:
            return {"success": False, "error": str(timeout_err)}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout: l'API non ha risposto entro 30 secondi"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Errore di connessione: {str(e)}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Errore HTTP: {str(e)}"}
        except Exception as e:
            # Endpoint UI sincrono di test API connection: eccezioni inattese
            # non devono leakare str(e) nella response JSON. Dettaglio tecnico
            # solo nel log.
            logger.error(
                f"[GESTITO] Errore test_api_connection: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "error": "Errore interno durante il test della connessione",
            }
