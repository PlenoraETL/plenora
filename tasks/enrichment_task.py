"""
Enrichment Task - Task Celery per arricchimento dati asincrono.

Questo task esegue l'arricchimento in background, aggiornando
il progress in Redis per permettere al frontend di mostrare
una barra di avanzamento.
"""

import time
import json
from typing import Dict, Any, Optional
from datetime import datetime
from celery import current_task
from celery_app import celery_app
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(bind=True, name="tasks.execute_enrichment")
def execute_enrichment_task(
    self,
    project_id: str,
    canvas: Dict,
    output_format: str = "csv",
    force_recalculate: bool = False
) -> Dict[str, Any]:
    """
    Task asincrono per eseguire l'arricchimento dati.
    
    Args:
        project_id: ID del progetto
        canvas: Canvas con nodi e edges
        output_format: Formato output (csv, xlsx, geojson, etc.)
        force_recalculate: Se True, riesegue anche se esiste cache
        
    Returns:
        Dict con risultato dell'esecuzione
    """
    task_id = self.request.id
    start_time = time.time()
    
    logger.info(f"[Task {task_id}] Avvio enrichment per progetto {project_id}")
    
    # Aggiorna stato: STARTED
    update_task_progress(self, 0, "Inizializzazione...")
    
    try:
        # Import qui per evitare circular imports
        from services.api_connection_service import api_connection_service
        
        nodes = canvas.get("nodes", [])
        edges = canvas.get("edges", [])
        
        if not nodes:
            raise ValueError("Nessun nodo nel canvas")
        
        # Conta righe totali per progress
        total_rows = estimate_total_rows(nodes, edges)
        processed_rows = 0
        
        update_task_progress(self, 5, f"Caricamento dati ({total_rows} righe stimate)...")
        
        # Carica API connections
        api_connections = {}
        for api in api_connection_service.list_api_connections():
            full_api = api_connection_service.get_api_connection(api["id"], include_token=True)
            if full_api:
                api_connections[api["id"]] = full_api
        
        update_task_progress(self, 10, "API caricate, inizio elaborazione...")
        
        # Ordine esecuzione topologico
        execution_order = get_execution_order(nodes, edges)
        node_results = {}
        
        # Esegui nodi in ordine
        for idx, node_id in enumerate(execution_order):
            node = next((n for n in nodes if n["id"] == node_id), None)
            if not node:
                continue
            
            node_type = node.get("type")
            node_data = node.get("data", {})
            
            # Calcola progress base per questo nodo
            node_progress = 10 + int((idx / len(execution_order)) * 80)
            
            if node_type == "project_source":
                update_task_progress(self, node_progress, f"Caricamento sorgente dati...")
                result = load_source_project(node_data, force_recalculate)
                node_results[node_id] = result
                
            elif node_type == "file_source":
                update_task_progress(self, node_progress, f"Caricamento file...")
                result = load_file_source(node_data)
                node_results[node_id] = result
                
            elif node_type == "api_enrich":
                api_id = node_data.get("api_id")
                api_config = api_connections.get(api_id)
                
                if not api_config:
                    raise ValueError(f"API '{api_id}' non trovata")
                
                api_type = api_config.get("api_type", "enricher")
                api_name = api_config.get("name", api_id)
                
                if api_type == "generator":
                    update_task_progress(self, node_progress, f"Chiamata API {api_name}...")
                    result = execute_api_generator(api_config)
                else:
                    # Enricher - elabora riga per riga con progress
                    input_edge = next((e for e in edges if e["target"] == node_id), None)
                    if not input_edge:
                        raise ValueError(f"Nodo {node_id} non ha input")
                    
                    input_df = node_results.get(input_edge["source"])
                    if input_df is None:
                        raise ValueError(f"Dati input non trovati per {node_id}")
                    
                    param_mapping = node_data.get("param_mapping", {})
                    
                    # Esegui con progress callback
                    result = execute_api_enricher_with_progress(
                        self,
                        input_df,
                        api_config,
                        param_mapping,
                        base_progress=node_progress,
                        api_name=api_name
                    )
                
                node_results[node_id] = result
                
            elif node_type == "result":
                input_edge = next((e for e in edges if e["target"] == node_id), None)
                if input_edge:
                    input_df = node_results.get(input_edge["source"])
                    if input_df is not None:
                        node_results[node_id] = input_df
        
        update_task_progress(self, 90, "Salvataggio risultati...")
        
        # Trova output finale
        result_node = next((n for n in nodes if n.get("type") == "result"), None)
        result_data = result_node.get("data", {}) if result_node else {}
        
        final_format = result_data.get("format", output_format) or output_format
        output_filename = result_data.get("fileName", "output") or "output"
        
        final_node_id = execution_order[-1] if execution_order else None
        final_df = node_results.get(final_node_id)
        
        if final_df is None or (hasattr(final_df, "empty") and final_df.empty):
            raise ValueError("Nessun risultato finale")
        
        # Salva output
        from utils.dataframe_utils import save_dataframe
        
        output_dir = settings.RESULTS_DIR / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_filename
        
        saved_path = save_dataframe(
            df=final_df,
            output_path=output_path,
            format=final_format
        )
        
        elapsed_time = time.time() - start_time
        
        update_task_progress(self, 100, "Completato!")
        
        result = {
            "success": True,
            "task_id": task_id,
            "project_id": project_id,
            "rows_processed": len(final_df),
            "columns": list(final_df.columns),
            "output_file": saved_path.name,
            "output_format": final_format,
            "elapsed_time": round(elapsed_time, 2),
            "completed_at": datetime.now().isoformat()
        }
        
        logger.info(f"[Task {task_id}] Completato in {elapsed_time:.2f}s - {len(final_df)} righe")
        return result
        
    except Exception as e:
        logger.error(f"[Task {task_id}] Errore: {str(e)}")
        update_task_progress(self, -1, f"Errore: {str(e)}")
        
        return {
            "success": False,
            "task_id": task_id,
            "project_id": project_id,
            "error": str(e),
            "failed_at": datetime.now().isoformat()
        }


