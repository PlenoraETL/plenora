import pandas as pd
import geopandas as gpd
from shapely import wkt, wkb
from pathlib import Path
from typing import Dict, Any, List, Optional
from models import CanvasState, Node, DatabaseConnection
from services.project_service import ProjectService
from services.query_executor import QueryExecutor
from services.query_generator import QueryGenerator
from services.connection_service import ConnectionService
from config import settings
from utils.logger import get_logger

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

    def execute_pipeline(self, project_id: str, force_recalculate: bool = False) -> Dict[str, Any]:
        """Punto di ingresso principale: decide se estrarre o manipolare."""
        logger.info(f"Esecuzione pipeline progetto: {project_id}")
        logger.debug(f"Force recalculate: {force_recalculate}")
        
        metadata = self.project_service.get_project_metadata(project_id)
        canvas = self.project_service.get_project_canvas(project_id)
        
        if not metadata or not canvas:
            return {"success": False, "message": f"Progetto {project_id} non trovato"}

        logger.debug(f"Tipo progetto: {metadata.project_type}")
        
        # Logica a cascata basata sul tipo progetto
        if metadata.project_type == "estrai":
            return self._execute_extract(project_id, canvas)
        elif metadata.project_type == "manipola":
            return self._execute_manipulate(project_id, canvas, force_recalculate)
        elif metadata.project_type == "arricchisci":
            return self._execute_enrich(project_id, canvas, force_recalculate)

        return {"success": False, "message": f"Tipo progetto '{metadata.project_type}' non supportato"}

    def _execute_extract(self, project_id: str, canvas: CanvasState) -> Dict[str, Any]:
        """Esegue query SQL e salva il file."""
        logger.info(f"Esecuzione progetto ESTRAI: {project_id}")
        
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        # 1. Recupera connessione dal METADATA del progetto (non dai nodi)
        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return {"success": False, "message": "Il progetto non ha una connessione database associata"}

        connection = self.connection_service.get_connection(metadata.default_connection_id)
        if not connection:
            return {"success": False, "message": f"Connessione {metadata.default_connection_id} non trovata"}

        # 2. Genera SQL tramite il generator (passando il tipo database)
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, 
                canvas.edges, 
                result_node.id,
                db_type=connection.type  # Passa il tipo database dalla connessione
            )
        except Exception as e:
            return {"success": False, "message": f"Errore generazione SQL: {str(e)}"}

        # 3. Esegue tramite l'executor
        res = self.executor.execute_query(
            connection=connection,
            sql=query_info['sql'],
            project_id=project_id,
            output_filename=result_node.fileName or 'output',
            output_format=result_node.format or 'csv',
            params=query_info.get('params')
        )
        
        # 4. Salva tipi colonne nel metadata del progetto
        if res.get("success") and res.get("output_path"):
            try:
                output_path = Path(res["output_path"])
                
                # Leggi il file per ottenere i tipi
                if output_path.suffix.lower() == '.xlsx':
                    df_for_types = pd.read_excel(output_path, nrows=1)
                elif output_path.suffix.lower() == '.json':
                    df_for_types = pd.read_json(output_path, orient='records', nrows=1)
                else:
                    df_for_types = pd.read_csv(output_path, nrows=1)
                
                # Calcola tipi semplificati (consistenti con DatabaseInspector)
                columns_metadata = []
                for col in df_for_types.columns:
                    dtype = str(df_for_types[col].dtype)
                    
                    # Mappa dtype pandas ai nostri tipi standard
                    # Tipi: int, real, str, bool, date, geometry:*
                    if 'int' in dtype:
                        simple_type = 'int'
                    elif 'float' in dtype:
                        simple_type = 'real'
                    elif 'datetime' in dtype:
                        simple_type = 'date'
                    elif 'bool' in dtype:
                        simple_type = 'bool'
                    else:
                        simple_type = 'str'
                    
                    columns_metadata.append({
                        "name": col,
                        "type": simple_type  # Usa 'type' per consistenza con frontend
                    })
                
                # Salva nel metadata del progetto
                self.project_service.update_project_columns_metadata(project_id, columns_metadata)
                
                # Aggiungi alla response
                res['columns_metadata'] = columns_metadata
                
                logger.info(f"Salvati tipi per {len(columns_metadata)} colonne")
                
            except Exception as e:
                logger.warning(f"Errore salvataggio tipi colonne: {e}")
        
        # Aggiungiamo l'SQL alla risposta per debug/frontend
        res['query'] = query_info['sql']
        return res

    def _execute_manipulate(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False) -> Dict[str, Any]:
        """Esegue la pipeline di manipolazione seguendo il grafo dei nodi."""
        logger.info(f"Esecuzione progetto Manipola: {project_id}")
        
        # A. Caricamento DataFrames dalle sorgenti (Progetti Estrai)
        dataframes = {}
        source_nodes = [n for n in canvas.nodes if n.type == "project_source"]
        
        logger.info(f"Trovati {len(source_nodes)} progetti sorgente")
        
        for node in source_nodes:
            # source_project_id è in node.data
            source_project_id = node.data.get('source_project_id') if node.data else None
            
            if not source_project_id:
                logger.warning(f"Nodo {node.id} non ha source_project_id configurato")
                continue
            
            # LOGICA CACHE
            output_dir = self.results_base_dir / source_project_id
            cached_files = list(output_dir.glob("*.*")) if output_dir.exists() else []
            
            # Se force_recalculate = False E esiste cache → usa cache
            if not force_recalculate and cached_files:
                cached_file = cached_files[0]  # Prendi il primo file
                logger.info(f"Usando cache esistente: {source_project_id}")
                logger.debug(f"File: {cached_file.name}")
                
                try:
                    file_ext = cached_file.suffix.lower()
                    if file_ext == '.xlsx':
                        dataframes[node.id] = pd.read_excel(cached_file)
                    elif file_ext == '.json':
                        dataframes[node.id] = pd.read_json(cached_file, orient='records')
                    elif file_ext == '.parquet':
                        dataframes[node.id] = pd.read_parquet(cached_file)
                    else:
                        dataframes[node.id] = pd.read_csv(cached_file)
                    
                    # Filtra colonne visible
                    node_columns = node.data.get('columns', []) if node.data else []
                    visible_columns = [col['name'] for col in node_columns if col.get('visible', True)]
                    
                    if visible_columns:
                        available_cols = [col for col in visible_columns if col in dataframes[node.id].columns]
                        if available_cols:
                            dataframes[node.id] = dataframes[node.id][available_cols]
                            logger.debug(f"Filtrate {len(available_cols)}/{len(node_columns)} colonne visibili")
                    
                    logger.info(f"Caricato da cache: {len(dataframes[node.id])} righe, {len(dataframes[node.id].columns)} colonne")
                    continue
                    
                except Exception as e:
                    logger.warning(f"Errore caricamento cache: {e}")
                    logger.debug(f"Procedo con ricalcolo...")
            
            # Ricalcola (o cache non disponibile)
            logger.info(f"Ricalcolo progetto upstream: {source_project_id}")
            
            # Esegui ricorsivamente il progetto a monte (sempre con force_recalculate per consistenza)
            upstream = self.execute_pipeline(source_project_id, force_recalculate=True)
            if not upstream.get("success"):
                return upstream  # Propaga errore
            
            # Carica il file prodotto dal progetto a monte
            file_path = Path(upstream["output_path"])
            
            if not file_path.exists():
                return {
                    "success": False, 
                    "message": f"File risultato del progetto {source_project_id} non trovato: {file_path}"
                }
            
            file_ext = file_path.suffix.lower()
            
            try:
                if file_ext == '.xlsx':
                    dataframes[node.id] = pd.read_excel(file_path)
                elif file_ext == '.json':
                    dataframes[node.id] = pd.read_json(file_path, orient='records')
                else:
                    # Default CSV
                    dataframes[node.id] = pd.read_csv(file_path)
                
                # Filtra solo colonne visible (checkbox selezionati)
                node_columns = node.data.get('columns', []) if node.data else []
                visible_columns = [col['name'] for col in node_columns if col.get('visible', True)]
                
                if visible_columns:
                    # Mantieni solo colonne che esistono E sono visible
                    available_cols = [col for col in visible_columns if col in dataframes[node.id].columns]
                    if available_cols:
                        dataframes[node.id] = dataframes[node.id][available_cols]
                        logger.debug(f"Filtrate {len(available_cols)}/{len(node_columns)} colonne visibili")
                
                logger.info(f"Caricato progetto {source_project_id}: {len(dataframes[node.id])} righe, {len(dataframes[node.id].columns)} colonne")
                
            except Exception as e:
                return {
                    "success": False, 
                    "message": f"Errore caricamento progetto {source_project_id}: {str(e)}"
                }

        logger.info(f"Caricati {len(dataframes)} dataframes dalle sorgenti")
        
        # B. Risoluzione dell'ordine di esecuzione
        transform_nodes = [n for n in canvas.nodes if n.type == "transform"]
        
        # Se non ci sono transform nodes, usa fallback
        if not transform_nodes:
            current_df = self._apply_default_joins(canvas, dataframes)
        else:
            # Applichiamo le trasformazioni definite dall'utente
            current_df = None
            for node in transform_nodes:
                # Recuperiamo i DF in ingresso basandoci sugli edges
                incoming_edges = [e for e in canvas.edges if e.target == node.id]
                inputs = [dataframes[e.source] for e in incoming_edges if e.source in dataframes]
                
                if not inputs:
                    logger.warning(f"Nodo transform {node.id} non ha input")
                    continue
                
                # transform_op è in node.data
                transform_op = node.data.get('transform_op') if node.data else None
                logger.info(f"Applicando trasformazione '{transform_op}' su nodo {node.id}")
                logger.debug(f"Input dataframes: {len(inputs)}")
                
                # Dispatcher: chiama la funzione corretta in base a transform_op
                current_df = self._dispatch_transform(node, inputs)
                dataframes[node.id] = current_df
        
        # C. Nodo Risultato finale
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if current_df is None or not result_node:
            return {"success": False, "message": "Nessun dato prodotto dalla manipolazione"}

        return self._save_manipulation_result(project_id, result_node, current_df, canvas)
    
    def _apply_default_joins(self, canvas: CanvasState, dataframes: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Gestisce caso senza nodi Transform.
        
        - Se 1 sorgente: restituisce il dataframe
        - Se 2+ sorgenti: errore (serve Transform esplicito)
        """
        if not dataframes:
            raise ValueError("Nessun progetto sorgente configurato")
        
        if len(dataframes) == 1:
            logger.info(f"Un solo progetto sorgente - restituisco direttamente il dataframe")
            return list(dataframes.values())[0]
        
        # Multiple sorgenti senza transform → richiedi configurazione esplicita
        raise ValueError(
            f"Il progetto ha {len(dataframes)} progetti sorgente ma nessun nodo Transform. "
            "Aggiungi un nodo 'Join' per specificare come combinare i dati."
        )
        
    def _dispatch_transform(self, node: Node, inputs: List[pd.DataFrame]) -> pd.DataFrame:
        """Dispatcher: chiama la funzione corretta in base a transform_op"""
        # transform_op è in node.data
        op = node.data.get('transform_op') if node.data else None
        
        logger.info(f"Esecuzione transform '{op}' per nodo {node.id}")
        
        if op == "join":
            return self._op_join(inputs, node)
        elif op == "aggregate":
            return self._op_aggregate(inputs[0] if inputs else pd.DataFrame(), node)
        elif op == "formula":
            return self._op_formula(inputs[0] if inputs else pd.DataFrame(), node)
        elif op == "concat":
            return self._op_concat(inputs, node)
        elif op == "filter":
            return self._op_filter(inputs[0] if inputs else pd.DataFrame(), node)
        # === OPERAZIONI SPAZIALI GEOPANDAS ===
        elif op == "sjoin":
            return self._op_sjoin(inputs, node)
        elif op == "geo_buffer":
            return self._op_geo_buffer(inputs[0] if inputs else pd.DataFrame(), node)
        elif op == "geo_centroid":
            return self._op_geo_centroid(inputs[0] if inputs else pd.DataFrame(), node)
        elif op == "geo_area":
            return self._op_geo_area(inputs[0] if inputs else pd.DataFrame(), node)
        elif op == "geo_clip":
            return self._op_geo_clip(inputs, node)
        
        logger.warning(f"Operazione '{op}' non implementata - restituisco primo input")
        return inputs[0] if inputs else pd.DataFrame()
        
    def _op_aggregate(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Esegue il raggruppamento. 
        Config atteso: { 'group_by': ['col1'], 'aggs': {'col2': 'sum', 'col3': 'count'} }
        """
        # Fix: accesso corretto a node.data
        config = node.data.get('config', {}) if node.data else {}
        group_cols = config.get('group_by', [])
        aggs = config.get('aggs', {})
        
        logger.debug(f"Group by: {group_cols}")
        logger.debug(f"Aggregations: {aggs}")
        
        if not group_cols or not aggs:
            logger.warning("Configurazione aggregazione incompleta - restituisco input")
            return df
        
        try:
            result = df.groupby(group_cols).agg(aggs).reset_index()
            logger.info(f"Aggregazione completata: {result.shape}")
            return result
        except Exception as e:
            logger.error(f"Errore aggregazione: {e}")
            return df
            
    def _op_join(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """
        Esegue un merge tra due DataFrame in ingresso.
        Config atteso: { 
            'left_on': 'colonna_A', 
            'right_on': 'colonna_B', 
            'how': 'left' | 'inner' | 'outer' | 'right'
        }
        """
        if len(inputs) < 2:
            logger.warning(f"Join richiede 2 input, trovati {len(inputs)} - restituisco primo")
            return inputs[0] if inputs else pd.DataFrame()

        # config è in node.data
        config = node.data.get('config', {}) if node.data else {}
        left_df = inputs[0]
        right_df = inputs[1]

        left_on = config.get('left_on')
        right_on = config.get('right_on')
        how = config.get('how', 'left')

        logger.debug(f"Join type: {how}")
        logger.debug(f"Left on: {left_on}, Right on: {right_on}")
        logger.debug(f"Left shape: {left_df.shape}, Right shape: {right_df.shape}")

        if not left_on or not right_on:
            logger.warning("Colonne join non configurate - restituisco primo input")
            return left_df

        try:
            # Rinomina TUTTE le colonne con suffisso _L e _R (tranne join key)
            left_cols_renamed = {col: f"{col}_L" for col in left_df.columns if col != left_on}
            right_cols_renamed = {col: f"{col}_R" for col in right_df.columns if col != right_on}
            
            left_df_renamed = left_df.rename(columns=left_cols_renamed)
            right_df_renamed = right_df.rename(columns=right_cols_renamed)
            
            # Aggiorna i nomi delle colonne join se sono state rinominate
            left_on_final = f"{left_on}_L" if left_on in left_cols_renamed else left_on
            right_on_final = f"{right_on}_R" if right_on in right_cols_renamed else right_on
            
            result_df = pd.merge(
                left_df_renamed, 
                right_df_renamed, 
                left_on=left_on_final, 
                right_on=right_on_final, 
                how=how
            )
            logger.info(f"Join completato: {result_df.shape}")
            return result_df
        except Exception as e:
            logger.error(f"Errore join: {e}")
            return left_df
    
    def _op_formula(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Aggiunge una nuova colonna calcolata.
        Config atteso: { 'new_column': 'nome_col', 'formula': 'colA + colB * 2' }
        
        Supporta sia operazioni numeriche che concatenazione stringhe.
        """
        import re
        
        # config è in node.data
        config = node.data.get('config', {}) if node.data else {}
        new_col = config.get('new_column')
        formula = config.get('formula')
        
        logger.debug(f"Nuova colonna: {new_col}")
        logger.debug(f"Formula: {formula}")
        
        if not new_col or not formula:
            logger.warning("Configurazione formula incompleta - restituisco input")
            return df
        
        # PATTERN DETECTION per concatenazione stringhe
        logger.debug(f"=== INIZIO PATTERN DETECTION ===")
        
        # Rimuovi spazi per analisi
        formula_clean = formula.replace(' ', '')
        logger.debug(f"Formula pulita: '{formula_clean}'")
        
        # Check se è solo concatenazione (no *, /, -, **, ecc)
        has_string_literal = "'" in formula or '"' in formula
        logger.debug(f"Ha stringhe letterali: {has_string_literal}")
        
        # Rimuovi stringhe letterali PRIMA di cercare operatori
        formula_no_strings = re.sub(r"'[^']*'|\"[^\"]*\"", "", formula)
        logger.debug(f"Formula senza stringhe: '{formula_no_strings}'")
        
        has_math_ops = any(op in formula_no_strings for op in ['*', '/', '-', '**', '%', '//', '>', '<', '==', '!=', '&', '|'])
        logger.debug(f"Ha operatori matematici: {has_math_ops}")
        if has_math_ops:
            ops_found = [op for op in ['*', '/', '-', '**', '%', '//', '>', '<', '==', '!=', '&', '|'] if op in formula_no_strings]
            logger.debug(f"Operatori trovati: {ops_found}")
        
        has_plus = '+' in formula
        logger.debug(f"Ha operatore +: {has_plus}")
        
        is_string_concat = has_plus and not has_math_ops
        logger.debug(f"È concatenazione stringhe: {is_string_concat}")
        logger.debug(f"=== FINE PATTERN DETECTION ===")
        
        if is_string_concat:
            logger.debug(f"Detectata concatenazione stringhe")
            try:
                # Estrai tokens: colonne e separatori
                logger.debug(f"Inizio parsing tokens...")
                tokens = re.findall(r"(\w+)|'([^']*)'|\"([^\"]*)\"", formula)
                logger.debug(f"Tokens trovati: {tokens}")
                
                result = None
                for i, token in enumerate(tokens):
                    col_name = token[0]  # Nome colonna
                    str_lit1 = token[1]  # Stringa con apici singoli
                    str_lit2 = token[2]  # Stringa con apici doppi
                    
                    logger.debug(f"Token {i}: col='{col_name}', str1='{str_lit1}', str2='{str_lit2}'")
                    
                    if col_name:
                        # È una colonna
                        if col_name in df.columns:
                            logger.debug(f"Colonna '{col_name}' trovata nel DataFrame")
                            col_series = df[col_name].fillna('').astype(str)
                            result = col_series if result is None else result + col_series
                            logger.debug(f"Result aggiornato (lunghezza: {len(result)})")
                        else:
                            logger.debug(f"WARNING: Colonna '{col_name}' NON trovata nel DataFrame!")
                    elif str_lit1 or str_lit2:
                        # È una stringa letterale
                        sep = str_lit1 if str_lit1 else str_lit2
                        logger.debug(f"Separatore trovato: '{sep}'")
                        if result is not None:
                            result = result + sep
                            logger.debug(f"Separatore aggiunto")
                
                if result is not None:
                    df[new_col] = result
                    logger.info(f"Formula applicata (concatenazione stringhe): colonna '{new_col}' creata")
                    return df
                else:
                    raise ValueError("Nessuna colonna trovata nella formula")
                    
            except Exception as e:
                logger.error(f"Errore concatenazione stringhe: {e}")
                import traceback
                logger.error(f"Errore: {e}", exc_info=True)
                return df
        
        # Se non è concatenazione, usa eval normale (operazioni numeriche)
        logger.debug(f"Provo con eval() numerico...")
        try:
            df[new_col] = df.eval(formula)
            logger.info(f"Formula applicata (numerica): colonna '{new_col}' creata")
            return df
        except Exception as e:
            logger.error(f"Errore formula numerica: {e}")
            return df
    
    def _op_concat(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """
        Concatena verticalmente più DataFrame.
        Config: { 
            'ignore_index': True/False,
            'join': 'inner' | 'outer'  # inner=solo colonne comuni, outer=tutte
        }
        """
        if not inputs:
            logger.warning("Concat richiede almeno 1 input")
            return pd.DataFrame()
        
        # config è in node.data
        config = node.data.get('config', {}) if node.data else {}
        ignore_index = config.get('ignore_index', True)
        join_mode = config.get('join', 'outer')  # Default: tutte le colonne
        
        logger.debug(f"Concatenando {len(inputs)} dataframes")
        logger.debug(f"Ignore index: {ignore_index}")
        logger.debug(f"Join mode: {join_mode}")
        
        # Log colonne di ogni input
        for i, df in enumerate(inputs):
            logger.debug(f"Input {i+1}: {len(df)} righe, {len(df.columns)} colonne")
        
        try:
            result = pd.concat(inputs, ignore_index=ignore_index, join=join_mode)
            logger.info(f"Concat completato: {result.shape[0]} righe, {result.shape[1]} colonne")
            return result
        except Exception as e:
            logger.error(f"Errore concat: {e}")
            return inputs[0] if inputs else pd.DataFrame()
    
    def _op_filter(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Filtra le righe in base a una condizione.
        Config atteso: { 'condition': 'colA > 10 & colB == "value"' }
        """
        # config è in node.data
        config = node.data.get('config', {}) if node.data else {}
        condition = config.get('condition')
        
        logger.debug(f"Condizione: {condition}")
        
        if not condition:
            logger.warning("Nessuna condizione specificata - restituisco input")
            return df
        
        try:
            result = df.query(condition)
            logger.info(f"Filter applicato: {len(result)}/{len(df)} righe mantenute")
            return result
        except Exception as e:
            logger.error(f"Errore filter: {e}")
            return df
    
    # ==========================================================================
    # OPERAZIONI SPAZIALI GEOPANDAS
    # ==========================================================================
    
    def _to_geodataframe(self, df: pd.DataFrame, geom_column: str = None) -> gpd.GeoDataFrame:
        """
        Converte un DataFrame pandas in GeoDataFrame.
        Cerca automaticamente la colonna geometria se non specificata.
        """
        if isinstance(df, gpd.GeoDataFrame):
            return df
        
        # Cerca colonna geometria
        if geom_column is None:
            # Cerca colonne con nomi comuni per geometrie
            geom_candidates = ['geom', 'geometry', 'the_geom', 'shape', 'wkt']
            for col in geom_candidates:
                if col in df.columns:
                    geom_column = col
                    break
            
            # Se non trovata, cerca colonne che contengono 'geom'
            if geom_column is None:
                for col in df.columns:
                    if 'geom' in col.lower():
                        geom_column = col
                        break
        
        if geom_column is None or geom_column not in df.columns:
            logger.warning(f"Colonna geometria non trovata. Colonne disponibili: {list(df.columns)}")
            raise ValueError("Nessuna colonna geometria trovata nel DataFrame")
        
        logger.debug(f"Usando colonna geometria: {geom_column}")
        
        # Converti geometrie in oggetti Shapely
        # Supporta: WKT, WKB (hex), EWKB (hex con SRID)
        try:
            def parse_geometry(x):
                """Parse geometry from various formats"""
                if pd.isna(x) or x is None or x == '':
                    return None
                
                # Se è già un oggetto geometria Shapely
                if hasattr(x, 'geom_type'):
                    return x
                
                x_str = str(x).strip()
                
                # Prova a rilevare il formato
                # EWKB/WKB: inizia con 0 ed è esadecimale
                if x_str.startswith('0') and all(c in '0123456789ABCDEFabcdef' for c in x_str[:20]):
                    try:
                        # EWKB ha SRID nei primi bytes, prova prima come EWKB
                        return wkb.loads(x_str, hex=True)
                    except Exception:
                        try:
                            # Fallback: prova a rimuovere SRID (primi 8 caratteri dopo il tipo)
                            # EWKB format: 01 03 00 00 20 A2100000 ...
                            # Byte order (01) + Type with SRID flag (03000020) + SRID (4 bytes) + geometry
                            if len(x_str) > 18 and x_str[8:10] == '20':
                                # Ha SRID flag, rimuoviamolo
                                clean_wkb = x_str[:2] + x_str[2:8].replace('20', '00') + x_str[18:]
                                return wkb.loads(clean_wkb, hex=True)
                        except Exception:
                            pass
                
                # Prova WKT (inizia con nome tipo: POINT, POLYGON, etc.)
                wkt_prefixes = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT', 
                               'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')
                if any(x_str.upper().startswith(p) for p in wkt_prefixes):
                    try:
                        return wkt.loads(x_str)
                    except Exception:
                        pass
                
                # Ultimo tentativo: forza WKB hex
                try:
                    return wkb.loads(x_str, hex=True)
                except Exception:
                    pass
                
                logger.warning(f"Impossibile parsare geometria: {x_str[:50]}...")
                return None
            
            if df[geom_column].dtype == 'object':
                geometries = df[geom_column].apply(parse_geometry)
            else:
                geometries = df[geom_column]
            
            # Estrai SRID se presente nell'EWKB per impostare il CRS corretto
            crs = "EPSG:4326"  # Default
            first_valid = df[geom_column].dropna().iloc[0] if len(df[geom_column].dropna()) > 0 else None
            if first_valid and str(first_valid).startswith('0') and len(str(first_valid)) > 18:
                try:
                    # EWKB: SRID è nei bytes 10-18 (little endian)
                    srid_hex = str(first_valid)[10:18]
                    # Little endian: inverti i byte pairs
                    srid_bytes = bytes.fromhex(srid_hex)
                    srid = int.from_bytes(srid_bytes, byteorder='little')
                    if srid > 0:
                        crs = f"EPSG:{srid}"
                        logger.debug(f"Rilevato SRID: {srid}")
                except Exception as e:
                    logger.debug(f"Impossibile estrarre SRID: {e}")
            
            gdf = gpd.GeoDataFrame(df, geometry=geometries, crs=crs)
            
            # Rimuovi la colonna originale se diversa da 'geometry'
            if geom_column != 'geometry' and geom_column in gdf.columns:
                gdf = gdf.drop(columns=[geom_column])
            
            logger.info(f"Convertito in GeoDataFrame: {len(gdf)} righe, CRS: {gdf.crs}")
            return gdf
            
        except Exception as e:
            logger.error(f"Errore conversione a GeoDataFrame: {e}")
            raise
    
    def _op_sjoin(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """
        Esegue un join spaziale tra due GeoDataFrame.
        Config atteso: {
            'predicate': 'intersects' | 'within' | 'contains' | 'touches' | 'crosses' | 'overlaps',
            'how': 'inner' | 'left' | 'right',
            'left_geom': 'colonna_geom_sinistra',  # opzionale
            'right_geom': 'colonna_geom_destra'    # opzionale
        }
        """
        if len(inputs) < 2:
            logger.warning(f"Spatial Join richiede 2 input, trovati {len(inputs)}")
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        predicate = config.get('predicate', 'intersects')
        how = config.get('how', 'inner')
        left_geom = config.get('left_geom')
        right_geom = config.get('right_geom')
        
        logger.info(f"Spatial Join: predicate={predicate}, how={how}")
        
        try:
            # Converti in GeoDataFrame
            left_gdf = self._to_geodataframe(inputs[0], left_geom)
            right_gdf = self._to_geodataframe(inputs[1], right_geom)
            
            logger.debug(f"Left GDF: {len(left_gdf)} righe, Right GDF: {len(right_gdf)} righe")
            
            # Assicura stesso CRS
            if left_gdf.crs != right_gdf.crs:
                logger.debug(f"Riproiezione: {right_gdf.crs} -> {left_gdf.crs}")
                right_gdf = right_gdf.to_crs(left_gdf.crs)
            
            # Esegui spatial join
            result = gpd.sjoin(left_gdf, right_gdf, how=how, predicate=predicate)
            
            # Rimuovi colonna index_right se presente
            if 'index_right' in result.columns:
                result = result.drop(columns=['index_right'])
            
            logger.info(f"Spatial Join completato: {len(result)} righe risultanti")
            
            # Converti geometry in WKT per compatibilità
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(lambda g: g.wkt if g else None)
            
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Spatial Join: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return inputs[0] if inputs else pd.DataFrame()
    
    def _op_geo_buffer(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Crea buffer intorno alle geometrie.
        Config atteso: {
            'geom_column': 'geometry',      # colonna geometria
            'distance': 100,                 # distanza buffer in metri
            'output_column': 'buffer_geom',  # nome colonna output
            'cap_style': 'round'             # round, flat, square
        }
        """
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        distance = config.get('distance', 100)
        output_column = config.get('output_column', 'buffer_geom')
        cap_style = config.get('cap_style', 'round')
        
        logger.info(f"Buffer: distance={distance}m, output={output_column}")
        
        try:
            gdf = self._to_geodataframe(df, geom_column)
            
            # Converti cap_style in valore numerico
            cap_styles = {'round': 1, 'flat': 2, 'square': 3}
            cap = cap_styles.get(cap_style, 1)
            
            # Per buffer in metri, riproietta temporaneamente in un CRS metrico
            original_crs = gdf.crs
            if original_crs and original_crs.is_geographic:
                # Usa UTM approssimativo basato sul centroide
                centroid = gdf.geometry.unary_union.centroid
                utm_zone = int((centroid.x + 180) / 6) + 1
                utm_crs = f"EPSG:326{utm_zone:02d}" if centroid.y >= 0 else f"EPSG:327{utm_zone:02d}"
                gdf_projected = gdf.to_crs(utm_crs)
                logger.debug(f"Riproiettato in {utm_crs} per buffer metrico")
            else:
                gdf_projected = gdf
            
            # Crea buffer
            buffer_geom = gdf_projected.geometry.buffer(distance, cap_style=cap)
            
            # Riproietta al CRS originale
            if original_crs and original_crs.is_geographic:
                buffer_gdf = gpd.GeoDataFrame(geometry=buffer_geom, crs=gdf_projected.crs)
                buffer_gdf = buffer_gdf.to_crs(original_crs)
                buffer_geom = buffer_gdf.geometry
            
            # Aggiungi colonna buffer come WKT
            result = df.copy()
            result[output_column] = buffer_geom.apply(lambda g: g.wkt if g else None)
            
            logger.info(f"Buffer completato: aggiunta colonna '{output_column}'")
            return result
            
        except Exception as e:
            logger.error(f"Errore Buffer: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return df
    
    def _op_geo_centroid(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Calcola il centroide delle geometrie.
        Config atteso: {
            'geom_column': 'geometry',
            'output_column': 'centroid'
        }
        """
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'centroid')
        
        logger.info(f"Centroid: output={output_column}")
        
        try:
            gdf = self._to_geodataframe(df, geom_column)
            
            # Calcola centroide
            centroids = gdf.geometry.centroid
            
            # Aggiungi come WKT
            result = df.copy()
            result[output_column] = centroids.apply(lambda g: g.wkt if g else None)
            
            logger.info(f"Centroid completato: aggiunta colonna '{output_column}'")
            return result
            
        except Exception as e:
            logger.error(f"Errore Centroid: {e}")
            return df
    
    def _op_geo_area(self, df: pd.DataFrame, node: Node) -> pd.DataFrame:
        """
        Calcola l'area delle geometrie in metri quadrati.
        Config atteso: {
            'geom_column': 'geometry',
            'output_column': 'area_m2'
        }
        """
        config = node.data.get('config', {}) if node.data else {}
        geom_column = config.get('geom_column')
        output_column = config.get('output_column', 'area_m2')
        
        logger.info(f"Area: output={output_column}")
        
        try:
            gdf = self._to_geodataframe(df, geom_column)
            
            # Per area in m², riproietta temporaneamente in CRS metrico
            original_crs = gdf.crs
            if original_crs and original_crs.is_geographic:
                centroid = gdf.geometry.unary_union.centroid
                utm_zone = int((centroid.x + 180) / 6) + 1
                utm_crs = f"EPSG:326{utm_zone:02d}" if centroid.y >= 0 else f"EPSG:327{utm_zone:02d}"
                gdf_projected = gdf.to_crs(utm_crs)
            else:
                gdf_projected = gdf
            
            # Calcola area
            areas = gdf_projected.geometry.area
            
            result = df.copy()
            result[output_column] = areas
            
            logger.info(f"Area completato: aggiunta colonna '{output_column}'")
            return result
            
        except Exception as e:
            logger.error(f"Errore Area: {e}")
            return df
    
    def _op_geo_clip(self, inputs: List[pd.DataFrame], node: Node) -> pd.DataFrame:
        """
        Ritaglia geometrie usando una maschera.
        Il primo input è il layer da ritagliare, il secondo è la maschera.
        Config atteso: {
            'input_geom': 'geometry',   # colonna geom input
            'mask_geom': 'geometry'     # colonna geom maschera
        }
        """
        if len(inputs) < 2:
            logger.warning(f"Clip richiede 2 input, trovati {len(inputs)}")
            return inputs[0] if inputs else pd.DataFrame()
        
        config = node.data.get('config', {}) if node.data else {}
        input_geom = config.get('input_geom')
        mask_geom = config.get('mask_geom')
        
        logger.info("Esecuzione Clip spaziale")
        
        try:
            input_gdf = self._to_geodataframe(inputs[0], input_geom)
            mask_gdf = self._to_geodataframe(inputs[1], mask_geom)
            
            # Assicura stesso CRS
            if input_gdf.crs != mask_gdf.crs:
                mask_gdf = mask_gdf.to_crs(input_gdf.crs)
            
            # Esegui clip
            result = gpd.clip(input_gdf, mask_gdf)
            
            logger.info(f"Clip completato: {len(result)} righe risultanti")
            
            # Converti geometry in WKT
            if 'geometry' in result.columns:
                result['geometry'] = result['geometry'].apply(lambda g: g.wkt if g else None)
            
            return pd.DataFrame(result)
            
        except Exception as e:
            logger.error(f"Errore Clip: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return inputs[0] if inputs else pd.DataFrame()
    
    def _extract_srid_from_columns(self, canvas: 'CanvasState') -> Optional[int]:
        """
        Estrae SRID dai metadati delle colonne geometriche nel canvas.
        Cerca nelle colonne dei nodi sorgente.
        Formato tipo: geometry:polygon:4326 -> estrae 4326
        """
        for node in canvas.nodes:
            columns = []
            if hasattr(node, 'columns') and node.columns:
                columns = node.columns
            elif node.data and 'columns' in node.data:
                columns = node.data['columns']
            
            for col in columns:
                col_type = col.get('type', '') if isinstance(col, dict) else getattr(col, 'type', '')
                if col_type and col_type.startswith('geometry'):
                    parts = col_type.split(':')
                    if len(parts) >= 3:
                        try:
                            srid = int(parts[2])
                            if srid > 0:
                                logger.debug(f"SRID trovato: {srid}")
                                return srid
                        except ValueError:
                            continue
        
        logger.debug("Nessun SRID trovato nei metadati, uso default 4326")
        return 4326  # Default WGS84
    
    def _save_manipulation_result(self, project_id: str, result_node: Node, df: pd.DataFrame, canvas: 'CanvasState' = None) -> Dict[str, Any]:
        """Salva il risultato finale della manipolazione"""
        output_dir = self.results_base_dir / project_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # fileName e format sono in node.data
        filename = result_node.data.get('fileName', 'output') if result_node.data else 'output'
        format_type = result_node.data.get('format', 'csv') if result_node.data else 'csv'
        geom_column = result_node.data.get('geomColumn', None) if result_node.data else None
        
        # Formati geospaziali richiedono GeoDataFrame
        geo_formats = ['geojson', 'gpkg', 'shp']
        
        if format_type in geo_formats:
            # Converti in GeoDataFrame se necessario
            try:
                # Usa la colonna geometria specificata dall'utente, se presente
                gdf = self._to_geodataframe(df, geom_column)
                logger.info(f"Usando colonna geometria: {geom_column or 'auto-detect'}")
                
                # Estrai SRID dai metadati del canvas
                if canvas:
                    srid = self._extract_srid_from_columns(canvas)
                    if srid and srid > 0:
                        gdf = gdf.set_crs(f"EPSG:{srid}", allow_override=True)
                        logger.info(f"CRS impostato da metadati: EPSG:{srid}")
                
                if format_type == 'geojson':
                    output_path = output_dir / f"{filename}.geojson"
                    gdf.to_file(output_path, driver='GeoJSON')
                elif format_type == 'gpkg':
                    output_path = output_dir / f"{filename}.gpkg"
                    gdf.to_file(output_path, driver='GPKG')
                elif format_type == 'shp':
                    output_path = output_dir / f"{filename}.shp"
                    gdf.to_file(output_path, driver='ESRI Shapefile')
                    
                logger.info(f"Risultato geospaziale salvato: {output_path}")
                
            except Exception as e:
                logger.warning(f"Impossibile salvare come {format_type}: {e}. Fallback a CSV.")
                output_path = output_dir / f"{filename}.csv"
                df.to_csv(output_path, index=False)
        
        elif format_type == 'xlsx':
            output_path = output_dir / f"{filename}.xlsx"
            df.to_excel(output_path, index=False)
        elif format_type == 'json':
            output_path = output_dir / f"{filename}.json"
            df.to_json(output_path, orient='records', indent=2)
        elif format_type == 'parquet':
            output_path = output_dir / f"{filename}.parquet"
            df.to_parquet(output_path, index=False)
        else:
            output_path = output_dir / f"{filename}.csv"
            df.to_csv(output_path, index=False)
        
        logger.info(f"Risultato salvato: {output_path}")
        logger.debug(f"Righe: {len(df)}, Colonne: {len(df.columns)}")
        
        return {
            "success": True,
            "output_path": str(output_path),
            "rows_count": len(df),
            "columns": list(df.columns),
            "message": f"Pipeline completata con successo"
        }
    
    def _execute_enrich(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False, format: str = 'csv') -> Dict[str, Any]:
        """
        Esegue la pipeline di arricchimento tramite API esterne.
        Delega l'esecuzione al api_connection_service.
        """
        logger.info(f"Esecuzione progetto Arricchisci: {project_id} (force_recalculate={force_recalculate})")
        
        # Import qui per evitare circular import
        from services.api_connection_service import api_connection_service
        
        # Converti canvas in dict se necessario
        canvas_dict = canvas.model_dump() if hasattr(canvas, 'model_dump') else canvas
        
        # Esegui tramite api_connection_service
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