"""
Servizio gestione connessioni API esterne per arricchimento dati.

Architettura
============
Questo modulo gestisce il ciclo di vita completo delle connessioni API utilizzate
dal modulo ARRICCHISCI: creazione, configurazione, test, esecuzione e salvataggio
risultati. Ogni connessione API è salvata come directory su filesystem con un
config.json che contiene endpoint, autenticazione, mapping parametri e output.

Componenti:
- ApiConnectionService: classe principale con operazioni CRUD + esecuzione

Struttura filesystem:
    data/api_connections/
    └── api_<uuid>/
        └── config.json    # Configurazione completa (endpoint, auth, mapping)

Sezioni principali:
- CRUD: list, get, create, update, delete connessioni API
- TEST API: test singola chiamata con parsing risposta JSON per validazione
- EXECUTE ENRICHMENT: esecuzione completa su DataFrame (riga per riga)

Flusso di esecuzione enrichment:
1. Carica DataFrame sorgente (da progetto ESTRAI/MANIPOLA o file)
2. Per ogni riga: costruisci URL con variabili template → chiama API → parsa risposta
3. Arricchisci DataFrame con colonne output mappate dalla risposta JSON
4. Salva risultato come file (CSV/GeoJSON/etc.)
5. Aggiorna tracking progresso in tempo reale

Supporto autenticazione:
- API key (header o query string)
- OAuth2 password grant (token_url + username/password)
- Headers custom

Integrazione con altri moduli:
- utils/dataframe.py: caricamento/salvataggio DataFrame
- utils/template_variables.py: sostituzione variabili nei parametri
- utils/response_parser.py: parsing strutturato risposte JSON
"""

import os
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import requests
import pandas as pd

from app.config import settings
from app.utils.logger import get_logger
from app.shared.api_utils import (
    extract_json_value as _shared_extract_json_value,
    prepare_headers as _shared_prepare_headers,
    get_oauth2_token as _shared_get_oauth2_token,
    get_oauth2_client_credentials_token as _shared_get_oauth2_cc_token,
    sanitize_for_log,
)

# Import nuove utility centralizzate
from app.utils.dataframe import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    find_latest_output_file
)
from app.utils.result_store import get_temp_output_path, save_result_blob
from app.utils.template_variables import process_static_fields
from app.utils.response_parser import ResponseParser, get_preset_config
from app.shared.crypto import encrypt as _crypto_encrypt, decrypt as _crypto_decrypt
import app.db.db_service as db_svc


# Campi sensibili nel blocco auth di una API connection — vengono cifrati
# at-rest. Restano in chiaro in memoria durante l'uso e vengono re-cifrati
# prima del save su SQLite.
_AUTH_SENSITIVE_FIELDS = (
    "password",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "key_value",
)


def _encrypt_auth_in_config(config: Dict) -> Dict:
    """Cifra in-place i campi sensibili di config['auth']. Idempotente."""
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return config
    for field in _AUTH_SENSITIVE_FIELDS:
        val = auth.get(field)
        if isinstance(val, str) and val:
            auth[field] = _crypto_encrypt(val)
    return config


def _decrypt_auth_in_config(config: Dict) -> Dict:
    """Decifra in-place i campi sensibili di config['auth']. Idempotente."""
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return config
    for field in _AUTH_SENSITIVE_FIELDS:
        val = auth.get(field)
        if isinstance(val, str) and val:
            auth[field] = _crypto_decrypt(val)
    return config


def _nd(node: dict, key: str, default=None):
    """Legge un campo flat dal nodo dict, con fallback su node['data'] (backward compat)."""
    val = node.get(key)
    if val is not None:
        return val
    return node.get('data', {}).get(key, default) if isinstance(node.get('data'), dict) else default


logger = get_logger(__name__)


