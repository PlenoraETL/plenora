"""
Executor del modulo MANIPOLA - pipeline di trasformazione dati.

Keeps transformation behavior isolated from orchestration callers.
"""

import time as _time
from typing import Any, List, Mapping, MutableMapping, Optional

from app.shared.execution.node_fields import node_field
from app.shared.execution.result_grouping import result_group_plan
from app.core.dataframes.output_columns import apply_output_columns
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
from app.core.manipola.join_memory import (
    MULTI_JOIN_MEMORY_STAGE,
    guard_estimated_merge_memory,
)
from app.config import settings
from app.core.projects.execution_context import get_project_group_id
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.executor_protocol import PipelineDispatcherProtocol
from app.shared.execution.failed_node import failed_node_fields, mark_failed_node, node_step
from app.shared.execution.graph import kahn_topological_order
from app.utils.logger import get_logger, append_step
from app.core.results.pipeline_result import save_pipeline_result_outcome
from app.core.dataframes.frame_io import load_dataframe_from_file, rename_duplicate_columns
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
from app.core.output_contract import persist_output_contract, result_columns_from_plan
from app.shared.resources.metadata import ResourceMetadata
from .plugin_executor import get_executor
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.execution.join_preparation import normalize_key_list as _normalize_key_list, normalize_join_pairs as _normalize_join_pairs, multi_join_renames
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
from app.core.projects.canvas_preview_scope import scoped_canvas


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



def _estimate_merge_output_rows_by_keys(
    left: FrameLike,
    right: FrameLike,
    left_keys: List[str],
    right_keys: List[str],
    how: str,
) -> Optional[int]:
    """Estimate equality-merge rows when left/right key names differ."""
    if left_keys == right_keys:
        return _estimate_merge_output_rows(left, right, left_keys, how)

    join_how = str(how or "inner").lower()
    if join_how not in {"inner", "left", "right", "outer"}:
        return None
    if not left_keys or len(left_keys) != len(right_keys):
        return None

    # Le proiezioni sono gia' DataFrame distinti. Un ulteriore shallow copy
    # duplicherebbe solo manager/metadati senza isolare altri buffer.
    left_tmp = left[left_keys]
    right_tmp = right[right_keys]
    right_tmp.columns = list(left_keys)
    return estimate_merge_output_rows(left_tmp, right_tmp, left_keys, join_how)


def _failure_step(message: str, step_start: float, exc: BaseException | None = None) -> JsonObject:
    """Emette `append_step` di failure manipola + ritorna il dict da
    `return`-are dal chiamante.

    Single-source dello shape "step di failure" usato da tutti i branch
    di errore di `execute()`. Concentrare lo shape qui evita branch
    dimenticati e step log incoerenti.

    ``exc`` sollevata dentro un nodo (``shared.execution.failed_node``)
    aggiunge ``error_node_id``/``error_node_type``, come Arricchisci; senza
    nodo l'esito resta ``{success, message}``.
    """
    append_step({
        "module":      "manipola",
        "status":      "failure",
        "rows_out":    0,
        "duration_ms": int((_time.time() - step_start) * 1000),
        "error":       message,
    })
    return {"success": False, "message": message, **failed_node_fields(exc)}


