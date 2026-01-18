"""
API Connection Service - Gestisce connessioni API esterne per arricchimento dati.

REFACTORED: Usa utils/dataframe_utils.py e utils/geo_utils.py
per eliminare codice duplicato.
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
                              'static_parameters', 'output_mapping']
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
    # TEST API
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
        
        if not url:
            return {"success": False, "error": "URL non configurato"}
        
        headers = self._prepare_headers(auth)
        params = dict(test_params or {})
        
        # API Key in query param
        if auth.get('type') == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
        
        try:
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=30)
            else:
                response = requests.post(url, json=params, headers=headers, timeout=30)
            
            return {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "response_preview": response.text[:500] if response.text else "",
                "headers": dict(response.headers)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

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
            output_filename = result_data.get('fileName', 'output') or 'output'
            
            final_node_id = execution_order[-1] if execution_order else None
            final_df = node_results.get(final_node_id)
            
            if final_df is None or (hasattr(final_df, 'empty') and final_df.empty):
                return {"success": False, "error": "Nessun risultato finale"}
            
            # Salva output usando utility centralizzata
            output_dir = self.results_dir / project_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / output_filename
            
            saved_path = save_dataframe(
                df=final_df,
                output_path=output_path,
                format=output_format
            )
            
            return {
                "success": True,
                "rows_processed": len(final_df),
                "columns": list(final_df.columns),
                "output_file": saved_path.name
            }
            
        except Exception as e:
            logger.error(f"Errore enrichment: {str(e)}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # HELPERS
    # =========================================================================
    
    def _get_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """Calcola ordine topologico dei nodi."""
        in_degree = {n['id']: 0 for n in nodes}
        adj = {n['id']: [] for n in nodes}
        
        for edge in edges:
            source, target = edge['source'], edge['target']
            if source in adj and target in in_degree:
                adj[source].append(target)
                in_degree[target] += 1
        
        queue = [n_id for n_id, deg in in_degree.items() if deg == 0]
        order = []
        
        while queue:
            node_id = queue.pop(0)
            order.append(node_id)
            for neighbor in adj.get(node_id, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order
    
    def _prepare_headers(self, auth: Dict) -> Dict:
        """Prepara headers per chiamata API."""
        headers = {"Accept": "application/json"}
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
        static_params = api_config.get('static_parameters', {})
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        headers = self._prepare_headers(auth)
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
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        # Prepara colonne output
        for mapping in output_mapping:
            col_name = mapping.get('column', mapping.get('path'))
            if col_name not in result_df.columns:
                result_df[col_name] = None
        
        headers = self._prepare_headers(auth)
        
        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0
        
        success_count = 0
        error_count = 0
        
        for idx, row in df.iterrows():
            try:
                # Costruisci parametri
                params = {}
                for api_param, df_column in param_mapping.items():
                    if df_column and df_column in row:
                        value = row[df_column]
                        params[api_param] = str(value) if pd.notna(value) else None
                
                # API Key in query
                if auth.get('type') == 'api_key' and auth.get('location') == 'query':
                    params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
                
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