class ApiConnectionService:
    """
    Servizio per gestire le connessioni API esterne.
    
    Ciclo di vita di una connessione:
    1. create → l'utente configura endpoint, auth, parametri, output mapping
    2. test → chiamata di prova con parametri test per verificare funzionamento
    3. execute → esecuzione completa su DataFrame (riga per riga o batch)
    
    Tipi API supportati:
    - enricher: arricchisce righe esistenti (1 chiamata per riga)
    - generator: genera un DataFrame dalla risposta (1 sola chiamata)
    
    Tipi connessione:
    - manual: configurazione manuale endpoint + parametri
    - template: basata su template preconfigurato (Nominatim, ORS, etc.)
    - datasource: sorgente dati esterna (WFS, REST catalog)
    """
    
    def __init__(self):
        self.data_dir = settings.DATA_DIR
        self.results_dir = settings.RESULTS_DIR
        # api_connections_dir mantenuto per backward compat con executable runner
        self.api_connections_dir = settings.API_CONNECTIONS_DIR

    # =========================================================================
    # CRUD CONNESSIONI API — storage SQLite tramite db_service
    # =========================================================================

    def _mask_credentials(self, config: Dict) -> Dict:
        """Oscura token/password/client_secret nella risposta API."""
        import copy
        config = copy.deepcopy(config)
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

    def list_api_connections(self, category: str = None, connection_type: str = None, group_id: str = None) -> List[Dict]:
        """Lista tutte le API connections dal DB.

        group_id=None → no filter (used by superadmin and by allowed_groups path).
        """
        if group_id is None:
            import app.db.config_db_service as _cfg_db
            rows = _cfg_db.fetchall(
                "SELECT * FROM api_connections ORDER BY created_at DESC"
            )
            for row in rows:
                if row.get("config") and isinstance(row["config"], str):
                    row["config"] = json.loads(row["config"])
        else:
            rows = db_svc.list_api_connections(group_id)
        connections = []
        for row in rows:
            config = row.get('config') or {}
            if isinstance(config, str):
                config = json.loads(config)
            if 'connection_type' not in config:
                config['connection_type'] = 'manual'
            if config.get('category') == 'datasource':
                continue
            if category and config.get('category') != category:
                continue
            if connection_type and config.get('connection_type') != connection_type:
                continue
            # Inietta id, group_id, created_by dalle colonne DB dentro il dict
            # esposto (servono per ownership checks e can_edit nel router).
            config["id"] = row.get("id") or config.get("id")
            config["group_id"] = row.get("group_id", "")
            config["created_by"] = row.get("created_by")
            connections.append(self._mask_credentials(config))
        connections.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return connections

    def get_api_connection(self, api_id: str, include_token: bool = False) -> Optional[Dict]:
        """Ottiene una API connection specifica dal DB.

        Se ``include_token=True`` decifra i campi auth sensibili (per uso
        interno: chiamate API, test connessione). Se ``False`` (default,
        per esposizione UI) i campi vengono mascherati e quindi non serve
        decifrare nulla.
        """
        row = db_svc.get_api_connection(api_id)
        if not row:
            return None
        config = row.get('config') or {}
        if isinstance(config, str):
            config = json.loads(config)
        # Inietta id, group_id, created_by dalle colonne DB (non sono nel config JSON)
        config["id"] = row.get("id") or config.get("id") or api_id
        config["group_id"] = row.get("group_id", "")
        config["created_by"] = row.get("created_by")
        if include_token:
            # Decifra i campi sensibili at-rest prima di restituirli al caller
            config = _decrypt_auth_in_config(config)
        else:
            config = self._mask_credentials(config)
        return config

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
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        if data.get("executable"):
            config["executable"] = data["executable"]
        if data.get("response_config") is not None:
            config["response_config"] = data["response_config"]
        # B-BE-32: salva paginazione al create (mancava, presente solo in update)
        if data.get("pagination"):
            config["pagination"] = data["pagination"]
        if data.get("batch"):
            config["batch"] = data["batch"]
        # Cifra i campi auth sensibili at-rest prima di salvare in SQLite
        _encrypt_auth_in_config(config)
        db_svc.save_api_connection(
            api_id=api_id,
            group_id=data.get('group_id', settings.DEFAULT_GROUP),
            config=config,
            created_by=data.get('created_by'),
        )
        logger.info(f"Creata API connection: {api_id}")
        # Restituisci la versione mascherata al client (mai esporre cifrato/chiaro al caller)
        return self.get_api_connection(api_id, include_token=False)

    def update_api_connection(self, api_id: str, data: Dict) -> Optional[Dict]:
        """Aggiorna una API connection esistente nel DB.

        I campi auth sensibili sono cifrati at-rest. Il flusso è:
          1. Carica config dal DB → decifra auth (per poter fare merge sui valori reali)
          2. Applica le modifiche del client (i campi mascherati ``"***"`` vengono
             ignorati per non sovrascrivere i valori esistenti)
          3. Re-cifra auth → salva su DB
        """
        row = db_svc.get_api_connection(api_id)
        if not row:
            return None
        config = row.get('config') or {}
        if isinstance(config, str):
            config = json.loads(config)
        # Decifra auth a partire dal record DB così il merge avviene su valori reali
        _decrypt_auth_in_config(config)

        updatable_fields = ['name', 'description', 'api_type', 'category', 'connection_type',
                            'parameters', 'executable', 'static_parameters', 'output_mapping',
                            'schema_fields', 'custom_headers', 'response_preset', 'response_config',
                            'pagination', 'options', 'batch']
        for field in updatable_fields:
            if field in data:
                config[field] = data[field]
        if "url" in data:
            config.setdefault("endpoint", {})["url"] = data["url"]
        if "method" in data:
            config.setdefault("endpoint", {})["method"] = data["method"]
        if "auth" in data:
            # B-BE-22: merge auth invece di sovrascrivere — i campi omessi dal client
            # (es. password mascherata non modificata) mantengono il valore esistente nel DB.
            # I campi mascherati con "***" inviati dal client vanno IGNORATI per non
            # sovrascrivere il valore reale con la maschera.
            existing_auth = config.get("auth", {})
            incoming_auth = {
                k: v for k, v in (data["auth"] or {}).items()
                if v != "***"
            }
            config["auth"] = {**existing_auth, **incoming_auth}
        config["updated_at"] = datetime.now().isoformat()

        # Re-cifra auth prima del save
        _encrypt_auth_in_config(config)
        db_svc.update_api_connection(api_id, config=config)
        logger.info(f"Aggiornata API connection: {api_id}")
        # Restituisci la versione mascherata al client
        return self.get_api_connection(api_id, include_token=False)

    def delete_api_connection(self, api_id: str) -> bool:
        """Elimina una API connection dal DB."""
        if not db_svc.get_api_connection(api_id):
            return False
        db_svc.delete_api_connection(api_id)
        logger.info(f"Eliminata API connection: {api_id}")
        return True

    # =========================================================================
    # TEST CONNESSIONE API
    # Test singola chiamata con parsing risposta JSON per validazione.
    # Estrae automaticamente i campi disponibili dalla risposta per
    # permettere la configurazione dell'output mapping nel frontend.
    # =========================================================================
    
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
            config = self.get_api_connection(api_id, include_token=True)
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
        
        # Merge: parametri di test (forniti dall'utente) + default_value dei
        # parametri configurati (fixed params come f=json, outFields, ecc.).
        # I test_params hanno priorità, i default_value riempiono i mancanti.
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

        # Processa i template nei parametri ({{NOW}}, {{NOW-2h}}, ecc.)
        params = {}
        for k, v in raw_params.items():
            if isinstance(v, str) and '{{' in v:
                # Usa process_static_fields per processare i template
                fields = [{'name': k, 'value': v}]
                processed = process_static_fields(fields)
                params[k] = processed.get(k, v)
            else:
                params[k] = v

        # Sostituisci path params nell'URL ({ip}, {coordinates}, {name}, ecc.)
        # NOTA: percent-encode i valori (safe: virgola e slash) per evitare che
        # urlparse di Python interpreti ';' come separatore di path-params (RFC legacy)
        import re
        from urllib.parse import quote as _quote
        path_param_names = re.findall(r'\{(\w+)\}', url)
        for pname in path_param_names:
            if pname in params:
                encoded = _quote(str(params.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                url = url.replace(f'{{{pname}}}', encoded)
        
        # API Key in query param
        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        # ArcGIS Token in query param (auto-richiede e cacha il token)
        if auth.get('type') == 'arcgis_token':
            from app.shared.api_utils import get_arcgis_token
            arcgis_tok = get_arcgis_token(auth)
            if arcgis_tok:
                params['token'] = arcgis_tok
            else:
                return {"success": False, "error": "ArcGIS token: impossibile ottenere il token. Verifica token_url, username e password."}

        try:
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=30)
            else:
                response = requests.post(url, json=params, headers=headers, timeout=30)
            
            # Parse JSON response and extract available fields
            result = {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "response_preview": response.text[:500] if response.text else "",
                "headers": dict(response.headers)
            }
            if response.status_code != 200:
                result["error"] = f"HTTP {response.status_code}: {response.text[:200] if response.text else 'Nessuna risposta'}"
            
            # Se success, prova a parsare JSON ed estrarre i campi.
            # NOTA: alcuni server (es. ArcGIS REST con f=json/pjson) ritornano
            # JSON valido con Content-Type=text/plain. Quindi tentiamo prima il
            # parsing del body e usiamo il Content-Type solo come hint.
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                json_data = None
                parse_error: Optional[str] = None
                try:
                    json_data = response.json()
                except (json.JSONDecodeError, ValueError):
                    # Fallback: prova a parsare manualmente il testo
                    try:
                        body_text = (response.text or "").strip()
                        if body_text and body_text[0] in "{[":
                            json_data = json.loads(body_text)
                        else:
                            parse_error = (
                                f"Risposta non è JSON valido (Content-Type: {content_type}). "
                                "Verifica l'URL e i parametri."
                            )
                    except (json.JSONDecodeError, ValueError) as e:
                        parse_error = (
                            f"Risposta non è JSON valido (Content-Type: {content_type}): {e}"
                        )

                if json_data is not None:
                    available_fields = self._extract_available_fields(json_data)
                    result["available_fields"] = available_fields
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
            return {"success": False, "error": str(e)}

    def _extract_available_fields(self, data: Any, prefix: str = "", max_depth: int = 3) -> List[Dict]:
        """
        Estrae i campi disponibili da una risposta JSON.
        
        Analizza ricorsivamente la struttura JSON fino a max_depth livelli,
        generando un elenco piatto di campi con path, tipo e valore di esempio.
        Gestisce casi speciali: GeoJSON FeatureCollection, array di oggetti,
        geometrie (non esplorate ricorsivamente).
        
        Returns:
            Lista di {name, path, type, sample_value} per ogni campo trovato
        """
        fields = []
        
        if max_depth <= 0:
            return fields
        
        # Caso: array - analizza il primo elemento
        if isinstance(data, list):
            if len(data) > 0:
                first_item = data[0]
                # Se è un array alla root, indica che ogni elemento ha questi campi
                if not prefix:
                    # GeoJSON FeatureCollection check
                    if isinstance(first_item, dict) and first_item.get('type') == 'Feature':
                        return self._extract_geojson_fields(data)
                    # B-BE-28: path senza prefisso [0] — _extract_rows_from_response
                    # itera già su ogni item, quindi i path devono essere relativi all'item
                    fields.extend(self._extract_available_fields(first_item, "", max_depth - 1))
                else:
                    fields.extend(self._extract_available_fields(first_item, f"{prefix}[0]", max_depth - 1))
            return fields
        
        # Caso: GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            return self._extract_geojson_fields(data)
        
        # Caso: oggetto
        if isinstance(data, dict):
            for key, value in data.items():
                field_path = f"{prefix}.{key}" if prefix else key
                field_type = self._get_field_type(value)
                
                # Aggiungi il campo
                fields.append({
                    "name": key,
                    "path": field_path,
                    "type": field_type,
                    "sample_value": self._get_sample_value(value)
                })
                
                # Se è un oggetto nested, estrai anche i suoi campi
                if isinstance(value, dict) and not self._is_geometry(value):
                    fields.extend(self._extract_available_fields(value, field_path, max_depth - 1))
                elif isinstance(value, list) and len(value) > 0:
                    if isinstance(value[0], dict):
                        fields.extend(self._extract_available_fields(value[0], f"{field_path}[0]", max_depth - 1))
                    else:
                        # Array di primitivi (es. latlng: [42.83, 12.83]) —
                        # espandi tutti gli indici presenti (max 10)
                        for idx, item in enumerate(value[:10]):
                            item_path = f"{field_path}[{idx}]"
                            fields.append({
                                "name": f"{field_path}[{idx}]",
                                "path": item_path,
                                "type": self._get_field_type(item),
                                "sample_value": self._get_sample_value(item)
                            })
        
        return fields
    
    def _extract_geojson_fields(self, data: Any) -> List[Dict]:
        """Estrae campi da GeoJSON FeatureCollection."""
        fields = []
        
        features = data.get('features', []) if isinstance(data, dict) else data
        if not features or not isinstance(features, list):
            return fields
        
        first_feature = features[0]
        if not isinstance(first_feature, dict):
            return fields
        
        # Geometry
        if first_feature.get('geometry'):
            fields.append({
                "name": "geometry",
                "path": "features[].geometry",
                "type": "geojson",
                "sample_value": first_feature['geometry'].get('type', 'Unknown')
            })
        
        # Properties
        properties = first_feature.get('properties', {})
        if isinstance(properties, dict):
            for key, value in properties.items():
                fields.append({
                    "name": key,
                    "path": f"features[].properties.{key}",
                    "type": self._get_field_type(value),
                    "sample_value": self._get_sample_value(value)
                })
        
        # ID
        if 'id' in first_feature:
            fields.insert(0, {
                "name": "id",
                "path": "features[].id",
                "type": self._get_field_type(first_feature['id']),
                "sample_value": self._get_sample_value(first_feature['id'])
            })
        
        return fields
    
    def _is_geometry(self, value: Any) -> bool:
        """Verifica se un valore è una geometria GeoJSON."""
        if not isinstance(value, dict):
            return False
        return value.get('type') in ['Point', 'LineString', 'Polygon', 
                                      'MultiPoint', 'MultiLineString', 'MultiPolygon',
                                      'GeometryCollection']
    
    def _get_field_type(self, value: Any) -> str:
        """Determina il tipo di un campo."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            if self._is_geometry(value):
                return "geojson"
            return "object"
        return "unknown"
    
    def _get_sample_value(self, value: Any, max_len: int = 50) -> str:
        """Restituisce un valore di esempio come stringa."""
        if value is None:
            return "null"
        if isinstance(value, (dict, list)):
            if self._is_geometry(value):
                return value.get('type', 'Geometry')
            return f"[{len(value)} items]" if isinstance(value, list) else "{...}"
        sample = str(value)
        if len(sample) > max_len:
            return sample[:max_len] + "..."
        return sample

    # =========================================================================
    # ESECUZIONE ARRICCHIMENTO
    # Pipeline: carica sorgenti → esegui nodi in ordine topologico →
    # arricchisci via API → salva output finale.
    # Supporta nodi: project_source, file_source, staticValues, api_enrich, result
    # =========================================================================
    
    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv', 
                          force_recalculate: bool = False, preview_limit: int = None,
                          run_id: str = None) -> Dict:
        """
        Esegue il progetto arricchisci dal canvas.
        
        Flusso:
        1. Carica tutte le API connections configurate
        2. Calcola ordine topologico dei nodi (BFS da sorgenti a risultato)
        3. Per ogni nodo: carica sorgente / esegui API / propaga risultato
        4. Salva DataFrame finale nel formato richiesto
        
        Tipi nodo gestiti:
        - project_source: carica output di un progetto ESTRAI/MANIPOLA
        - file_source: carica file sorgente (CSV, GeoJSON, etc.)
        - staticValues: genera DataFrame 1-riga con valori statici/template
        - api_enrich: arricchisce (enricher) o genera (generator) dati via API
        - result: nodo terminale, definisce formato e nome output
        """
        import time
        self._preview_limit = preview_limit  # usato da _load_file_source
        
        try:
            nodes = canvas.get('nodes', [])
            edges = canvas.get('edges', [])
            
            if not nodes:
                return {"success": False, "error": "Nessun nodo nel canvas"}
            
            # Carica tutte le API connections
            api_connections = {}
            for api in self.list_api_connections():
                full_api = self.get_api_connection(api['id'], include_token=True)
                if full_api:
                    api_connections[api['id']] = full_api
            
            # Ordine di esecuzione topologico
            execution_order = self._get_execution_order(nodes, edges)
            logger.info(f"Ordine esecuzione: {execution_order}")
            
            # Esegui nodi in ordine
            node_results = {}
            
            for node_id in execution_order:
                node = next((n for n in nodes if n['id'] == node_id), None)
                if not node:
                    continue
                
                node_type = node.get('type')
                node_data = node  # leggi campi flat direttamente
                
                logger.info(f"Esecuzione nodo: {node_id} ({node_type})")
                
                if node_type == 'project_source':
                    result = self._load_source_project(node_data, force_recalculate)
                    node_results[node_id] = result
                
                elif node_type == 'file_source':
                    # Controlla se il nodo downstream è un executable
                    downstream_edges = [e for e in edges if e['source'] == node_id]
                    is_for_executable = False
                    for de in downstream_edges:
                        target_node = next((n for n in nodes if n['id'] == de['target']), None)
                        if target_node and target_node.get('type') == 'api_enrich':
                            target_api_id = _nd(target_node, 'api_id')
                            target_api_config = api_connections.get(target_api_id, {})
                            if target_api_config.get('connection_type') == 'executable':
                                is_for_executable = True
                                break
                    
                    if is_for_executable:
                        # Passa un marker — _execute_executable risolverà il path via _resolve_file_source_path
                        node_results[node_id] = {"_passthrough_file_source": True, "file_source_id": _nd(node, 'file_source_id')}
                    else:
                        result = self._load_file_source(node_data)
                        node_results[node_id] = result
                
                elif node_type == 'staticValues':
                    result = self._load_static_values(node_data)
                    node_results[node_id] = result
                    
                elif node_type == 'api_enrich':
                    api_id = _nd(node, 'api_id')
                    api_config = api_connections.get(api_id)
                    
                    if not api_config:
                        return {"success": False, "error": f"API '{api_id}' non trovata"}
                    
                    # ── EXECUTABLE: esegue processore locale (file→file) ──
                    if api_config.get('connection_type') == 'executable':
                        result = self._execute_executable(
                            node_data, api_config, nodes, edges, node_id, project_id
                        )
                        if isinstance(result, dict) and not result.get('success', True):
                            return result
                        node_results[node_id] = result
                    else:
                        api_type = api_config.get('api_type', 'enricher')
                        
                        if api_type == 'generator':
                            # Cerca eventuale input (es. da StaticValues)
                            input_edge = next((e for e in edges if e['target'] == node_id), None)
                            input_df = None
                            if input_edge:
                                input_df = node_results.get(input_edge['source'])
                            result = self._execute_api_generator(api_config, input_df)
                        else:
                            input_edge = next((e for e in edges if e['target'] == node_id), None)
                            if not input_edge:
                                return {"success": False, "error": f"Nodo {node_id} non ha input"}
                            
                            input_df = node_results.get(input_edge['source'])
                            if input_df is None:
                                return {"success": False, "error": f"Dati input non trovati per {node_id}"}
                            
                            param_mapping = _nd(node, 'param_mapping', {})
                            try:
                                result = self._execute_api_enricher(input_df, api_config, param_mapping)
                            except ValueError as e:
                                return {"success": False, "error": str(e)}
                        
                        node_results[node_id] = result

                    # Snapshot intermediate.db dopo api_enrich (non bloccante)
                    if run_id:
                        try:
                            import pandas as _pd
                            from app.core.intermediate.writer import write_node as _write_node
                            _df = node_results.get(node_id)
                            if isinstance(_df, _pd.DataFrame) and not _df.empty:
                                _write_node(
                                    run_id=run_id,
                                    node_id=node_id,
                                    node_type="api_enrich",
                                    rows=_df.to_dict(orient="records"),
                                    col_schema=_df.dtypes.astype(str).to_dict(),
                                    exec_time=0.0,
                                    status="ok",
                                )
                        except Exception as _iex:
                            logger.warning(f"Intermediate write nodo {node_id} fallita (non bloccante): {_iex}")

                elif node_type == 'result':
                    input_edge = next((e for e in edges if e['target'] == node_id), None)
                    if input_edge:
                        input_df = node_results.get(input_edge['source'])
                        if input_df is not None:
                            node_results[node_id] = input_df
            
            # Trova output finale
            result_node = next((n for n in nodes if n.get('type') == 'result'), None)
            output_format = _nd(result_node, 'format', format) or format if result_node else format
            output_name = _nd(result_node, 'fileName', _nd(result_node, 'name', 'output')) if result_node else 'output'
            
            # Trova ultimo risultato (DataFrame o ExecutableResult)
            final_result = None
            if result_node:
                final_result = node_results.get(result_node['id'])
            
            if final_result is None:
                # Cerca ultimo nodo con risultato (ignora marker passthrough)
                for node_id in reversed(execution_order):
                    r = node_results.get(node_id)
                    if r is not None and not (isinstance(r, dict) and r.get('_passthrough_file_source')):
                        final_result = r
                        break
            
            if final_result is None:
                return {"success": False, "error": "Nessun risultato generato"}
            
            # ── EXECUTABLE RESULT: il risultato è un file, non un DataFrame ──
            if isinstance(final_result, dict) and final_result.get('is_file_result'):
                src_path = Path(final_result['output_path'])
                save_result_blob(project_id, src_path, src_path.suffix.lstrip('.') or 'bin', 0)
                
                return {
                    "success": True,
                    "output_file": str(src_path),
                    "output_path": str(src_path),
                    "output_type": "file",
                    "output_filename": src_path.name,
                    "output_size_bytes": src_path.stat().st_size,
                    "rows": 0,
                    "rows_count": 0,
                    "rows_processed": 0,
                    "columns": [],
                    "columns_count": 0,
                    "execution_time_ms": final_result.get('execution_time_ms', 0),
                    "metadata": final_result.get('metadata', {}),
                    "message": f"Processamento completato: {src_path.name} ({src_path.stat().st_size // 1024} KB)"
                }
            
            # ── DATAFRAME RESULT: flusso standard ──
            final_df = final_result
            if not isinstance(final_df, pd.DataFrame):
                return {"success": False, "error": "Nessun DataFrame generato"}
            
            # Salva output su blob DB
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{output_name}_{timestamp}.{output_format}"
            output_path = get_temp_output_path(output_filename)
            
            saved_path = save_dataframe(final_df, output_path, output_format)
            save_result_blob(project_id, saved_path, output_format, len(final_df))
            
            return {
                "success": True,
                "output_file": str(saved_path),
                "output_path": str(saved_path),
                "rows": len(final_df),
                "rows_count": len(final_df),
                "rows_processed": len(final_df),
                "columns": list(final_df.columns),
                "columns_metadata": [
                    {"name": c, "type": str(final_df[c].dtype)} for c in final_df.columns
                ],
                "columns_count": len(final_df.columns),
                "message": f"Arricchimento completato: {len(final_df)} righe processate"
            }
            
        except Exception as e:
            logger.error(f"[GESTITO] Errore esecuzione enrichment {project_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _get_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """
        Calcola ordine topologico per esecuzione (algoritmo di Kahn).
        
        Partendo dai nodi sorgente (in_degree=0), visita il grafo in ampiezza
        e restituisce l'ordine in cui i nodi devono essere eseguiti per
        rispettare le dipendenze (ogni nodo viene eseguito dopo i suoi input).
        """
        from collections import deque
        
        graph = {n['id']: [] for n in nodes}
        in_degree = {n['id']: 0 for n in nodes}
        
        for edge in edges:
            source = edge.get('source')
            target = edge.get('target')
            if source in graph and target in in_degree:
                graph[source].append(target)
                in_degree[target] += 1
        
        queue = deque([n for n, d in in_degree.items() if d == 0])
        order = []
        
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order
    
    def _prepare_headers(self, auth: Dict, custom_headers: Dict = None) -> Dict:
        """Prepara headers HTTP per chiamata API. Delegato a shared/api_utils."""
        return _shared_prepare_headers(auth, custom_headers)

    def _get_oauth2_client_credentials_token(self, auth: Dict) -> Optional[str]:
        """Ottiene token OAuth2 con client_credentials flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_cc_token(auth)
    
    def _get_oauth2_token(self, auth: Dict) -> Optional[str]:
        """Ottiene token OAuth2 con password flow. Delegato a shared/api_utils."""
        return _shared_get_oauth2_token(auth)
    
    def _load_source_project(self, node_data: Dict, force_recalculate: bool = False) -> pd.DataFrame:
        """Carica dati dal progetto sorgente."""
        source_project_id = node_data.get('source_project_id')
        
        if not source_project_id:
            raise ValueError("source_project_id non specificato")
        
        output_dir = self.results_dir / source_project_id
        
        # Ricalcola se necessario
        if force_recalculate or not output_dir.exists():
            logger.info(f"Riesecuzione progetto upstream: {source_project_id}")
            # NON usare 'from main import orchestrator': main.py non è nel path del worker Celery.
            # Istanzia l'orchestrator direttamente con le dipendenze minime necessarie.
            try:
                from app.core.orchestrator.service import OrchestratorService
                from app.services.project_service import ProjectService
                from app.services.connection_service import ConnectionService
                from app.core.estrai.query_generator import QueryGenerator
                from app.db.query_executor import QueryExecutor
                from app.core.features.features_service import FeaturesService
                from app.utils.dataframe import save_dataframe as _save_df
                _proj_svc = ProjectService()
                _conn_svc = ConnectionService()
                _generator = QueryGenerator()
                _executor = QueryExecutor(
                    results_dir=settings.DATA_DIR / "temp",
                    save_function=_save_df
                )
                _features = FeaturesService(save_function=_save_df)
                _orchestrator = OrchestratorService(
                    project_service=_proj_svc,
                    executor=_executor,
                    generator=_generator,
                    connection_service=_conn_svc,
                    features_service=_features
                )
                upstream_result = _orchestrator.execute_pipeline(
                    source_project_id, force_recalculate=force_recalculate
                )
            except ImportError as _ie:
                raise ValueError(f"Impossibile importare orchestrator per ricalcolo: {_ie}")
            if not upstream_result.get('success'):
                raise ValueError(f"Errore progetto sorgente: {upstream_result.get('message')}")
        
        # B-BE-43: carica output dal blob DB (nuovo pattern), fallback su filesystem (legacy)
        df = None
        try:
            from app.utils.result_store import get_result_as_tempfile
            from app.utils.dataframe import load_dataframe_from_file
            import os
            tmp = get_result_as_tempfile(source_project_id)
            if tmp:
                df = load_dataframe_from_file(tmp)
                try:
                    if os.path.exists(str(tmp)):
                        os.unlink(str(tmp))
                except Exception:
                    pass
        except Exception as _e:
            logger.warning(f"Blob DB non disponibile per {source_project_id}: {_e}")

        if df is None:
            # Fallback filesystem
            output_file = find_latest_output_file(output_dir)
            if not output_file:
                raise ValueError(f"Nessun output per progetto '{source_project_id}'")
            df = load_dataframe_from_file(output_file)
        
        # Filtra colonne visibili
        node_columns = node_data.get('columns', [])
        df = filter_visible_columns(df, node_columns)
        
        logger.info(f"Caricato progetto {source_project_id}: {len(df)} righe")
        return df
    
    def _load_file_source(self, node_data: Dict) -> pd.DataFrame:
        """Carica dati da file source."""
        file_source_id = node_data.get('file_source_id')
        
        if not file_source_id:
            raise ValueError("file_source_id non specificato")
        
        from app.services.file_service import file_source_service
        df, _ = file_source_service.load_dataframe(file_source_id)
        
        # Tronca per preview
        _plimit = getattr(self, '_preview_limit', None)
        if _plimit and isinstance(_plimit, int) and _plimit > 0:
            df = df.head(_plimit)
        
        # Filtra colonne visibili
        node_columns = node_data.get('columns', [])
        df = filter_visible_columns(df, node_columns)
        
        return df
    
    def _load_static_values(self, node_data: Dict) -> pd.DataFrame:
        """
        Carica dati da nodo StaticValues.
        Genera un DataFrame con 1 riga contenente i campi definiti.
        Le variabili template ({{NOW}}, {{TODAY}}, ecc.) vengono processate.
        """
        fields = _nd(node_data, 'fields') or []
        
        if not fields:
            logger.warning("Nodo StaticValues senza campi - restituisco DataFrame con 1 riga vuota")
            return pd.DataFrame([{}])
        
        # Processa le variabili template nei valori
        processed_values = process_static_fields(fields)
        
        # Crea DataFrame con una singola riga
        df = pd.DataFrame([processed_values])
        
        return df
    
    # =========================================================================
    # EXECUTABLE: esecuzione processori locali (script Python, exe, shell, node)
    # =========================================================================
    
    def _execute_executable(self, node_data: Dict, api_config: Dict, 
                            nodes: List[Dict], edges: List[Dict], 
                            node_id: str, project_id: str) -> Dict:
        """
        Esegue un processore locale (script Python, eseguibile, shell, node.js).
        
        A differenza di enricher/generator che lavorano su DataFrame riga per riga,
        executable lavora file→file: riceve un file in input e produce un file in output.
        
        Flusso:
        1. Risolve il path del file input dal nodo sorgente collegato
        2. Prepara la directory output
        3. Esegue il processore secondo la configurazione (subprocess)
        4. Ritorna un dict con is_file_result=True e output_path
        
        Supporta:
        - Script Python (.py) con input_mode: stdin, file, args, json_arg
        - Eseguibili nativi (.exe)
        - Script Shell (.sh)
        - Script Node.js (.js)
        - Moduli Python built-in (importati dinamicamente)
        """
        import subprocess
        import time
        import importlib
        
        exec_config = api_config.get('executable', {})
        exec_type = exec_config.get('type', 'python')  # python, executable, shell, node
        exec_path = exec_config.get('path', '')
        working_dir = exec_config.get('working_dir', '') or None
        
        # Se working_dir non è specificato, usa il parent del parent dello script
        # (per risolvere import relativi al package, es. dxf_imdf_sync/process.py → mdf_project/)
        if not working_dir and exec_path:
            script_parent = Path(exec_path).resolve().parent.parent
            if script_parent.exists():
                working_dir = str(script_parent)
        input_mode = exec_config.get('input_mode', 'file')
        output_mode = exec_config.get('output_mode', 'file')
        timeout = exec_config.get('timeout', 600)
        env_vars = exec_config.get('env_vars', {})
        
        # Builtin module support (per DXF→IMDF e simili)
        builtin_module = exec_config.get('module')
        builtin_function = exec_config.get('function', 'process')
        
        # Risolvi file input dal nodo sorgente collegato
        input_path = self._resolve_file_source_path(nodes, edges, node_id)
        
        if not input_path and not builtin_module:
            return {"success": False, "error": "Nessun file di input collegato"}
        
        # Usa directory temp per output subprocess
        import tempfile, os
        tmp_exec_dir = Path(tempfile.mkdtemp(prefix="urby_exec_"))
        
        # Parametri extra dal nodo (es. site_id)
        exec_params = node_data.get('exec_params', {}) or {}
        
        # B-BE-37: merge default_value dai parameters del connettore
        # (parametri definiti nello step 4 del wizard con nome+valore_default)
        connector_params = api_config.get('parameters', []) or []
        for p in connector_params:
            name = p.get('name', '')
            default_val = p.get('default_value', '')
            if name and default_val and name not in exec_params:
                exec_params[name] = default_val
        
        start_time = time.time()
        
        try:
            # ── BUILTIN MODULE: import Python diretto ──
            if builtin_module:
                logger.info(f"Esecuzione modulo built-in: {builtin_module}.{builtin_function}")
                
                mod = importlib.import_module(builtin_module)
                func = getattr(mod, builtin_function)
                
                # Filtra kwargs: passa solo quelli che la funzione accetta
                import inspect
                sig = inspect.signature(func)
                valid_params = set(sig.parameters.keys())
                
                kwargs = {}
                if 'input_path' in valid_params and input_path:
                    kwargs['input_path'] = str(input_path)
                if 'output_dir' in valid_params:
                    kwargs['output_dir'] = str(tmp_exec_dir)
                if 'output_zip' in valid_params:
                    kwargs['output_zip'] = str(tmp_exec_dir)
                
                # Aggiungi exec_params filtrati
                for k, v in exec_params.items():
                    if k in valid_params and v:
                        kwargs[k] = v
                
                output_path = func(**kwargs)
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                return {
                    "is_file_result": True,
                    "success": True,
                    "output_path": str(output_path),
                    "execution_time_ms": round(elapsed_ms, 1),
                    "metadata": {
                        "module": builtin_module,
                        "function": builtin_function,
                        "params": exec_params
                    }
                }
            
            # ── SUBPROCESS: esegue script/eseguibile esterno ──
            if not exec_path:
                return {"success": False, "error": "Percorso eseguibile non configurato"}
            
            # Costruisci comando
            cmd = []
            
            if exec_type == 'python':
                cmd.append('python')
                
                # Detect se lo script è dentro un package Python (ha __init__.py)
                script_path = Path(exec_path)
                package_dir = script_path.parent
                init_file = package_dir / '__init__.py'
                
                if init_file.exists():
                    # È un package → usa -m notation
                    # Es: mdf_project/dxf_imdf_sync/process.py → -m dxf_imdf_sync.process
                    package_name = package_dir.name
                    module_name = script_path.stem
                    cmd.extend(['-m', f'{package_name}.{module_name}'])
                    
                    # Working dir = parent del package
                    if not working_dir:
                        working_dir = str(package_dir.parent)
                    
                    logger.info(f"Rilevato package Python: -m {package_name}.{module_name} (cwd={working_dir})")
                else:
                    cmd.append(exec_path)
            else:
                cmd_map = {'shell': 'bash', 'node': 'node', 'r': 'Rscript', 'rscript': 'Rscript', 'executable': ''}
                cmd_prefix = cmd_map.get(exec_type, '')
                if cmd_prefix:
                    cmd.append(cmd_prefix)
                cmd.append(exec_path)
            
            # Gestisci input mode
            stdin_data = None
            if input_mode == 'file' and input_path:
                # Passa come argomento posizionale (la maggior parte degli script lo accetta così)
                cmd.append(str(input_path))
            elif input_mode == 'json_arg' and input_path:
                import json as json_mod
                cmd.append(json_mod.dumps({"input_path": str(input_path), **exec_params}))
            elif input_mode == 'args':
                if input_path:
                    cmd.append(str(input_path))
                for k, v in exec_params.items():
                    if v:
                        cmd.extend([f'--{k}', str(v)])
            elif input_mode == 'stdin' and input_path:
                stdin_data = Path(input_path).read_bytes()
            
            # Output file path
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # B-BE-35: estensione output da config, default .csv per python, .zip per altri
            out_ext = exec_config.get('output_extension', '')
            if not out_ext:
                out_ext = '.csv' if exec_type in ('python', 'r', 'rscript') else '.zip'
            out_filename = f"exec_output_{timestamp}{out_ext}"
            out_path = (tmp_exec_dir / out_filename).resolve()
            
            if output_mode == 'file':
                cmd.extend(['-o', str(out_path)])
            
            # Env — sempre per Python (PYTHONPATH), opzionale per altri
            import os
            env = {**os.environ, **(env_vars or {})}
            if working_dir:
                existing_pp = env.get('PYTHONPATH', '')
                env['PYTHONPATH'] = f"{working_dir}{os.pathsep}{existing_pp}" if existing_pp else working_dir
            
            logger.info(f"Esecuzione subprocess: {' '.join(cmd)}")
            
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                cwd=working_dir,
                input=stdin_data,
                env=env
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            if proc.returncode != 0:
                # Se il file output esiste, lo script ha funzionato
                # (l'errore potrebbe essere nel riepilogo CLI, encoding, etc.)
                if output_mode == 'file' and out_path.exists() and out_path.stat().st_size > 0:
                    logger.warning(
                        f"Executable exit code {proc.returncode} ma output generato: {out_path.name} "
                        f"({out_path.stat().st_size // 1024} KB)"
                    )
                else:
                    stderr = proc.stderr.decode('utf-8', errors='replace')[:2000]
                    return {
                        "success": False,
                        "error": f"Eseguibile terminato con codice {proc.returncode}: {stderr}"
                    }
            
            # Determina output path
            actual_output = None
            if output_mode == 'file' and out_path.exists():
                actual_output = str(out_path)
            elif output_mode in ('stdout', 'json_stdout'):
                # Scrivi stdout su file
                stdout_data = proc.stdout
                out_file = tmp_exec_dir / f"{out_filename}.json"
                out_file.write_bytes(stdout_data)
                actual_output = str(out_file)
            
            # Cerca anche file generati nella tmp_exec_dir (pattern recente)
            if not actual_output:
                import glob
                recent_files = sorted(
                    tmp_exec_dir.glob('*'),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True
                )
                for f in recent_files:
                    if f.is_file() and f.stat().st_mtime >= start_time:
                        actual_output = str(f)
                        break
            
            if not actual_output:
                return {"success": False, "error": "Nessun file di output generato"}
            
            return {
                "is_file_result": True,
                "success": True,
                "output_path": actual_output,
                "execution_time_ms": round(elapsed_ms, 1),
                "metadata": {
                    "executable": exec_path,
                    "type": exec_type,
                    "params": exec_params
                }
            }
            
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout: esecuzione superato {timeout}s"}
        except ImportError as e:
            return {"success": False, "error": f"Modulo non trovato: {e}"}
        except Exception as e:
            logger.error(f"[GESTITO] Errore esecuzione executable: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _resolve_file_source_path(self, nodes: List[Dict], edges: List[Dict], 
                                   target_node_id: str) -> Optional[str]:
        """
        Risolve il path originale del file dal nodo file_source collegato.
        
        Cerca il nodo sorgente collegato via edge al target_node_id.
        Se è un file_source, trova il file originale nel file system.
        Se è un project_source, trova l'ultimo output del progetto.
        """
        # Trova edge in ingresso
        input_edge = next((e for e in edges if e['target'] == target_node_id), None)
        if not input_edge:
            return None
        
        source_node = next((n for n in nodes if n['id'] == input_edge['source']), None)
        if not source_node:
            return None
        
        if source_node.get('type') == 'file_source':
            file_source_id = _nd(source_node, 'file_source_id')
            if not file_source_id:
                return None
            
            from app.config import settings
            
            # Prima prova su disco (directory file_source)
            source_dir = settings.get_file_source_dir(file_source_id)
            if source_dir.exists():
                for f in source_dir.iterdir():
                    if f.is_file() and f.name != 'metadata.json':
                        return str(f.resolve())
            
            # B-BE-34: fallback — estrai blob dal DB e scrivi file temporaneo
            try:
                import app.db.db_service as db_svc
                import tempfile, os
                row = db_svc.get_file_source(file_source_id, include_data=True)
                if row and row.get('data'):
                    raw = row['data']
                    original_filename = row.get('original_filename', 'input.csv')
                    suffix = Path(original_filename).suffix or '.csv'
                    # Scrivi in directory del file_source (persistente per run successivi)
                    source_dir.mkdir(parents=True, exist_ok=True)
                    out_path = source_dir / original_filename
                    out_path.write_bytes(raw if isinstance(raw, bytes) else raw)
                    logger.info(f"B-BE-34: blob estratto dal DB → {out_path}")
                    return str(out_path.resolve())
            except Exception as e:
                logger.warning(f"B-BE-34: errore estrazione blob file_source {file_source_id}: {e}")
            
            return None
        
        elif source_node.get('type') == 'project_source':
            source_project_id = _nd(source_node, 'source_project_id')
            if not source_project_id:
                return None
            
            # Leggi dal blob DB
            try:
                from app.utils.result_store import get_result_as_tempfile
                tmp = get_result_as_tempfile(source_project_id)
                return str(tmp) if tmp else None
            except Exception:
                return None
        
        return None
    
    def _execute_api_generator(self, api_config: Dict, input_df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Esegue API di tipo 'generator' — genera un DataFrame dalla risposta.

        Supporta paginazione automatica con tre strategie:
            - offset:  aggiunge offset/limit ad ogni chiamata fino a risultati vuoti o max_rows
            - cursor:  usa un token/cursor dalla risposta per la chiamata successiva
            - link:    segue un URL 'next' nel body o nell'header Link della risposta

        Configurazione paginazione (in api_config['pagination']):
            type:            'offset' | 'cursor' | 'link'   (default: nessuna)
            page_size:       righe per pagina               (default: 100)
            max_rows:        limite totale righe             (default: 10000)
            offset_param:    nome parametro offset          (default: 'offset')
            limit_param:     nome parametro limit/page_size (default: 'limit')
            cursor_param:    nome parametro cursor          (default: 'cursor')
            cursor_path:     path JSON del cursor nella risposta (default: 'next_cursor')
            link_path:       path JSON URL prossima pagina  (default: 'next')
            start_page:      indice prima pagina (offset)   (default: 0)

        Strategie di parsing risposta (in ordine di priorità):
            1. response_preset: preset predefinito (italiameteo_observations, geojson_features)
            2. response_config: configurazione custom per strutture JSON annidate
            3. output_mapping:  mapping semplice path→colonna (retrocompatibilità)
        """
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        static_params = api_config.get('static_parameters', {})
        output_mapping = api_config.get('output_mapping', [])
        response_config = api_config.get('response_config', {})
        response_preset = api_config.get('response_preset', '')
        pagination = api_config.get('pagination', {})

        if not url:
            raise ValueError("URL API non configurato")

        headers = self._prepare_headers(auth, custom_headers)

        # Costruisci parametri base
        if input_df is not None and not input_df.empty:
            row = input_df.iloc[0].to_dict()
            base_params = {k: str(v).strip() for k, v in row.items()
                          if v is not None and str(v).strip()}
            logger.info(f"Generator usando parametri da input: {sanitize_for_log(base_params, auth)}")
        else:
            base_params = {}
            for k, v in static_params.items():
                if isinstance(v, str):
                    fields = [{'name': k, 'value': v}]
                    processed = process_static_fields(fields)
                    base_params[k] = processed.get(k, v)
                else:
                    base_params[k] = v
            logger.info(f"Generator usando static_parameters (processed): {sanitize_for_log(base_params, auth)}")

        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            base_params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')

        # Helper: esegui una singola chiamata e parsa le righe
        def _call_and_parse(call_params, call_url=None):
            target_url = call_url or url
            # Sostituisci path params nell'URL ({name}, {id}, ecc.)
            query_params = {}
            for pname, pvalue in list(call_params.items()):
                placeholder = f'{{{pname}}}'
                if placeholder in target_url:
                    encoded = requests.utils.quote(str(pvalue), safe='')
                    target_url = target_url.replace(placeholder, encoded)
                else:
                    query_params[pname] = pvalue
            logger.info(f"Generator API call: {method} {target_url}")
            if method == 'GET':
                resp = requests.get(target_url, params=query_params, headers=headers, timeout=60)
            else:
                resp = requests.post(target_url, json=call_params, headers=headers, timeout=60)
            if resp.status_code != 200:
                err = resp.text[:500] if resp.text else 'nessuna risposta'
                raise ValueError(f"API error: HTTP {resp.status_code} — {err}")
            data = resp.json()

            if response_preset:
                cfg = get_preset_config(response_preset)
                if cfg:
                    return ResponseParser(cfg).parse(data), data, resp
            # B-BE-30: usa response_config solo se ha mappings configurati
            # e senza wildcard [*] (quelli vengono gestiti da _extract_rows_from_response)
            rc_mappings = response_config.get('mappings', []) if response_config else []
            has_wildcard = any('[*]' in m.get('path', '') for m in rc_mappings)
            if response_config and rc_mappings and not has_wildcard:
                return ResponseParser(response_config).parse(data), data, resp
            return self._extract_rows_from_response(data, output_mapping), data, resp

        # ── NESSUNA PAGINAZIONE ──
        pagination_type = pagination.get('type', '')
        if not pagination_type:
            rows, _, _ = _call_and_parse(base_params)
            logger.info(f"Generator completato: {len(rows)} righe (no paginazione)")
            return pd.DataFrame(rows) if rows else pd.DataFrame()

        # ── PAGINAZIONE ──
        page_size  = int(pagination.get('page_size', 100))
        max_rows   = int(pagination.get('max_rows', 10000))
        all_rows   = []
        page_num   = 0
        MAX_PAGES  = max(1, max_rows // max(1, page_size)) + 1  # safety cap

        if pagination_type == 'offset':
            offset_param = pagination.get('offset_param', 'offset')
            limit_param  = pagination.get('limit_param', 'limit')
            start        = int(pagination.get('start_page', 0))

            for page_num in range(MAX_PAGES):
                call_params = {**base_params,
                               offset_param: start + page_num * page_size,
                               limit_param:  page_size}
                rows, _, _ = _call_and_parse(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(f"Generator offset pag {page_num+1}: +{len(rows)} righe (tot {len(all_rows)})")
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                if len(rows) < page_size:
                    break  # ultima pagina parziale

        elif pagination_type == 'cursor':
            cursor_param = pagination.get('cursor_param', 'cursor')
            cursor_path  = pagination.get('cursor_path', 'next_cursor')
            cursor_value = None

            for page_num in range(MAX_PAGES):
                call_params = {**base_params}
                if cursor_value:
                    call_params[cursor_param] = cursor_value
                rows, data, _ = _call_and_parse(call_params)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(f"Generator cursor pag {page_num+1}: +{len(rows)} righe (tot {len(all_rows)})")
                # Estrai cursore per prossima chiamata
                cursor_value = self._extract_json_value(data, cursor_path)
                if not cursor_value:
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break

        elif pagination_type == 'link':
            link_path  = pagination.get('link_path', 'next')
            next_url   = None

            for page_num in range(MAX_PAGES):
                rows, data, resp = _call_and_parse(base_params, call_url=next_url)
                if not rows:
                    break
                all_rows.extend(rows)
                logger.info(f"Generator link pag {page_num+1}: +{len(rows)} righe (tot {len(all_rows)})")
                # Cerca next URL nel body JSON
                next_url = self._extract_json_value(data, link_path)
                # Fallback: header Link RFC 5988
                if not next_url:
                    link_header = resp.headers.get('Link', '')
                    match = re.search(r'<([^>]+)>;\s*rel=["\']next["\']', link_header)
                    next_url = match.group(1) if match else None
                if not next_url:
                    break
                if len(all_rows) >= max_rows:
                    all_rows = all_rows[:max_rows]
                    break
                # Dopo prima pagina, non rimandare base_params (URL è già completo)
                base_params = {}
        else:
            logger.warning(f"Tipo paginazione sconosciuto: '{pagination_type}', eseguo singola chiamata")
            rows, _, _ = _call_and_parse(base_params)
            all_rows = rows

        logger.info(f"Generator completato: {len(all_rows)} righe totali in {page_num+1} pagine")
        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
    
    def _extract_rows_from_response(self, data: Any, output_mapping: List[Dict]) -> List[Dict]:
        """
        Estrae righe dalla response API usando output_mapping semplice.
        
        Per strutture annidate complesse, usare response_config o response_preset.
        Gestisce path con wildcard [*] tipo data[*].id generati dal wizard.
        """
        rows = []

        # B-BE-31: rileva path con wildcard [*] (es. data[*].id)
        # → estrai il root array e itera con path relativo all'item
        wildcard_paths = [m.get('path', '') for m in output_mapping if '[*]' in m.get('path', '')]
        if wildcard_paths:
            # Trova il prefisso comune prima di [*] (es. "data")
            first_path = wildcard_paths[0]
            array_root = first_path.split('[*]')[0].rstrip('.')  # es. "data"
            # Naviga all'array root
            import re as _re
            array_data = self._extract_json_value(data, array_root) if array_root else data
            if not isinstance(array_data, list):
                array_data = [array_data] if array_data is not None else []
            for item in array_data:
                row = {}
                for m in output_mapping:
                    path = m.get('path', '')
                    col = m.get('column', path)
                    if '[*]' in path:
                        # path relativo all'item (dopo [*].)
                        rel = path.split('[*]', 1)[1].lstrip('.')
                        row[col] = self._extract_json_value(item, rel) if rel else item
                    else:
                        row[col] = self._extract_json_value(data, path)
                rows.append(row)
            return rows

        # GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            for feature in data.get('features', []):
                row = {}
                for mapping in output_mapping:
                    path = mapping.get('path', '')
                    col_name = mapping.get('column', path)
                    if path.startswith('features['):
                        parts = path.split('.', 1)
                        path = parts[1] if len(parts) > 1 else ''
                    row[col_name] = self._extract_json_value(feature, path)
                rows.append(row)
        
        # Array — controlla se i path sono assoluti sulla lista (es. [0].name.common)
        # In questo caso produce UNA sola riga estraendo i valori dalla lista intera
        elif isinstance(data, list):
            all_absolute = output_mapping and all(
                re.match(r'^\[\d+\]', m.get('path', '')) for m in output_mapping
            )
            if all_absolute:
                row = {}
                for m in output_mapping:
                    col = m.get('column', m.get('path'))
                    row[col] = self._extract_json_value(data, m.get('path', ''))
                rows.append(row)
            else:
                for item in data:
                    row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', ''))
                           for m in output_mapping}
                    rows.append(row)
        
        # Oggetto singolo con array "data" (auto-detect)
        elif isinstance(data, dict) and 'data' in data and isinstance(data.get('data'), list):
            logger.info("Auto-detect: usando array 'data' come root")
            for item in data['data']:
                row = {m.get('column', m.get('path')): self._extract_json_value(item, m.get('path', '')) 
                       for m in output_mapping}
                rows.append(row)
        
        # Oggetto singolo
        elif isinstance(data, dict):
            row = {m.get('column', m.get('path')): self._extract_json_value(data, m.get('path', '')) 
                   for m in output_mapping}
            rows.append(row)
        
        return rows
    
    def _execute_api_enricher(self, df: pd.DataFrame, api_config: Dict, param_mapping: Dict) -> pd.DataFrame:
        """
        Esegue API di tipo 'enricher' — arricchisce ogni riga del DataFrame.
        
        Per ogni riga: costruisce parametri (fissi + mappati da colonne),
        chiama l'API, estrae i campi dalla risposta JSON, e li scrive
        nelle colonne output. Rispetta il rate limiting tra le chiamate.
        
        Questa è la versione semplificata (sincrona, senza deduplicazione).
        Per l'esecuzione avanzata (parallela, con cache, batch) vedere
        enrichment_service.py / EnrichmentExecutor.
        """
        import time
        
        result_df = df.copy()
        
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        # Prepara colonne output
        for mapping in output_mapping:
            col_name = mapping.get('column', mapping.get('path'))
            if col_name not in result_df.columns:
                result_df[col_name] = None
        
        # B-BE-24: token OAuth2 rinnovato ogni N righe invece che una volta sola
        # (evita 401 su dataset grandi quando il token scade durante l'esecuzione)
        TOKEN_RENEW_EVERY = 500  # righe
        _token_counter = [0]

        def _get_fresh_headers():
            return self._prepare_headers(auth, custom_headers)

        headers = _get_fresh_headers()
        
        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0
        
        success_count = 0
        error_count = 0
        
        for idx, row in df.iterrows():
            try:
                # Rinnova il token OAuth2 ogni TOKEN_RENEW_EVERY righe
                _token_counter[0] += 1
                if _token_counter[0] % TOKEN_RENEW_EVERY == 0 and auth.get('type') in ('oauth2_password', 'oauth2_client_credentials'):
                    headers = _get_fresh_headers()
                    logger.info(f"Token OAuth2 rinnovato alla riga {_token_counter[0]}")
                
                # Costruisci parametri - PRIMA i fissi, poi i mappati
                params = dict(api_config.get('static_parameters', {}))  # Parametri fissi
                
                # Aggiungi parametri mappati dalle colonne
                for api_param, df_column in param_mapping.items():
                    if df_column and df_column in row:
                        value = row[df_column]
                        params[api_param] = str(value) if pd.notna(value) else None
                
                # API Key in query
                if auth.get('type') == 'api_key' and auth.get('location') == 'query':
                    params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
                
                
                # Chiamata API — sostituisci path params nell'URL se presenti
                import re as _re
                from urllib.parse import quote as _quote
                call_url = url
                call_params = dict(params)
                for pname in _re.findall(r'\{(\w+)\}', call_url):
                    if pname in call_params:
                        encoded = _quote(str(call_params.pop(pname)), safe='-._~:@!$&\'()*+,=;/')
                        call_url = call_url.replace(f'{{{pname}}}', encoded)

                if method == 'GET':
                    response = requests.get(call_url, params=call_params, headers=headers, timeout=30)
                else:
                    response = requests.post(call_url, json=call_params, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    for mapping in output_mapping:
                        path = mapping.get('path', '')
                        col_name = mapping.get('column', path)
                        extracted = self._extract_json_value(data, path)
                        result_df.at[idx, col_name] = extracted
                    success_count += 1
                else:
                    logger.warning(f"Riga {idx}: HTTP {response.status_code} - {response.text[:200]}")
                    error_count += 1
                
                if delay > 0:
                    time.sleep(delay)
                    
            except Exception as e:
                error_count += 1
            
            if (idx + 1) % 100 == 0:
                logger.info(f"Progresso: {idx + 1}/{len(df)} (OK: {success_count}, ERR: {error_count})")
        
        logger.info(f"Completato: {success_count}/{len(df)} righe, {error_count} errori")
        return result_df
    
    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando dot notation. Delegato a shared/api_utils."""
        return _shared_extract_json_value(data, path)


# --- Singleton globale ---
# Usato da api/enrichment.py e main.py per accedere al servizio
api_connection_service = ApiConnectionService()