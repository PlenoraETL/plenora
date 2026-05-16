"""
Executor del modulo MANIPOLA - pipeline di trasformazione dati.

Keeps transformation behavior isolated from orchestration callers.
"""

import time as _time
from typing import Any, List, Optional

from app.core.dataframes.ports import FrameLike
from app.core.dataframes.tabular import (
    dataframe_preview_records,
    empty_dataframe,
    estimate_merge_output_rows,
    group_aggregate_as_columns,
    group_keys_dataframe,
    group_size_values,
    merge_dataframes,
)
from app.core.projects.canvas_models import CanvasState, Node
from app.config import settings
from app.core.projects.execution_context import get_project_group_id
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.executor_protocol import PipelineDispatcherProtocol
from app.shared.execution.graph import kahn_topological_order
from app.utils.logger import get_logger, append_step
from app.core.results.pipeline_result import save_pipeline_result_outcome
from app.core.dataframes.frame_io import load_dataframe_from_file
from app.core.artifacts import SourceLoader, SourceRef
from app.core.dataframes.geodata import (
    detect_geometry_column,
    is_geodataframe,
    to_geodataframe,
)
from app.core.intermediate.snapshot import (
    record_checkpoint_snapshot,
    record_final_snapshot,
)
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.projects.columns_metadata import save_columns_metadata_with_retry
from app.shared.resources.metadata import ResourceMetadata
from .plugin_executor import get_executor
from .source_loading import load_file_sources
from .datasource_node_loading import load_datasource_nodes
from .canvas_schema_runtime import (
    build_canvas_type_map,
    build_columns_with_types,
    detect_geometry_from_content,
    extract_srid_from_canvas,
)
from .project_source_runtime import (
    ProjectLineageCycleError,
    load_project_sources,
    _push_to_execution_chain,
    _pop_from_execution_chain,
)
from .transform_ordering import sorted_incoming_edges
from .types import ColumnMetadataRows, FrameMap, JsonObject, JsonRows, StringMap


logger = get_logger(__name__)

__all__ = [
    "ManipolaExecutor",
    "load_dataframe_from_file",
]

# Stage id consumed by the global OOM handler for row-count-aware messaging.
MULTI_JOIN_STAGE = "manipola_multi_join"


def _estimate_merge_output_rows(
    left: FrameLike,
    right: FrameLike,
    join_keys: List[str],
    how: str,
) -> Optional[int]:
    """Estimate merge output cardinality from key frequencies.

    The estimate is exact for equality joins exposed through
    `merge_dataframes` on the configured keys and join types used by
    multi-join (`inner`, `left`, `right`, `outer`). It costs O(N+M), but it
    runs before `merge_dataframes` allocates the output frame.
    """
    return estimate_merge_output_rows(left, right, join_keys, how)


def _nf(node: Node, key: str, default: Any = None) -> Any:
    """Legge un campo flat dal nodo Pydantic, con fallback su node.data (backward compat)."""
    field_value = getattr(node, key, None)
    if field_value is not None and field_value != [] and field_value != {}:
        return field_value
    if node.data and isinstance(node.data, dict):
        return node.data.get(key, default)
    return default


