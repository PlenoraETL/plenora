"""
Executor del modulo ESTRAI — esecuzione e preview di query SQL e ArcGIS Feature Service.

Keeps this module's responsibility explicit and isolated from adjacent code.
"""

import time as _time
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

from app.core.projects.canvas_models import CanvasState
from app.utils.logger import get_logger, append_step
from app.core.auth import visibility as visibility_module
from app.core.dataframes.frame_copy import copy_frame_for_update
from app.core.dataframes.geodata import extract_srid_from_column_type
from app.core.dataframes.ports import FrameLike
from app.core.dataframes.schema import series_to_canvas_type
from app.core.dataframes.tabular import (
    dataframe_column_by_position,
    dataframe_preview_records,
    dataframe_from_rows,
    make_unique_column_names,
)
from app.infrastructure.security.crypto import CredentialUnavailableError
from app.shared.resources.metadata import ResourceMetadata, SourceKind
from app.core.estrai.sql_errors import (
    is_user_error_message as _is_user_error_message,
    translate_sql_error as _traduci_errore_sql,
)
from app.core.estrai.types import ColumnMetadataRows, JsonObject, StringMap
from app.core import output_contract as output_contract_module
from app.core.resources.column_types import (
    lookup_canvas_column_type,
)

try:
    from sqlalchemy.exc import DataError as SqlAlchemyDataError
    from sqlalchemy.exc import ProgrammingError as SqlAlchemyProgrammingError
except ImportError:
    SqlAlchemyDataError = None
    SqlAlchemyProgrammingError = None

logger = get_logger(__name__)


@dataclass(frozen=True)
class EstraiPreviewResource:
    dataframe: FrameLike
    metadata: ResourceMetadata


class ArcGISConnectionRecord(Protocol):
    id: str


class ArcGISFeatureReaderProtocol(Protocol):
    """Read/output boundary EstrAi needs from the generic ArcGIS provider."""

    def get_connection(self, connection_id: str) -> Optional[ArcGISConnectionRecord]:
        ...

    def query_layer(
        self,
        connection_id: str,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: Optional[int] = None,
        out_sr: Optional[int] = None,
    ) -> Tuple[Sequence[Sequence[object]], Sequence[str]]:
        ...

    def execute_query(
        self,
        connection_id: str,
        layer_url: str,
        project_id: str,
        output_filename: str,
        output_format: str = "csv",
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        max_features: Optional[int] = None,
        out_sr: Optional[int] = None,
        geom_column: str = "geometry",
    ) -> MutableMapping[str, object]:
        ...


