"""
API Connection Service - Gestisce connessioni API esterne per arricchimento dati.

REFACTORED: Usa utils/dataframe_utils.py e utils/geo_utils.py
per eliminare codice duplicato.

UPDATED: Aggiunto supporto per headers custom e parsing risposta JSON per test.
"""

import os
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import requests
import pandas as pd

from config import settings
from utils.logger import get_logger

# Import nuove utility centralizzate
from utils.dataframe_utils import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    find_latest_output_file
)

logger = get_logger(__name__)


class ApiConnectionService:
    """Servizio per gestire le connessioni API esterne."""
    
    def __init__(self):
        self.data_dir = settings.DATA_DIR
        self.api_connections_dir = settings.API_CONNECTIONS_DIR
        self.results_dir = settings.RESULTS_DIR

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================
    
    def list_api_connections(self) -> List[Dict]:
        """Lista tutte le API connections configurate."""
        connections = []
        
        if not self.api_connections_dir.exists():
            return connections
        
        for api_dir in self.api_connections_dir.iterdir():
            if api_dir.is_dir():
                config_path = api_dir / "config.json"
                if config_path.exists():
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                            # Non esporre il token nella lista
                            if 'auth' in config and 'token' in config.get('auth', {}):
                                config['auth']['token'] = '***'
                            connections.append(config)
                    except Exception as e:
                        logger.error(f"Errore lettura {config_path}: {e}")
        
        connections.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return connections
    
    def get_api_connection(self, api_id: str, include_token: bool = False) -> Optional[Dict]:
        """Ottiene una API connection specifica."""
        config_path = self.api_connections_dir / api_id / "config.json"
        
        if not config_path.exists():
            return None
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
                if not include_token and 'auth' in config:
                    if 'token' in config.get('auth', {}):
                        config['auth']['token'] = '***'
                    if 'password' in config.get('auth', {}):
                        config['auth']['password'] = '***'
                
                return config
        except Exception as e:
            logger.error(f"Errore lettura API connection {api_id}: {e}")
            return None
    
    def create_api_connection(self, data: Dict) -> Dict:
        """Crea una nuova API connection."""
        api_id = f"api_{uuid.uuid4().hex[:12]}"
        
        api_dir = self.api_connections_dir / api_id
        api_dir.mkdir(parents=True, exist_ok=True)
        
        config = {
            "id": api_id,
            "name": data.get("name", "Nuova API"),
            "description": data.get("description", ""),
            "api_type": data.get("api_type", "enricher"),
            "endpoint": {
                "url": data.get("url", ""),
                "method": data.get("method", "GET")
            },
            "auth": data.get("auth", {"type": "none"}),
            "custom_headers": data.get("custom_headers", {}),  # NEW: Headers custom
            "parameters": data.get("parameters", []),
            "static_parameters": data.get("static_parameters", {}),
            "output_mapping": data.get("output_mapping", []),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        config_path = api_dir / "config.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Creata API connection: {api_id}")
        return config
    
    def update_api_connection(self, api_id: str, data: Dict) -> Optional[Dict]:
        """Aggiorna una API connection esistente."""
        config_path = self.api_connections_dir / api_id / "config.json"
        
        if not config_path.exists():
            return None
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Aggiorna campi
            updatable_fields = ['name', 'description', 'api_type', 'parameters', 
                              'static_parameters', 'output_mapping', 'custom_headers']
            for field in updatable_fields:
                if field in data:
                    config[field] = data[field]
            
            if "url" in data:
                config["endpoint"]["url"] = data["url"]
            if "method" in data:
                config["endpoint"]["method"] = data["method"]
            if "auth" in data:
                config["auth"] = data["auth"]
            
            config["updated_at"] = datetime.now().isoformat()
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Aggiornata API connection: {api_id}")
            return config
            
        except Exception as e:
            logger.error(f"Errore aggiornamento API connection {api_id}: {e}")
            return None
    
    def delete_api_connection(self, api_id: str) -> bool:
        """Elimina una API connection."""
        api_dir = self.api_connections_dir / api_id
        
        if not api_dir.exists():
            return False
        
        try:
            import shutil
            shutil.rmtree(api_dir)
            logger.info(f"Eliminata API connection: {api_id}")
            return True
        except Exception as e:
            logger.error(f"Errore eliminazione API connection {api_id}: {e}")
            return False

    # =========================================================================
    # TEST API - UPDATED with JSON parsing
    # =========================================================================
    
    def test_api_connection(self, api_id: str = None, config: Dict = None, test_params: Dict = None) -> Dict:
        """Testa una API connection con parametri di test."""
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
        params = dict(test_params or {})
        
        # API Key in query param
        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
        
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
            
            # Se success, prova a parsare JSON ed estrarre i campi
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    try:
                        json_data = response.json()
                        available_fields = self._extract_available_fields(json_data)
                        result["available_fields"] = available_fields
                        result["response_data"] = json_data  # Per debug
                    except json.JSONDecodeError:
                        result["success"] = False
                        result["error"] = "Risposta non è JSON valido"
                else:
                    result["success"] = False
                    result["error"] = f"Content-Type non è JSON: {content_type}. Verifica l'URL e i parametri."
            
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
        Ritorna una lista di {name, path, type, sample_value}
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
                    fields.extend(self._extract_available_fields(first_item, "[0]", max_depth - 1))
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
    # EXECUTE ENRICHMENT
    # =========================================================================
    
    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv', 
                          force_recalculate: bool = False) -> Dict:
        """Esegue il progetto arricchisci."""
        import time
        
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
                node_data = node.get('data', {})
                
                logger.info(f"Esecuzione nodo: {node_id} ({node_type})")
                
                if node_type == 'project_source':
                    result = self._load_source_project(node_data, force_recalculate)
                    node_results[node_id] = result
                
                elif node_type == 'file_source':
                    result = self._load_file_source(node_data)
                    node_results[node_id] = result
                    
                elif node_type == 'api_enrich':
                    api_id = node_data.get('api_id')
                    api_config = api_connections.get(api_id)
                    
                    if not api_config:
                        return {"success": False, "error": f"API '{api_id}' non trovata"}
                    
                    api_type = api_config.get('api_type', 'enricher')
                    
                    if api_type == 'generator':
                        result = self._execute_api_generator(api_config)
                    else:
                        input_edge = next((e for e in edges if e['target'] == node_id), None)
                        if not input_edge:
                            return {"success": False, "error": f"Nodo {node_id} non ha input"}
                        
                        input_df = node_results.get(input_edge['source'])
                        if input_df is None:
                            return {"success": False, "error": f"Dati input non trovati per {node_id}"}
                        
                        param_mapping = node_data.get('param_mapping', {})
                        result = self._execute_api_enricher(input_df, api_config, param_mapping)
                    
                    node_results[node_id] = result
                
                elif node_type == 'result':
                    input_edge = next((e for e in edges if e['target'] == node_id), None)
                    if input_edge:
                        input_df = node_results.get(input_edge['source'])
                        if input_df is not None:
                            node_results[node_id] = input_df
            
            # Trova output finale
            result_node = next((n for n in nodes if n.get('type') == 'result'), None)
            result_data = result_node.get('data', {}) if result_node else {}
            
            output_format = result_data.get('format', format) or format
            output_name = result_data.get('name', 'output')
            
            # Trova ultimo DataFrame
            final_df = None
            if result_node:
                final_df = node_results.get(result_node['id'])
            
            if final_df is None:
                # Trova ultimo nodo con risultato
                for node_id in reversed(execution_order):
                    if node_id in node_results and isinstance(node_results[node_id], pd.DataFrame):
                        final_df = node_results[node_id]
                        break
            
            if final_df is None:
                return {"success": False, "error": "Nessun risultato generato"}
            
            # Salva output
            output_dir = self.results_dir / project_id
            output_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{output_name}_{timestamp}.{output_format}"
            output_path = output_dir / output_filename
            
            save_dataframe(final_df, output_path, output_format)
            
            return {
                "success": True,
                "output_file": str(output_path),
                "output_path": str(output_path),
                "rows": len(final_df),
                "rows_count": len(final_df),  # Per ExecutionModal
                "rows_processed": len(final_df),  # Alias
                "columns": list(final_df.columns),
                "columns_count": len(final_df.columns),
                "message": f"Arricchimento completato: {len(final_df)} righe processate"
            }
            
        except Exception as e:
            logger.error(f"Errore esecuzione enrichment {project_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _get_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """Calcola ordine topologico per esecuzione."""
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
        """Prepara headers per chiamata API."""
        headers = {
            "Accept": "application/json",
            "User-Agent": "URBY-ETL/1.0"  # Default User-Agent
        }
        
        # Aggiungi headers custom
        if custom_headers:
            headers.update(custom_headers)
        
        auth_type = auth.get('type', 'none')
        
        if auth_type == 'oauth2_password':
            token = self._get_oauth2_token(auth)
            if token:
                headers['Authorization'] = f"Bearer {token}"
        elif auth_type == 'api_key':
            if auth.get('location', 'header') == 'header':
                headers[auth.get('key_name', 'X-API-Key')] = auth.get('key_value', '')
        
        return headers
    
    def _get_oauth2_token(self, auth: Dict) -> Optional[str]:
        """Ottiene token OAuth2 con password flow."""
        token_url = auth.get('token_url', '')
        username = auth.get('username', '')
        password = auth.get('password', '')
        
        if not token_url or not username:
            return None
        
        try:
            response = requests.post(
                token_url,
                data={
                    'grant_type': 'password',
                    'username': username,
                    'password': password
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json().get('access_token')
            return None
        except Exception:
            return None
    
    def _load_source_project(self, node_data: Dict, force_recalculate: bool = False) -> pd.DataFrame:
        """Carica dati dal progetto sorgente."""
        source_project_id = node_data.get('source_project_id')
        
        if not source_project_id:
            raise ValueError("source_project_id non specificato")
        
        output_dir = self.results_dir / source_project_id
        
        # Ricalcola se necessario
        if force_recalculate or not output_dir.exists():
            logger.info(f"Riesecuzione progetto upstream: {source_project_id}")
            from main import orchestrator
            upstream_result = orchestrator.execute_pipeline(source_project_id, force_recalculate=force_recalculate)
            if not upstream_result.get('success'):
                raise ValueError(f"Errore progetto sorgente: {upstream_result.get('message')}")
        
        # Carica file usando utility centralizzata
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
        
        from services.file_source_service import file_source_service
        df, _ = file_source_service.load_dataframe(file_source_id)
        
        # Filtra colonne visibili
        node_columns = node_data.get('columns', [])
        df = filter_visible_columns(df, node_columns)
        
        logger.info(f"Caricato file source {file_source_id}: {len(df)} righe")
        return df
    
    def _execute_api_generator(self, api_config: Dict) -> pd.DataFrame:
        """Esegue API generator - genera DataFrame da response."""
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        custom_headers = api_config.get('custom_headers', {})
        static_params = api_config.get('static_parameters', {})
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        headers = self._prepare_headers(auth, custom_headers)
        params = dict(static_params)
        
        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
        
        logger.info(f"Generator API call: {method} {url}")
        
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=60)
        else:
            response = requests.post(url, json=params, headers=headers, timeout=60)
        
        if response.status_code != 200:
            raise ValueError(f"API error: HTTP {response.status_code}")
        
        data = response.json()
        rows = self._extract_rows_from_response(data, output_mapping)
        
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    
    def _extract_rows_from_response(self, data: Any, output_mapping: List[Dict]) -> List[Dict]:
        """Estrae righe dalla response API."""
        rows = []
        
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
        
        # Array
        elif isinstance(data, list):
            for item in data:
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
        """Esegue API enricher - chiama API per ogni riga."""
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
        
        headers = self._prepare_headers(auth, custom_headers)
        
        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0
        
        success_count = 0
        error_count = 0
        
        for idx, row in df.iterrows():
            try:
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
                
                logger.debug(f"Riga {idx}: chiamata {method} {url} con params={params}")
                
                # Chiamata API
                if method == 'GET':
                    response = requests.get(url, params=params, headers=headers, timeout=30)
                else:
                    response = requests.post(url, json=params, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    for mapping in output_mapping:
                        path = mapping.get('path', '')
                        col_name = mapping.get('column', path)
                        result_df.at[idx, col_name] = self._extract_json_value(data, path)
                    success_count += 1
                else:
                    logger.warning(f"Riga {idx}: HTTP {response.status_code} - {response.text[:200]}")
                    error_count += 1
                
                if delay > 0:
                    time.sleep(delay)
                    
            except Exception as e:
                logger.debug(f"Riga {idx}: errore - {str(e)}")
                error_count += 1
            
            if (idx + 1) % 100 == 0:
                logger.info(f"Progresso: {idx + 1}/{len(df)} (OK: {success_count}, ERR: {error_count})")
        
        logger.info(f"Completato: {success_count}/{len(df)} righe, {error_count} errori")
        return result_df
    
    def _extract_json_value(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando path."""
        if not path:
            return data
        
        parts = path.replace(']', '').replace('[', '.').split('.')
        current = data
        
        for part in parts:
            if not part:
                continue
            if current is None:
                return None
            if part.isdigit():
                idx = int(part)
                if isinstance(current, list) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        
        # Serializza geometry come JSON
        if isinstance(current, dict) and current.get('type') in ['Point', 'Polygon', 'MultiPolygon', 'LineString']:
            return json.dumps(current)
        
        return current


# Singleton
api_connection_service = ApiConnectionService()