"""
Manipola Executor - Gestisce esecuzione e preview progetti MANIPOLA.

Responsabilità:
- Caricamento DataFrame da progetti sorgente e file source
- Orchestrazione trasformazioni
- Salvataggio risultati
- Preview pipeline
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional

from models import CanvasState, Node
from config import settings
from utils.logger import get_logger
from utils.dataframe_utils import (
    load_dataframe_from_file,
    save_dataframe,
    filter_visible_columns,
    sanitize_for_json,
    find_latest_output_file
)
from utils.geo_utils import (
    detect_geometry_column,
    extract_srid_from_column_type
)

from .transform_operations import TransformOperations
from .spatial_operations import SpatialOperations

logger = get_logger(__name__)


class ManipolaExecutor:
    """Gestisce esecuzione e preview per progetti MANIPOLA."""
    
    def __init__(self, project_service, orchestrator_callback=None):
        """
        Args:
            project_service: Servizio per gestione progetti
            orchestrator_callback: Callback per eseguire progetti upstream (evita import circolari)
        """
        self.project_service = project_service
        self.orchestrator_callback = orchestrator_callback
        self.results_base_dir = settings.RESULTS_DIR
        
        # Inizializza operazioni
        self.transform_ops = TransformOperations()
        self.spatial_ops = SpatialOperations()

    def set_orchestrator_callback(self, callback):
        """Imposta callback per esecuzione ricorsiva progetti."""
        self.orchestrator_callback = callback

    # =========================================================================
    # EXECUTE
    # =========================================================================

    def execute(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False) -> Dict[str, Any]:
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

    # =========================================================================
    # PREVIEW
    # =========================================================================

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
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
            
            # Usa tipi dal canvas invece di inferirli
            canvas_type_map = self._build_canvas_type_map(canvas)
            columns = self._build_columns_with_canvas_types(preview_df, canvas_type_map)
            
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
    # LOAD SOURCES
    # =========================================================================

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
            if not self.orchestrator_callback:
                return {"success": False, "message": "Orchestrator callback non configurato"}
            
            logger.info(f"Ricalcolo progetto upstream: {source_project_id}")
            upstream = self.orchestrator_callback(source_project_id, force_recalculate=True)
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

    # =========================================================================
    # TRANSFORMATIONS
    # =========================================================================

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

    def _dispatch_transform(self, node: Node, inputs: List[pd.DataFrame]) -> pd.DataFrame:
        """Dispatch trasformazione al metodo corretto."""
        transform_op = node.data.get('transform_op') if node.data else None
        
        if not transform_op:
            return inputs[0] if inputs else pd.DataFrame()
        
        # Mappa operazioni base
        base_ops = {
            'join': self.transform_ops.op_join,
            'union': self.transform_ops.op_union,
            'formula': self.transform_ops.op_formula,
            'aggregate': self.transform_ops.op_aggregate,
            'filter': self.transform_ops.op_filter,
            'select': self.transform_ops.op_select,
            'sort': self.transform_ops.op_sort,
            'distinct': self.transform_ops.op_distinct,
            'rename': self.transform_ops.op_rename,
            'drop_columns': self.transform_ops.op_drop_columns,
            'fill_na': self.transform_ops.op_fill_na,
            'replace': self.transform_ops.op_replace,
            'split_column': self.transform_ops.op_split_column,
        }
        
        # Mappa operazioni spaziali
        spatial_ops = {
            'sjoin': self.spatial_ops.op_sjoin,
            'geo_buffer': self.spatial_ops.op_geo_buffer,
            'geo_centroid': self.spatial_ops.op_geo_centroid,
            'geo_area': self.spatial_ops.op_geo_area,
            'geo_clip': self.spatial_ops.op_geo_clip,
            'geo_dissolve': self.spatial_ops.op_geo_dissolve,
            'geo_intersection': self.spatial_ops.op_geo_intersection,
            'geo_union': self.spatial_ops.op_geo_union,
            'geo_difference': self.spatial_ops.op_geo_difference,
            'geo_simplify': self.spatial_ops.op_geo_simplify,
            'geo_convex_hull': self.spatial_ops.op_geo_convex_hull,
            'geo_reproject': self.spatial_ops.op_geo_reproject,
        }
        
        # Operazioni che richiedono multipli input
        multi_input_ops = {'join', 'union', 'sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference'}
        
        # Cerca handler
        handler = base_ops.get(transform_op) or spatial_ops.get(transform_op)
        
        if handler:
            if transform_op in multi_input_ops:
                return handler(inputs, node)
            else:
                return handler(inputs[0], node)
        
        logger.warning(f"Trasformazione '{transform_op}' non implementata")
        return inputs[0] if inputs else pd.DataFrame()

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
                col = agg.get('column')
                func = agg.get('function', 'count')
                alias = agg.get('alias', f"{col}_{func}")
                
                if col not in df.columns and func != 'count':
                    continue
                
                if func == 'count':
                    agg_dict[df.columns[0]] = 'count'
                    rename_dict[df.columns[0]] = alias
                elif func in ['sum', 'mean', 'min', 'max', 'std']:
                    agg_dict[col] = func
                    rename_dict[col] = alias
            
            if group_cols:
                group_cols = [c for c in group_cols if c in df.columns]
                if group_cols:
                    result = df.groupby(group_cols, as_index=False).agg(agg_dict)
                    result = result.rename(columns=rename_dict)
                    return result
            
            return df
            
        except Exception as e:
            logger.error(f"Errore GROUP BY: {e}")
            return df

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
        
        # Auto-detect colonna geometria se non specificata
        if geom_column is None:
            geom_column = detect_geometry_column(df)
        
        # Determina CRS
        crs = self._determine_output_crs(canvas, df, geom_column)
        
        output_path = output_dir / f"{filename}.{format_type}"
        
        try:
            save_dataframe(df, output_path, format_type, geom_column, crs)
            
            return {
                "success": True,
                "output_path": str(output_path),
                "format": format_type,
                "rows_count": len(df),
                "columns_count": len(df.columns),
                "message": f"Salvato {len(df)} righe in {output_path.name}"
            }
        except Exception as e:
            logger.error(f"Errore salvataggio risultato: {e}")
            return {"success": False, "message": f"Errore salvataggio: {str(e)}"}

    def _determine_output_crs(self, canvas: CanvasState, df: pd.DataFrame, geom_column: Optional[str]) -> str:
        """Determina il CRS da usare per l'output."""
        if canvas:
            srid = self._extract_srid_from_canvas(canvas)
            if srid:
                return f"EPSG:{srid}"
        
        return "EPSG:4326"

    def _extract_srid_from_canvas(self, canvas: CanvasState) -> Optional[int]:
        """Estrae SRID dal canvas (dai tipi colonne geometry)."""
        for node in canvas.nodes:
            columns = None
            if hasattr(node, 'columns') and node.columns:
                columns = node.columns
            elif node.data and 'columns' in node.data:
                columns = node.data['columns']
            
            if not columns:
                continue
            
            for col in columns:
                col_type = col.get('type', '') if isinstance(col, dict) else getattr(col, 'type', '')
                srid = extract_srid_from_column_type(col_type)
                if srid:
                    return srid
        
        return None

    # =========================================================================
    # UTILITY
    # =========================================================================

    def _build_canvas_type_map(self, canvas: CanvasState) -> Dict[str, str]:
        """
        Costruisce una mappa nome_colonna -> tipo dal canvas.
        Include anche varianti con suffissi _L e _R per gestire i JOIN.
        """
        canvas_type_map = {}
        
        for node in canvas.nodes:
            columns = None
            if hasattr(node, 'columns') and node.columns:
                columns = node.columns
            elif node.data and 'columns' in node.data:
                columns = node.data['columns']
            
            if columns:
                for col in columns:
                    if isinstance(col, dict):
                        col_name = col.get('alias') or col.get('name')
                        col_type = col.get('type', 'str')
                        original_name = col.get('name')
                    else:
                        col_name = col.alias if hasattr(col, 'alias') and col.alias else col.name
                        col_type = col.type if hasattr(col, 'type') and col.type else "str"
                        original_name = col.name if hasattr(col, 'name') else None
                    
                    if col_name:
                        canvas_type_map[col_name] = col_type
                        # Aggiungi varianti con suffissi _L e _R per gestire i JOIN
                        canvas_type_map[f"{col_name}_L"] = col_type
                        canvas_type_map[f"{col_name}_R"] = col_type
                    
                    if original_name and original_name != col_name:
                        canvas_type_map[original_name] = col_type
                        # Aggiungi varianti con suffissi _L e _R
                        canvas_type_map[f"{original_name}_L"] = col_type
                        canvas_type_map[f"{original_name}_R"] = col_type
            
            # Colonne calcolate
            calculated = None
            if hasattr(node, 'calculated_columns') and node.calculated_columns:
                calculated = node.calculated_columns
            
            if calculated:
                for calc in calculated:
                    if isinstance(calc, dict):
                        calc_name = calc.get("name")
                        calc_type = calc.get("output_type", "str")
                    else:
                        calc_name = getattr(calc, 'name', None)
                        calc_type = getattr(calc, 'output_type', 'str')
                    
                    if calc_name:
                        canvas_type_map[calc_name] = calc_type
                        # Anche per le calcolate, aggiungi varianti
                        canvas_type_map[f"{calc_name}_L"] = calc_type
                        canvas_type_map[f"{calc_name}_R"] = calc_type
        
        return canvas_type_map

    def _build_columns_with_canvas_types(self, df: pd.DataFrame, canvas_type_map: Dict[str, str]) -> List[Dict[str, str]]:
        """
        Costruisce lista colonne con tipi dal canvas.
        Gestisce colonne con suffissi _L/_R cercando anche il nome base.
        """
        columns = []
        canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}
        
        for col in df.columns:
            col_type = None
            
            # 1. Cerca match esatto
            if col in canvas_type_map:
                col_type = canvas_type_map[col]
            # 2. Cerca match case-insensitive
            elif col.lower() in canvas_type_map_lower:
                col_type = canvas_type_map_lower[col.lower()]
            # 3. Se ha suffisso _L o _R, cerca il nome base
            elif col.endswith('_L') or col.endswith('_R'):
                base_name = col[:-2]  # Rimuovi _L o _R
                if base_name in canvas_type_map:
                    col_type = canvas_type_map[base_name]
                elif base_name.lower() in canvas_type_map_lower:
                    col_type = canvas_type_map_lower[base_name.lower()]
            
            # 4. Fallback: inferisci dal dtype pandas
            if col_type is None:
                dtype = str(df[col].dtype)
                if 'int' in dtype:
                    col_type = 'int'
                elif 'float' in dtype:
                    col_type = 'real'
                elif 'bool' in dtype:
                    col_type = 'bool'
                elif 'datetime' in dtype:
                    col_type = 'date'
                else:
                    # Ultimo tentativo: controlla se il contenuto sembra geometria WKT
                    col_type = self._detect_geometry_from_content(df, col)
            
            columns.append({'name': col, 'type': col_type})
        
        return columns

    def _detect_geometry_from_content(self, df: pd.DataFrame, col_name: str) -> str:
        """
        Rileva se una colonna contiene geometrie WKT analizzando il contenuto.
        Ritorna 'geometry' se rilevato, altrimenti 'str'.
        """
        try:
            # Prendi il primo valore non-null
            sample = df[col_name].dropna().head(1)
            if sample.empty:
                return 'str'
            
            value = str(sample.iloc[0])
            
            # Pattern WKT comuni
            wkt_prefixes = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT', 
                           'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')
            
            value_upper = value.upper().strip()
            for prefix in wkt_prefixes:
                if value_upper.startswith(prefix):
                    return 'geometry'
            
            return 'str'
            
        except Exception:
            return 'str'