"""
Executor del modulo ESTRAI — esecuzione e preview di query SQL e ArcGIS Feature Service.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import time as _time
import re
import pandas as pd
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.models import CanvasState, Node
from app.utils.logger import get_logger, append_step
from app.utils.dataframe import load_dataframe_from_file, sanitize_for_json
from app.utils.geo import extract_srid_from_column_type
from app.shared.crypto import CredentialUnavailableError
from app.shared.resources.metadata import ResourceMetadata, SourceKind

try:
    from sqlalchemy.exc import DataError as SqlAlchemyDataError
    from sqlalchemy.exc import ProgrammingError as SqlAlchemyProgrammingError
except ImportError:
    SqlAlchemyDataError = None
    SqlAlchemyProgrammingError = None

logger = get_logger(__name__)


@dataclass(frozen=True)
class EstraiPreviewResource:
    dataframe: pd.DataFrame
    metadata: ResourceMetadata


def _traduci_errore_sql(message: str) -> str:
    """
    Traduce messaggi di errore SQL tecnici in messaggi comprensibili per l'utente.
    Mantiene il messaggio originale tra parentesi per il debug.
    """
    _TRANSLATIONS = [
        ("Invalid column name",     "Colonna non trovata nel database"),
        ("Invalid object name",     "Tabella non trovata nel database"),
        ("Ambiguous column name",   "Nome colonna ambiguo — presente in più tabelle"),
        ("Conversion failed",       "Errore di conversione del tipo di dato"),
        ("division by zero",        "Divisione per zero nella query"),
        ("Cannot insert duplicate", "Violazione chiave univoca — valore duplicato"),
        ("Timeout expired",         "Timeout query superato"),
        ("Login failed",            "Errore di autenticazione al database"),
        ("Cannot open database",    "Impossibile aprire il database"),
        ("String or binary data would be truncated", "Valore troppo lungo per la colonna"),
        ("column name or number of supplied values", "Numero di colonne non corrisponde"),
        # Errori funzione — più specifici, devono stare PRIMA di "does not exist"
        ("operator does not exist", "Tipi incompatibili nel JOIN — le colonne collegate devono avere lo stesso tipo di dato"),
        ("undefinedfunction",       "Funzione SQL non applicabile al tipo della colonna — controlla le opzioni di JOIN o i campi calcolati"),
        ("no function matches",     "Funzione SQL non applicabile al tipo della colonna — controlla le opzioni di JOIN o i campi calcolati"),
        ("undefinedcolumn",         "Colonna non trovata nel database — controlla i campi selezionati o gli alias"),
        # Errori tipo dato — più specifici, devono stare PRIMA di "syntax error"
        ("invalid input syntax for type", "Valore del filtro incompatibile con il tipo della colonna — controlla i filtri applicati"),
        ("invalid input syntax",    "Valore non valido per il tipo di dato atteso — controlla i filtri applicati"),
        ("syntax error",            "Errore di sintassi SQL"),
        ("does not exist",          "Oggetto non trovato nel database"),
        ("permission denied",       "Permesso negato sul database"),
    ]
    msg_lower = message.lower()
    for pattern, friendly in _TRANSLATIONS:
        if pattern.lower() in msg_lower:
            return f"{friendly}. Dettaglio tecnico: {message[:300]}"
    return message


_USER_ERROR_PATTERNS = (
    "invalid input syntax",
    "undefinedfunction",
    "undefinedcolumn",
    "no function matches",
    "operator does not exist",
    "ambiguous column name",
    "column name or number of supplied values",
    "conversion failed",
    "division by zero",
    "string or binary data would be truncated",
    "syntax error",
)


def _is_user_error_message(message: str) -> bool:
    """
    Ritorna True se il messaggio corrisponde a un errore causato dall'utente
    (configurazione query sbagliata), non da un crash infrastrutturale.
    Usato per abbassare il livello di log da ERROR a WARNING.
    """
    msg_lower = message.lower()
    return any(p in msg_lower for p in _USER_ERROR_PATTERNS)


def _dedup_column_names(columns: List[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Rinomina nomi colonna duplicati aggiungendo suffisso `_2`, `_3`, ...

    Stabile (preserva ordine). Gestisce collisioni con nomi gia' presenti
    (es. colonne ``["id", "id_2", "id"]`` -> ``["id", "id_2", "id_3"]``).

    Returns:
        (new_columns, renames) dove `renames` e' la lista di
        ``(original_name, new_name)`` per ogni colonna rinominata.
        Se non ci sono duplicati, `new_columns == columns` e `renames` e' [].
    """
    seen: Dict[str, int] = {}
    new_columns: List[str] = []
    renames: List[Tuple[str, str]] = []
    for col in columns:
        if col not in seen:
            seen[col] = 1
            new_columns.append(col)
            continue
        # Duplicato: incrementa il counter finche' il nuovo nome e' libero
        # necessario quando un suffisso candidato e' gia' un altro nome
        candidate = None
        while candidate is None or candidate in seen:
            seen[col] += 1
            candidate = f"{col}_{seen[col]}"
        seen[candidate] = 1
        new_columns.append(candidate)
        renames.append((col, candidate))
    return new_columns, renames