def _dedup_column_names(columns: List[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Rinomina nomi colonna duplicati aggiungendo suffisso `_2`, `_3`, ...

    Stabile (preserva ordine). Gestisce collisioni con nomi gia' presenti
    (es. colonne ``["id", "id_2", "id"]`` -> ``["id", "id_2", "id_3"]``).

    Returns:
        (new_columns, renames) dove `renames` e' la lista di
        ``(original_name, new_name)`` per ogni colonna rinominata.
        Se non ci sono duplicati, `new_columns == columns` e `renames` e' [].
    """
    return make_unique_column_names(columns)


def _dedup_dataframe_columns_for_serialization(
    df: FrameLike,
) -> Tuple[FrameLike, List[str]]:
    """Se ``df`` ha nomi colonna duplicati, li rinomina in modo stabile e
    ritorna ``(df_dedup, warnings)``. Senza duplicati, ritorna il df
    originale e una lista vuota.

    La proiezione record con colonne duplicate puo' perdere silenziosamente
    colonne. Qui rinominiamo invece di scartare, cosi' nessuna colonna
    sparisce dalla preview.

    Tipica causa upstream: JOIN SQL senza alias espliciti che produce
    due colonne con lo stesso nome (es. ``a.id`` e ``b.id``).
    """
    columns = list(df.columns)
    new_columns, renames = _dedup_column_names(columns)
    if not renames:
        return df, []

    # Il costruttore righe/colonne conserva l'ordine; assegniamo direttamente
    # la nuova lista per rinominare anche colonne duplicate.
    df_renamed = copy_frame_for_update(df)
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
    df: FrameLike,
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


def _infer_dataframe_column_type(series: Any) -> str:
    return series_to_canvas_type(series)


def _build_db_preview_resource_from_cursor_schema(
    df: FrameLike,
    cursor_columns: Sequence[str],
    canvas_type_map: Mapping[str, str],
    *,
    row_count: int,
    project_id: str,
) -> EstraiPreviewResource:
    """
    Build preview metadata from the DB boundary first.

    `cursor_columns` comes from the DB cursor description exposed by
    QueryExecutor.execute_raw. The DataFrame is used only as a fallback for
    unresolved scalar types because current DB adapters do not expose portable
    cursor type codes through this Estrai boundary.
    """
    original_columns = [str(column) for column in cursor_columns]
    metadata_column_names, _ = _dedup_column_names(original_columns)
    if len(metadata_column_names) != len(df.columns):
        metadata_column_names = [str(column) for column in df.columns]
        original_columns = metadata_column_names

    columns_info: List[Dict[str, str]] = []
    for index, column_name in enumerate(metadata_column_names):
        original_column_name = original_columns[index]
        column_type = lookup_canvas_column_type(
            column_name,
            canvas_type_map,
            original_column_name,
        )
        if column_type is None:
            column_type = _infer_dataframe_column_type(
                dataframe_column_by_position(df, index)
            )
        columns_info.append({'name': column_name, 'type': column_type})

    return _build_preview_resource(
        df,
        columns_info,
        source_kind="db",
        row_count=row_count,
        project_id=project_id,
    )


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

    def __init__(
        self,
        project_service: object,
        executor: object,
        generator: object,
        connection_service: object,
        features_service: Optional[ArcGISFeatureReaderProtocol] = None,
    ) -> None:
        self.project_service = project_service
        self.executor = executor
        self.generator = generator
        self.connection_service = connection_service
        self.features_service = features_service

    def _require_feature_reader(self) -> ArcGISFeatureReaderProtocol:
        if self.features_service is None:
            raise ValueError("Provider ArcGIS Feature Service non configurato")
        return self.features_service

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
        feature_reader = self.features_service
        if feature_reader:
            connection = feature_reader.get_connection(connection_id)
            if connection:
                return connection, 'arcgis'
        
        raise ValueError(f"Connessione {connection_id} non trovata")

    def _assert_connection_visible_for_project(
        self,
        connection_id: str,
        metadata: Any,
        connection_type: str,
    ) -> None:
        """Ensure a project cannot run against another group's connection."""
        group_id = getattr(metadata, "group_id", None)
        if not group_id:
            raise ValueError("Gruppo progetto non determinabile")

        resource_table = (
            "arcgis_connections" if connection_type == "arcgis" else "connections"
        )
        if not visibility_module.is_resource_visible(
            resource_table,
            connection_id,
            str(group_id),
            is_superadmin=False,
        ):
            raise ValueError(
                f"Connessione '{connection_id}' non visibile al gruppo del progetto"
            )

    # ESECUZIONE COMPLETA

    def execute(
        self, project_id: str, canvas: CanvasState, run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> JsonObject:
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

        # Pre-fix: 4 early-return (result_node missing, default_connection_id
        # missing, CredentialUnavailableError, ValueError) uscivano da execute()
        # PRIMA del blocco append_step finale. Tutte le failure pre-connessione
        # erano invisibili nel run step log — impossibile triagiare via UI.
        # Post-fix: helper `_failure_step` single-exit-point allinea il
        # pattern a manipola/executor.py (gia' fixato in sessione precedente).
        def _failure_step(message: str, *, user_error: bool = False) -> JsonObject:
            duration_ms = int((_time.time() - _step_start) * 1000)
            append_step({
                "module":      "estrai",
                "status":      "failure",
                "rows_out":    0,
                "duration_ms": duration_ms,
                "error":       message,
            })
            result: JsonObject = {"success": False, "message": message}
            if user_error:
                result["user_error"] = True
            return result

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return _failure_step("Nodo risultato non trovato")

        metadata = self.project_service.get_project_metadata(project_id)
        if not metadata or not metadata.default_connection_id:
            return _failure_step("Il progetto non ha una connessione associata")

        try:
            connection, conn_type = self._resolve_connection(metadata.default_connection_id)
            self._assert_connection_visible_for_project(
                metadata.default_connection_id,
                metadata,
                conn_type,
            )
        except CredentialUnavailableError as e:
            # Policy uniforme con /test endpoints (fd35cf4): credenziale
            # cifrata non disponibile o non decriptabile → user_error invece
            # di [CRASH] worker
            return _failure_step(
                f"credenziale non disponibile o non decriptabile: {e}",
                user_error=True,
            )
        except ValueError as e:
            return _failure_step(str(e))

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
    ) -> JsonObject:
        """Percorso esecuzione SQL classico."""
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, canvas.edges, result_node.id,
                db_type=connection.type,
                db_schema=connection.db_schema
            )
        except Exception as e:
            # Pre-fix nessun logger qui: errori non-SQL (KeyError nel
            # generator, bug interni, ecc.) sparivano dal log aggregato.
            # Allinea al pattern di `_execute_feature_service` e
            # `_preview_database` che loggano sempre con exc_info.
            logger.error(
                f"[CRASH] Errore generazione SQL: {e}",
                op="execute", project_id=project_id, exc_info=True,
            )
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
            # Pre-fix: _salva_tipi_colonne_da_canvas chiama
            # update_project_output_contract senza try/except. Su failure
            # (DB deadlock/timeout) post-blob-save l'eccezione propaga,
            # bypassa append_step downstream e lascia il run in stato
            # ambiguo. Post-fix: stesso pattern di arricchisci/manipola
            # — log ERROR + flag contract_update_failed nel res.
            try:
                self._salva_tipi_colonne_da_canvas(project_id, canvas, res)
            except Exception as contract_err:
                logger.error(
                    f"[CRASH] _salva_tipi_colonne_da_canvas fallito "
                    f"post-estrai: blob persistito, contract resta stale. "
                    f"Caller deve gestire drift: {contract_err}",
                    op="execute", project_id=project_id, exc_info=True,
                )
                res["contract_update_failed"] = True
                res["contract_update_error"] = str(contract_err)
        elif res.get("message"):
            original_msg = res["message"]
            res["message"] = _traduci_errore_sql(original_msg)
            if _is_user_error_message(original_msg):
                res["user_error"] = True

        res['query'] = query_info['sql']
        return res

    def _execute_feature_service(
        self,
        project_id: str,
        canvas: CanvasState,
        result_node,
        connection,
    ) -> JsonObject:
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
            feature_reader = self._require_feature_reader()
            res = feature_reader.execute_query(
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
                # P1 (2026-05-26): contract update schermata. Pre-fix
                # un'eccezione in `_salva_tipi_colonne_da_canvas`
                # (DB transient, contract write race) sollevava e
                # veniva inghiottita dal try/except esterno → la run
                # ArcGIS chiudeva con `success=False` nonostante il
                # blob fosse persistito. Pattern allineato a
                # MANIPOLA/ESTRAI database (vedi executor.py:425-442).
                try:
                    self._salva_tipi_colonne_da_canvas(project_id, canvas, res)
                except Exception as exc_contract:
                    logger.warning(
                        f"[GESTITO] ESTRAI ArcGIS: contract update "
                        f"fallito (blob persistito, marcatura "
                        f"contract_update_failed): "
                        f"{type(exc_contract).__name__}: {exc_contract}",
                        op="execute",
                        project_id=project_id,
                    )
                    res["contract_update_failed"] = True

            res['query'] = f"ArcGIS REST: {layer_url} WHERE {where}"
            return res

        except Exception as e:
            logger.error(f"[GESTITO] Errore esecuzione Feature Service: {e}", op="execute", exc_info=True)
            return {"success": False, "message": str(e)}

    # ANTEPRIMA (PREVIEW

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> JsonObject:
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
            self._assert_connection_visible_for_project(
                metadata.default_connection_id,
                metadata,
                conn_type,
            )
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

    def _preview_database(
        self,
        project_id: str,
        canvas: CanvasState,
        result_node,
        connection,
        limit: int,
    ) -> JsonObject:
        """Percorso preview SQL classico."""
        try:
            query_info = self.generator.generate_sql(
                canvas.nodes, canvas.edges, result_node.id,
                db_type=connection.type,
                db_schema=connection.db_schema
            )

            base_sql = query_info['sql']
            params = query_info.get('params', {})

            # Rispetta il LIMIT utente sul result node, se presente,
            # clampando il preview_limit. Senza questo clamp il preview
            # ignorava il limit configurato dall'utente: per esempio,
            # canvas con `distinct=True, limit=2` su `customers.status`
            # in preview mostrava 4 distinct (tutti) invece di 2,
            # confondendo l'utente sulla "anteprima del risultato vero".
            user_limit = getattr(result_node, "limit", None)
            if isinstance(user_limit, int) and user_limit > 0:
                effective_preview_limit = min(limit, user_limit)
            else:
                effective_preview_limit = limit

            sql_no_limit = self._rimuovi_limit(base_sql)
            preview_sql = self._costruisci_sql_preview_only(
                sql_no_limit, effective_preview_limit, connection.type
            )

            rows, columns = self.executor.execute_raw(connection, preview_sql, params)

            # Se abbiamo ricevuto effective_preview_limit+1 righe, ci
            # sono più dati → troncato. Calcoliamo sul limit effettivo
            # (post clamp utente), non sul parametro preview originale.
            truncated = len(rows) > effective_preview_limit
            if truncated:
                rows = rows[:effective_preview_limit]
            total_count = len(rows)

            df = dataframe_from_rows(rows, list(columns))

            # Deduplica colonne duplicate PRIMA di costruire columns_info e
            # PRIMA della proiezione record, altrimenti il dataframe engine
            # puo' scartarle silenziosamente.
            df, dedup_warnings = _dedup_dataframe_columns_for_serialization(df)

            canvas_type_map = self._build_canvas_type_map(canvas)
            preview_resource = _build_db_preview_resource_from_cursor_schema(
                df,
                columns,
                canvas_type_map,
                row_count=total_count,
                project_id=project_id,
            )

            rows_json = dataframe_preview_records(
                preview_resource.dataframe,
                len(preview_resource.dataframe),
            )

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

    def _preview_feature_service(
        self,
        project_id: str,
        canvas: CanvasState,
        result_node,
        connection,
        limit: int,
    ) -> JsonObject:
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
            feature_reader = self._require_feature_reader()
            rows, columns = feature_reader.query_layer(
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

            df = dataframe_from_rows(rows, list(columns))

            # Deduplica colonne duplicate PRIMA di columns_info e to_dict; vedi
            # _preview_database per il razionale.
            df, dedup_warnings = _dedup_dataframe_columns_for_serialization(df)

            canvas_type_map = self._build_canvas_type_map(canvas)
            columns_info = self._costruisci_colonne_con_tipi_canvas(df, canvas_type_map)

            rows_json = dataframe_preview_records(df, len(df))

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
        """Salva l'output_contract del progetto.

        Usa ricerca case-insensitive per gestire differenze di naming tra
        canvas e output (es. Oracle restituisce nomi UPPERCASE).

        L'output_contract e' il boundary pubblico tra progetti. Il canvas
        resta stato editor.
        """
        canvas_type_map = self._build_canvas_type_map(canvas)
        result_columns_by_physical = {}
        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        for canvas_col in (getattr(result_node, "columns", None) or []):
            physical_name = getattr(canvas_col, "alias", None) or getattr(canvas_col, "name", None)
            if physical_name:
                result_columns_by_physical[str(physical_name)] = canvas_col
        # Fallback: i source/transform node portano il mapping alias->nome DB.
        # Senza questo, quando il result node non ha colonne esplicite, una
        # colonna aliasata cadeva sul ramo `{"name": col}` con source_name =
        # alias (es. "age_public"), incoerente con il path update_canvas che
        # risolve al nome DB ("age"). L'incoerenza faceva mintare un id nuovo
        # a ogni rename dell'alias (rompendo la stabilita' id cross-progetto).
        # Il result node, se presente, ha precedenza (mappa gia' popolata sopra).
        for node in canvas.nodes:
            if node.type == "result":
                continue
            for canvas_col in (getattr(node, "columns", None) or []):
                physical_name = getattr(canvas_col, "alias", None) or getattr(canvas_col, "name", None)
                if physical_name and str(physical_name) not in result_columns_by_physical:
                    result_columns_by_physical[str(physical_name)] = canvas_col

        col_names = res.get("columns", [])
        result_columns = []
        for col in col_names:
            canvas_col = result_columns_by_physical.get(str(col))
            if canvas_col is not None:
                result_columns.append(canvas_col)
                continue
            col_type = lookup_canvas_column_type(col, canvas_type_map) or 'str'
            result_columns.append({"name": col, "type": col_type})

        output_contract = output_contract_module.build_output_contract(
            project_id=project_id,
            result_columns=result_columns,
            previous_contract=self.project_service.get_project_output_contract(project_id),
        )
        self.project_service.update_project_output_contract(
            project_id,
            output_contract.model_dump(),
        )
        res["output_contract"] = output_contract.model_dump()

    # SQL HELPERS (per percorso database

    def _rimuovi_limit(self, sql: str) -> str:
        """Rimuove clausole LIMIT/TOP/FETCH FIRST esistenti dalla query SQL."""
        result = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql, flags=re.IGNORECASE)
        result = re.sub(r'^SELECT\s+TOP\s+\d+\s+', 'SELECT ', result, flags=re.IGNORECASE)
        result = re.sub(r'\s+FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$', '', result, flags=re.IGNORECASE)
        return result

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

    def _build_canvas_type_map(self, canvas: CanvasState) -> StringMap:
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
        self, df: FrameLike, canvas_type_map: Dict[str, str]
    ) -> ColumnMetadataRows:
        """
        Costruisce la lista colonne con tipi, preferendo quelli del canvas.

        Strategia di lookup:
        1. Match esatto nel canvas
        2. Match case-insensitive (per Oracle UPPERCASE)
        3. Fallback: inferenza via helper `app.core.dataframes.schema`
        """
        columns = []
        for col in df.columns:
            col_type = lookup_canvas_column_type(col, canvas_type_map)
            if col_type is None:
                col_type = series_to_canvas_type(df[col])

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