def _nf(node: Node, key: str, default: Any = None) -> Any:
    """Legge un campo flat dal nodo Pydantic o dal wrapper ``node.data``."""
    return node_field(node, key, default)


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
    ``node_columns`` is skipped — metadata travels from the loader rather
    than being reconstructed here. ``node_columns`` is still required for
    callers that do not supply ``meta``.
    """
    if is_geodataframe(df):
        return df  # Gia' risorsa geospaziale: non toccare.

    if meta is None:
        # Normalise alias → name before building ResourceMetadata: after
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

    def set_pipeline_dispatcher(self, dispatcher: PipelineDispatcherProtocol) -> None:
        """Set the dispatcher used for recursive upstream project execution."""
        self.pipeline_dispatcher = dispatcher

    # ESECUZIONE COMPLETA

    def _init_security_context(self, project_id: str, *, strict: bool) -> Optional[str]:
        """Risolve il `group_id` del progetto corrente e lo ritorna.

        I caller (execute / preview) propagano il valore ritornato come
        kwarg esplicito ai source loader. Tutti e tre i loader sono
        ctx-strict: il group_id deve venire da qui, non da stato di istanza.

        - `strict=True` (execute): ValueError fail-closed se il group_id
          non e' determinabile (l'esecuzione persiste output e va legata
          a un tenant).
        - `strict=False` (preview): ritorna None in caso di lookup fallito;
          i loader fail-closed e il preview torna "Nessun dato sorgente
          disponibile" — comportamento corretto per canvas con sorgenti
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
        execution_context: Optional[Mapping[str, Any]] = None,
        output_persistence: Any = None,
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

        executing_group_id = (
            str(execution_context.get("group_id") or "")
            if execution_context is not None
            else self._init_security_context(project_id, strict=True)
        )
        if not executing_group_id:
            raise ValueError(f"group_id non determinabile per esecuzione {project_id}")
        canvas_graph = CanvasGraph.from_canvas(canvas)

        _push_to_execution_chain(project_id)

        _step_start = _time.time()
        dataframes = {}

        try:
            # Tutti e 3 i loader sono ctx-strict: `executing_group_id` e'
            # l'unico canale tenant, nessuno stato di istanza coinvolto.
            for loader in [
                lambda: self._carica_sorgenti_progetto(
                    canvas, dataframes, force_recalculate, run_id=run_id, group_id=executing_group_id,
                    test_run=bool((execution_context or {}).get("test_run")),
                ),
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
            # Ciclo cross-project rilevato a runtime (defense-in-depth per
            # canvas che sfuggono al check di update_canvas).
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
                project_id=project_id,
                canvas_graph=canvas_graph,
                intermediate_mode_override=intermediate_mode_override,
            )
        except MemoryLimitExceededError as mem_err:
            # `MemoryLimitExceededError` NON e' una sottoclasse di
            # `ValueError`: serve handler esplicito per emettere `append_step`
            # e distinguere stage OOM dai generici errori worker.
            stage = getattr(mem_err, "stage", "unknown")
            return _failure_step(
                f"Memoria insufficiente durante manipolazione "
                f"(stage={stage}): {mem_err}",
                _step_start, mem_err,
            )
        except ValueError as e:
            return _failure_step(str(e), _step_start, e)
        except Exception as e:
            # Catch-all per eccezioni non previste dai plugin transform:
            # log con exc_info e step di failure, cosi' il modulo resta
            # visibile nello step log della run.
            logger.error(
                f"[CRASH] Errore imprevisto trasformazione manipola: "
                f"{type(e).__name__}: {e}",
                op="execute", project_id=project_id, exc_info=True,
            )
            return _failure_step(
                f"Errore imprevisto trasformazione: {type(e).__name__}",
                _step_start, e,
            )
        if current_df is None:
            # Failure path "trasformazioni hanno prodotto None".
            return _failure_step(
                "Nessun dato prodotto dalla manipolazione", _step_start,
            )

        # `current_df` mantiene il risultato necessario. Il frame-map del
        # grafo non serve piu' e potrebbe trattenere terminali di rami
        # secondari o il frame pre-GROUP-BY per tutta la serializzazione.
        dataframes.clear()

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return _failure_step("Nodo risultato non trovato", _step_start)

        try:
            with node_step(result_node):
                current_df = self._applica_group_by(
                    apply_output_columns(current_df, _nf(result_node, "output_columns"), self._result_input_columns(canvas, result_node, project_id)), result_node
                )
        except MemoryLimitExceededError as mem_err:
            stage = getattr(mem_err, "stage", "group_by")
            return _failure_step(
                f"Memoria insufficiente durante GROUP BY (stage={stage}): "
                f"{mem_err}",
                _step_start, mem_err,
            )
        except ValueError as e:
            # `_applica_group_by` solleva ValueError su config invalide
            # (colonne inesistenti, aggregations vuote).
            return _failure_step(str(e), _step_start, e)

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

        current_df, renamed_columns = rename_duplicate_columns(current_df)
        if renamed_columns:
            logger.warning(
                "MANIPOLA result con colonne duplicate normalizzate: "
                + ", ".join(f"{old}->{new}" for old, new in renamed_columns),
                op="execute",
                project_id=project_id,
            )

        save_result = self._salva_risultato(
            project_id,
            result_node,
            current_df,
            canvas,
            canvas_graph=canvas_graph,
            group_id_override=executing_group_id,
            output_persistence=output_persistence,
        )

        # Keep project_source reuse aligned with the actual persisted schema.
        # persist_contract: policy dichiarata dal contract path (vedi
        # PipelineExecutionContract.build_runtime_context).
        if save_result.get("success") and (
            execution_context is None
            or bool(execution_context.get("persist_contract", True))
        ):
            persisted_columns = save_result.get("output_columns")
            result_columns = result_columns_from_plan(
                project_id=project_id, columns=current_df.columns, plan=[],
                dataframe=current_df,
            )
            try:

                schema_service = SchemaInferenceService(self.project_service)
                nodes_by_id = {n.id: n for n in canvas.nodes}
                edges_by_target = schema_service._group_edges_by_target(canvas.edges)
                result_node_schema = schema_service._infer_node_schema(
                    node=result_node,
                    nodes_by_id=nodes_by_id,
                    edges_by_target=edges_by_target,
                    project_id=project_id,
                )
                physical_names = (
                    persisted_columns if isinstance(persisted_columns, list)
                    and len(persisted_columns) == len(current_df.columns) else list(current_df.columns)
                )
                result_columns = result_columns_from_plan(
                    project_id=project_id, columns=current_df.columns, plan=result_node_schema,
                    physical_names=physical_names,
                    dataframe=current_df,
                )
            except Exception as exc:
                logger.warning(
                    f"output_contract: schema id non disponibili, uso colonne DataFrame: {exc}"
                )
            # Output contract: errore DB dopo blob persistito non deve
            # oscurare il save. Log ERROR + flag contract_update_failed nel
            # save_result rendono visibile eventuale drift del contract.
            try:
                persist_output_contract(
                    project_service=self.project_service,
                    project_id=project_id,
                    result_columns=result_columns,
                )
            except Exception as contract_err:
                logger.error(
                    f"[CRASH] update_project_output_contract fallito "
                    f"post-manipola: blob persistito, contract resta stale. "
                    f"Caller deve gestire drift: {contract_err}",
                    op="execute", project_id=project_id, exc_info=True,
                )
                save_result["contract_update_failed"] = True
                save_result["contract_update_error"] = str(contract_err)

        append_step({
            "module":      "manipola",
            "status":      "success" if save_result.get("success") else "failure",
            "rows_out":    save_result.get("rows_count", 0) or 0,
            "duration_ms": int((_time.time() - _step_start) * 1000),
            "error":       save_result.get("message") if not save_result.get("success") else None,
        })
        return save_result

    # ANTEPRIMA

    def preview(
        self,
        project_id: str,
        canvas: CanvasState,
        limit: int,
        execution_context: Optional[Mapping[str, Any]] = None,
        node_id: str | None = None,
    ) -> JsonObject:
        """Preview: stessa pipeline dell'execute ma senza salvataggio."""
        logger.info(f"Preview MANIPOLA: {project_id}", op="preview", project_id=project_id)
        node_id = node_id if node_id is not None else (execution_context or {}).get("preview_node_id")
        # Passo scelto o, senza passo, solo cio' che arriva all'Output (L3).
        canvas = scoped_canvas(canvas, node_id)
        try:
            # Stesso tenant binding di execute. `strict=False` perche'
            # preview non persiste output: se il lookup DB fallisce
            # `executing_group_id` resta None e i loader fail-closed
            executing_group_id = str(
                (execution_context or {}).get("group_id") or ""
            ) or self._init_security_context(project_id, strict=False)
            canvas_graph = CanvasGraph.from_canvas(canvas)

            dataframes = {}
            # Ogni loader puo' tornare un dict d'errore: propaghiamolo subito
            # al caller invece di mascherarlo con "Nessun dato sorgente".
            for loader in [
                lambda: self._carica_sorgenti_progetto(
                    canvas, dataframes, force_recalculate=False,
                    group_id=executing_group_id,
                ),
                lambda: self._carica_sorgenti_file(
                    canvas, dataframes, group_id=executing_group_id,
                ),
                lambda: self._carica_sorgenti_datasource(
                    canvas, dataframes, group_id=executing_group_id,
                ),
                lambda: self._carica_sorgenti_statiche(
                    canvas, dataframes, group_id=executing_group_id,
                ),
            ]:
                err = loader()
                if err:
                    return err

            if not dataframes:
                return {"success": False, "message": "Nessun dato sorgente disponibile"}

            # Preview is a bounded sample, not a full execution followed by
            # head(). Keep enough rows for truncation detection while capping
            # every source before joins/group-by/formulas materialize large
            # intermediate frames.
            try:
                configured_preview_cap = int(
                    settings.get("PIPELINE_PREVIEW_SOURCE_MAX_ROWS", 10_000)
                    or 10_000
                )
            except (TypeError, ValueError):
                configured_preview_cap = 10_000
            preview_source_cap = max(int(limit) + 1, configured_preview_cap, 1)
            source_truncated = False
            source_row_counts: MutableMapping[str, int] = {}
            for source_id, source_df in list(dataframes.items()):
                source_rows = int(len(source_df))
                source_row_counts[source_id] = source_rows
                if source_rows > preview_source_cap:
                    dataframes[source_id] = source_df.head(preview_source_cap).copy()
                    source_truncated = True

            current_df = self._applica_trasformazioni(
                canvas, dataframes, project_id=project_id, canvas_graph=canvas_graph
            )
            if node_id is not None and node_id in dataframes:
                current_df = dataframes[node_id]
            if current_df is None:
                return {"success": False, "message": "Nessun dato prodotto dalla pipeline"}

            # Evita che preview/group-by mantengano vivi frame terminali non
            # piu' raggiungibili dal risultato corrente.
            dataframes.clear()

            result_node = next((n for n in canvas.nodes if n.type == "result"), None)
            if result_node:
                with node_step(result_node):
                    current_df = self._applica_group_by(
                        apply_output_columns(current_df, _nf(result_node, "output_columns"), self._result_input_columns(canvas, result_node, project_id)), result_node
                    )

            total_count = len(current_df)
            truncated = source_truncated or total_count > limit
            preview_df = current_df.head(limit)

            columns = self._costruisci_colonne_con_tipi(preview_df, {})
            rows = dataframe_preview_records(current_df, limit)

            return {
                "success": True, "columns": columns, "rows": rows,
                "total_count": total_count, "preview_count": len(rows),
                "truncated": truncated,
                "approximate": source_truncated,
                "source_row_counts": source_row_counts,
            }
        except ValueError as user_err:
            # ValueError = errore di configurazione utente (es. GROUP BY su
            # colonne inesistenti). E' "gestito" perche' atteso: log a
            # WARNING senza stack trace e' sufficiente per triage.
            logger.warning(
                f"[GESTITO] Preview MANIPOLA fallita per config: {user_err}",
                op="preview", project_id=project_id,
            )
            return {"success": False, "message": str(user_err), **failed_node_fields(user_err)}
        except Exception as e:
            # Eccezione non prevista (KeyError, TypeError, bug di codice):
            # log a ERROR con exc_info=True per consentire il triage.
            logger.error(
                f"[CRASH] Preview MANIPOLA: {e}",
                op="preview", project_id=project_id, exc_info=True,
            )
            return {"success": False, "message": str(e), **failed_node_fields(e)}

    # CARICAMENTO SORGENTI

    def _carica_sorgenti_progetto(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        force_recalculate: bool,
        run_id: Optional[str] = None,
        *, group_id: Optional[str] = None,
        test_run: bool = False,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai progetti sorgente (type=project_source).

        SECURITY: verifica che ogni source_project_id appartenga allo stesso
        gruppo del progetto corrente. Senza questo check, un utente potrebbe
        inserire nel canvas un source_project_id di un altro tenant e
        esfiltrare i dati durante l'esecuzione della pipeline.

        Il ``group_id`` esplicito e' obbligatorio: se assente, il metodo
        ritorna fail-closed senza invocare il resolver. I caller
        (execute / preview) ricevono il ``group_id`` da
        ``_init_security_context`` e lo propagano esplicitamente.
        """
        return load_project_sources(
            canvas,
            dataframes,
            force_recalculate=force_recalculate,
            pipeline_dispatcher=self.pipeline_dispatcher,
            run_id=run_id,
            group_id=group_id,
            logger=logger,
            test_run=test_run,
        )

    def _carica_sorgenti_file(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        *, group_id: Optional[str] = None,
    ) -> Optional[JsonObject]:
        """Carica DataFrame dai nodi file_source via SourceLoader.

        thin delegator. Il body vive in
        ``app.core.manipola.source_loading.load_file_sources`` —
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
          - ``group_id`` esplicito e' il canale tenant per questa
            catena (production: ``execute``/``preview`` lo passano
            dopo ``_init_security_context``).
          - Il delegator inoltra al helper solo l'argomento ricevuto:
            ``_carica_sorgenti_progetto`` e' fail-closed senza ctx,
            mentre ``_carica_sorgenti_statiche`` usa l'argomento
            esplicito o il sentinel non-autorizzante ``"_static_"``.
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
        Stesso contratto degli altri loader: `group_id` esplicito,
        mutazione in-place di `dataframes`, ritorno
        `{success: False, message}` su errore canonical.
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

        Lo static branch e' pure processing (no DB, no file I/O). Se
        ``group_id`` e' assente il sentinel non-autorizzante
        ``"_static_"`` soddisfa la guardia non-vuota del loader senza
        ambiguita' cross-tenant (nessuna risorsa esterna caricata in
        questo branch).
        """
        static_nodes = [n for n in canvas.nodes if n.type == "staticValues"]
        if not static_nodes:
            return None

        # Pure processing: explicit group_id (production path) or
        # non-authorizing sentinel.
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
                # Logga il node_id per rendere triageable crash del SourceLoader
                # su nodi staticValues.
                node_id = getattr(node, "id", "<sconosciuto>")
                logger.error(
                    f"[CRASH] Errore StaticValues nodo {node_id}: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
                return {
                    "success": False,
                    "message": f"Errore StaticValues nodo {node_id}: {type(e).__name__}",
                }
        return None

    # TRASFORMAZIONI

    def _applica_trasformazioni(
        self,
        canvas: CanvasState,
        dataframes: FrameMap,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        canvas_graph: Optional[CanvasGraph] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Optional[FrameLike]:
        """
        Applica le trasformazioni del canvas in ordine topologico.
        Supporta sia nodi 'transform' che 'multi_join'.
        Se run_id è presente, scrive uno snapshot intermediate per ogni nodo.
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
        process_node_ids = set(node_map)
        remaining_consumers = {}
        for edge in canvas.edges:
            if edge.target in process_node_ids:
                remaining_consumers[edge.source] = (
                    remaining_consumers.get(edge.source, 0) + 1
                )
        
        current_df = None
        graph = canvas_graph or CanvasGraph.from_canvas(canvas)
        schema_service = None
        schema_nodes_by_id = None
        schema_edges_by_target = None
        if project_id:
            try:

                schema_service = SchemaInferenceService(self.project_service)
                schema_service._schema_cache = {}
                schema_nodes_by_id = {n.id: n for n in canvas.nodes}
                schema_edges_by_target = schema_service._group_edges_by_target(canvas.edges)
            except Exception as exc:
                logger.warning(
                    f"Schema runtime non disponibile per risoluzione column_id: {exc}"
                )

        def _release_consumed_inputs(
            incoming_edges: List[Any],
            current_node_id: str,
        ) -> None:
            for edge in incoming_edges:
                source_id = edge.source
                if source_id not in dataframes:
                    continue

                remaining = remaining_consumers.get(source_id)
                if remaining is None:
                    continue

                remaining -= 1
                remaining_consumers[source_id] = remaining
                if remaining <= 0 and source_id != current_node_id:
                    dataframes.pop(source_id, None)

        def _take_last_consumer_inputs(incoming_edges: List[Any]) -> bool:
            """Trasferisce ownership degli input al nodo quando e' l'ultimo consumer."""
            if not incoming_edges or not all(
                remaining_consumers.get(edge.source) == 1
                for edge in incoming_edges
            ):
                return False
            for edge in incoming_edges:
                dataframes.pop(edge.source, None)
                remaining_consumers[edge.source] = 0
            return True

        for node in sorted_nodes:
            # Trova input per questo nodo - ORDINA per targetHandle o posizione Y
            incoming = sorted_incoming_edges(canvas.edges, node.id, graph)

            inputs = [dataframes[e.source] for e in incoming if e.source in dataframes]

            if not inputs:
                # Se il nodo ha incoming edges ma nessuna source e' presente
                # in `dataframes`, e' un
                # mismatch di stato (upstream fallito, edge orfano,
                # ordering bug) — fail-loud invece di continuare e
                # rischiare che i downstream leggano stale dal grafo
                # parziale. Se invece il nodo e' completamente
                # orfano (no incoming edges) accettiamo lo skip:
                # canvas in fase di draft puo' avere nodi disconnessi.
                if incoming:
                    msg = (
                        f"Nodo {node.id} ({node.type}) ha "
                        f"{len(incoming)} incoming edges ma nessuna "
                        f"source disponibile in dataframes "
                        f"({[e.source for e in incoming]}). Upstream "
                        "fallito o edge mismatch?"
                    )
                    logger.error(f"[CRASH] {msg}", op="execute", project_id=project_id)
                    raise mark_failed_node(ValueError(msg), node.id, node.type)
                logger.warning(f"Nodo {node.id} senza input, skip")
                continue

            owns_inputs = _take_last_consumer_inputs(incoming)
            # Non mantenere vivo un risultato di un ramo precedente durante
            # una trasformazione pesante su un altro ramo.
            current_df = None

            if schema_service and schema_nodes_by_id is not None and schema_edges_by_target is not None:
                try:
                    input_columns = []
                    for edge in incoming:
                        source_node = schema_nodes_by_id.get(edge.source)
                        if not source_node:
                            continue
                        source_schema = schema_service._infer_node_schema(
                            node=source_node,
                            nodes_by_id=schema_nodes_by_id,
                            edges_by_target=schema_edges_by_target,
                            project_id=project_id,
                        )
                        input_columns.extend(col.to_dict() for col in source_schema)
                    # Non mutare l'istanza Pydantic ricevuta dal caller:
                    # `model_copy` produce un clone arricchito per i metodi
                    # downstream, lasciando intatta l'istanza esterna.
                    base_data = node.data if isinstance(node.data, dict) else {}
                    node = node.model_copy(update={"data": {**base_data, "inputColumns": input_columns}})
                except Exception as exc:
                    logger.warning(
                        f"Risoluzione inputColumns fallita per nodo {node.id}: {exc}"
                    )
            
            # Il lavoro del nodo: un errore porta id e tipo del nodo
            # nell'esito (shared.execution.failed_node), messaggio invariato.
            with node_step(node):
                if node.type == "multi_join":
                    # Esegui multi-join
                    current_df = self._esegui_multi_join(node, inputs)
                    logger.info(f"Multi-join: {len(inputs)} input -> {len(current_df)} righe", op="transform")
                else:
                    # Trasformazione normale
                    transform_op = _nf(node, "transform_op")
                    if transform_op == "join":
                        from app.core.manipola.join_optimization import (
                            compact_owned_join_string_columns,
                            project_columns_dropped_immediately_after_join,
                        )
                        from app.core.resources.memory_guard import cleanup_worker_memory

                        inputs = project_columns_dropped_immediately_after_join(
                            canvas, node, inputs
                        )
                        if owns_inputs:
                            inputs = compact_owned_join_string_columns(inputs, node)
                        # Pivot/merge precedenti possono avere gia' liberato i
                        # buffer, lasciandoli pero' residenti nelle arena libc.
                        # Il guard del join usa l'RSS: trim prima della stima per
                        # contare dati vivi, non pagine riutilizzabili.
                        cleanup_stats = cleanup_worker_memory(
                            run_gc=bool(
                                settings.get("WORKER_MEMORY_CLEANUP_ENABLED", True)
                            ),
                            run_malloc_trim=bool(
                                settings.get(
                                    "WORKER_MEMORY_MALLOC_TRIM_ENABLED", True
                                )
                            ),
                        )
                        logger.info(
                            "[JOIN-PRE-TRIM] node=%s rss=%s -> %s bytes gc=%s trim=%s",
                            node.id,
                            cleanup_stats.get("before_rss"),
                            cleanup_stats.get("after_rss"),
                            cleanup_stats.get("collected"),
                            cleanup_stats.get("malloc_trim"),
                        )
                    else:
                        from app.core.manipola.transform_memory import (
                            SPECIALIZED_TRANSFORM_MEMORY_OPS,
                            guard_transform_headroom,
                        )

                        if transform_op not in SPECIALIZED_TRANSFORM_MEMORY_OPS:
                            guard_transform_headroom(inputs, node_id=str(node.id))
                    current_df = self._esegui_trasformazione(node, inputs)
            
            # Salva risultato per nodi successivi
            dataframes[node.id] = current_df
            _release_consumed_inputs(incoming, node.id)
            inputs = []

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

        join_keys, join_type, left_keys, right_keys, use_pair_keys = prepare_multi_join(node, inputs)

        if not join_keys and not use_pair_keys:
            logger.warning("Multi-join senza chiavi specificate, cerco colonne comuni")
            # Trova colonne comuni a tutti i DataFrame
            common_cols = set(inputs[0].columns)
            for df in inputs[1:]:
                common_cols &= set(df.columns)
            join_keys = list(common_cols)

            if not join_keys:
                logger.warning("Nessuna colonna comune trovata per il join")
                return inputs[0]

        if use_pair_keys:
            missing_left = [k for k in left_keys if k not in inputs[0].columns]
            missing_right = [k for k in right_keys if k not in inputs[1].columns]
            if missing_left or missing_right:
                logger.warning(
                    f"Multi-join: chiavi mancanti left={missing_left}, right={missing_right}"
                )
                return inputs[0]
        else:
            # Verifica che le chiavi esistano in tutti i DataFrame
            for idx, df in enumerate(inputs):
                missing = [k for k in join_keys if k not in df.columns]
                if missing:
                    logger.warning(f"Input {idx}: chiavi mancanti {missing}")
                    return inputs[0]


        # Inizia con il primo DataFrame
        # `merge_dataframes` non muta gli input e materializza un nuovo frame.
        # Manteniamo quindi il primo input read-only fino al primo merge.
        result = inputs[0]

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
        except (AttributeError, TypeError, ValueError):
            # Contesto pure-test senza Settings: guard off
            _multi_cap = 0

        # Sanity: input[0] da solo non deve superare il cap. Cattura
        # casi degenere (l'utente passa un DataFrame gia' troppo grande
        # come base del multi-join
        if _multi_cap > 0 and len(result) > _multi_cap:
            logger.warning(
                f"Multi-join base input ({len(result)} righe) supera il cap "
                f"configurato di {_multi_cap} righe — guard attivato pre-loop."
            )
            raise MemoryLimitExceededError(
                observed_bytes=len(result),
                limit_bytes=_multi_cap,
                path_basename="multi_join_output",
                stage=MULTI_JOIN_STAGE,
            )


        if use_pair_keys:
            df_copy = inputs[1]
            result_cols = set(result.columns)
            df_cols = set(df_copy.columns) - set(right_keys)
            duplicates = result_cols & df_cols

            if duplicates:
                rename_map = multi_join_renames(result.columns, df_copy.columns, left_keys, right_keys, 2, paired=True)
                df_copy = df_copy.rename(columns=rename_map)
                logger.info(f"Rinominate colonne: {list(rename_map.keys())}")

            _estimated_rows = _estimate_merge_output_rows_by_keys(
                result, df_copy, left_keys, right_keys, join_type
            )
            if _multi_cap > 0:
                if _estimated_rows is not None and _estimated_rows > _multi_cap:
                    logger.warning(
                        f"Multi-join con coppie chiavi: output stimato "
                        f"{_estimated_rows} righe sopra il cap {_multi_cap} "
                        f"prima del merge â€” guard attivato."
                    )
                    raise MemoryLimitExceededError(
                        observed_bytes=_estimated_rows,
                        limit_bytes=_multi_cap,
                        path_basename="multi_join_output",
                        stage=MULTI_JOIN_STAGE,
                    )

            if _estimated_rows is not None:
                guard_estimated_merge_memory(
                    result,
                    df_copy,
                    _estimated_rows,
                    stage=MULTI_JOIN_MEMORY_STAGE,
                    path_basename="multi_join_output",
                )

            result = merge_dataframes(
                result,
                df_copy,
                left_on=left_keys,
                right_on=right_keys,
                how=join_type,
            )

            right_keys_to_drop = [
                right_key
                for left_key, right_key in zip(left_keys, right_keys)
                if left_key != right_key and right_key not in left_keys and right_key in result.columns
            ]
            if right_keys_to_drop:
                result = result.drop(columns=[*dict.fromkeys(right_keys_to_drop)])

            logger.info(
                f"Dopo multi-join a coppie: {len(result)} righe, {len(result.columns)} col"
            )

            if _multi_cap > 0 and len(result) > _multi_cap:
                logger.warning(
                    f"Multi-join a coppie: output {len(result)} righe supera "
                    f"il cap {_multi_cap} â€” guard attivato."
                )
                raise MemoryLimitExceededError(
                    observed_bytes=len(result),
                    limit_bytes=_multi_cap,
                    path_basename="multi_join_output",
                    stage=MULTI_JOIN_STAGE,
                )

            return result

        # Merge sequenziale con gli altri
        for idx, df in enumerate(inputs[1:], start=2):
            df_copy = df

            # Trova colonne duplicate (non-chiave
            result_cols = set(result.columns) - set(join_keys)
            df_cols = set(df_copy.columns) - set(join_keys)
            duplicates = result_cols & df_cols

            if duplicates:
                # Rinomina nel df corrente
                rename_map = multi_join_renames(result.columns, df_copy.columns, join_keys, join_keys, idx)
                df_copy = df_copy.rename(columns=rename_map)
                logger.info(f"Rinominate colonne: {list(rename_map.keys())}")

            # Pre-merge exact cardinality estimate. Runs before
            # merge_dataframes materializes the merge output.
            _estimated_rows = _estimate_merge_output_rows(
                result, df_copy, join_keys, join_type
            )
            if _multi_cap > 0:
                if _estimated_rows is not None and _estimated_rows > _multi_cap:
                    logger.warning(
                        f"Multi-join step #{idx}: output stimato "
                        f"{_estimated_rows} righe sopra il cap {_multi_cap} "
                        f"prima del merge — guard attivato."
                    )
                    raise MemoryLimitExceededError(
                        observed_bytes=_estimated_rows,
                        limit_bytes=_multi_cap,
                        path_basename="multi_join_output",
                        stage=MULTI_JOIN_STAGE,
                    )

            if _estimated_rows is not None:
                guard_estimated_merge_memory(
                    result,
                    df_copy,
                    _estimated_rows,
                    stage=MULTI_JOIN_MEMORY_STAGE,
                    path_basename="multi_join_output",
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
                    f"{len(result)} righe supera il cap {_multi_cap} — "
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

    def _result_input_columns(self, canvas: CanvasState, result_node: Node, project_id: str) -> JsonRows:
        """Ricava le identità dall'ingresso dell'Output nel canvas della chiamata."""

        if not _nf(result_node, "output_columns"):
            return []
        service = SchemaInferenceService(self.project_service)
        inputs = service._get_input_schemas(
            result_node.id, {node.id: node for node in canvas.nodes},
            service._group_edges_by_target(canvas.edges), project_id,
        )
        return [col.to_dict() for col in inputs[0]] if inputs else []

    def _applica_group_by(self, df: FrameLike, result_node: Node) -> FrameLike:
        """Applica GROUP BY dal nodo risultato (count, sum, mean, min, max, std).

        Contratto failure-visibility (vedi
        `tests2/manipola/unit/test_executor_failure_visibility_contract.py`):

        - GROUP BY non richiesto (flag disabled, oppure ne' group_cols ne'
          aggregations) → ritorna `df` invariato (no-op intenzionale).
        - GROUP BY richiesto ma config invalida (colonne inesistenti,
          aggregations vuote o tutte invalide) → solleva `ValueError`.
          `execute()` lo cattura e produce `{success: False}` con
          `append_step` di failure: l'utente vede l'errore invece di
          ricevere dati non aggregati.
        - Errore runtime (pandas raise, casting fallito, ...) →
          eccezione propaga, `execute()` la registra come failure.

        Gli errori propagano: non persistere dati raw come output ufficiale
        quando il group_by e' configurato ma fallisce.
        """
        group_cols, aggregations = result_group_plan(result_node, list(df.columns))
        if not group_cols and not aggregations:
            return df
        result = group_keys_dataframe(df, group_cols)
        for col, func, alias in aggregations:
            if func == "count":
                result[alias] = group_size_values(df, group_cols)
            else:
                aggregated = group_aggregate_as_columns(df, group_cols, {col: func})
                result[alias] = aggregated[col].values
        return result

    # SALVATAGGIO

    def _salva_risultato(
        self,
        project_id: str,
        result_node: Node,
        df: FrameLike,
        canvas: Optional[CanvasState] = None,
        canvas_graph: Optional[CanvasGraph] = None,
        group_id_override: Optional[str] = None,
        output_persistence: Any = None,
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
            # Leggi group_id del progetto per isolamento corretto dei risultati.
            # Errori DB sul lookup sono WARNING visibili; il save procede
            # con group_id=None preservando l'invariante "salvataggio
            # best-effort" del path attuale.
            group_id = group_id_override
            if group_id is None:
                try:
                    metadata = self.project_service.get_project_metadata(project_id)
                    if metadata:
                        group_id = getattr(metadata, 'group_id', None)
                except Exception as meta_err:
                    logger.warning(
                        f"[GESTITO] group_id non recuperabile per {project_id}: "
                        f"{meta_err}; salvataggio procede con group_id=None.",
                        op="execute", project_id=project_id, exc_info=True,
                    )

            # Single entry-point: in-memory per csv/xlsx/json/parquet/
            # geojson (zero temp); i formati container sono gestiti
            # dentro il boundary risultati, senza duplicare branch nel
            # caller.
            column_types = None
            if canvas is not None:
                column_types = self._costruisci_mappa_tipi_canvas(
                    canvas,
                    canvas_graph=canvas_graph,
                )
            if output_persistence is not None:
                saved = output_persistence.persist_dataframe_output(
                    df=df,
                    output_format=fmt,
                    filename_stem=filename,
                    geom_column=geom_col,
                    crs=crs,
                    column_types=column_types,
                )
            else:
                saved = save_pipeline_result_outcome(
                    project_id=project_id,
                    df=df,
                    output_format=fmt,
                    filename_stem=filename,
                    rows_count=len(df),
                    group_id=group_id,
                    geom_column=geom_col,
                    crs=crs,
                    column_types=column_types,
                )
            saved_format = (
                "xlsx" if str(fmt).lower() == "xls" else str(fmt).lower()
            )
            return {
                "success": True,
                "output_path": saved.output_filename,
                "format": saved_format,
                "rows_count": len(df),
                "columns_count": len(df.columns),
                "result_id": getattr(saved, "result_id", None),
                "storage_key": getattr(saved, "storage_key", None),
                "output_columns": saved.output_columns,
                "message": f"Salvato {len(df)} righe in {saved.output_filename}"
            }
        except Exception as e:
            # Log esplicito per errori I/O (MinIO down, disco pieno, S3 5xx).
            logger.error(
                f"[CRASH] Errore salvataggio MANIPOLA: {e}",
                op="execute", project_id=project_id, exc_info=True,
            )
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

    # MAPPA TIPI CANVAS (solo salvataggio dell'output, identica a prima)

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


def prepare_multi_join(node: Node, inputs: list) -> tuple:
    """Resolve keys and pair constraints using the execution configuration."""
    data_config = (node.data or {}).get('config', {}) if isinstance(node.data, dict) else {}
    join_keys = _nf(node, 'join_keys') or data_config.get('join_keys', [])
    join_key_ids = _nf(node, 'join_key_ids') or data_config.get('join_key_ids', [])
    if join_key_ids and isinstance(node.data, dict):
        id_to_name = {
            str(col.get("id") or col.get("column_id")): str(col.get("name"))
            for col in (node.data.get("inputColumns") or [])
            if isinstance(col, dict) and (col.get("id") or col.get("column_id")) and col.get("name")
        }
        resolved_keys = [
            id_to_name.get(str(column_id), join_keys[idx] if idx < len(join_keys) else None)
            for idx, column_id in enumerate(join_key_ids)
        ]
        join_keys = [key for key in resolved_keys if key]
    join_keys = _normalize_key_list(join_keys)
    join_type = _nf(node, 'multi_join_type') or data_config.get('join_type', 'inner')

    left_keys = _normalize_key_list(_nf(node, 'left_keys') or data_config.get('left_keys'))
    right_keys = _normalize_key_list(_nf(node, 'right_keys') or data_config.get('right_keys'))
    pair_left_keys, pair_right_keys = _normalize_join_pairs(
        _nf(node, 'join_pairs') or data_config.get('join_pairs')
    )
    if (not left_keys or not right_keys) and pair_left_keys and pair_right_keys:
        left_keys = pair_left_keys
        right_keys = pair_right_keys

    use_pair_keys = len(inputs) == 2 and bool(left_keys or right_keys)
    if use_pair_keys:
        if not left_keys or not right_keys or len(left_keys) != len(right_keys):
            raise ValueError(
                "Multi-join con coppie di chiavi non valido: il numero di "
                "chiavi sinistre deve coincidere con quello delle chiavi destre."
            )

    return join_keys, join_type, left_keys, right_keys, use_pair_keys
