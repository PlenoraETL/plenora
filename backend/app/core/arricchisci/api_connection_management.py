"""
Mixin API_CONNECTION_MANAGEMENT — CRUD lifecycle + test interactive

per `ApiConnectionService`.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.config import settings
from app.core.arricchisci._api_connection_helpers import (
    _decrypt_auth_in_config,
    _encrypt_auth_in_config,
)
import app.core.arricchisci.db as db_svc
from app.utils.logger import get_logger
from app.utils.template_variables import process_static_fields

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (incluso il test B3-B.2a-modernizzato che usa
# handler stdlib agganciato a `"app.core.arricchisci.api_connector"`
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionManagementMixin:
    """Mixin per `ApiConnectionService`: CRUD lifecycle + test
    interactive delle API connections persistite.
    """

    # CRUD CONNESSIONI API — storage PostgreSQL tramite db_service

    def _mask_credentials(self, config: Dict) -> Dict:
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
        return config

    @staticmethod
    def _config_from_api_connection_row(row: db_svc.ApiConnectionRow) -> Dict[str, Any]:
        return deepcopy(row.config or {})

    def _public_config_from_api_connection_row(
        self,
        row: db_svc.ApiConnectionRow,
        *,
        include_token: bool = False,
        strict: bool = False,
    ) -> Dict[str, Any]:
        config = self._config_from_api_connection_row(row)
        config["id"] = row.id or config.get("id")
        config["group_id"] = row.group_id
        config["created_by"] = row.created_by
        if include_token:
            return _decrypt_auth_in_config(config, strict=strict)
        return self._mask_credentials(config)

    def list_api_connections(self, category: str = None, connection_type: str = None, group_id: str = None) -> List[Dict]:
        """Lista tutte le API connections dal DB.

        group_id=None → no filter (used by superadmin and by allowed_groups path).
        """
        rows = db_svc.list_api_connection_rows(group_id)
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
            connections.append(self._mask_credentials(config))
        connections.sort(
            key=lambda connection: connection.get('created_at', ''),
            reverse=True,
        )
        return connections

    def list_api_connections_decrypted(self, group_id: str = None) -> Dict[str, Dict]:
        """Lista API connections con credenziali decifrate (per uso interno).

        Fix N+1: era un loop di get_api_connection(id, include_token=True).
        Ora fa UNA sola query e decifra inline. Ritorna dict id -> config.
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
            # Decifra credenziali at-rest
            config = _decrypt_auth_in_config(config)
            result[api_id] = config
        return result

    def get_api_connection(self, api_id: str, include_token: bool = False, *, strict: bool = False) -> Optional[Dict]:
        """Ottiene una API connection specifica dal DB.

        Se ``include_token=True`` decifra i campi auth sensibili (per uso
        interno: chiamate API, test connessione). Se ``False`` (default,
        per esposizione UI) i campi vengono mascherati e quindi non serve
        decifrare nulla.

        Se ``strict=True`` (valido solo con include_token=True), un campo
        cifrato il cui decrypt ritorna None solleva ``CredentialUnavailableError``
        invece di propagare None. Usato dal path test_api_connection
        (Bug #2, 2026-04-23) per fallire esplicitamente invece di eseguire
        chiamate HTTP con auth invalida silenziosa.
        """
        row = db_svc.get_api_connection_row(api_id)
        if not row:
            return None
        return self._public_config_from_api_connection_row(
            row,
            include_token=include_token,
            strict=strict,
        )

    def create_api_connection(self, data: Dict) -> Dict:
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
        # rest_file (Processore File): config multipart in/binary out
        if data.get("request") is not None:
            config["request"] = data["request"]
        if data.get("response") is not None:
            config["response"] = data["response"]
        # Cifra i campi auth sensibili at-rest prima di salvare nel DB
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

    def update_api_connection(self, api_id: str, data: Dict) -> Optional[Dict]:
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
                            'pagination', 'options', 'batch',
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
            # sovrascriviamo il valore reale con la maschera
            existing_auth = config.get("auth", {})
            incoming_auth = {
                k: v for k, v in (data["auth"] or {}).items()
                if v != "***"
            }
            config["auth"] = {**existing_auth, **incoming_auth}
        config["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Re-cifra auth prima del save
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

    @staticmethod
    def _build_test_call_params(config: Dict, test_params: Optional[Dict]) -> Dict:
        """Costruisce i parametri per `test_api_connection`.

        Merge di:
        - `test_params` (forniti dall'utente, hanno priorita')
        - `config['parameters']` con `default_value` (fixed params come
          `f=json`, `outFields=*`, ecc.) — riempiono solo i mancanti.

        Poi processa i template `{{NOW}}`, `{{NOW-2h}}` ecc. su valori
        string via `process_static_fields`.
        """
        raw_params = dict(test_params or {})
        for p in (config.get("parameters") or []):
            if not isinstance(p, dict):
                continue
            pname = p.get("name")
            if not pname or pname in raw_params:
                continue
            dval = p.get("default_value")
            if dval not in (None, ""):
                raw_params[pname] = dval

        params: Dict = {}
        for k, v in raw_params.items():
            if isinstance(v, str) and '{{' in v:
                fields = [{'name': k, 'value': v}]
                processed = process_static_fields(fields)
                params[k] = processed.get(k, v)
            else:
                params[k] = v
        return params

    @staticmethod
    def _inject_auth_query_params(auth: Dict, params: Dict) -> Optional[Dict]:
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
            from app.shared.api_utils import get_arcgis_token
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
    def _parse_test_response_json(
        response: requests.Response,
    ) -> tuple[Any | None, str | None]:
        """Parsa il body JSON di una `test_api_connection` response.

        Alcuni server (ArcGIS REST con `f=json/pjson`) ritornano JSON
        valido con `Content-Type=text/plain`. Tentiamo prima
        `response.json` standard; se fallisce, fallback al parsing
        manuale del testo grezzo (preserva il caso text/plain).

        Ritorna `(json_data, parse_error)`:
        - `(dict_or_list, None)` su success
        - `(None, "Risposta non è JSON valido ...")` su failure
        """
        content_type = response.headers.get('Content-Type', '')
        try:
            return response.json(), None
        except (json.JSONDecodeError, ValueError):
            pass

        try:
            body_text = (response.text or "").strip()
            if body_text and body_text[0] in "{[":
                return json.loads(body_text), None
            return None, (
                f"Risposta non è JSON valido (Content-Type: {content_type}). "
                "Verifica l'URL e i parametri."
            )
        except (json.JSONDecodeError, ValueError) as e:
            return None, (
                f"Risposta non è JSON valido (Content-Type: {content_type}): {e}"
            )

    def test_api_connection(self, api_id: str = None, config: Dict = None, test_params: Dict = None) -> Dict:
        """
        Testa una connessione API con parametri di prova.

        Esegue una singola chiamata HTTP e restituisce:
        - success: se la chiamata ha avuto successo (HTTP 200 + JSON valido)
        - available_fields: campi estratti dalla risposta (per configurare output mapping)
        - raw_response: risposta JSON completa (per ResponseConfigBuilder nel frontend)

        I parametri test vengono processati per risolvere variabili template
        ({{NOW}}, {{TODAY}}, etc.) prima della chiamata.
        """
        if api_id:
            # FIXME: #2 (2026-04-23): strict=True → se un campo auth cifrato non
            # e' decifrabile, CredentialUnavailableError propagato al router
            # per HTTP 422 invece di eseguire la chiamata HTTP con auth None
            config = self.get_api_connection(api_id, include_token=True, strict=True)
            if not config:
                return {"success": False, "error": "API connection non trovata"}

        if not config:
            return {"success": False, "error": "Nessuna configurazione fornita"}

        url = config.get("endpoint", {}).get("url", "")
        method = config.get("endpoint", {}).get("method", "GET").upper()
        auth = config.get("auth", {})
        custom_headers = config.get("custom_headers", {})

        if not url:
            return {"success": False, "error": "URL non configurato"}

        headers = self._prepare_headers(auth, custom_headers)

        params = self._build_test_call_params(config, test_params)
        url, params = self._substitute_url_path_params(url, params)

        auth_err = self._inject_auth_query_params(auth, params)
        if auth_err is not None:
            return auth_err

        # Anti-SSRF: valida URL prima di eseguire la richiesta. Il
        # messaggio di `validate_external_url` include gia' il prefisso
        # "URL bloccato:" / "URL non valido:", quindi NON wrappare
        # ulteriormente o si ottiene il prefisso doppio
        from app.shared.api_utils import validate_external_url
        try:
            validate_external_url(url)
        except ValueError as ssrf_err:
            return {"success": False, "error": str(ssrf_err)}

        try:
            from app.shared.api_utils import get_shared_session
            if method == 'GET':
                response = get_shared_session().get(url, params=params, headers=headers, timeout=30)
            else:
                response = get_shared_session().post(url, json=params, headers=headers, timeout=30)

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

            json_data, parse_error = self._parse_test_response_json(response)
            if json_data is not None:
                result["available_fields"] = self._extract_available_fields(json_data)
                result["response_data"] = json_data  # Per debug
                result["raw_response"] = json_data  # Per ResponseConfigBuilder
            else:
                result["success"] = False
                result["error"] = parse_error or "Risposta vuota o non parseabile"

            return result

        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout: l'API non ha risposto entro 30 secondi"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Errore di connessione: {str(e)}"}
        except Exception as e:
            # Fix-D safety-net #5 ramo Exception: endpoint UI sincrono
            # di test delle API connection. Eccezioni inattese (bug
            # interno di _prepare_headers, TypeError su parsing custom
            # headers, ecc.) NON devono leakare str(e) nella response
            # JSON esposta al form di setup. Messaggio safe generico
            # dettaglio tecnico solo nel log
            logger.error(
                f"[GESTITO] Errore test_api_connection: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "error": "Errore interno durante il test della connessione",
            }