def update_task_progress(task, progress: int, message: str):
    """
    Aggiorna il progress del task in Redis.
    
    Args:
        task: Istanza task Celery
        progress: Percentuale (0-100, -1 per errore)
        message: Messaggio stato
    """
    task.update_state(
        state="PROGRESS" if progress >= 0 else "FAILURE",
        meta={
            "progress": progress,
            "message": message,
            "updated_at": datetime.now().isoformat()
        }
    )


def estimate_total_rows(nodes: list, edges: list) -> int:
    """Stima il numero totale di righe da elaborare."""
    # TODO: Implementare stima più accurata
    return 1000


def get_execution_order(nodes: list, edges: list) -> list:
    """Calcola ordine topologico dei nodi."""
    in_degree = {n["id"]: 0 for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    
    for edge in edges:
        source, target = edge["source"], edge["target"]
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


def load_source_project(node_data: Dict, force_recalculate: bool = False):
    """Carica dati dal progetto sorgente."""
    import pandas as pd
    from utils.dataframe_utils import load_dataframe_from_file, filter_visible_columns, find_latest_output_file
    
    source_project_id = node_data.get("source_project_id")
    
    if not source_project_id:
        raise ValueError("source_project_id non specificato")
    
    output_dir = settings.RESULTS_DIR / source_project_id
    
    # Ricalcola se necessario
    if force_recalculate or not output_dir.exists():
        logger.info(f"Riesecuzione progetto upstream: {source_project_id}")
        from services.orchestrator_service import OrchestratorService
        from services.project_service import ProjectService
        from services.query_executor import QueryExecutor
        from services.query_generator import QueryGenerator
        from services.connection_service import ConnectionService
        
        project_service = ProjectService()
        executor = QueryExecutor()
        generator = QueryGenerator()
        connection_service = ConnectionService()
        
        orchestrator = OrchestratorService(
            project_service=project_service,
            executor=executor,
            generator=generator,
            connection_service=connection_service
        )
        
        upstream_result = orchestrator.execute_pipeline(source_project_id, force_recalculate=force_recalculate)
        if not upstream_result.get("success"):
            raise ValueError(f"Errore progetto sorgente: {upstream_result.get('message')}")
    
    output_file = find_latest_output_file(output_dir)
    if not output_file:
        raise ValueError(f"Nessun output per progetto '{source_project_id}'")
    
    df = load_dataframe_from_file(output_file)
    
    node_columns = node_data.get("columns", [])
    df = filter_visible_columns(df, node_columns)
    
    logger.info(f"Caricato progetto {source_project_id}: {len(df)} righe")
    return df


def load_file_source(node_data: Dict):
    """Carica dati da file source."""
    from utils.dataframe_utils import filter_visible_columns
    
    file_source_id = node_data.get("file_source_id")
    
    if not file_source_id:
        raise ValueError("file_source_id non specificato")
    
    from services.file_source_service import file_source_service
    df, _ = file_source_service.load_dataframe(file_source_id)
    
    node_columns = node_data.get("columns", [])
    df = filter_visible_columns(df, node_columns)
    
    logger.info(f"Caricato file source {file_source_id}: {len(df)} righe")
    return df


def execute_api_generator(api_config: Dict):
    """Esegue API generator - genera DataFrame da response."""
    import pandas as pd
    import requests
    
    endpoint = api_config.get("endpoint", {})
    url = endpoint.get("url", "")
    method = endpoint.get("method", "GET").upper()
    auth = api_config.get("auth", {})
    static_params = api_config.get("static_parameters", {})
    output_mapping = api_config.get("output_mapping", [])
    
    if not url:
        raise ValueError("URL API non configurato")
    
    headers = prepare_headers(auth)
    params = dict(static_params)
    
    if auth.get("type") == "api_key" and auth.get("location") == "query":
        params[auth.get("key_name", "apiKey")] = auth.get("key_value", "")
    
    logger.info(f"Generator API call: {method} {url}")
    
    if method == "GET":
        response = requests.get(url, params=params, headers=headers, timeout=60)
    else:
        response = requests.post(url, json=params, headers=headers, timeout=60)
    
    if response.status_code != 200:
        raise ValueError(f"API error: HTTP {response.status_code}")
    
    data = response.json()
    rows = extract_rows_from_response(data, output_mapping)
    
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def execute_api_enricher_with_progress(
    task,
    df,
    api_config: Dict,
    param_mapping: Dict,
    base_progress: int = 10,
    api_name: str = "API"
):
    """
    Esegue API enricher con aggiornamento progress.
    
    Args:
        task: Istanza task Celery per aggiornare progress
        df: DataFrame input
        api_config: Configurazione API
        param_mapping: Mapping parametri
        base_progress: Progress di partenza
        api_name: Nome API per messaggi
    """
    import pandas as pd
    import requests
    import time
    
    result_df = df.copy()
    
    endpoint = api_config.get("endpoint", {})
    url = endpoint.get("url", "")
    method = endpoint.get("method", "GET").upper()
    auth = api_config.get("auth", {})
    output_mapping = api_config.get("output_mapping", [])
    
    if not url:
        raise ValueError("URL API non configurato")
    
    # Prepara colonne output
    for mapping in output_mapping:
        col_name = mapping.get("column", mapping.get("path"))
        if col_name not in result_df.columns:
            result_df[col_name] = None
    
    headers = prepare_headers(auth)
    
    rate_limit = api_config.get("options", {}).get("rate_limit", 5)
    delay = 1.0 / rate_limit if rate_limit > 0 else 0
    
    success_count = 0
    error_count = 0
    total_rows = len(df)
    
    # Progress range: base_progress -> base_progress + 20
    progress_range = 20
    
    for idx, row in df.iterrows():
        try:
            # Costruisci parametri
            params = {}
            for api_param, df_column in param_mapping.items():
                if df_column and df_column in row:
                    value = row[df_column]
                    params[api_param] = str(value) if pd.notna(value) else None
            
            # API Key in query
            if auth.get("type") == "api_key" and auth.get("location") == "query":
                params[auth.get("key_name", "apiKey")] = auth.get("key_value", "")
            
            # Chiamata API
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=30)
            else:
                response = requests.post(url, json=params, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                for mapping in output_mapping:
                    path = mapping.get("path", "")
                    col_name = mapping.get("column", path)
                    result_df.at[idx, col_name] = extract_json_value(data, path)
                success_count += 1
            else:
                error_count += 1
            
            if delay > 0:
                time.sleep(delay)
                
        except Exception as e:
            logger.debug(f"Riga {idx}: errore - {str(e)}")
            error_count += 1
        
        # Aggiorna progress ogni 10 righe o alla fine
        if (idx + 1) % 10 == 0 or (idx + 1) == total_rows:
            row_progress = (idx + 1) / total_rows
            current_progress = base_progress + int(row_progress * progress_range)
            update_task_progress(
                task,
                current_progress,
                f"{api_name}: {idx + 1}/{total_rows} righe ({success_count} OK, {error_count} errori)"
            )
    
    logger.info(f"Completato: {success_count}/{total_rows} righe, {error_count} errori")
    return result_df


def prepare_headers(auth: Dict) -> Dict:
    """Prepara headers per chiamata API."""
    import requests
    
    headers = {"Accept": "application/json"}
    auth_type = auth.get("type", "none")
    
    if auth_type == "oauth2_password":
        token = get_oauth2_token(auth)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key":
        if auth.get("location", "header") == "header":
            headers[auth.get("key_name", "X-API-Key")] = auth.get("key_value", "")
    
    return headers


def get_oauth2_token(auth: Dict) -> Optional[str]:
    """Ottiene token OAuth2 con password flow."""
    import requests
    
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")
    
    if not token_url or not username:
        return None
    
    try:
        response = requests.post(
            token_url,
            data={
                "grant_type": "password",
                "username": username,
                "password": password
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json().get("access_token")
        return None
    except Exception:
        return None


def extract_rows_from_response(data: Any, output_mapping: list) -> list:
    """Estrae righe dalla response API."""
    rows = []
    
    # GeoJSON FeatureCollection
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        for feature in data.get("features", []):
            row = {}
            for mapping in output_mapping:
                path = mapping.get("path", "")
                col_name = mapping.get("column", path)
                if path.startswith("features["):
                    parts = path.split(".", 1)
                    path = parts[1] if len(parts) > 1 else ""
                row[col_name] = extract_json_value(feature, path)
            rows.append(row)
    
    # Array
    elif isinstance(data, list):
        for item in data:
            row = {m.get("column", m.get("path")): extract_json_value(item, m.get("path", "")) 
                   for m in output_mapping}
            rows.append(row)
    
    # Oggetto singolo
    elif isinstance(data, dict):
        row = {m.get("column", m.get("path")): extract_json_value(data, m.get("path", "")) 
               for m in output_mapping}
        rows.append(row)
    
    return rows


def extract_json_value(data: Any, path: str) -> Any:
    """Estrae valore da JSON usando path."""
    if not path:
        return data
    
    parts = path.replace("]", "").replace("[", ".").split(".")
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
    if isinstance(current, dict) and current.get("type") in ["Point", "Polygon", "MultiPolygon", "LineString"]:
        return json.dumps(current)
    
    return current
