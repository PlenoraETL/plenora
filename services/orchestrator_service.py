"""
Orchestrator Service - Gestisce l'esecuzione delle pipeline.

REFACTORED: Usa utils/dataframe_utils.py e utils/geo_utils.py
per eliminare codice duplicato.

Responsabilità:
- Orchestrazione pipeline ESTRAI, MANIPOLA, ARRICCHISCI
- Esecuzione ricorsiva delle dipendenze
- Gestione cache risultati
"""

import pandas as pd
import geopandas as gpd
from pathlib import Path
from typing import Dict, Any, List, Optional

from models import CanvasState, Node
from services.project_service import ProjectService
from services.query_executor import QueryExecutor
from services.query_generator import QueryGenerator
from services.connection_service import ConnectionService
from config import settings
from utils.logger import get_logger

# Import nuove utility centralizzate
from utils.dataframe_utils import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    sanitize_for_json,
    find_latest_output_file,
    infer_column_types
)
from utils.geo_utils import (
    to_geodataframe,
    detect_geometry_column,
    geometry_to_wkt,
    ensure_same_crs,
    get_utm_crs_for_geometry,
    extract_srid_from_column_type
)

logger = get_logger(__name__)


class OrchestratorService:
    """
    Gestisce l'esecuzione a cascata dei progetti.
    Se un progetto è di tipo 'manipola', esegue ricorsivamente i progetti sorgente.
    """
    
    def __init__(
        self, 
        project_service: ProjectService, 
        executor: QueryExecutor, 
        generator: QueryGenerator,
        connection_service: ConnectionService
    ):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.results_base_dir = settings.RESULTS_DIR

    # =========================================================================
    # ENTRY POINTS
    # =========================================================================

    def execute_pipeline(self, project_id: str, force_recalculate: bool = False) -> Dict[str, Any]:
        """Punto di ingresso principale: decide se estrarre o manipolare."""
        logger.info(f"Esecuzione pipeline progetto: {project_id}")
        logger.debug(f"Force recalculate: {force_recalculate}")
        
        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)
        
        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}

        logger.debug(f"Tipo progetto: {metadata.project_type}")
        
        if metadata.project_type == "estrai":
            return self._execute_extract(project_id, canvas)
        elif metadata.project_type == "manipola":
            return self._execute_manipulate(project_id, canvas, force_recalculate)
        elif metadata.project_type == "arricchisci":
            return self._execute_enrich(project_id, canvas, force_recalculate)

        return {"success": False, "message": f"Tipo progetto '{metadata.project_type}' non supportato"}

    def execute_preview(self, project_id: str, limit: int = 100) -> Dict[str, Any]:
        """Esegue una preview dei dati senza salvare file."""
        logger.info(f"Preview progetto: {project_id} (limit={limit})")
        
        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)
        
        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}
        
        if metadata.project_type == "estrai":
            return self._preview_extract(project_id, canvas, limit)
        elif metadata.project_type == "manipola":
            return self._preview_manipulate(project_id, canvas, limit)
        else:
            return {"success": False, "message": f"Preview non supportata per tipo '{metadata.project_type}'"}

    # =========================================================================
    # EXECUTE ESTRAI
    # =========================================================================

    def _execute_extract(self, project_id: str, canvas: CanvasState) -> Dict[str, Any]:
        """Esegue query SQL e salva il file."""
        logger.info(f"Esecuzione progetto ESTRAI: {project_id}")
        
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return {"success": False, "message": "Il progetto non ha una connessione database associata"}

        connection = self.connection_service.get_connection(metadata.default_connection_id)
        if not connection:
            return {"success": False, "message": f"Connessione {metadata.default_connection_id} non trovata"}

        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, 
                canvas.edges, 
                result_node.id,
                db_type=connection.type
            )
        except Exception as e:
            return {"success": False, "message": f"Errore generazione SQL: {str(e)}"}

        res = self.executor.execute_query(
            connection=connection,
            sql=query_info['sql'],
            project_id=project_id,
            output_filename=result_node.fileName or 'output',
            output_format=result_node.format or 'csv',
            params=query_info.get('params')
        )
        
        # Salva tipi colonne nel metadata
        if res.get("success"):
            self._save_column_types_from_canvas(project_id, canvas, res)
        
        res['query'] = query_info['sql']
        return res

    def _save_column_types_from_canvas(self, project_id: str, canvas: CanvasState, res: Dict):
        """Salva i tipi colonne dal canvas nel metadata del progetto."""
        try:
            canvas_type_map = {}
            for node in canvas.nodes:
                if node.type == "source" and node.columns:
                    for col in node.columns:
                        col_name = col.alias or col.name
                        col_type = col.type or "str"
                        canvas_type_map[col_name] = col_type
                if node.calculated_columns:
                    for calc in node.calculated_columns:
                        canvas_type_map[calc.get("name")] = calc.get("output_type", "str")
            
            output_path = Path(res["output_path"])
            df_for_cols = load_dataframe_from_file(output_path)
            
            columns_metadata = []
            for col in df_for_cols.columns:
                col_type = canvas_type_map.get(col, 'str')
                columns_metadata.append({"name": col, "type": col_type})
            
            self.project_service.update_project_columns_metadata(project_id, columns_metadata)
            res['columns_metadata'] = columns_metadata
            logger.info(f"Salvati tipi per {len(columns_metadata)} colonne")
            
        except Exception as e:
            logger.warning(f"Errore salvataggio tipi colonne: {e}")

    # =========================================================================
    # EXECUTE MANIPOLA
    # =========================================================================

    def _execute_manipulate(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False) -> Dict[str, Any]:
        """Esegue la pipeline di manipolazione seguendo il grafo dei nodi."""
        logger.info(f"Esecuzione progetto Manipola: {project_id}")
        
        # A. Carica tutti i DataFrame sorgente
        dataframes = {}
        
        # A.1 Progetti sorgente
        load_result = self._load_project_sources(canvas, dataframes, force_recalculate)
        if load_result:
            return load_result  # Errore
        
        # A.2 File source
        load_result = self._load_file_sources(canvas, dataframes)
        if load_result:
            return load_result  # Errore

        logger.info(f"Caricati {len(dataframes)} dataframes totali")
        
        # B. Applica trasformazioni
        current_df = self._apply_transformations(canvas, dataframes)
        
        if current_df is None:
            return {"success": False, "message": "Nessun dato prodotto dalla manipolazione"}

        # C. Nodo Risultato finale
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        # Applica GROUP BY se configurato
        current_df = self._apply_result_node_groupby(current_df, result_node)

        return self._save_manipulation_result(project_id, result_node, current_df, canvas)

    def _load_project_sources(self, canvas: CanvasState, dataframes: Dict, force_recalculate: bool) -> Optional[Dict]:
        """Carica DataFrame dai progetti sorgente. Ritorna errore dict se fallisce."""
        source_nodes = [n for n in canvas.nodes if n.type == "project_source"]
        logger.info(f"Trovati {len(source_nodes)} progetti sorgente")
        
        for node in source_nodes:
            source_project_id = node.data.get('source_project_id') if node.data else None
            if not source_project_id:
                logger.warning(f"Nodo {node.id} non ha source_project_id configurato")
                continue
            
            output_dir = self.results_base_dir / source_project_id
            cached_file = find_latest_output_file(output_dir)
            
            # Usa cache se disponibile e non forzato ricalcolo
            if not force_recalculate and cached_file:
                logger.info(f"Usando cache esistente: {source_project_id}")
                try:
                    df = load_dataframe_from_file(cached_file)
                    node_columns = node.data.get('columns', []) if node.data else []
                    df = filter_visible_columns(df, node_columns)
                    dataframes[node.id] = df
                    logger.info(f"Caricato da cache: {len(df)} righe")
                    continue
                except Exception as e:
                    logger.warning(f"Errore caricamento cache: {e}, procedo con ricalcolo")
            
            # Ricalcola progetto upstream
            logger.info(f"Ricalcolo progetto upstream: {source_project_id}")
            upstream = self.execute_pipeline(source_project_id, force_recalculate=True)
            if not upstream.get("success"):
                return upstream
            
            file_path = Path(upstream["output_path"])
            if not file_path.exists():
                return {"success": False, "message": f"File risultato non trovato: {file_path}"}
            
            try:
                df = load_dataframe_from_file(file_path)
                node_columns = node.data.get('columns', []) if node.data else []
                df = filter_visible_columns(df, node_columns)
                dataframes[node.id] = df
                logger.info(f"Caricato progetto {source_project_id}: {len(df)} righe")
            except Exception as e:
                return {"success": False, "message": f"Errore caricamento progetto {source_project_id}: {str(e)}"}
        
        return None  # Nessun errore

    def _load_file_sources(self, canvas: CanvasState, dataframes: Dict) -> Optional[Dict]:
        """Carica DataFrame dai file source. Ritorna errore dict se fallisce."""
        file_source_nodes = [n for n in canvas.nodes if n.type == "file_source"]
        logger.info(f"Trovati {len(file_source_nodes)} file source")
        
        for node in file_source_nodes:
            file_source_id = node.data.get('file_source_id') if node.data else None
            if not file_source_id:
                file_source_id = getattr(node, 'file_source_id', None)
            
            if not file_source_id:
                logger.warning(f"Nodo {node.id} non ha file_source_id configurato")
                continue
            
            try:
                from services.file_source_service import file_source_service
                df, gdf = file_source_service.load_dataframe(file_source_id)
                
                node_columns = node.data.get('columns', []) if node.data else []
                df = filter_visible_columns(df, node_columns)
                dataframes[node.id] = df
                logger.info(f"Caricato file source {file_source_id}: {len(df)} righe")
                
            except Exception as e:
                logger.error(f"Errore caricamento file source {file_source_id}: {e}")
                return {"success": False, "message": f"Errore caricamento file '{file_source_id}': {str(e)}"}
        
        return None  # Nessun errore

    def _apply_transformations(self, canvas: CanvasState, dataframes: Dict) -> Optional[pd.DataFrame]:
        """Applica le trasformazioni definite nel canvas."""
        transform_nodes = [n for n in canvas.nodes if n.type == "transform"]
        
        if not transform_nodes:
            return self._apply_default_joins(canvas, dataframes)
        
        current_df = None
        for node in transform_nodes:
            incoming_edges = [e for e in canvas.edges if e.target == node.id]
            inputs = [dataframes[e.source] for e in incoming_edges if e.source in dataframes]
            
            if not inputs:
                logger.warning(f"Nodo transform {node.id} non ha input")
                continue
            
            transform_op = node.data.get('transform_op') if node.data else None
            logger.info(f"Applicando trasformazione '{transform_op}' su nodo {node.id}")
            
            current_df = self._dispatch_transform(node, inputs)
            dataframes[node.id] = current_df
        
        return current_df

    def _apply_default_joins(self, canvas: CanvasState, dataframes: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Gestisce caso senza nodi Transform."""
        if not dataframes:
            raise ValueError("Nessun progetto sorgente configurato")
        
        if len(dataframes) == 1:
            logger.info("Un solo progetto sorgente - restituisco direttamente il dataframe")
            return list(dataframes.values())[0]
        
        raise ValueError(
            f"Il progetto ha {len(dataframes)} sorgenti ma nessun nodo Transform. "
            "Aggiungi un nodo 'Join' per specificare come combinare i dati."
        )

    # =========================================================================
    # SAVE RESULT
    # =========================================================================

    def _save_manipulation_result(self, project_id: str, result_node: Node, df: pd.DataFrame, canvas: CanvasState = None) -> Dict[str, Any]:
        """Salva il risultato finale della manipolazione."""
        output_dir = self.results_base_dir / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        filename = result_node.data.get('fileName', 'output') if result_node.data else 'output'
        format_type = result_node.data.get('format', 'csv') if result_node.data else 'csv'
        geom_column = result_node.data.get('geomColumn', None) if result_node.data else None
        
        # Determina CRS dai metadati del canvas
        crs = "EPSG:4326"
        if canvas:
            srid = self._extract_srid_from_canvas(canvas)
            if srid:
                crs = f"EPSG:{srid}"
        
        output_path = output_dir / filename
        
        try:
            saved_path = save_dataframe(
                df=df,
                output_path=output_path,
                format=format_type,
                geom_column=geom_column,
                crs=crs
            )
            
            logger.info(f"Risultato salvato: {saved_path}")
            
            return {
                "success": True,
                "output_path": str(saved_path),
                "rows_count": len(df),
                "columns": list(df.columns),
                "message": "Pipeline completata con successo"
            }
            
        except Exception as e:
            logger.error(f"Errore salvataggio: {e}")
            # Fallback a CSV
            csv_path = output_dir / f"{filename}.csv"
            df.to_csv(csv_path, index=False)
            return {
                "success": True,
                "output_path": str(csv_path),
                "rows_count": len(df),
                "columns": list(df.columns),
                "message": f"Salvato come CSV (fallback da {format_type})"
            }

    def _extract_srid_from_canvas(self, canvas: CanvasState) -> Optional[int]:
        """Estrae SRID dai metadati delle colonne geometriche nel canvas."""
        for node in canvas.nodes:
            columns = []
            if hasattr(node, 'columns') and node.columns:
                columns = node.columns
            elif node.data and 'columns' in node.data:
                columns = node.data['columns']
            
            for col in columns:
                col_type = col.get('type', '') if isinstance(col, dict) else getattr(col, 'type', '')
                srid = extract_srid_from_column_type(col_type)
                if srid:
                    return srid
        
        return None
    # =========================================================================
    # PREVIEW
    # =========================================================================

    def _preview_extract(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
        """Preview per progetti ESTRAI - esegue SQL con LIMIT."""
        logger.info(f"Preview ESTRAI: {project_id}")
        
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}
        
        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return {"success": False, "message": "Connessione database non configurata"}
        
        connection = self.connection_service.get_connection(metadata.default_connection_id)
        if not connection:
            return {"success": False, "message": f"Connessione {metadata.default_connection_id} non trovata"}
        
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, canvas.edges, result_node.id, db_type=connection.type
            )
            
            base_sql = query_info['sql']
            params = query_info.get('params', {})
            
            import re
            sql_no_limit = re.sub(r'\s+LIMIT\s+\d+\s*$', '', base_sql, flags=re.IGNORECASE)
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"
            
            from sqlalchemy import create_engine, text
            
            if connection.type in ('postgresql', 'postgres'):
                conn_str = f"postgresql://{connection.username}:{connection.password}@{connection.host}:{connection.port}/{connection.database}"
            elif connection.type == 'mysql':
                conn_str = f"mysql+pymysql://{connection.username}:{connection.password}@{connection.host}:{connection.port}/{connection.database}"
            else:
                return {"success": False, "message": f"Tipo database '{connection.type}' non supportato per preview"}
            
            engine = create_engine(conn_str)
            
            with engine.connect() as conn:
                df = pd.read_sql(text(preview_sql), conn, params=params)
                try:
                    count_result = conn.execute(text(count_sql), params)
                    total_count = count_result.scalar() or 0
                except:
                    total_count = len(df)
            
            truncated = len(df) > limit
            if truncated:
                df = df.head(limit)
            
            columns = infer_column_types(df)
            rows = sanitize_for_json(df.to_dict(orient='records'))
            
            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "total_count": total_count,
                "preview_count": len(rows),
                "truncated": truncated,
                "query": base_sql
            }
            
        except Exception as e:
            logger.error(f"Errore preview ESTRAI: {e}")
            return {"success": False, "message": str(e)}

    def _preview_manipulate(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
        """Preview per progetti MANIPOLA."""
        logger.info(f"Preview MANIPOLA: {project_id}")
        
        try:
            dataframes = {}
            
            # Carica sorgenti (usa cache)
            self._load_project_sources(canvas, dataframes, force_recalculate=False)
            self._load_file_sources(canvas, dataframes)
            
            if not dataframes:
                return {"success": False, "message": "Nessun dato sorgente disponibile"}
            
            # Applica trasformazioni
            current_df = self._apply_transformations(canvas, dataframes)
            
            if current_df is None:
                return {"success": False, "message": "Nessun dato prodotto dalla pipeline"}
            
            # Applica GROUP BY
            result_node = next((n for n in canvas.nodes if n.type == "result"), None)
            if result_node:
                current_df = self._apply_result_node_groupby(current_df, result_node)
            
            # Limita e prepara output
            total_count = len(current_df)
            truncated = total_count > limit
            preview_df = current_df.head(limit)
            
            columns = infer_column_types(preview_df)
            rows = sanitize_for_json(preview_df.to_dict(orient='records'))
            
            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "total_count": total_count,
                "preview_count": len(rows),
                "truncated": truncated
            }
            
        except Exception as e:
            logger.error(f"Errore preview MANIPOLA: {e}")
            return {"success": False, "message": str(e)}

    # =========================================================================
    # GROUP BY
    # =========================================================================

    def _apply_result_node_groupby(self, df: pd.DataFrame, result_node: Node) -> pd.DataFrame:
        """Applica GROUP BY configurato nel result node."""
        if not result_node.data:
            return df
            
        if not result_node.data.get('group_by_enabled', False):
            return df
            
        group_cols = result_node.data.get('group_by_columns', [])
        aggregations = result_node.data.get('aggregations', [])
        
        if not group_cols and not aggregations:
            return df
        
        logger.info(f"Applicando GROUP BY: columns={group_cols}, aggs={len(aggregations)}")
        
        try:
            agg_dict = {}
            rename_dict = {}
            
            for agg in aggregations:
                col = agg.get('column', '*') if isinstance(agg, dict) else getattr(agg, 'column', '*')
                func = (agg.get('function', 'COUNT') if isinstance(agg, dict) else getattr(agg, 'function', 'COUNT')).lower()
                alias = agg.get('alias') if isinstance(agg, dict) else getattr(agg, 'alias', None)
                
                if col == '*':
                    if len(df.columns) > 0:
                        first_col = df.columns[0]
                        agg_dict[first_col] = 'count'
                        rename_dict[first_col] = alias or 'count_all'
                elif col in df.columns:
                    agg_dict[col] = func
                    if alias:
                        rename_dict[col] = alias
            
            if not agg_dict:
                return df
            
            if not group_cols:
                result = df.agg(agg_dict).to_frame().T.reset_index(drop=True)
            else:
                valid_group_cols = [c for c in group_cols if c in df.columns]
                if not valid_group_cols:
                    return df
                result = df.groupby(valid_group_cols).agg(agg_dict).reset_index()
            
            if rename_dict:
                result = result.rename(columns=rename_dict)
            
            logger.info(f"GROUP BY completato: {result.shape}")
            return result
            
        except Exception as e:
            logger.error(f"Errore GROUP BY: {e}")
            return df

    # =========================================================================
    # TRANSFORM DISPATCHER
    # =========================================================================

    def _dispatch_transform(self, node: Node, inputs: List[pd.DataFrame]) -> pd.DataFrame:
        """Dispatcher: chiama la funzione corretta in base a transform_op."""
        op = node.data.get('transform_op') if node.data else None
        
        logger.info(f"Esecuzione transform '{op}' per nodo {node.id}")
        
        dispatch_map = {
            "join": lambda: self._op_join(inputs, node),
            "aggregate": lambda: self._op_aggregate(inputs[0] if inputs else pd.DataFrame(), node),
            "formula": lambda: self._op_formula(inputs[0] if inputs else pd.DataFrame(), node),
            "concat": lambda: self._op_concat(inputs, node),
            "filter": lambda: self._op_filter(inputs[0] if inputs else pd.DataFrame(), node),
            "sort": lambda: self._op_sort(inputs[0] if inputs else pd.DataFrame(), node),
            "distinct": lambda: self._op_distinct(inputs[0] if inputs else pd.DataFrame(), node),
            "sjoin": lambda: self._op_sjoin(inputs, node),
            "geo_buffer": lambda: self._op_geo_buffer(inputs[0] if inputs else pd.DataFrame(), node),
            "geo_centroid": lambda: self._op_geo_centroid(inputs[0] if inputs else pd.DataFrame(), node),
            "geo_area": lambda: self._op_geo_area(inputs[0] if inputs else pd.DataFrame(), node),
            "geo_clip": lambda: self._op_geo_clip(inputs, node),
        }
        
        if op in dispatch_map:
            return dispatch_map[op]()
        
        logger.warning(f"Operazione '{op}' non implementata - restituisco primo input")
        return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # TRANSFORM OPERATIONS - BASE
    # =========================================================================

    def _op_join(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Merge tra due DataFrame."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()

        config = node.data.get('config', {}) if node.data else {}
        left_df, right_df = inputs[0], inputs[1]
        left_on = config.get('left_on')
        right_on = config.get('right_on')
        how = config.get('how', 'left')

        if not left_on or not right_on:
            logger.warning("Colonne join non configurate")
            return left_df

        try:
            # Rinomina colonne con suffisso
            left_cols = {col: f"{col}_L" for col in left_df.columns if col != left_on}
            right_cols = {col: f"{col}_R" for col in right_df.columns if col != right_on}
            
            left_df = left_df.rename(columns=left_cols)
            right_df = right_df.rename(columns=right_cols)
            
            left_on_final = f"{left_on}_L" if left_on in left_cols else left_on
            right_on_final = f"{right_on}_R" if right_on in right_cols else right_on
            
            result = pd.merge(left_df, right_df, left_on=left_on_final, right_on=right_on_final, how=how)
            logger.info(f"Join completato: {result.shape}")
            return result
        except Exception as e:
            logger.error(f"Errore join: {e}")
            return left_df

    def _op_aggregate(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Raggruppamento con aggregazioni."""
        config = node.data.get('config', {}) if node.data else {}
        group_cols = config.get('group_by', [])
        aggs = config.get('aggs', {})
        
        if not group_cols or not aggs:
            return df
        
        try:
            return df.groupby(group_cols).agg(aggs).reset_index()
        except Exception as e:
            logger.error(f"Errore aggregazione: {e}")
            return df

    def _op_formula(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Aggiunge colonna calcolata."""
        import re
        
        config = node.data.get('config', {}) if node.data else {}
        new_col = config.get('new_column')
        formula = config.get('formula')
        
        if not new_col or not formula:
            return df
        
        # Detect concatenazione stringhe
        formula_no_strings = re.sub(r"'[^']*'|\"[^\"]*\"", "", formula)
        has_math_ops = any(op in formula_no_strings for op in ['*', '/', '-', '**', '%'])
        is_string_concat = '+' in formula and not has_math_ops
        
        if is_string_concat:
            try:
                tokens = re.findall(r"(\w+)|'([^']*)'|\"([^\"]*)\"", formula)
                result = None
                for token in tokens:
                    col_name, str_lit1, str_lit2 = token
                    if col_name and col_name in df.columns:
                        col_series = df[col_name].fillna('').astype(str)
                        result = col_series if result is None else result + col_series
                    elif str_lit1 or str_lit2:
                        sep = str_lit1 or str_lit2
                        if result is not None:
                            result = result + sep
                
                if result is not None:
                    df[new_col] = result
                    return df
            except Exception as e:
                logger.error(f"Errore concatenazione: {e}")
                return df
        
        # Formula numerica
        try:
            df[new_col] = df.eval(formula)
            return df
        except Exception as e:
            logger.error(f"Errore formula: {e}")
            return df

    def _op_concat(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Concatena verticalmente più DataFrame."""
        if not inputs:
            return pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        ignore_index = config.get('ignore_index', True)
        join_mode = config.get('join', 'outer')
        
        try:
            return pd.concat(inputs, ignore_index=ignore_index, join=join_mode)
        except Exception as e:
            logger.error(f"Errore concat: {e}")
            return inputs[0]

    def _op_filter(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Filtra righe con condizione."""
        config = node.data.get('config', {}) if node.data else {}
        condition = config.get('condition')
        
        if not condition:
            return df
        
        try:
            return df.query(condition)
        except Exception as e:
            logger.error(f"Errore filter: {e}")
            return df

    def _op_sort(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Ordina DataFrame."""
        config = node.data.get('config', {}) if node.data else {}
        sort_columns = config.get('sort_columns', [])
        na_position = config.get('na_position', 'last')
        
        if not sort_columns:
            return df
        
        try:
            columns = []
            ascending = []
            for item in sort_columns:
                col = item.get('column')
                if col and col in df.columns:
                    columns.append(col)
                    ascending.append(item.get('ascending', True))
            
            if columns:
                return df.sort_values(by=columns, ascending=ascending, na_position=na_position).reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Errore sort: {e}")
            return df

    def _op_distinct(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Rimuove duplicati."""
        config = node.data.get('config', {}) if node.data else {}
        subset = config.get('subset')
        keep = config.get('keep', 'first')
        
        try:
            if subset:
                subset = [c for c in subset if c in df.columns] or None
            if keep == 'false':
                keep = False
            return df.drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Errore distinct: {e}")
            return df
    # =========================================================================
    # TRANSFORM OPERATIONS - SPATIAL
    # =========================================================================

    def _op_sjoin(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Spatial join tra due GeoDataFrame."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        predicate = config.get('predicate', 'intersects')
        how = config.get('how', 'inner')
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        try:
            left_gdf = to_geodataframe(inputs[0], left_geom)
            right_gdf = to_geodataframe(inputs[1], right_geom)
            
            left_gdf, right_gdf = ensure_same_crs(left_gdf, right_gdf)
            
            result = gpd.sjoin(left_gdf, right_gdf, how=how, predicate=predicate)
            
            if 'index_right' in result.columns:
                result = result.drop(columns=['index_right'])
            
            # Converti geometry in WKT
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Spatial Join completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Spatial Join: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    def _op_geo_buffer(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Crea buffer intorno alle geometrie."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        distance = config.get('distance', 100)
        output_column = config.get('output_column', 'buffer_geom')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            original_crs = gdf.crs
            
            # Riproietta in CRS metrico se necessario
            if original_crs and original_crs.is_geographic:
                utm_crs = get_utm_crs_for_geometry(gdf)
                gdf_projected = gdf.to_crs(utm_crs)
            else:
                gdf_projected = gdf
            
            buffer_geom = gdf_projected.geometry.buffer(distance)
            
            # Riproietta al CRS originale
            if original_crs and original_crs.is_geographic:
                buffer_gdf = gpd.GeoDataFrame(geometry=buffer_geom, crs=gdf_projected.crs)
                buffer_gdf = buffer_gdf.to_crs(original_crs)
                buffer_geom = buffer_gdf.geometry
            
            result = df.copy()
            result[output_column] = buffer_geom.apply(geometry_to_wkt)
            
            logger.info(f"Buffer completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Buffer: {e}")
            return df

    def _op_geo_centroid(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Calcola centroide delle geometrie."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'centroid')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            centroids = gdf.geometry.centroid
            
            result = df.copy()
            result[output_column] = centroids.apply(geometry_to_wkt)
            
            logger.info(f"Centroid completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Centroid: {e}")
            return df

    def _op_geo_area(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """Calcola area in metri quadrati."""
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'area_m2')
        
        try:
            gdf = to_geodataframe(df, geom_column)
            
            # Riproietta in CRS metrico se necessario
            if gdf.crs and gdf.crs.is_geographic:
                utm_crs = get_utm_crs_for_geometry(gdf)
                gdf_projected = gdf.to_crs(utm_crs)
            else:
                gdf_projected = gdf
            
            result = df.copy()
            result[output_column] = gdf_projected.geometry.area
            
            logger.info(f"Area completato: colonna '{output_column}' aggiunta")
            return result
            
        except Exception as e:
            logger.error(f"Errore Area: {e}")
            return df

    def _op_geo_clip(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """Ritaglia geometrie usando una maschera."""
        if len(inputs) < 2:
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        input_geom = config.get('input_geom')
        mask_geom = config.get('mask_geom')
        
        try:
            input_gdf = to_geodataframe(inputs[0], input_geom)
            mask_gdf = to_geodataframe(inputs[1], mask_geom)
            
            input_gdf, mask_gdf = ensure_same_crs(input_gdf, mask_gdf)
            
            result = gpd.clip(input_gdf, mask_gdf)
            
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(geometry_to_wkt)
            
            logger.info(f"Clip completato: {len(result)} righe")
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Clip: {e}")
            return inputs[0] if inputs else pd.DataFrame()

    # =========================================================================
    # EXECUTE ENRICH
    # =========================================================================

    def _execute_enrich(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False, format: str = 'csv') -> Dict[str, Any]:
        """Esegue la pipeline di arricchimento tramite API esterne."""
        logger.info(f"Esecuzione progetto Arricchisci: {project_id}")
        
        from services.api_connection_service import api_connection_service
        
        canvas_dict = canvas.model_dump() if hasattr(canvas, 'model_dump') else canvas
        
        result = api_connection_service.execute_enrichment(
            project_id=project_id,
            canvas=canvas_dict,
            format=format,
            force_recalculate=force_recalculate
        )
        
        if result.get("success"):
            return {
                "success": True,
                "output_path": str(self.results_base_dir / project_id / result.get("output_file", "")),
                "rows_count": result.get("rows_processed", 0),
                "columns": result.get("columns", []),
                "message": f"Arricchimento completato: {result.get('rows_processed', 0)} righe processate"
            }
        else:
            return {
                "success": False,
                "message": result.get("error", "Errore durante l'arricchimento")
            }