"""
Estrai Executor - Gestisce esecuzione e preview progetti ESTRAI.

Responsabilità:
- Esecuzione query SQL su tutti i database supportati
- Preview dati con limite righe
- Gestione tipi colonne dal canvas
"""

import re
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus

from models import CanvasState, Node
from utils.logger import get_logger
from utils.dataframe_utils import (
    load_dataframe_from_file,
    sanitize_for_json
)
from utils.geo_utils import extract_srid_from_column_type

logger = get_logger(__name__)


class EstraiExecutor:
    """Gestisce esecuzione e preview per progetti ESTRAI."""
    
    def __init__(self, project_service, executor, generator, connection_service):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service

    # =========================================================================
    # EXECUTE
    # =========================================================================

    def execute(self, project_id: str, canvas: CanvasState) -> Dict[str, Any]:
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

        # Estrai nome colonna geometria (rimuovi prefisso tabella se presente)
        geom_column = None
        if result_node.geomColumn:
            geom_column = result_node.geomColumn.split('.')[-1] if '.' in result_node.geomColumn else result_node.geomColumn

        res = self.executor.execute_query(
            connection=connection,
            sql=query_info['sql'],
            project_id=project_id,
            output_filename=result_node.fileName or 'output',
            output_format=result_node.format or 'csv',
            params=query_info.get('params'),
            geom_column=geom_column
        )
        
        # Salva tipi colonne nel metadata
        if res.get("success"):
            self._save_column_types_from_canvas(project_id, canvas, res)
        
        res['query'] = query_info['sql']
        return res

    def _save_column_types_from_canvas(self, project_id: str, canvas: CanvasState, res: Dict):
        """Salva i tipi colonne dal canvas nel metadata del progetto."""
        try:
            canvas_type_map = self.build_canvas_type_map(canvas)
            
            # FIX: Crea anche versione case-insensitive della mappa
            canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}
            
            output_path = Path(res["output_path"])
            df_for_cols = load_dataframe_from_file(output_path)
            
            columns_metadata = []
            for col in df_for_cols.columns:
                # FIX: Prova prima esatto, poi lowercase, poi uppercase
                col_type = canvas_type_map.get(col) or canvas_type_map_lower.get(col.lower()) or 'str'
                columns_metadata.append({"name": col, "type": col_type})
            
            self.project_service.update_project_columns_metadata(project_id, columns_metadata)
            res['columns_metadata'] = columns_metadata
            logger.info(f"Salvati tipi per {len(columns_metadata)} colonne")
            
        except Exception as e:
            logger.warning(f"Errore salvataggio tipi colonne: {e}")

    # =========================================================================
    # PREVIEW
    # =========================================================================

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
        """Preview per progetti ESTRAI - SUPPORTA TUTTI I DATABASE."""
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
            
            # Rimuovi eventuali LIMIT/TOP esistenti
            sql_no_limit = re.sub(r'\s+LIMIT\s+\d+\s*$', '', base_sql, flags=re.IGNORECASE)
            sql_no_limit = re.sub(r'^SELECT\s+TOP\s+\d+\s+', 'SELECT ', sql_no_limit, flags=re.IGNORECASE)
            sql_no_limit = re.sub(r'\s+FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$', '', sql_no_limit, flags=re.IGNORECASE)
            
            # Costruisci connection string e query con LIMIT in base al tipo database
            db_type = connection.type
            connect_args = {}
            
            conn_str, preview_sql, count_sql, connect_args = self._build_db_specific_query(
                connection, sql_no_limit, limit, db_type
            )
            
            if conn_str is None:
                return {"success": False, "message": f"Tipo database '{db_type}' non supportato per preview"}
            
            from sqlalchemy import create_engine, text
            
            # Per MSSQL, prova anche con Driver 17 se Driver 18 fallisce
            try:
                engine = create_engine(conn_str, connect_args=connect_args)
                with engine.connect() as conn:
                    df = pd.read_sql(text(preview_sql), conn, params=params)
            except Exception as e:
                if db_type == 'mssql' and 'ODBC Driver 18' in str(e):
                    logger.info("Fallback a ODBC Driver 17 per SQL Server")
                    conn_str = conn_str.replace('ODBC+Driver+18', 'ODBC+Driver+17')
                    engine = create_engine(conn_str, connect_args=connect_args)
                    with engine.connect() as conn:
                        df = pd.read_sql(text(preview_sql), conn, params=params)
                else:
                    raise
            
            # Conta totale righe
            try:
                with engine.connect() as conn:
                    count_result = conn.execute(text(count_sql), params)
                    total_count = count_result.scalar() or 0
            except:
                total_count = len(df)
            
            truncated = len(df) > limit
            if truncated:
                df = df.head(limit)
            
            # Usa tipi dal canvas invece di inferirli dal DataFrame
            canvas_type_map = self.build_canvas_type_map(canvas)
            columns = self._build_columns_with_canvas_types(df, canvas_type_map)
            
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

    def _build_db_specific_query(self, connection, sql_no_limit: str, limit: int, db_type: str):
        """Costruisce query e connection string specifici per tipo database."""
        connect_args = {}
        
        if db_type in ('postgresql', 'postgres'):
            conn_str = f"postgresql://{connection.username}:{quote_plus(connection.password)}@{connection.host}:{connection.port}/{connection.database}"
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"
            connect_args = {'connect_timeout': 60}
            
        elif db_type == 'mysql':
            conn_str = f"mysql+pymysql://{connection.username}:{quote_plus(connection.password)}@{connection.host}:{connection.port}/{connection.database}"
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"
            connect_args = {'connect_timeout': 60, 'read_timeout': 60, 'write_timeout': 60}
            
        elif db_type == 'mssql':
            pwd_encoded = quote_plus(connection.password)
            conn_str = (
                f"mssql+pyodbc://{connection.username}:{pwd_encoded}@"
                f"{connection.host}:{connection.port}/{connection.database}"
                f"?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
            )
            preview_sql = re.sub(
                r'^SELECT\s+', 
                f'SELECT TOP {limit + 1} ', 
                sql_no_limit, 
                count=1, 
                flags=re.IGNORECASE
            )
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"
            connect_args = {'timeout': 60}
            
        elif db_type == 'oracle':
            pwd_encoded = quote_plus(connection.password)
            conn_str = f"oracle+oracledb://{connection.username}:{pwd_encoded}@{connection.host}:{connection.port}/?service_name={connection.database}"
            preview_sql = f"{sql_no_limit} FETCH FIRST {limit + 1} ROWS ONLY"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit})"
            
        elif db_type == 'sqlite':
            conn_str = f"sqlite:///{connection.database}"
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit})"
            connect_args = {'timeout': 60}
            
        else:
            return None, None, None, None
        
        return conn_str, preview_sql, count_sql, connect_args

    # =========================================================================
    # UTILITY - CANVAS TYPE MAP
    # =========================================================================

    def build_canvas_type_map(self, canvas: CanvasState) -> Dict[str, str]:
        """
        Costruisce una mappa nome_colonna -> tipo dal canvas.
        Usato per preservare i tipi geometry nella preview.
        """
        canvas_type_map = {}
        
        for node in canvas.nodes:
            # Leggi colonne da qualsiasi tipo di nodo
            columns = None
            if hasattr(node, 'columns') and node.columns:
                columns = node.columns
            
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
                    if original_name and original_name != col_name:
                        canvas_type_map[original_name] = col_type
            
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
        
        logger.debug(f"Canvas type map: {canvas_type_map}")
        return canvas_type_map

    def _build_columns_with_canvas_types(self, df: pd.DataFrame, canvas_type_map: Dict[str, str]) -> List[Dict[str, str]]:
        """
        Costruisce lista colonne con tipi dal canvas.
        Se una colonna non è nel canvas, inferisce il tipo dal DataFrame.
        """
        columns = []
        
        # Crea versione case-insensitive della mappa
        canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}
        
        for col in df.columns:
            # Prima cerca match esatto nel canvas
            if col in canvas_type_map:
                col_type = canvas_type_map[col]
            # Poi cerca match case-insensitive
            elif col.lower() in canvas_type_map_lower:
                col_type = canvas_type_map_lower[col.lower()]
            else:
                # Fallback: inferisci dal DataFrame
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
                    col_type = 'str'
            
            columns.append({'name': col, 'type': col_type})
        
        return columns

    def extract_srid_from_canvas(self, canvas: CanvasState) -> Optional[int]:
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