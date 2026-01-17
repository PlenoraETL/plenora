"""
API Connection Service
Gestisce le configurazioni delle API esterne per l'arricchimento dati.
"""

import os
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import requests
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class ApiConnectionService:
    """Servizio per gestire le connessioni API esterne."""
    
    def __init__(self):
            self.data_dir = settings.DATA_DIR
            self.api_connections_dir = settings.API_CONNECTIONS_DIR
            self.results_dir = settings.RESULTS_DIR  # Stessa directory usata da Manipola
    
    # ==================== CRUD ====================
    
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
        
        # Ordina per data creazione (più recenti prima)
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
                
                # Nascondi token se non richiesto
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
        # Genera ID univoco
        api_id = f"api_{uuid.uuid4().hex[:12]}"
        
        # Crea directory
        api_dir = self.api_connections_dir / api_id
        api_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepara config
        config = {
            "id": api_id,
            "name": data.get("name", "Nuova API"),
            "description": data.get("description", ""),
            "api_type": data.get("api_type", "enricher"),  # "enricher" o "generator"
            "endpoint": {
                "url": data.get("url", ""),
                "method": data.get("method", "GET")
            },
            "auth": data.get("auth", {"type": "none"}),
            "parameters": data.get("parameters", []),  # Per enricher: definizione parametri da mappare
            "static_parameters": data.get("static_parameters", {}),  # Per generator: valori fissi
            "output_mapping": data.get("output_mapping", []),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # Salva config
        config_path = api_dir / "config.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        logger.info(f" Creata API connection: {api_id} (type: {config['api_type']})")
        return config
    
    def update_api_connection(self, api_id: str, data: Dict) -> Optional[Dict]:
        """Aggiorna una API connection esistente."""
        config_path = self.api_connections_dir / api_id / "config.json"
        
        if not config_path.exists():
            return None
        
        try:
            # Leggi config esistente
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Aggiorna campi
            if "name" in data:
                config["name"] = data["name"]
            if "description" in data:
                config["description"] = data["description"]
            if "api_type" in data:
                config["api_type"] = data["api_type"]
            if "url" in data:
                config["endpoint"]["url"] = data["url"]
            if "method" in data:
                config["endpoint"]["method"] = data["method"]
            if "auth" in data:
                config["auth"] = data["auth"]
            if "parameters" in data:
                config["parameters"] = data["parameters"]
            if "static_parameters" in data:
                config["static_parameters"] = data["static_parameters"]
            if "output_mapping" in data:
                config["output_mapping"] = data["output_mapping"]
            
            config["updated_at"] = datetime.now().isoformat()
            
            # Salva
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            logger.info(f" Aggiornata API connection: {api_id}")
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
            logger.info(f" Eliminata API connection: {api_id}")
            return True
        except Exception as e:
            logger.error(f"Errore eliminazione API connection {api_id}: {e}")
            return False
    
    # ==================== TEST API ====================
    
    def test_api_connection(self, api_id: str = None, config: Dict = None, test_params: Dict = None) -> Dict:
        """
        Testa una API connection con parametri di test.
        Può usare un api_id esistente o un config temporaneo (per wizard).
        """
        # Ottieni config
        if api_id:
            config = self.get_api_connection(api_id, include_token=True)
            if not config:
                return {"success": False, "error": "API connection non trovata"}
        
        if not config:
            return {"success": False, "error": "Nessuna configurazione fornita"}
        
        # Estrai info
        url = config.get("endpoint", {}).get("url", "")
        method = config.get("endpoint", {}).get("method", "GET").upper()
        auth = config.get("auth", {})
        
        if not url:
            return {"success": False, "error": "URL non configurato"}
        
        # Prepara headers
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        # Autenticazione
        auth_type = auth.get("type", "none")
        
        # OAuth2 Password Flow - Login automatico
        if auth_type == "oauth2_password":
            token_url = auth.get("token_url", "")
            username = auth.get("username", "")
            password = auth.get("password", "")
            
            if not token_url or not username:
                return {"success": False, "error": "OAuth2: token_url e username richiesti"}
            
            try:
                logger.info(f" OAuth2 Login: {token_url}")
                login_response = requests.post(
                    token_url,
                    data={
                        "grant_type": "password",
                        "username": username,
                        "password": password
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30
                )
                
                if login_response.status_code != 200:
                    return {
                        "success": False, 
                        "error": f"Login fallito: HTTP {login_response.status_code}",
                        "raw_response": login_response.text[:500]
                    }
                
                token_data = login_response.json()
                access_token = token_data.get("access_token", "")
                
                if not access_token:
                    return {"success": False, "error": "Login riuscito ma nessun access_token ricevuto"}
                
                headers["Authorization"] = f"Bearer {access_token}"
                logger.info(f" OAuth2 Login OK, token ottenuto")
                
            except Exception as e:
                return {"success": False, "error": f"Errore login OAuth2: {str(e)}"}
        
        elif auth_type == "api_key":
            key_name = auth.get("key_name", "X-API-Key")
            key_value = auth.get("key_value", "")
            if auth.get("location", "header") == "header":
                headers[key_name] = key_value
            # Query param gestito sotto
        
        # Prepara parametri
        params = test_params or {}
        
        # Se API key in query param
        if auth_type == "api_key" and auth.get("location") == "query":
            params[auth.get("key_name", "apiKey")] = auth.get("key_value", "")
        
        try:
            logger.info(f" Test API: {method} {url}")
            logger.info(f" Params: {params}")
            
            # Esegui richiesta
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=30)
            elif method == "POST":
                response = requests.post(url, json=params, headers=headers, timeout=30)
            else:
                return {"success": False, "error": f"Metodo {method} non supportato"}
            
            # Controlla risposta
            if response.status_code == 200:
                try:
                    data = response.json()
                    
                    # Estrai campi disponibili per il mapping
                    available_fields = self._extract_fields(data)
                    
                    return {
                        "success": True,
                        "status_code": 200,
                        "data": data,
                        "available_fields": available_fields
                    }
                except json.JSONDecodeError:
                    return {
                        "success": False,
                        "error": "Risposta non è JSON valido",
                        "raw_response": response.text[:500]
                    }
            else:
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error": f"HTTP {response.status_code}",
                    "raw_response": response.text[:500]
                }
                
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout - API non risponde"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Errore connessione - verificare URL"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _extract_fields(self, data: Any, prefix: str = "") -> List[Dict]:
        """
        Estrae ricorsivamente i campi disponibili da una risposta JSON.
        Gestisce GeoJSON in modo speciale.
        """
        fields = []
        
        if isinstance(data, dict):
            # Check se è GeoJSON FeatureCollection
            if data.get("type") == "FeatureCollection" and "features" in data:
                features = data.get("features", [])
                if features and len(features) > 0:
                    feature = features[0]
                    
                    # Geometry in blocco
                    if "geometry" in feature:
                        geom_type = feature["geometry"].get("type", "Geometry")
                        fields.append({
                            "path": "features[0].geometry",
                            "name": f"geometry ({geom_type})",
                            "type": "geojson",
                            "sample": json.dumps(feature["geometry"])[:100] + "..."
                        })
                    
                    # Properties singole
                    if "properties" in feature:
                        for key, value in feature["properties"].items():
                            fields.append({
                                "path": f"features[0].properties.{key}",
                                "name": key,
                                "type": self._guess_type(value),
                                "sample": str(value)[:50]
                            })
            
            # JSON normale
            else:
                for key, value in data.items():
                    full_path = f"{prefix}.{key}" if prefix else key
                    
                    if isinstance(value, dict):
                        # Oggetto nested - se è geometry, importa in blocco
                        if key == "geometry" or (isinstance(value.get("type"), str) and value.get("type") in ["Point", "Polygon", "MultiPolygon", "LineString"]):
                            fields.append({
                                "path": full_path,
                                "name": key,
                                "type": "geojson",
                                "sample": json.dumps(value)[:100] + "..."
                            })
                        else:
                            # Ricorsione per altri oggetti
                            fields.extend(self._extract_fields(value, full_path))
                    
                    elif isinstance(value, list):
                        # Array - salta per ora (troppo complesso)
                        fields.append({
                            "path": full_path,
                            "name": key,
                            "type": "array",
                            "sample": f"[{len(value)} elementi]"
                        })
                    
                    else:
                        # Valore semplice
                        fields.append({
                            "path": full_path,
                            "name": key,
                            "type": self._guess_type(value),
                            "sample": str(value)[:50]
                        })
        
        return fields
    
    def _guess_type(self, value: Any) -> str:
        """Indovina il tipo di un valore."""
        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "number"
        elif isinstance(value, str):
            return "string"
        elif isinstance(value, list):
            return "array"
        elif isinstance(value, dict):
            return "object"
        else:
            return "unknown"
    
    # ==================== ENRICHMENT EXECUTION ====================
    
    def execute_enrichment(self, project_id: str, canvas: Dict, format: str = 'csv', force_recalculate: bool = False) -> Dict:
        """
        Esegue il progetto arricchisci.
        
        Args:
            project_id: ID del progetto
            canvas: Canvas con nodi e edges
            format: Formato output (csv, xlsx, geojson, parquet)
            force_recalculate: Se True, riesegue tutto. Se False, usa cache se disponibile.
        
        Returns:
            Dict con risultato esecuzione
        """
        import pandas as pd
        import time
        
        try:
            # ===== CHECK CACHE =====
            if not force_recalculate:
                cached_result = self._check_enrichment_cache(project_id)
                if cached_result:
                    logger.info(f" Usando cache esistente per {project_id}")
                    return cached_result
            
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
            
            # Trova ordine di esecuzione (topological sort)
            execution_order = self._get_execution_order(nodes, edges)
            logger.info(f" Ordine esecuzione: {execution_order}")
            
            # Esegui nodi in ordine
            node_results = {}  # node_id -> DataFrame
            
            for node_id in execution_order:
                node = next((n for n in nodes if n['id'] == node_id), None)
                if not node:
                    continue
                
                node_type = node.get('type')
                node_data = node.get('data', {})
                
                logger.info(f" Esecuzione nodo: {node_id} ({node_type})")
                
                if node_type == 'project_source':
                    # Carica dati dal progetto sorgente
                    result = self._load_source_project(node_data)
                    node_results[node_id] = result
                    
                elif node_type == 'api_enrich':
                    # Esegui arricchimento API
                    api_id = node_data.get('api_id')
                    api_config = api_connections.get(api_id)
                    
                    if not api_config:
                        return {"success": False, "error": f"Configurazione API '{api_id}' non trovata"}
                    
                    api_type = api_config.get('api_type', 'enricher')
                    
                    if api_type == 'generator':
                        # Generator: non richiede input
                        result = self._execute_api_generator(api_config)
                    else:
                        # Enricher: richiede input
                        input_edge = next((e for e in edges if e['target'] == node_id), None)
                        if not input_edge:
                            return {"success": False, "error": f"Nodo {node_id} (enricher) non ha input collegato"}
                        
                        input_df = node_results.get(input_edge['source'])
                        if input_df is None:
                            return {"success": False, "error": f"Dati input non trovati per {node_id}"}
                        
                        param_mapping = node_data.get('param_mapping', {})
                        result = self._execute_api_enricher(input_df, api_config, param_mapping)
                    
                    node_results[node_id] = result
                
                elif node_type == 'result':
                    # Nodo risultato: prendi output dal nodo precedente e salva con la sua configurazione
                    input_edge = next((e for e in edges if e['target'] == node_id), None)
                    if input_edge:
                        input_df = node_results.get(input_edge['source'])
                        if input_df is not None:
                            node_results[node_id] = input_df
                            
                            # Usa configurazione dal nodo result
                            result_format = node_data.get('format', format) or format
                            result_filename = node_data.get('fileName', 'output') or 'output'
                            
                            logger.info(f" Nodo result: {len(input_df)} righe -> {result_filename}.{result_format}")
            
            # Trova nodo result per la configurazione di output
            result_node = next((n for n in nodes if n.get('type') == 'result'), None)
            result_data = result_node.get('data', {}) if result_node else {}
            
            # Usa formato e filename dal nodo result, fallback ai parametri
            output_format = result_data.get('format', format) or format
            output_filename = result_data.get('fileName', 'output') or 'output'
            
            # Trova ultimo nodo (output finale)
            final_node_id = execution_order[-1] if execution_order else None
            final_df = node_results.get(final_node_id)
            
            if final_df is None or (hasattr(final_df, 'empty') and final_df.empty):
                return {"success": False, "error": "Nessun risultato finale"}
            
            # Salva output con configurazione dal nodo result
            output_file = self._save_enrichment_output(
                project_id, 
                final_df, 
                output_format,
                output_filename
            )
            
            return {
                "success": True,
                "rows_processed": len(final_df),
                "columns": list(final_df.columns),
                "output_file": output_file
            }
            
        except Exception as e:
            logger.info(f" Errore: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _get_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """Calcola ordine topologico dei nodi."""
        in_degree = {n['id']: 0 for n in nodes}
        adj = {n['id']: [] for n in nodes}
        
        for edge in edges:
            source = edge['source']
            target = edge['target']
            if source in adj and target in in_degree:
                adj[source].append(target)
                in_degree[target] += 1
        
        # BFS (Kahn's algorithm)
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
    
    def _check_enrichment_cache(self, project_id: str) -> Optional[Dict]:
        """
        Controlla se esiste una cache valida per il progetto.
        Restituisce il risultato dalla cache se esiste, None altrimenti.
        """
        import pandas as pd
        
        output_dir = self.results_dir / project_id
        
        if not output_dir.exists():
            return None
        
        # Cerca file output esistenti
        cached_files = list(output_dir.glob("*.*"))
        
        if not cached_files:
            return None
        
        # Prendi il file più recente
        output_file = max(cached_files, key=lambda x: x.stat().st_mtime)
        logger.info(f" Cache trovata: {output_file}")
        
        try:
            file_ext = output_file.suffix.lower()
            
            if file_ext == '.csv':
                df = pd.read_csv(output_file)
            elif file_ext == '.xlsx':
                df = pd.read_excel(output_file)
            elif file_ext == '.parquet':
                df = pd.read_parquet(output_file)
            elif file_ext == '.json':
                df = pd.read_json(output_file, orient='records')
            elif file_ext == '.geojson':
                import geopandas as gpd
                df = gpd.read_file(output_file)
            else:
                df = pd.read_csv(output_file)
            
            return {
                "success": True,
                "rows_count": len(df),
                "columns": list(df.columns),
                "output_file": output_file.name,
                "from_cache": True
            }
        except Exception as e:
            logger.info(f" Errore lettura cache: {e}")
            return None
    
    def _load_source_project(self, node_data: Dict):
        """
        Carica dati dal progetto sorgente.
        Usa la stessa logica di orchestrator_service per uniformità.
        Cerca in: results_dir/source_project_id/
        """
        import pandas as pd
        
        source_project_id = node_data.get('source_project_id')
        
        if not source_project_id:
            raise ValueError("source_project_id non specificato")
        
        # Directory output - STESSA usata da Manipola (orchestrator)
        output_dir = self.results_dir / source_project_id
        
        logger.info(f" Cercando output in: {output_dir}")
        
        if not output_dir.exists():
            raise ValueError(f"Nessun file output trovato per progetto '{source_project_id}'")
        
        # Cerca file nella directory del progetto
        cached_files = list(output_dir.glob("*.*"))
        
        if not cached_files:
            raise ValueError(f"Nessun file output trovato per progetto '{source_project_id}'")
        
        # Prendi il file più recente
        output_file = max(cached_files, key=lambda x: x.stat().st_mtime)
        logger.info(f" Caricamento sorgente: {output_file}")
        
        file_ext = output_file.suffix.lower()
        
        if file_ext == '.csv':
            return pd.read_csv(output_file)
        elif file_ext == '.xlsx':
            return pd.read_excel(output_file)
        elif file_ext == '.parquet':
            return pd.read_parquet(output_file)
        elif file_ext == '.json':
            return pd.read_json(output_file, orient='records')
        elif file_ext == '.geojson':
            import geopandas as gpd
            return gpd.read_file(output_file)
        else:
            # Prova come CSV di default
            return pd.read_csv(output_file)
    
    def _execute_api_generator(self, api_config: Dict):
        """Esegue API generator - una sola chiamata, genera DataFrame da response."""
        import pandas as pd
        
        endpoint = api_config.get('endpoint', {})
        url = endpoint.get('url', '')
        method = endpoint.get('method', 'GET').upper()
        auth = api_config.get('auth', {})
        static_params = api_config.get('static_parameters', {})
        output_mapping = api_config.get('output_mapping', [])
        
        if not url:
            raise ValueError("URL API non configurato")
        
        # Ottieni token se OAuth2
        headers = {"Accept": "application/json"}
        auth_type = auth.get('type', 'none')
        
        if auth_type == 'oauth2_password':
            token = self._get_oauth2_token_for_enrichment(auth)
            if token:
                headers['Authorization'] = f"Bearer {token}"
        elif auth_type == 'api_key':
            if auth.get('location', 'header') == 'header':
                headers[auth.get('key_name', 'X-API-Key')] = auth.get('key_value', '')
        
        # Prepara parametri statici
        params = dict(static_params)
        
        # API Key in query param
        if auth_type == 'api_key' and auth.get('location') == 'query':
            params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
        
        logger.info(f" Generator API call: {method} {url}")
        logger.info(f" Params: {params}")
        
        # Esegui chiamata
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=60)
        elif method == 'POST':
            response = requests.post(url, json=params, headers=headers, timeout=60)
        else:
            raise ValueError(f"Metodo {method} non supportato")
        
        if response.status_code != 200:
            raise ValueError(f"API error: HTTP {response.status_code} - {response.text[:200]}")
        
        data = response.json()
        
        # Converti response in DataFrame
        rows = self._extract_rows_from_response(data, output_mapping)
        
        if not rows:
            logger.warning("Nessuna riga estratta dalla response")
            return pd.DataFrame()
        
        result_df = pd.DataFrame(rows)
        logger.info(f" Generator: generate {len(result_df)} righe")
        
        return result_df
    
    def _extract_rows_from_response(self, data: Any, output_mapping: List[Dict]) -> List[Dict]:
        """Estrae righe dalla response API per generator."""
        rows = []
        
        # Caso GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            features = data.get('features', [])
            for feature in features:
                row = {}
                for mapping in output_mapping:
                    path = mapping.get('path', '')
                    col_name = mapping.get('column', path)
                    
                    # Adatta path per feature singola
                    adapted_path = path
                    if path.startswith('features['):
                        parts = path.split('.', 1)
                        if len(parts) > 1:
                            adapted_path = parts[1]
                        else:
                            adapted_path = ''
                    
                    value = self._extract_json_value_for_enrichment(feature, adapted_path)
                    row[col_name] = value
                
                rows.append(row)
        
        # Caso array semplice
        elif isinstance(data, list):
            for item in data:
                row = {}
                for mapping in output_mapping:
                    path = mapping.get('path', '')
                    col_name = mapping.get('column', path)
                    value = self._extract_json_value_for_enrichment(item, path)
                    row[col_name] = value
                rows.append(row)
        
        # Caso oggetto singolo
        elif isinstance(data, dict):
            row = {}
            for mapping in output_mapping:
                path = mapping.get('path', '')
                col_name = mapping.get('column', path)
                value = self._extract_json_value_for_enrichment(data, path)
                row[col_name] = value
            rows.append(row)
        
        return rows
    
    def _execute_api_enricher(self, df, api_config: Dict, param_mapping: Dict):
        """Esegue API enricher - chiama API per ogni riga del DataFrame."""
        import pandas as pd
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
        
        # Ottieni token se OAuth2
        headers = {"Accept": "application/json"}
        auth_type = auth.get('type', 'none')
        
        if auth_type == 'oauth2_password':
            token = self._get_oauth2_token_for_enrichment(auth)
            if token:
                headers['Authorization'] = f"Bearer {token}"
        elif auth_type == 'api_key':
            if auth.get('location', 'header') == 'header':
                headers[auth.get('key_name', 'X-API-Key')] = auth.get('key_value', '')
        
        # Rate limiting
        rate_limit = api_config.get('options', {}).get('rate_limit', 5)
        delay = 1.0 / rate_limit if rate_limit > 0 else 0
        
        # Chiama API per ogni riga
        total_rows = len(df)
        success_count = 0
        error_count = 0
        
        for idx, row in df.iterrows():
            try:
                # Costruisci parametri
                params = {}
                for api_param, df_column in param_mapping.items():
                    if df_column and df_column in row:
                        value = row[df_column]
                        import pandas as pd
                        if pd.isna(value):
                            value = None
                        else:
                            value = str(value)
                        params[api_param] = value
                
                # Salta se parametri obbligatori mancanti
                required_params = [p['name'] for p in api_config.get('parameters', []) if p.get('required')]
                missing = [p for p in required_params if not params.get(p)]
                
                if missing:
                    logger.info(f" Riga {idx}: parametri mancanti {missing}, skip")
                    error_count += 1
                    continue
                
                # API Key in query param
                if auth_type == 'api_key' and auth.get('location') == 'query':
                    params[auth.get('key_name', 'apiKey')] = auth.get('key_value', '')
                
                # Esegui chiamata
                if method == 'GET':
                    response = requests.get(url, params=params, headers=headers, timeout=30)
                elif method == 'POST':
                    response = requests.post(url, json=params, headers=headers, timeout=30)
                else:
                    raise ValueError(f"Metodo {method} non supportato")
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Estrai valori secondo mapping
                    for mapping in output_mapping:
                        path = mapping.get('path', '')
                        col_name = mapping.get('column', path)
                        
                        value = self._extract_json_value_for_enrichment(data, path)
                        result_df.at[idx, col_name] = value
                    
                    success_count += 1
                else:
                    logger.info(f" Riga {idx}: HTTP {response.status_code}")
                    error_count += 1
                
                # Rate limiting
                if delay > 0:
                    time.sleep(delay)
                    
            except Exception as e:
                logger.info(f" Riga {idx}: errore - {str(e)}")
                error_count += 1
                continue
            
            # Progress log ogni 100 righe
            if (idx + 1) % 100 == 0:
                logger.info(f" Progresso: {idx + 1}/{total_rows} (OK: {success_count}, ERR: {error_count})")
        
        logger.info(f" Completato: {success_count}/{total_rows} righe arricchite, {error_count} errori")
        
        return result_df
    
    def _get_oauth2_token_for_enrichment(self, auth: Dict) -> Optional[str]:
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
                data = response.json()
                return data.get('access_token')
            else:
                logger.info(f" OAuth2 login fallito: {response.status_code}")
                return None
                
        except Exception as e:
            logger.info(f" OAuth2 login errore: {str(e)}")
            return None
    
    def _extract_json_value_for_enrichment(self, data: Any, path: str) -> Any:
        """Estrae valore da JSON usando path semplificato."""
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
        
        # Se è geometry, serializza come JSON string
        if isinstance(current, dict) and current.get('type') in ['Point', 'Polygon', 'MultiPolygon', 'LineString', 'MultiLineString']:
            return json.dumps(current)
        
        return current
    
    def _save_enrichment_output(self, project_id: str, df, format: str, filename: str = 'output') -> str:
        """
        Salva DataFrame come file.
        Usa la stessa struttura di Manipola: results_dir/project_id/filename.ext
        """
        from datetime import datetime
        
        # Stessa struttura di Manipola
        output_dir = self.results_dir / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Nome file dalla configurazione del nodo result
        output_filename = f"{filename}.{format}"
        output_path = output_dir / output_filename
        
        # Formati geospaziali
        geo_formats = ['geojson', 'gpkg', 'shp']
        
        if format == 'csv':
            df.to_csv(output_path, index=False)
        elif format == 'xlsx':
            df.to_excel(output_path, index=False)
        elif format == 'parquet':
            df.to_parquet(output_path, index=False)
        elif format in geo_formats:
            # Cerca colonna geometry
            geom_col = None
            for col in df.columns:
                if 'geometry' in col.lower() or col.lower() in ['geom', 'the_geom', 'shape']:
                    geom_col = col
                    break
            
            if geom_col:
                import geopandas as gpd
                from shapely.geometry import shape
                from shapely import wkt, wkb
                
                geometries = []
                for val in df[geom_col]:
                    if val is None:
                        geometries.append(None)
                    elif isinstance(val, str):
                        # Prova GeoJSON
                        if val.startswith('{'):
                            try:
                                geom_dict = json.loads(val)
                                geometries.append(shape(geom_dict))
                                continue
                            except:
                                pass
                        # Prova WKT
                        if any(val.upper().startswith(prefix) for prefix in ['POINT', 'LINE', 'POLYGON', 'MULTI', 'GEOMETRY']):
                            try:
                                geometries.append(wkt.loads(val))
                                continue
                            except:
                                pass
                        # Prova WKB hex
                        if val.startswith('01') and len(val) > 20:
                            try:
                                geometries.append(wkb.loads(val, hex=True))
                                continue
                            except:
                                pass
                        geometries.append(None)
                    else:
                        geometries.append(None)
                
                gdf = gpd.GeoDataFrame(df.drop(columns=[geom_col]), geometry=geometries, crs="EPSG:4326")
                
                # Salva nel formato richiesto
                if format == 'geojson':
                    gdf.to_file(output_path, driver='GeoJSON')
                elif format == 'gpkg':
                    gdf.to_file(output_path, driver='GPKG')
                elif format == 'shp':
                    gdf.to_file(output_path, driver='ESRI Shapefile')
                    
                logger.info(f" Output geospaziale salvato: {output_path}")
            else:
                # Fallback a CSV se non trova geometrie
                logger.warning(f"Nessuna colonna geometry trovata, fallback a CSV")
                output_filename = f"{filename}.csv"
                output_path = output_dir / output_filename
                df.to_csv(output_path, index=False)
        else:
            df.to_csv(output_path, index=False)
        
        logger.info(f" Output salvato: {output_path}")
        return output_filename


# Singleton per uso globale
api_connection_service = ApiConnectionService()