def _dedup_dataframe_columns_for_serialization(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[str]]:
    """Se ``df`` ha nomi colonna duplicati, li rinomina in modo stabile e
    ritorna ``(df_dedup, warnings)``. Senza duplicati, ritorna il df
    originale e una lista vuota.

    Risolve BUG.md #6: ``df.to_dict(orient='records')`` con colonne duplicate
    emette ``UserWarning: DataFrame columns are not unique, some columns
    will be omitted`` e perde silenziosamente colonne. Qui rinominiamo
    invece di scartare, cosi' nessuna colonna sparisce dalla preview.

    Tipica causa upstream: JOIN SQL senza alias espliciti che produce
    due colonne con lo stesso nome (es. ``a.id`` e ``b.id``).
    """
    columns = list(df.columns)
    new_columns, renames = _dedup_column_names(columns)
    if not renames:
        return df, []

    # Rinomina posizionale: pd.DataFrame(rows, columns=...) col costruttore
    # attuale conserva l'ordine; assegniamo direttamente la nuova lista
    df_renamed = df.copy()
    df_renamed.columns = new_columns

    # NOTE: visibili: uno per coppia rinominata + log server-side aggregato
    warnings_list = [
        f"Colonna duplicata rinominata: '{orig}' -> '{new}'"
        for orig, new in renames
    ]
    rename_summary = ", ".join(f"{o}->{n}" for o, n in renames)
    logger.warning(
        f"Preview DataFrame con {len(renames)} colonne duplicate "
        f"rinominate per evitare perdita silenziosa: {rename_summary}",
        op="preview",
    )
    return df_renamed, warnings_list


def _build_preview_resource(
    df: pd.DataFrame,
    columns_info: List[Dict[str, str]],
    *,
    source_kind: SourceKind,
    row_count: int,
    project_id: str,
    group_id: Optional[str] = None,
) -> EstraiPreviewResource:
    metadata = ResourceMetadata.from_columns_list(
        columns_info,
        source_kind=source_kind,
        row_count=row_count,
        project_id=project_id,
        group_id=group_id,
    )
    return EstraiPreviewResource(dataframe=df, metadata=metadata)