def _applica_crs_da_colonne(
    df: FrameLike,
    node_columns: Optional[JsonRows],
    meta: Any = None,
) -> FrameLike:
    """
    Se il frame contiene una colonna geometrica e node_columns ha il CRS
    (formato geometry:subtype:SRID), delega al boundary geodata per ottenere
    una risorsa geospaziale con il CRS corretto. Evita che le trasformazioni
    spaziali downstream perdano il CRS per sorgenti tabulari.

    ``meta`` (optional): pre-built ``ResourceMetadata`` produced by the
    source-loading path. When provided the internal construction from
    ``node_columns`` is skipped â€” metadata travels from the loader rather
    than being reconstructed here. ``node_columns`` is still required for
    the legacy fallback path (callers that do not supply ``meta``).
    """
    if is_geodataframe(df):
        return df  # Gia' risorsa geospaziale: non toccare.

    if meta is None:
        # Normalise alias â†’ name before building ResourceMetadata: after
        # filter_visible_columns the DataFrame uses the alias as the actual
        # column name, so the lookup must use the same key
        _meta_input = [
            {"name": c.get("alias") or c.get("name", ""), "type": c.get("type", "")}
            for c in (node_columns or [])
            if c.get("alias") or c.get("name")
        ]
        meta = ResourceMetadata.from_columns_list(_meta_input, source_kind="file")

    # Apply CRS only when the geometry column is present in the DataFrame
    # and carries an explicit EPSG code in the type string
    geom_col = meta.geometry_column if (meta.geometry_column and meta.geometry_column in df.columns) else None
    srid = meta.crs_epsg if geom_col else None

    if geom_col and srid:
        try:
            gdf = to_geodataframe(df, geom_col, crs=f"EPSG:{srid}")
            # Override esplicito nel caso extract_srid_from_ewkb abbia letto diversamente
            if gdf.crs and str(gdf.crs.to_epsg()) != str(srid):
                gdf = gdf.set_crs(f"EPSG:{srid}", allow_override=True)
            logger.info(f"CRS applicato da metadati nodo: EPSG:{srid} su colonna '{geom_col}'")
            return gdf
        except Exception as e:
            logger.warning(f"Impossibile applicare CRS da metadati: {e}")

    return df