class EstraiExecutor:
    """
    Executor del modulo ESTRAI: gestisce esecuzione e preview di query SQL
    e ArcGIS Feature Service.

    Riceve le dipendenze via costruttore (Dependency Injection):
    - project_service: accesso a metadati e file dei progetti
    - executor: QueryExecutor per eseguire query SQL sui database
    - generator: QueryGenerator per tradurre il canvas in SQL
    - connection_service: recupero delle connessioni database configurate
    - features_service: (opzionale) accesso a Feature Service ArcGIS
    """

    def __init__(self, project_service, executor, generator, connection_service, features_service=None):
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.features_service = features_service

    # RISOLUZIONE CONNESSIONE (DB o ArcGIS

    def _resolve_connection(self, connection_id: str) -> Tuple[Any, str]:
        """
        Risolve una connessione per ID cercando prima tra DB, poi tra ArcGIS.
        
        Returns:
            (connection_object, connection_type) dove connection_type è 'db' o 'arcgis'
            
        Raises:
            ValueError se la connessione non è trovata in nessuno dei due servizi
        """
        # Cerca prima tra le connessioni database
        connection = self.connection_service.get_connection(connection_id)
        if connection:
            return connection, 'db'
        
        # Cerca tra le connessioni ArcGIS
        if self.features_service:
            connection = self.features_service.get_connection(connection_id)
            if connection:
                return connection, 'arcgis'
        
        raise ValueError(f"Connessione {connection_id} non trovata")

    # ESECUZIONE COMPLETA

    def execute(
        self, project_id: str, canvas: CanvasState, run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Esegue la query del progetto e salva il file di output.
        Rileva automaticamente se usare percorso SQL o ArcGIS REST.

        `intermediate_mode_override` (R-25 PR-B2.b): kwarg propagato a
        `_execute_database` -> `QueryExecutor.execute_query`. None = bit-compat.
        Path ArcGIS: l'override e' ignorato (no snapshot intermediate per
        feature service).
        """
        logger.info(f"Esecuzione ESTRAI: {project_id}", op="execute", project_id=project_id)
        _step_start = _time.time()

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return {"success": False, "message": "Il progetto non ha una connessione associata"}

        try:
            connection, conn_type = self._resolve_connection(metadata.default_connection_id)
        except CredentialUnavailableError as e:
            # Policy uniforme con /test endpoints (fd35cf4): credenziale
            # cifrata non disponibile o non decriptabile → user_error invece
            # di [CRASH] worker
            return {
                "success": False,
                "message": f"credenziale non disponibile o non decriptabile: {e}",
                "user_error": True,
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}

        if conn_type == 'arcgis':
            res = self._execute_feature_service(project_id, canvas, result_node, connection)
        else:
            res = self._execute_database(
                project_id, canvas, result_node, connection, metadata, run_id=run_id,
                intermediate_mode_override=intermediate_mode_override,
            )

        duration_ms = int((_time.time() - _step_start) * 1000)
        append_step({
            "module":      "estrai",
            "status":      "success" if res.get("success") else "failure",
            "rows_out":    res.get("rows_count", 0) or 0,
            "duration_ms": duration_ms,
            "error":       res.get("message") if not res.get("success") else None,
        })

        return res


    def _execute_database(
        self, project_id: str, canvas: CanvasState, result_node, connection,
        metadata=None, run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Percorso esecuzione SQL classico."""
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, canvas.edges, result_node.id,
                db_type=connection.type,
                db_schema=connection.db_schema
            )
        except Exception as e:
            return {"success": False, "message": f"Errore generazione SQL: {e}"}

        geom_column = None
        if result_node.geomColumn:
            geom_column = result_node.geomColumn.split('.')[-1] if '.' in result_node.geomColumn else result_node.geomColumn

        logger.info(f"SQL generato:\n{query_info['sql']}\nParams: {query_info.get('params', {})}")
        res = self.executor.execute_query(
            connection=connection,
            sql=query_info['sql'],
            project_id=project_id,
            output_filename=result_node.fileName or 'output',
            output_format=result_node.format or 'csv',
            params=query_info.get('params'),
            geom_column=geom_column,
            group_id=getattr(metadata, 'group_id', None),
            run_id=run_id,
            node_id=result_node.id,
            intermediate_mode_override=intermediate_mode_override,
        )

        if res.get("success"):
            self._salva_tipi_colonne_da_canvas(project_id, canvas, res)
        elif res.get("message"):
            original_msg = res["message"]
            res["message"] = _traduci_errore_sql(original_msg)
            if _is_user_error_message(original_msg):
                res["user_error"] = True

        res['query'] = query_info['sql']
        return res

    def _execute_feature_service(self, project_id: str, canvas: CanvasState, result_node, connection) -> Dict[str, Any]:
        """
        Percorso esecuzione ArcGIS Feature Service.
        
        Legge i nodi feature_source dal canvas, query via REST API,
        salva il file di output.
        """
        feature_nodes = [n for n in canvas.nodes if n.type == "feature_source"]
        if not feature_nodes:
            return {"success": False, "message": "Nessun nodo Feature Source trovato nel canvas"}

        # Per ora supporta un singolo feature_source (no JOIN tra layer
        node = feature_nodes[0]
        layer_url = node.layer_url or (node.data or {}).get('layer_url', '')
        where = node.where or (node.data or {}).get('where', '') or '1=1'
        
        # Colonne visibili (escludi geometry che è gestito da return_geometry
        visible_cols = [c for c in self._get_visible_columns(node) if c != 'geometry']
        out_fields = ','.join(visible_cols) if visible_cols else '*'
        
        # Geometria: attiva se il nodo ha geometry_type E geometry è visibile
        has_geom = bool(node.geometry_type or (node.data or {}).get('geometry_type'))
        geom_visible = self._is_column_visible(node, 'geometry')
        return_geometry = has_geom and geom_visible
        geom_column = result_node.geomColumn or 'geometry'
        
        # Limit dal nodo result
        max_features = result_node.limit if result_node.limit else None

        try:
            res = self.features_service.execute_query(
                connection_id=connection.id,
                layer_url=layer_url,
                project_id=project_id,
                output_filename=result_node.fileName or 'output',
                output_format=result_node.format or 'csv',
                where=where,
                out_fields=out_fields,
                return_geometry=return_geometry,
                max_features=max_features,
                geom_column=geom_column,
            )

            if res.get("success"):
                self._salva_tipi_colonne_da_canvas(project_id, canvas, res)

            res['query'] = f"ArcGIS REST: {layer_url} WHERE {where}"
            return res

        except Exception as e:
            logger.error(f"[GESTITO] Errore esecuzione Feature Service: {e}", op="execute", exc_info=True)
            return {"success": False, "message": str(e)}

    # ANTEPRIMA (PREVIEW

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
        """
        Anteprima dati per progetti ESTRAI.
        Rileva automaticamente se usare percorso SQL o ArcGIS REST.
        """
        logger.info(f"Preview ESTRAI: {project_id}", op="preview", project_id=project_id)

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return {"success": False, "message": "Connessione non configurata"}

        try:
            connection, conn_type = self._resolve_connection(metadata.default_connection_id)
        except CredentialUnavailableError as e:
            # Policy uniforme con /test endpoints (fd35cf4): credenziale
            # cifrata non disponibile o non decriptabile → user_error invece
            # di [CRASH] worker
            return {
                "success": False,
                "message": f"credenziale non disponibile o non decriptabile: {e}",
                "user_error": True,
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}

        if conn_type == 'arcgis':
            return self._preview_feature_service(project_id, canvas, result_node, connection, limit)
        else:
            return self._preview_database(project_id, canvas, result_node, connection, limit)

    def _preview_database(self, project_id: str, canvas: CanvasState, result_node, connection, limit: int) -> Dict[str, Any]:
        """Percorso preview SQL classico."""
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, canvas.edges, result_node.id,
                db_type=connection.type,
                db_schema=connection.db_schema
            )

            base_sql = query_info['sql']
            params = query_info.get('params', {})

            sql_no_limit = self._rimuovi_limit(base_sql)
            preview_sql = self._costruisci_sql_preview_only(
                sql_no_limit, limit, connection.type
            )

            rows, columns = self.executor.execute_raw(connection, preview_sql, params)

            # Se abbiamo ricevuto limit+1 righe, ci sono più dati → troncato
            truncated = len(rows) > limit
            if truncated:
                rows = rows[:limit]
            total_count = len(rows)

            df = pd.DataFrame(rows, columns=columns)

            # FIXME(BUG.md #6): dedup colonne duplicate PRIMA di costruire columns_info
            # e PRIMA di to_dict, altrimenti pandas le scarta silenziosamente
            # e l'utente non se ne accorge
            df, dedup_warnings = _dedup_dataframe_columns_for_serialization(df)

            canvas_type_map = self._build_canvas_type_map(canvas)
            columns_info = self._costruisci_colonne_con_tipi_canvas(df, canvas_type_map)
            preview_resource = _build_preview_resource(
                df,
                columns_info,
                source_kind="db",
                row_count=total_count,
                project_id=project_id,
            )

            rows_json = sanitize_for_json(preview_resource.dataframe.to_dict(orient='records'))

            response: Dict[str, Any] = {
                "success": True,
                "columns": preview_resource.metadata.to_columns_list(),
                "rows": rows_json,
                "total_count": total_count,
                "preview_count": len(rows_json),
                "truncated": truncated,
                "query": base_sql,
            }
            if dedup_warnings:
                response["warnings"] = dedup_warnings
            return response

        except Exception as e:
            if SqlAlchemyDataError and isinstance(e, SqlAlchemyDataError):
                # Errore di tipo dati — colpa dell'utente, non serve il traceback
                msg = _traduci_errore_sql(str(e))
                logger.warning(f"[GESTITO] Filtro incompatibile con il tipo DB: {msg[:200]}", op="preview")
                return {"success": False, "message": msg, "user_error": True}
            if SqlAlchemyProgrammingError and isinstance(e, SqlAlchemyProgrammingError):
                # Errore di programmazione SQL lato utente (es. TRIM su bigint, funzione inesistente
                msg = _traduci_errore_sql(str(e))
                logger.warning(f"[GESTITO] Configurazione query non valida: {msg[:200]}", op="preview")
                return {"success": False, "message": msg, "user_error": True}
            logger.error(f"[GESTITO] Errore preview ESTRAI: {e}", op="preview", exc_info=True)
            return {"success": False, "message": _traduci_errore_sql(str(e))}

    def _preview_feature_service(self, project_id: str, canvas: CanvasState, result_node, connection, limit: int) -> Dict[str, Any]:
        """
        Percorso preview ArcGIS Feature Service.
        
        Query limitata via REST API, restituisce dati nello stesso formato
        della preview SQL per compatibilità con il frontend.
        """
        feature_nodes = [n for n in canvas.nodes if n.type == "feature_source"]
        if not feature_nodes:
            return {"success": False, "message": "Nessun nodo Feature Source trovato nel canvas"}

        node = feature_nodes[0]
        layer_url = node.layer_url or (node.data or {}).get('layer_url', '')
        where = node.where or (node.data or {}).get('where', '') or '1=1'
        
        # Colonne visibili (escludi geometry che è gestito da return_geometry
        visible_cols = [c for c in self._get_visible_columns(node) if c != 'geometry']
        out_fields = ','.join(visible_cols) if visible_cols else '*'
        
        # Geometria: attiva se il nodo ha geometry_type E geometry è visibile
        has_geom = bool(node.geometry_type or (node.data or {}).get('geometry_type'))
        geom_visible = self._is_column_visible(node, 'geometry')
        return_geometry = has_geom and geom_visible

        try:
            # Query con limit+1 per detect troncamento (stessa logica del DB
            rows, columns = self.features_service.query_layer(
                connection_id=connection.id,
                layer_url=layer_url,
                where=where,
                out_fields=out_fields,
                return_geometry=return_geometry,
                max_features=limit + 1,
            )

            total_count = len(rows)
            truncated = len(rows) > limit
            if truncated:
                rows = rows[:limit]

            df = pd.DataFrame(rows, columns=columns)

            # FIXME(BUG.md #6): dedup colonne duplicate PRIMA di columns_info e to_dict
            # vedi _preview_database per il razionale
            df, dedup_warnings = _dedup_dataframe_columns_for_serialization(df)

            canvas_type_map = self._build_canvas_type_map(canvas)
            columns_info = self._costruisci_colonne_con_tipi_canvas(df, canvas_type_map)

            rows_json = sanitize_for_json(df.to_dict(orient='records'))

            response: Dict[str, Any] = {
                "success": True,
                "columns": columns_info,
                "rows": rows_json,
                "total_count": total_count,
                "preview_count": len(rows_json),
                "truncated": truncated,
                "query": f"ArcGIS REST: {layer_url} WHERE {where}",
            }
            if dedup_warnings:
                response["warnings"] = dedup_warnings
            return response

        except Exception as e:
            logger.error(f"[GESTITO] Errore preview Feature Service: {e}", op="preview", exc_info=True)
            return {"success": False, "message": str(e)}

    # HELPER: COLONNE VISIBILI DA NODO FEATURE_SOURCE

    def _get_visible_columns(self, node) -> List[str]:
        """
        Estrae le colonne visibili da un nodo feature_source.
        
        Legge node.columns (Pydantic) o node.data.columns (dict),
        filtra per visible=True, restituisce lista nomi.
        """
        columns = []
        
        # Prova prima node.columns (modello Pydantic
        if hasattr(node, 'columns') and node.columns:
            columns = node.columns
        elif node.data and isinstance(node.data, dict) and 'columns' in node.data:
            columns = node.data['columns']
        
        if not columns:
            return []
        
        visible = []
        for col in columns:
            if isinstance(col, dict):
                if col.get('visible', True):
                    visible.append(col.get('name', ''))
            else:
                if getattr(col, 'visible', True):
                    visible.append(getattr(col, 'name', ''))
        
        return [c for c in visible if c]

    def _is_column_visible(self, node, column_name: str) -> bool:
        """
        Verifica se una colonna specifica è visibile nel nodo.
        
        Se la colonna non esiste nella lista, restituisce True (default visibile).
        """
        columns = []
        if hasattr(node, 'columns') and node.columns:
            columns = node.columns
        elif node.data and isinstance(node.data, dict) and 'columns' in node.data:
            columns = node.data['columns']
        
        if not columns:
            return True
        
        for col in columns:
            name = col.get('name', '') if isinstance(col, dict) else getattr(col, 'name', '')
            if name == column_name:
                visible = col.get('visible', True) if isinstance(col, dict) else getattr(col, 'visible', True)
                return visible
        
        # Colonna non trovata nella lista → default visibile
        return True

    # SALVATAGGIO TIPI COLONNE

    def _salva_tipi_colonne_da_canvas(self, project_id: str, canvas: CanvasState, res: Dict):
        """Salva i tipi colonne definiti nel canvas nei metadati del progetto.

        Usa ricerca case-insensitive per gestire differenze di naming tra
        canvas e output (es. Oracle restituisce nomi UPPERCASE).

        La logica retry+backoff+raise vive in
        `app.shared.columns_metadata.save_columns_metadata_with_retry`:
        unica fonte di verita' condivisa con manipola/arricchisci/
        enrichment_task. Vedi quel modulo per la classificazione delle
        eccezioni transient vs non-transient.

        Pre-fix: qualsiasi eccezione era catchata con warning silenzioso
        → downstream riceveva tipi fallback `str`, numeri/date diventavano
        testo nel CARICA target, nessun segnale all'utente.
        """
        from app.shared.columns_metadata import save_columns_metadata_with_retry

        canvas_type_map = self._build_canvas_type_map(canvas)
        canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}

        col_names = res.get("columns", [])
        columns_metadata = []
        for col in col_names:
            col_type = (
                canvas_type_map.get(col)
                or canvas_type_map_lower.get(col.lower())
                or 'str'
            )
            columns_metadata.append({"name": col, "type": col_type})

        save_columns_metadata_with_retry(
            project_service=self.project_service,
            project_id=project_id,
            columns_metadata=columns_metadata,
            logger=logger,
        )
        res['columns_metadata'] = columns_metadata

    # SQL HELPERS (per percorso database

    def _rimuovi_limit(self, sql: str) -> str:
        """Rimuove clausole LIMIT/TOP/FETCH FIRST esistenti dalla query SQL."""
        result = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql, flags=re.IGNORECASE)
        result = re.sub(r'^SELECT\s+TOP\s+\d+\s+', 'SELECT ', result, flags=re.IGNORECASE)
        result = re.sub(r'\s+FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$', '', result, flags=re.IGNORECASE)
        return result

    def _costruisci_sql_preview(self, sql_no_limit: str, limit: int, db_type: str):
        """
        Costruisce la coppia (preview_sql, count_sql) con sintassi specifica per database.

        Returns:
            Tupla (preview_sql, count_sql)
        """
        if db_type in ('postgresql', 'postgres'):
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"

        elif db_type == 'mysql':
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"

        elif db_type == 'mssql':
            preview_sql = re.sub(
                r'^SELECT\s+', f'SELECT TOP {limit + 1} ',
                sql_no_limit, count=1, flags=re.IGNORECASE
            )
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit}) AS subquery"

        elif db_type == 'oracle':
            preview_sql = f"{sql_no_limit} FETCH FIRST {limit + 1} ROWS ONLY"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit})"

        elif db_type == 'sqlite':
            preview_sql = f"{sql_no_limit} LIMIT {limit + 1}"
            count_sql = f"SELECT COUNT(*) as cnt FROM ({sql_no_limit})"

        else:
            raise ValueError(f"Tipo database '{db_type}' non supportato per preview")

        return preview_sql, count_sql

    def _costruisci_sql_preview_only(self, sql_no_limit: str, limit: int, db_type: str) -> str:
        """
        Costruisce solo la query preview (senza count separata).

        Usa LIMIT n+1 per rilevare troncamento: se tornano n+1 righe,
        ci sono più dati disponibili. Evita una seconda query COUNT.
        """
        if db_type in ('postgresql', 'postgres', 'mysql', 'sqlite'):
            return f"{sql_no_limit} LIMIT {limit + 1}"
        elif db_type == 'mssql':
            return re.sub(
                r'^SELECT\s+', f'SELECT TOP {limit + 1} ',
                sql_no_limit, count=1, flags=re.IGNORECASE
            )
        elif db_type == 'oracle':
            return f"{sql_no_limit} FETCH FIRST {limit + 1} ROWS ONLY"
        else:
            raise ValueError(f"Tipo database '{db_type}' non supportato per preview")

    # MAPPA TIPI DAL CANVAS

    def _build_canvas_type_map(self, canvas: CanvasState) -> Dict[str, str]:
        """
        Costruisce una mappa nome_colonna → tipo_interno dal canvas.

        Scansiona tutti i nodi del canvas e raccoglie:
        - Colonne normali (con alias o nome originale)
        - Colonne calcolate (con output_type)

        La mappa viene usata per preservare i tipi geometry nella preview
        e nell'esecuzione, evitando di inferirli dal DataFrame.
        """
        canvas_type_map = {}

        for node in canvas.nodes:
            if node.type == "feature_source":
                geom_type = (
                    node.geometry_type 
                    or (node.data or {}).get('geometry_type', '')
                )
                if geom_type:
                    # Normalizza: "geometry:polygon" o raw "esriGeometryPolygon" → "geometry:polygon"
                    normalized = self._normalize_geometry_type(geom_type)
                    canvas_type_map['geometry'] = normalized

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

        return canvas_type_map

    def _costruisci_colonne_con_tipi_canvas(
        self, df: pd.DataFrame, canvas_type_map: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """
        Costruisce la lista colonne con tipi, preferendo quelli del canvas.

        Strategia di lookup:
        1. Match esatto nel canvas
        2. Match case-insensitive (per Oracle UPPERCASE)
        3. Fallback: inferenza dal dtype del DataFrame
        """
        columns = []
        canvas_type_map_lower = {k.lower(): v for k, v in canvas_type_map.items()}

        for col in df.columns:
            if col in canvas_type_map:
                col_type = canvas_type_map[col]
            elif col.lower() in canvas_type_map_lower:
                col_type = canvas_type_map_lower[col.lower()]
            else:
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

    # HELPER: NORMALIZZAZIONE TIPO GEOMETRIA

    def _normalize_geometry_type(self, geom_type: str) -> str:
        """
        Normalizza il tipo geometria ArcGIS/PLENORA al formato interno.
        
        Input possibili:
        - "geometry:polygon", "geometry:point" → già normalizzato
        - "esriGeometryPolygon", "esriGeometryPoint" → formato ArcGIS REST
        - "Polygon", "Point", "MultiPolygon" → formato generico
        
        Output: "geometry:polygon", "geometry:point", etc.
        """
        if not geom_type:
            return "geometry"
        
        # Già nel formato corretto
        if geom_type.startswith("geometry:"):
            return geom_type
        
        # Mappa esri → interno
        esri_map = {
            "esrigeometrypoint": "geometry:point",
            "esrigeometrymultipoint": "geometry:multipoint",
            "esrigeometrypolyline": "geometry:line",
            "esrigeometrypolygon": "geometry:polygon",
            "esrigeometryenvelope": "geometry:polygon",
        }
        
        lower = geom_type.lower().replace(" ", "")
        if lower in esri_map:
            return esri_map[lower]
        
        # Formato generico
        generic_map = {
            "point": "geometry:point",
            "multipoint": "geometry:multipoint",
            "linestring": "geometry:line",
            "multilinestring": "geometry:line",
            "polyline": "geometry:line",
            "polygon": "geometry:polygon",
            "multipolygon": "geometry:multipolygon",
        }
        
        if lower in generic_map:
            return generic_map[lower]
        
        return "geometry"

    # ESTRAZIONE SRID

    def extract_srid_from_canvas(self, canvas: CanvasState) -> Optional[int]:
        """
        Estrae il primo SRID trovato dai tipi colonna geometry nel canvas.
        """
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