class ManipolaExecutor:
    """Executor MANIPOLA: orchestra la pipeline di trasformazione dati."""

    def __init__(
        self,
        project_service: object,
        pipeline_dispatcher: Optional[PipelineDispatcherProtocol] = None,
        group_id_resolver: Any = None,
    ) -> None:
        self.project_service = project_service
        self.pipeline_dispatcher = pipeline_dispatcher
        self.group_id_resolver = group_id_resolver
        self.results_base_dir = settings.RESULTS_DIR

    def set_pipeline_dispatcher(self, dispatcher: PipelineDispatcherProtocol) -> None:
        """Set the dispatcher used for recursive upstream project execution."""
        self.pipeline_dispatcher = dispatcher

    # ESECUZIONE COMPLETA

    def _init_security_context(self, project_id: str, *, strict: bool) -> Optional[str]:
        """Risolve il `group_id` del progetto corrente e lo ritorna.

        I caller (execute / preview) propagano il valore ritornato come
        kwarg esplicito ai source loader. Tutti e tre i loader sono
        ctx-strict (TE-007/008): il group_id deve venire da qui, non da
        stato di istanza.

        - `strict=True` (execute): ValueError fail-closed se il group_id
          non e' determinabile (l'esecuzione persiste output e va legata
          a un tenant).
        - `strict=False` (preview): ritorna None in caso di lookup fallito;
          i loader fail-closed e il preview torna "Nessun dato sorgente
          disponibile" â€” comportamento corretto per canvas con sorgenti
          inaccessibili.
        """
        resolved: Optional[str]
        try:
            resolver = self.group_id_resolver or get_project_group_id
            resolved = resolver(project_id)
        except Exception as e:
            logger.error(f"[GESTITO] Impossibile determinare group_id per {project_id}: {e}", exc_info=True)
            resolved = None

        if strict and resolved is None:
            raise ValueError(f"group_id non determinabile per progetto {project_id}")
        return resolved

    def execute(
        self,
        project_id: str,
        canvas: CanvasState,
        force_recalculate: bool = False,
        run_id: Optional[str] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> JsonObject:
        """Esegue la pipeline completa: carica sorgenti, trasforma, salva.

        `intermediate_mode_override` (R-25 PR-B2.b): override per-call della
        `INTERMEDIATE_PERSISTENCE_MODE` globale, propagato a
        `record_final_snapshot` (nodo result) e a tutti i
        `record_checkpoint_snapshot` chiamati dentro `_applica_trasformazioni`.
        None = bit-compat default-path. Validazione enum a record-time.
        Kwarg esplicito (no stato d'istanza per coerenza con QueryExecutor).
        """
        logger.info(f"Esecuzione MANIPOLA: {project_id}", op="execute", project_id=project_id)

        executing_group_id = self._init_security_context(project_id, strict=True)
        canvas_graph = CanvasGraph.from_canvas(canvas)

        _push_to_execution_chain(project_id)

        _step_start = _time.time()
        dataframes = {}

        try:
            # TE-007/008/009: tutti e 3 i loader sono ctx-strict
            # `executing_group_id` (da `_init_security_context`) e' l'unico
            # canale tenant â€” nessuno stato di istanza coinvolto
            for loader in [
                lambda: self._carica_sorgenti_progetto(canvas, dataframes, force_recalculate, run_id=run_id, group_id=executing_group_id),
                lambda: self._carica_sorgenti_file(canvas, dataframes, group_id=executing_group_id),
                lambda: self._carica_sorgenti_datasource(canvas, dataframes, group_id=executing_group_id),
                lambda: self._carica_sorgenti_statiche(canvas, dataframes, group_id=executing_group_id),
            ]:
                err = loader()
                if err:
                    append_step({
                        "module":      "manipola",
                        "status":      "failure",
                        "rows_out":    0,
                        "duration_ms": int((_time.time() - _step_start) * 1000),
                        "error":       err.get("message"),
                    })
                    return err
        except ProjectLineageCycleError as cycle_err:
            # Ciclo cross-project rilevato a runtime (defense-in-depth
            # per canvas legacy che sfuggono al check di update_canvas
            logger.error(
                f"[SECURITY] Ciclo project_source rilevato a runtime: {cycle_err}",
                op="execute", project_id=project_id,
            )
            append_step({
                "module":      "manipola",
                "status":      "failure",
                "rows_out":    0,
                "duration_ms": int((_time.time() - _step_start) * 1000),
                "error":       str(cycle_err),
            })
            return {"success": False, "message": str(cycle_err)}
        finally:
            _pop_from_execution_chain(project_id)

        logger.info(f"Caricati {len(dataframes)} DataFrame", op="execute", project_id=project_id)
        try:
            current_df = self._applica_trasformazioni(
                canvas, dataframes, run_id=run_id,
                canvas_graph=canvas_graph,
                intermediate_mode_override=intermediate_mode_override,
            )
        except ValueError as e:
            append_step({
                "module":      "manipola",
                "status":      "failure",
                "rows_out":    0,
                "duration_ms": int((_time.time() - _step_start) * 1000),
                "error":       str(e),
            })
            return {"success": False, "message": str(e)}
        if current_df is None:
            return {"success": False, "message": "Nessun dato prodotto dalla manipolazione"}

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        current_df = self._applica_group_by(current_df, result_node)

        if run_id and current_df is not None:
            try:
                record_final_snapshot(
                    run_id=run_id,
                    module="manipola",
                    node_id=result_node.id,
                    node_type="result",
                    df=current_df,
                    exec_time=0.0,
                    status="ok",
                    node_data=getattr(result_node, "data", None),
                    mode_override=intermediate_mode_override,
                )
            except Exception as _iex:
                logger.warning(f"Intermediate write result node fallita (non bloccante): {_iex}")

        save_result = self._salva_risultato(
            project_id, result_node, current_df, canvas, canvas_graph=canvas_graph
        )

        # Keep project_source reuse aligned with the actual persisted schema.
        # Non-transient metadata failures propagate so Celery marks the run failed.
        if save_result.get("success"):
            _col_meta = [
                {"name": c, "type": str(current_df[c].dtype)} for c in current_df.columns
            ]
            save_columns_metadata_with_retry(
                project_service=self.project_service,
                project_id=project_id,
                columns_metadata=_col_meta,
                logger=logger,
            )

        append_step({
            "module":      "manipola",
            "status":      "success" if save_result.get("success") else "failure",
            "rows_out":    save_result.get("rows_count", 0) or 0,
            "duration_ms": int((_time.time() - _step_start) * 1000),
            "error":       save_result.get("message") if not save_result.get("success") else None,
        })
        return save_result

    # ANTEPRIMA

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> JsonObject:
        """Preview: stessa pipeline dell'execute ma senza salvataggio."""
        logger.info(f"Preview MANIPOLA: {project_id}", op="preview", project_id=project_id)
        try:
            # Stesso tenant binding di execute. `strict=False` perche'
            # preview non persiste output: se il lookup DB fallisce
            # `executing_group_id` resta None e i loader fail-closed
            executing_group_id = self._init_security_context(
                project_id, strict=False
            )
            canvas_graph = CanvasGraph.from_canvas(canvas)

            dataframes = {}
            # stesso tenant
            # boundary esplicito di execute
            self._carica_sorgenti_progetto(
                canvas, dataframes, force_recalculate=False,
                group_id=executing_group_id,
            )
            self._carica_sorgenti_file(
                canvas, dataframes, group_id=executing_group_id,
            )
            self._carica_sorgenti_datasource(
                canvas, dataframes, group_id=executing_group_id,
            )
            self._carica_sorgenti_statiche(
                canvas, dataframes, group_id=executing_group_id,
            )

            if not dataframes:
                return {"success": False, "message": "Nessun dato sorgente disponibile"}

            current_df = self._applica_trasformazioni(
                canvas, dataframes, canvas_graph=canvas_graph
            )
            if current_df is None:
                return {"success": False, "message": "Nessun dato prodotto dalla pipeline"}

            result_node = next((n for n in canvas.nodes if n.type == "result"), None)
            if result_node:
                current_df = self._applica_group_by(current_df, result_node)

            total_count = len(current_df)
            truncated = total_count > limit
            preview_df = current_df.head(limit)

            canvas_type_map = self._costruisci_mappa_tipi_canvas(
                canvas, canvas_graph=canvas_graph
            )
            columns = self._costruisci_colonne_con_tipi(preview_df, canvas_type_map)
            rows = dataframe_preview_records(current_df, limit)

            return {
                "success": True, "columns": columns, "rows": rows,
                "total_count": total_count, "preview_count": len(rows),
                "truncated": truncated
            }
        except Exception as e:
            logger.warning(f"[GESTITO] Errore preview MANIPOLA: {e}", op="preview")
            return {"success": False, "message": str(e)}

    # CARICAMENTO SORGENTI

    def _carica_sorgenti_progetto(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        force_recalculate: bool,
        run_id: Optional[str] = None,
        *, group_id: Optional[str] = None,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai progetti sorgente (type=project_source).

        SECURITY: verifica che ogni source_project_id appartenga allo stesso
        gruppo del progetto corrente. Senza questo check, un utente potrebbe
        inserire nel canvas un source_project_id di un altro tenant e
        esfiltrare i dati durante l'esecuzione della pipeline.

         (ctx-strict): il
        ``group_id`` esplicito e' OBBLIGATORIO. Il singleton fallback
        ``self._current_group_id`` e' stato rimosso da questa catena.
        Se ``group_id`` e' None o vuoto il metodo ritorna fail-closed
        senza invocare il resolver. I caller (execute / preview)
        ricevono il ``group_id`` da ``_init_security_context`` e lo
        propagano esplicitamente.
        """
        return load_project_sources(
            canvas,
            dataframes,
            force_recalculate=force_recalculate,
            pipeline_dispatcher=self.pipeline_dispatcher,
            results_base_dir=self.results_base_dir,
            run_id=run_id,
            group_id=group_id,
            logger=logger,
        )

    def _carica_sorgenti_file(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        *, group_id: Optional[str] = None,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai nodi file_source via SourceLoader.

        thin delegator. Il body vive in
        ``app.core.manipola.source_loading.load_file_sources`` â€”
        funzione pura con parametri espliciti, zero stato di
        classe. Il nome del metodo e' preservato cosi' i test
        esistenti e i caller esterni (oggi nessuno fuori dal
        modulo, ma il metodo e' parte della superficie pubblica
        dell'executor) continuano a funzionare invariati.

        Behaviour-preserving:

          - Visibility check cross-tenant + preserve della risorsa
            geospaziale sono ancora garantiti dal ``SourceLoader``
            unificato e dagli helper `app.core.dataframes`, nessuna
            duplicazione inline.
          - ``TRANSFORMATION-ENGINE-004``: ``group_id`` esplicito
            e' il canale primario di tenant binding (production:
            ``execute``/``preview`` lo passano sempre dopo
            ``_init_security_context``).
          - ``TRANSFORMATION-ENGINE-005`` (chain-local cut):
            questa catena NON consulta ``self._current_group_id``
            ne' nel delegator ne' nel modulo estratto. Una stale
            singleton (race window cross-run, residuo da
            un'esecuzione precedente nello stesso processo
            worker) NON puo' autorizzare un ``file_source``.
            Mirror della cut arricchisci

        Il delegator NON passa ``self._current_group_id`` al
        helper: l'argomento ``group_id`` del helper e' l'**unico**
        canale tenant per questa catena.
        ``_carica_sorgenti_progetto`` e' ctx-strict (TE-007 cut: nessun
        fallback singleton). ``_carica_sorgenti_statiche`` usa
        l'argomento esplicito ``group_id`` o il sentinel
        ``"_static_"`` non-autorizzante (TE-008).
        """
        return load_file_sources(
            canvas,
            dataframes,
            group_id=group_id,
            logger=logger,
        )

    def _carica_sorgenti_datasource(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        *, group_id: Optional[str] = None,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai nodi `datasource_node` in `mode=source`.

        Body in `app.core.manipola.datasource_node_loading.load_datasource_nodes`.
        Stesso contratto degli altri loader: ctx-strict su `group_id`,
        nessun fallback singleton, mutazione in-place di `dataframes`,
        ritorno `{success: False, message}` su errore canonical.
        """
        return load_datasource_nodes(
            canvas,
            dataframes,
            group_id=group_id,
            logger=logger,
        )

    def _carica_sorgenti_statiche(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        *, group_id: Optional[str] = None,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai nodi StaticValues via SourceLoader.

        Wrapper sottile sopra `SourceLoader._load_static`. Il processing
        dei parametri statici vive nel loader unico;
        qui resta solo l'iterazione sui nodi del canvas.

         (ctx-strict cut): lo
        static branch e' pure processing (no DB, no file I/O). Il
        ``group_id`` esplicito del caller e' l'unico canale tenant;
        ``self._current_group_id`` NON e' consultato. Se ``group_id``
        e' assente il sentinel non-autorizzante ``"_static_"`` soddisfa
        la guardia non-vuota del loader senza ambiguita' cross-tenant
        (nessuna risorsa esterna caricata in questo branch).
        """
        static_nodes = [n for n in canvas.nodes if n.type == "staticValues"]
        if not static_nodes:
            return None

        # TE-008: pure processing â€” do NOT read self._current_group_id
        # Explicit group_id (production path) or non-authorizing sentinel
        current_group_id = group_id or "_static_"
        loader = SourceLoader()

        for node in static_nodes:
            try:
                fields = _nf(node, 'fields', [])
                ref = SourceRef.from_node_data(
                    'staticValues',
                    {'fields': fields},
                )
                dataframes[node.id] = loader.load(
                    ref, expected_group_id=current_group_id
                )
            except Exception as e:
                return {"success": False, "message": f"Errore StaticValues: {e}"}
        return None

    # TRASFORMAZIONI

    def _applica_trasformazioni(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        run_id: Optional[str] = None,
        canvas_graph: Optional[CanvasGraph] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Optional[FrameLike]:
        """
        Applica le trasformazioni del canvas in ordine topologico.
        Supporta sia nodi 'transform' che 'multi_join'.
        Se run_id Ã¨ presente, scrive uno snapshot intermediate per ogni nodo.
        """
        # Prendi sia transform che multi_join
        process_nodes = [n for n in canvas.nodes if n.type in ("transform", "multi_join")]
        
        if not process_nodes:
            return self._gestisci_join_default(dataframes)
        
        node_map = {node.id: node for node in process_nodes}
        sorted_node_ids = kahn_topological_order(
            [node.id for node in process_nodes],
            [(edge.source, edge.target) for edge in canvas.edges],
            on_cycle="raise",
        )
        sorted_nodes = [node_map[node_id] for node_id in sorted_node_ids]
        
        current_df = None
        graph = canvas_graph or CanvasGraph.from_canvas(canvas)

        for node in sorted_nodes:
            # Trova input per questo nodo - ORDINA per targetHandle o posizione Y
            incoming = sorted_incoming_edges(canvas.edges, node.id, graph)

            inputs = [dataframes[e.source] for e in incoming if e.source in dataframes]

            if not inputs:
                logger.warning(f"Nodo {node.id} senza input, skip")
                continue
            
            if node.type == "multi_join":
                # Esegui multi-join
                current_df = self._esegui_multi_join(node, inputs)
                logger.info(f"Multi-join: {len(inputs)} input -> {len(current_df)} righe", op="transform")
            else:
                # Trasformazione normale
                current_df = self._esegui_trasformazione(node, inputs)
            
            # Salva risultato per nodi successivi
            dataframes[node.id] = current_df

            if run_id and current_df is not None:
                try:
                    transform_id = (
                        _nf(node, "transform_op")
                        if node.type == "transform" else None
                    )
                    record_checkpoint_snapshot(
                        run_id=run_id,
                        module="manipola",
                        node_id=node.id,
                        node_type=node.type,
                        df=current_df,
                        exec_time=0.0,
                        status="ok",
                        node_data=getattr(node, "data", None),
                        transform_id=transform_id,
                        mode_override=intermediate_mode_override,
                    )
                except Exception as _iex:
                    logger.warning(f"Intermediate write nodo {node.id} fallita (non bloccante): {_iex}")

        return current_df

    def _esegui_multi_join(self, node: Node, inputs: List[FrameLike]) -> FrameLike:
        """
        Esegue multi-join su N DataFrame.

        Strategia:
        1. Prende il primo DataFrame come base
        2. Fa merge sequenziale con gli altri
        3. Gestisce colonne duplicate
        """
        if len(inputs) < 2:
            logger.warning("Multi-join richiede almeno 2 input")
            return inputs[0] if inputs else empty_dataframe()

        join_keys = _nf(node, 'join_keys') or (node.data or {}).get('config', {}).get('join_keys', [])
        join_type = _nf(node, 'multi_join_type') or (node.data or {}).get('config', {}).get('join_type', 'inner')

        if not join_keys:
            logger.warning("Multi-join senza chiavi specificate, cerco colonne comuni")
            # Trova colonne comuni a tutti i DataFrame
            common_cols = set(inputs[0].columns)
            for df in inputs[1:]:
                common_cols &= set(df.columns)
            join_keys = list(common_cols)

            if not join_keys:
                logger.warning("Nessuna colonna comune trovata per il join")
                return inputs[0]

        # Verifica che le chiavi esistano in tutti i DataFrame
        for idx, df in enumerate(inputs):
            missing = [k for k in join_keys if k not in df.columns]
            if missing:
                logger.warning(f"Input {idx}: chiavi mancanti {missing}")
                return inputs[0]


        # Inizia con il primo DataFrame
        result = inputs[0].copy()

        # Multi-join sequenziale: ogni step `merge_dataframes` puo' inflate
        # l'output rispetto agli input se le chiavi non sono unique
        # Senza un guard, un'esplosione al merge step k si propaga ai
        # merge step k+1, k+2, ... saturando la memoria del worker
        # SIGKILL OS
        # Strategia: pre-merge cardinality estimate. Prima di ogni
        # `merge_dataframes`, stimiamo le righe output dalle frequenze delle
        # join key tramite il boundary dataframe. Se la stima supera il cap
        # solleviamo PRIMA della materializzazione del DataFrame. Reuse
        # del contratto Memory/OOM
        # cross-join 003): stessa eccezione `MemoryLimitExceededError`
        # stesso codice envelope `memory_limit_exceeded`, stage
        # `manipola_multi_join`, status 413
        # Il costo e' O(N+M) per step, ma evita sia il falso positivo
        # del worst-case cartesian (`len(result) * len(next_df)`) sia
        # l'OOM del primo merge esplosivo. Il post-merge check resta
        # come safety net se merge_dataframes o join_type non stimabile
        # divergono
        try:
            _multi_cap = int(settings.get("MULTI_JOIN_MAX_OUTPUT_ROWS", 50_000_000))
        except Exception:
            # Contesto pure-test senza Settings: guard off
            _multi_cap = 0

        # Sanity: input[0] da solo non deve superare il cap. Cattura
        # casi degenere (l'utente passa un DataFrame gia' troppo grande
        # come base del multi-join
        if _multi_cap > 0 and len(result) > _multi_cap:
            logger.warning(
                f"Multi-join base input ({len(result)} righe) supera il cap "
                f"configurato di {_multi_cap} righe â€” guard attivato pre-loop."
            )
            raise MemoryLimitExceededError(
                observed_bytes=len(result),
                limit_bytes=_multi_cap,
                path_basename="multi_join_output",
                stage=MULTI_JOIN_STAGE,
            )

        # Merge sequenziale con gli altri
        for idx, df in enumerate(inputs[1:], start=2):
            df_copy = df.copy()

            # Trova colonne duplicate (non-chiave
            result_cols = set(result.columns) - set(join_keys)
            df_cols = set(df_copy.columns) - set(join_keys)
            duplicates = result_cols & df_cols

            if duplicates:
                # Rinomina nel df corrente
                rename_map = {col: f"{col}_{idx}" for col in duplicates}
                df_copy = df_copy.rename(columns=rename_map)
                logger.info(f"Rinominate colonne: {list(rename_map.keys())}")

            # Pre-merge exact cardinality estimate. Runs before
            # merge_dataframes materializes the merge output.
            if _multi_cap > 0:
                _estimated_rows = _estimate_merge_output_rows(
                    result, df_copy, join_keys, join_type
                )
                if _estimated_rows is not None and _estimated_rows > _multi_cap:
                    logger.warning(
                        f"Multi-join step #{idx}: output stimato "
                        f"{_estimated_rows} righe sopra il cap {_multi_cap} "
                        f"prima del merge â€” guard attivato."
                    )
                    raise MemoryLimitExceededError(
                        observed_bytes=_estimated_rows,
                        limit_bytes=_multi_cap,
                        path_basename="multi_join_output",
                        stage=MULTI_JOIN_STAGE,
                    )

            # Merge
            result = merge_dataframes(
                result,
                df_copy,
                on=join_keys,
                how=join_type
            )

            logger.info(f"Dopo merge #{idx}: {len(result)} righe, {len(result.columns)} col")

            # POST-merge cumulative guard: raise PRIMA del merge
            # successivo se il cumulative output ha sforato. Cattura
            # l'inflazione effettiva (incluso il caso di non-unique
            # keys) prevenendo la propagazione N-step
            if _multi_cap > 0 and len(result) > _multi_cap:
                logger.warning(
                    f"Multi-join post-step #{idx}: output cumulato "
                    f"{len(result)} righe supera il cap {_multi_cap} â€” "
                    f"guard attivato. Step rimanenti: {len(inputs) - idx}."
                )
                raise MemoryLimitExceededError(
                    observed_bytes=len(result),
                    limit_bytes=_multi_cap,
                    path_basename="multi_join_output",
                    stage=MULTI_JOIN_STAGE,
                )

        return result

    def _gestisci_join_default(
        self,
        dataframes: FrameMap,
    ) -> FrameLike:
        """Singola sorgente: passthrough. Multiple sorgenti senza join: errore."""
        if not dataframes:
            raise ValueError("Nessun progetto sorgente configurato")
        if len(dataframes) == 1:
            return list(dataframes.values())[0]
        raise ValueError(f"{len(dataframes)} sorgenti ma nessun nodo Transform.")

    def _esegui_trasformazione(
        self,
        node: Node,
        inputs: List[FrameLike],
    ) -> FrameLike:
        """Delega al PluginExecutor. Rilancia l'eccezione in caso di errore (no silent fail)."""
        transform_op = _nf(node, 'transform_op')
        if not transform_op:
            return inputs[0] if inputs else empty_dataframe()
        return get_executor().execute(transform_op, inputs, node)

    # GROUP BY

    def _applica_group_by(self, df: FrameLike, result_node: Node) -> FrameLike:
        """Applica GROUP BY dal nodo risultato (count, sum, mean, min, max, std)."""
        if not _nf(result_node, 'group_by_enabled', False):
            return df
        group_cols = _nf(result_node, 'group_by_columns', [])
        aggregations = _nf(result_node, 'aggregations', [])
        if not group_cols and not aggregations:
            return df

        try:
            group_cols = [c for c in group_cols if c in df.columns]
            if not group_cols:
                return df

            # Separa aggregazioni count da quelle su colonne specifiche
            count_aliases = []
            agg_dict, rename_dict = {}, {}

            for agg in aggregations:
                col  = agg.get('column')
                func = agg.get('function', 'count')
                alias = agg.get('alias', f"{col}_{func}")

                if func == 'count':
                    # COUNT(*) via groupby.size â€” non dipende da una
                    # colonna specifica, non perde righe sul gruppo
                    count_aliases.append(alias)
                elif func in ['sum', 'mean', 'min', 'max', 'std']:
                    if col in df.columns:
                        agg_dict[col] = func
                        rename_dict[col] = alias

            # Esegui aggregazioni colonna (solo se ci sono colonne valide
            if agg_dict:
                result = group_aggregate_as_columns(df, group_cols, agg_dict)
                result = result.rename(columns=rename_dict)
            elif count_aliases:
                # Solo COUNT(*) richiesti â€” parti da una base con i gruppi
                result = group_keys_dataframe(df, group_cols)
            else:
                # Nessuna aggregazione valida
                return df

            # Aggiungi COUNT(*) per ogni richiesta
            for alias in count_aliases:
                result[alias] = group_size_values(df, group_cols)

            return result

        except Exception as e:
            logger.error(f"[CRASH] Errore GROUP BY: {e}", exc_info=True)
            return df

    # SALVATAGGIO

    def _salva_risultato(
        self,
        project_id: str,
        result_node: Node,
        df: FrameLike,
        canvas: Optional[CanvasState] = None,
        canvas_graph: Optional[CanvasGraph] = None,
    ) -> JsonObject:
        """Salva il risultato finale nel formato richiesto."""
        filename = _nf(result_node, 'fileName', 'output')
        fmt      = _nf(result_node, 'format',   'csv')
        geom_col = _nf(result_node, 'geomColumn')

        if geom_col is None:
            geom_col = detect_geometry_column(df)

        crs = "EPSG:4326"
        if canvas:
            srid = self._estrai_srid_da_canvas(canvas, canvas_graph=canvas_graph)
            if srid:
                crs = f"EPSG:{srid}"

        try:
            # Leggi group_id del progetto per isolamento corretto dei risultati
            group_id = None
            try:
                metadata = self.project_service.get_project_metadata(project_id)
                if metadata:
                    group_id = getattr(metadata, 'group_id', None)
            except Exception:
                pass

            # Single entry-point: in-memory per csv/xlsx/json/parquet/
            # geojson (zero temp), filesystem fallback interno per
            # gpkg/shp. Nessun if/else duplicato caller-side
            saved = save_pipeline_result_outcome(
                project_id=project_id,
                df=df,
                output_format=fmt,
                filename_stem=filename,
                rows_count=len(df),
                group_id=group_id,
                geom_column=geom_col,
                crs=crs,
            )
            return {
                "success": True,
                "output_path": saved.output_filename,
                "format": fmt.lower(),
                "rows_count": len(df),
                "columns_count": len(df.columns),
                "result_id": saved.result_id,
                "message": f"Salvato {len(df)} righe in {saved.output_filename}"
            }
        except Exception as e:
            return {"success": False, "message": f"Errore salvataggio: {e}"}

    def _estrai_srid_da_canvas(
        self,
        canvas: CanvasState,
        *,
        canvas_graph: Optional[CanvasGraph] = None,
    ) -> Optional[int]:
        """Estrae primo SRID da tipi geometry nel canvas via ResourceMetadata.

         Parses canvas once through CanvasGraph so
        node field access uses CanvasNode.get_field instead of ad-hoc
        getattr/data.get dual-path. CanvasGraph stays structural: no
        Manipola-specific logic added to it.
        """
        return extract_srid_from_canvas(canvas, canvas_graph=canvas_graph)

    # MAPPA TIPI CANVAS

    def _costruisci_mappa_tipi_canvas(
        self,
        canvas: CanvasState,
        *,
        canvas_graph: Optional[CanvasGraph] = None,
    ) -> StringMap:
        """Costruisce mappa colonna->tipo dal canvas. Include suffissi _L/_R per JOIN."""
        return build_canvas_type_map(canvas, canvas_graph=canvas_graph)

    def _costruisci_colonne_con_tipi(
        self,
        df: FrameLike,
        canvas_type_map: StringMap,
    ) -> ColumnMetadataRows:
        """Costruisce lista colonne con tipi. Lookup: canvas -> dtype -> WKT."""
        return build_columns_with_types(df, canvas_type_map, logger=logger)

    def _rileva_geometria_da_contenuto(self, df: FrameLike, col_name: str) -> str:
        """Rileva geometria WKT o oggetti Shapely dal contenuto di una colonna."""
        return detect_geometry_from_content(df, col_name, logger=logger)
