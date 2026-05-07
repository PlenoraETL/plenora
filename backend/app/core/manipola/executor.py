"""
Executor del modulo MANIPOLA - pipeline di trasformazione dati.

Keeps transformation behavior isolated from orchestration callers.
"""

import re
import threading
import time as _time
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.models import CanvasState, Edge, Node
from app.config import settings
from app.services.project_lineage import ProjectLineageCycleError
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.graph import kahn_topological_order
from app.utils.logger import get_logger, append_step
from app.utils.result_store import save_pipeline_result
from app.utils.dataframe import (
    load_dataframe_from_file, filter_visible_columns,
    sanitize_for_json, find_latest_output_file,
)
from app.utils.geo import detect_geometry_column
from app.utils.template_variables import process_static_fields
from .plugin_executor import get_executor
from .source_loading import load_file_sources, load_project_source_artifact


logger = get_logger(__name__)

# stage identifier per il guard OOM pre-merge del multi-join
# Estende la family `manipola_*` (cross-join: `manipola_cross_join`
# Il handler in app/main.py riconosce questo stage come row-count
# stage e produce messaggio + suggested_action row-count-aware
MULTI_JOIN_STAGE = "manipola_multi_join"


def _estimate_merge_output_rows(
    left: pd.DataFrame,
    right: pd.DataFrame,
    join_keys: List[str],
    how: str,
) -> Optional[int]:
    """Estimate pandas merge output cardinality from key frequencies.

    The estimate is exact for pandas equality joins on the configured keys and
    join types used by multi-join (`inner`, `left`, `right`, `outer`). It costs
    O(N+M), but it runs before `pd.merge` allocates the output DataFrame.
    """
    join_how = str(how or "inner").lower()
    if join_how not in {"inner", "left", "right", "outer"}:
        return None
    if not join_keys:
        return None

    left_counts = left.groupby(join_keys, dropna=False).size()
    right_counts = right.groupby(join_keys, dropna=False).size()

    matched_keys = left_counts.index.intersection(right_counts.index)
    if len(matched_keys):
        left_matched = left_counts.loc[matched_keys]
        right_matched = right_counts.loc[matched_keys]
        matched_rows = int((left_matched * right_matched).sum())
        left_matched_input_rows = int(left_matched.sum())
        right_matched_input_rows = int(right_matched.sum())
    else:
        matched_rows = 0
        left_matched_input_rows = 0
        right_matched_input_rows = 0

    left_unmatched_rows = len(left) - left_matched_input_rows
    right_unmatched_rows = len(right) - right_matched_input_rows

    if join_how == "inner":
        return matched_rows
    if join_how == "left":
        return matched_rows + left_unmatched_rows
    if join_how == "right":
        return matched_rows + right_unmatched_rows
    return matched_rows + left_unmatched_rows + right_unmatched_rows


# C3 round 3 fix 3: cycle detection runtime cross-project
# Thread-local chain di project_id attualmente in esecuzione. Usata come
# defense-in-depth contro cicli nei project_source: se _carica_sorgenti_progetto
# sta per invocare orchestrator_callback su un progetto gia' presente nella
# chain, stiamo chiudendo un ciclo A→B→A → ProjectLineageCycleError con chain
# readable
# Why threading.local: ogni worker Celery gira i suoi task in thread/processo
# separato; il chain e' per-thread cosi' due esecuzioni parallele non si
# vedono a vicenda
# Why runtime check in aggiunta al save-time check: ProjectService.update_canvas
# blocca la creazione di canvas ciclici, ma non copre canvas legacy creati
# prima del fix, bypass del service (direct DB edit), o race save-vs-execute
_manipola_execution_chain = threading.local()


def _get_execution_chain() -> List[str]:
    if not hasattr(_manipola_execution_chain, "projects"):
        _manipola_execution_chain.projects = []
    return _manipola_execution_chain.projects


def _push_to_execution_chain(project_id: str) -> None:
    _get_execution_chain().append(project_id)


def _pop_from_execution_chain(project_id: str) -> None:
    chain = _get_execution_chain()
    if chain and chain[-1] == project_id:
        chain.pop()


def _assert_no_lineage_cycle_on_chain(source_project_id: str) -> None:
    """Solleva ProjectLineageCycleError se source_project_id e' gia'
    nella chain di esecuzione corrente (stiamo chiudendo un ciclo)."""
    chain = _get_execution_chain()
    if source_project_id in chain:
        cycle_chain = chain[chain.index(source_project_id):] + [source_project_id]
        raise ProjectLineageCycleError(cycle_chain)

def _nf(node: Node, key: str, default: Any = None) -> Any:
    """Legge un campo flat dal nodo Pydantic, con fallback su node.data (backward compat)."""
    field_value = getattr(node, key, None)
    if field_value is not None and field_value != [] and field_value != {}:
        return field_value
    if node.data and isinstance(node.data, dict):
        return node.data.get(key, default)
    return default


def _applica_crs_da_colonne(
    df: pd.DataFrame,
    node_columns: Optional[List[Dict[str, Any]]],
    meta: Any = None,
) -> pd.DataFrame:
    """
    Se il DataFrame contiene una colonna geometrica e node_columns ha il CRS
    (formato geometry:subtype:SRID), converte il DataFrame in GeoDataFrame
    con il CRS corretto. Evita che le trasformazioni spaziali downstream
    perdano il CRS perché il CSV non lo preserva.

    ``meta`` (optional): pre-built ``ResourceMetadata`` produced by the
    source-loading path. When provided the internal construction from
    ``node_columns`` is skipped — metadata travels from the loader rather
    than being reconstructed here. ``node_columns`` is still required for
    the legacy fallback path (callers that do not supply ``meta``).
    """
    import geopandas as gpd
    from app.utils.geo import to_geodataframe

    if isinstance(df, gpd.GeoDataFrame):
        return df  # già GeoDataFrame, non toccare

    if meta is None:
        from app.shared.resources.metadata import ResourceMetadata
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

    def __init__(self, project_service: object, orchestrator_callback: object = None) -> None:
        self.project_service = project_service
        self.orchestrator_callback = orchestrator_callback
        self.results_base_dir = settings.RESULTS_DIR

    def set_orchestrator_callback(self, callback: object) -> None:
        """Imposta callback per esecuzione ricorsiva progetti upstream."""
        self.orchestrator_callback = callback

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
          disponibile" — comportamento corretto per canvas con sorgenti
          inaccessibili.
        """
        resolved: Optional[str]
        try:
            from app.db.config.projects import get_project as _get_project
            _proj_row = _get_project(project_id)
            resolved = _proj_row.get("group_id") if _proj_row else None
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
    ) -> Dict[str, Any]:
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

        # C3 round 3 fix 3: push del project_id sulla chain di
        # esecuzione corrente. _carica_sorgenti_progetto verifica la
        # chain prima di chiamare orchestrator_callback per rilevare
        # cicli A → B → A (defense-in-depth contro canvas legacy
        _push_to_execution_chain(project_id)

        _step_start = _time.time()
        dataframes = {}

        try:
            # TE-007/008/009: tutti e 3 i loader sono ctx-strict
            # `executing_group_id` (da `_init_security_context`) e' l'unico
            # canale tenant — nessuno stato di istanza coinvolto
            for loader in [
                lambda: self._carica_sorgenti_progetto(canvas, dataframes, force_recalculate, run_id=run_id, group_id=executing_group_id),
                lambda: self._carica_sorgenti_file(canvas, dataframes, group_id=executing_group_id),
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

        # Snapshot intermediate.db per il nodo result
        # passa per `record_final_snapshot`
        # che applica la policy globale `INTERMEDIATE_PERSISTENCE_MODE`
        # default `preview_only` -> prime 500 righe + hash + meta
        # Pre-fix: cap fisso a 10.000 righe + write_node legacy che
        # materializzava `df.head(10k).to_dict(records)` sempre
        # Post-fix: niente df.to_dict(full); l'hash del DataFrame e' calcolato
        # via pandas_hash_v1 senza materializzare; preview cap dalla policy
        if run_id and current_df is not None:
            try:
                from app.core.intermediate.snapshot import record_final_snapshot
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

        # Allinea columns_metadata del progetto allo schema reale del
        # DataFrame finale: senza, l'output file resta corretto ma il
        # metadata stale rompe il riuso come project_source
        # Usa l'helper condiviso (retry transient + raise non-transient
        # i fallimenti propagano a Celery che marca FAILED. Pre-fix il
        # try/except warning rendeva impossibile distinguere "tutto ok"
        # da "ok ma metadata stale" downstream
        if save_result.get("success"):
            from app.shared.columns_metadata import save_columns_metadata_with_retry
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

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
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
            rows = sanitize_for_json(preview_df.to_dict(orient='records'))

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
        dataframes: Dict[str, pd.DataFrame],
        force_recalculate: bool,
        run_id: Optional[str] = None,
        *, group_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
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
        source_nodes = [n for n in canvas.nodes if n.type == "project_source"]

        # Ctx-strict: no singleton fallback. Il group_id deve provenire
        # da _init_security_context via execute/preview. Se manca
        # fail-closed immediato senza toccare il resolver
        executing_group_id = group_id
        if not executing_group_id:
            logger.error(
                "[SECURITY] manipola._carica_sorgenti_progetto: "
                "group_id esplicito mancante (ctx-strict), fail-closed"
            )
            return {
                "success": False,
                "message": "Contesto di sicurezza non determinabile (group_id mancante)",
            }

        from app.core.artifacts import (
            ArtifactResolver,
            ArtifactResolutionError,
            LatestProjectArtifactRef,
            MaterializedArtifact,
        )

        for node in source_nodes:
            source_project_id = _nf(node, 'source_project_id')
            if not source_project_id:
                logger.warning(f"Nodo {node.id}: source_project_id mancante")
                continue

            # Cross-tenant isolation delegata a `ArtifactResolver
            # materialize_latest`: il check fail-closed solleva
            # `ArtifactResolutionError` per progetto inesistente / gruppo
            # diverso / errore DB. NON catturare con warn "non bloccante"
            # sarebbe fail-OPEN, l'errore deve risalire al caller
            if not force_recalculate:
                try:
                    materialized = ArtifactResolver().materialize_latest(
                        LatestProjectArtifactRef(project_id=source_project_id),
                        expected_group_id=executing_group_id,
                    )
                except ArtifactResolutionError as _sec_err:
                    logger.error(
                        f"[SECURITY] Cross-tenant access blocked: "
                        f"progetto sorgente {source_project_id} non risolvibile "
                        f"per group={executing_group_id}: {_sec_err}",
                        op="execute",
                    )
                    return {
                        "success": False,
                        "message": (
                            f"Accesso negato: il progetto sorgente "
                            f"'{source_project_id}' appartiene a un gruppo diverso."
                        ),
                    }

                if materialized is None:
                    output_dir = self.results_base_dir / source_project_id
                    fs_file = find_latest_output_file(output_dir)
                    if fs_file:
                        logger.warning(
                            f"[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto: "
                            f"filesystem fallback scattato per "
                            f"source_project={source_project_id} file={fs_file} — "
                            f"progetto upstream senza blob DB, pre-migrazione"
                        )
                        materialized = MaterializedArtifact(
                            path=Path(fs_file), is_temporary=False
                        )

                if materialized is not None:
                    try:
                        node_columns = _nf(node, 'columns', [])
                        with materialized as m:
                            loaded = load_project_source_artifact(
                                m.path,
                                node_columns,
                                source_project_id=source_project_id,
                                group_id=executing_group_id,
                            )
                        dataframes[node.id] = loaded.data
                        continue
                    except Exception as e:
                        logger.warning(f"Errore cache: {e}, procedo con ricalcolo")

            if not self.orchestrator_callback:
                return {"success": False, "message": "Orchestrator callback non configurato"}

            # C3 round 3 fix 3: cycle detection PRIMA di riesegue upstream
            # Se source_project_id e' gia' nella chain di esecuzione, stiamo
            # per chiudere un ciclo A→B→A invocando orchestrator_callback
            # ricorsivamente. Senza questo check, stack overflow garantito
            # su canvas legacy con dipendenze circolari che il check al save
            # di update_canvas non ha intercettato
            _assert_no_lineage_cycle_on_chain(source_project_id)

            # Genera un run_id fresco per il progetto upstream (ESTRAI
            # così i suoi dati intermedi vengono tracciati in intermediate.db
            import uuid as _uuid
            upstream_run_id = str(_uuid.uuid4())
            try:
                from app.utils.logger import open_run
                from app.core.intermediate import db as idb
                from app.db.config.projects import get_project as _get_project
                upstream_meta = _get_project(source_project_id) or {}
                open_run(
                    run_id=upstream_run_id,
                    project_id=source_project_id,
                    group_id=upstream_meta.get("group_id", ""),
                    module=upstream_meta.get("project_type", "estrai"),
                    triggered_by="cascade",
                )
                idb.create_run(
                    run_id=upstream_run_id,
                    canvas_id=source_project_id,
                    group_id=upstream_meta.get("group_id", ""),
                )
            except Exception as _oe:
                logger.warning(f"Apertura run upstream {source_project_id} fallita (non bloccante): {_oe}")
                upstream_run_id = None

            # `orchestrator_callback` e' `OrchestratorService.execute_pipeline`
            # vedi orchestrator/service.py:83) che ritorna `PipelineArtifact`
            # — dict-subclass con typed property. Usiamo le proprieta'
            # tipizzate (`upstream.success`/`upstream.message`/
            # `upstream.rows_count`/`upstream.output_path`) invece di `.get`
            # raw per coerenza con il modello artifact unificato
            upstream = self.orchestrator_callback(source_project_id, force_recalculate=True, run_id=upstream_run_id)

            # Chiudi run upstream
            if upstream_run_id:
                try:
                    from app.utils.logger import close_run
                    from app.core.intermediate.writer import close_run as idb_close_run
                    status = "success" if upstream.success else "failure"
                    close_run(
                        run_id=upstream_run_id,
                        status=status,
                        rows_out=upstream.rows_count or 0,
                        duration_ms=0,
                        steps=[],
                        error_message=upstream.message if not upstream.success else None,
                    )
                    idb_close_run(
                        run_id=upstream_run_id, input_hash="", output_hash="",
                        total_exec_time=0.0, success=upstream.success,
                    )
                except Exception as _ce:
                    logger.warning(f"Chiusura run upstream {source_project_id} fallita (non bloccante): {_ce}")
            if not upstream.success:
                # FIXME: #25 (2026-04-28): pre-fix `return upstream`
                # propagava un PipelineArtifact con project_id=
                # source_project_id (canvas upstream). Saliva fino
                # a `OrchestratorService.execute_pipeline` che lo
                # passa a `PipelineArtifact.from_executor_result
                # result, project_id=<corrente>)`. Il safety net
                # rilevava il mismatch e sollevava ValueError
                # "from_executor_result: project_id mismatch ..."
                # — fuorviante: nascondeva la vera causa upstream
                # es. SSO scaduto) dietro un errore di consistenza
                # Post-fix: ritorniamo un dict locale senza
                # project_id, con messaggio diagnostico che cita il
                # source_project_id colpevole + il messaggio
                # upstream. `from_executor_result` populera' il
                # project_id col valore corrente (caller-side
                return {
                    "success": False,
                    "message": (
                        f"Errore caricamento sorgente upstream "
                        f"'{source_project_id}': "
                        f"{upstream.message or 'errore non specificato'}"
                    ),
                }

            # Legge il risultato upstream via ArtifactResolver
            # Tenant binding fail-closed con expected_group_id: il check
            # cross-tenant viene applicato anche post-recompute, non solo
            # nel ramo cached del primo blocco
            materialized = None
            raw_output_path = upstream.output_path or ""
            if raw_output_path and Path(raw_output_path).exists():
                materialized = MaterializedArtifact(
                    path=Path(raw_output_path), is_temporary=False
                )
            if materialized is None:
                try:
                    materialized = ArtifactResolver().materialize_latest(
                        LatestProjectArtifactRef(project_id=source_project_id),
                        expected_group_id=executing_group_id,
                    )
                except ArtifactResolutionError as _sec_err:
                    logger.error(
                        f"[SECURITY] Cross-tenant access blocked post-recompute: "
                        f"{source_project_id} per group={executing_group_id}: {_sec_err}",
                        op="execute",
                    )
                    return {
                        "success": False,
                        "message": (
                            f"Accesso negato: il progetto sorgente "
                            f"'{source_project_id}' appartiene a un gruppo diverso."
                        ),
                    }

            if materialized is None or materialized.path is None:
                return {"success": False, "message": f"Risultato upstream non trovato per: {source_project_id}"}

            try:
                node_columns = _nf(node, 'columns', [])
                with materialized as m:
                    loaded = load_project_source_artifact(
                        m.path,
                        node_columns,
                        source_project_id=source_project_id,
                        group_id=executing_group_id,
                    )
                dataframes[node.id] = loaded.data
            except Exception as e:
                return {"success": False, "message": f"Errore progetto {source_project_id}: {e}"}

        return None

    def _carica_sorgenti_file(
        self,
        canvas: CanvasState,
        dataframes: Dict[str, pd.DataFrame],
        *, group_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Carica DataFrame dai nodi file_source via SourceLoader.

        thin delegator. Il body vive in
        ``app.core.manipola.source_loading.load_file_sources`` —
        funzione pura con parametri espliciti, zero stato di
        classe. Il nome del metodo e' preservato cosi' i test
        esistenti e i caller esterni (oggi nessuno fuori dal
        modulo, ma il metodo e' parte della superficie pubblica
        dell'executor) continuano a funzionare invariati.

        Behaviour-preserving:

          - Visibility check cross-tenant + preserve del
            GeoDataFrame sono ancora garantiti dal ``SourceLoader``
            unificato, nessuna duplicazione inline.
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

    def _carica_sorgenti_statiche(
        self,
        canvas: CanvasState,
        dataframes: Dict[str, pd.DataFrame],
        *, group_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Carica DataFrame dai nodi StaticValues via SourceLoader.

        Wrapper sottile sopra `SourceLoader._load_static`. Il template
        processing (`process_static_fields`) vive nel loader unico;
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

        from app.core.artifacts import SourceLoader, SourceRef

        # TE-008: pure processing — do NOT read self._current_group_id
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
        dataframes: Dict[str, pd.DataFrame],
        run_id: Optional[str] = None,
        canvas_graph: Optional[CanvasGraph] = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
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
        
        current_df = None
        graph = canvas_graph or CanvasGraph.from_canvas(canvas)

        # Helper per estrarre targetHandle dall'edge (da attributo o da ID
        def get_target_handle(edge: Edge) -> Optional[str]:
            # Prima prova attributo diretto
            handle = getattr(edge, 'targetHandle', None)
            if handle:
                return handle
            # Estrai da ID: "...targetNodeIdinput-0" → "input-0"
            edge_id = getattr(edge, 'id', '') or ''
            match = re.search(r'(input-\d+)$', edge_id)
            if match:
                return match.group(1)
            return None

        for node in sorted_nodes:
            # Trova input per questo nodo - ORDINA per targetHandle o posizione Y
            incoming = [e for e in canvas.edges if e.target == node.id]

            # Ordina edges per garantire ordine consistente left/right
            def edge_sort_key(edge: Edge) -> tuple[int, object]:
                # Prima prova targetHandle (da attributo o ID
                handle = get_target_handle(edge)
                if handle:
                    return (0, handle)
                # Fallback: posizione Y del nodo sorgente via CanvasGraph raw lookup
                # graph.raw returns a plain dict (model_dump at intake), so
                # position is always a dict — no isinstance dual-path needed
                _raw_src = graph.raw(edge.source)
                if _raw_src:
                    pos = _raw_src.get("position") or {}
                    if isinstance(pos, dict):
                        return (1, pos.get("y", 0))
                return (2, edge.source)
            
            incoming.sort(key=edge_sort_key)
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
                    from app.core.intermediate.snapshot import record_checkpoint_snapshot
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

    def _esegui_multi_join(self, node: Node, inputs: List[pd.DataFrame]) -> pd.DataFrame:
        """
        Esegue multi-join su N DataFrame.

        Strategia:
        1. Prende il primo DataFrame come base
        2. Fa merge sequenziale con gli altri
        3. Gestisce colonne duplicate
        """
        if len(inputs) < 2:
            logger.warning("Multi-join richiede almeno 2 input")
            return inputs[0] if inputs else pd.DataFrame()

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

        # Multi-join sequenziale: ogni step `pd.merge` puo' inflate
        # l'output rispetto agli input se le chiavi non sono unique
        # Senza un guard, un'esplosione al merge step k si propaga ai
        # merge step k+1, k+2, ... saturando la memoria del worker
        # SIGKILL OS
        # Strategia: pre-merge cardinality estimate. Prima di ogni
        # `pd.merge`, stimiamo le righe output dalle frequenze delle
        # join key (`groupby(...).size`). Se la stima supera il cap
        # solleviamo PRIMA della materializzazione del DataFrame. Reuse
        # del contratto Memory/OOM
        # cross-join 003): stessa eccezione `MemoryLimitExceededError`
        # stesso codice envelope `memory_limit_exceeded`, stage
        # `manipola_multi_join`, status 413
        # Il costo e' O(N+M) per step, ma evita sia il falso positivo
        # del worst-case cartesian (`len(result) * len(next_df)`) sia
        # l'OOM del primo merge esplosivo. Il post-merge check resta
        # come safety net se pandas semantics o join_type non stimabile
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
            from app.shared.exceptions import MemoryLimitExceededError
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

            # Pre-merge exact cardinality estimate. Runs before pandas
            # materializes the merge output
            if _multi_cap > 0:
                _estimated_rows = _estimate_merge_output_rows(
                    result, df_copy, join_keys, join_type
                )
                if _estimated_rows is not None and _estimated_rows > _multi_cap:
                    from app.shared.exceptions import MemoryLimitExceededError
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

            # Merge
            result = pd.merge(
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
                from app.shared.exceptions import MemoryLimitExceededError
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
        dataframes: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Singola sorgente: passthrough. Multiple sorgenti senza join: errore."""
        if not dataframes:
            raise ValueError("Nessun progetto sorgente configurato")
        if len(dataframes) == 1:
            return list(dataframes.values())[0]
        raise ValueError(f"{len(dataframes)} sorgenti ma nessun nodo Transform.")

    def _esegui_trasformazione(
        self,
        node: Node,
        inputs: List[pd.DataFrame],
    ) -> pd.DataFrame:
        """Delega al PluginExecutor. Rilancia l'eccezione in caso di errore (no silent fail)."""
        transform_op = _nf(node, 'transform_op')
        if not transform_op:
            return inputs[0] if inputs else pd.DataFrame()
        return get_executor().execute(transform_op, inputs, node)

    # GROUP BY

    def _applica_group_by(self, df: pd.DataFrame, result_node: Node) -> pd.DataFrame:
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
                    # COUNT(*) via groupby.size — non dipende da una
                    # colonna specifica, non perde righe sul gruppo
                    count_aliases.append(alias)
                elif func in ['sum', 'mean', 'min', 'max', 'std']:
                    if col in df.columns:
                        agg_dict[col] = func
                        rename_dict[col] = alias

            # Esegui aggregazioni colonna (solo se ci sono colonne valide
            if agg_dict:
                result = df.groupby(group_cols, as_index=False).agg(agg_dict)
                result = result.rename(columns=rename_dict)
            elif count_aliases:
                # Solo COUNT(*) richiesti — parti da una base con i gruppi
                result = df.groupby(group_cols, as_index=False).size().drop(columns=['size'])
            else:
                # Nessuna aggregazione valida
                return df

            # Aggiungi COUNT(*) per ogni richiesta
            for alias in count_aliases:
                result[alias] = df.groupby(group_cols).size().values

            return result

        except Exception as e:
            logger.error(f"[CRASH] Errore GROUP BY: {e}", exc_info=True)
            return df

    # SALVATAGGIO

    def _salva_risultato(
        self,
        project_id: str,
        result_node: Node,
        df: pd.DataFrame,
        canvas: Optional[CanvasState] = None,
        canvas_graph: Optional[CanvasGraph] = None,
    ) -> Dict[str, Any]:
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
            saved = save_pipeline_result(
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
                "output_path": saved["output_filename"],
                "format": fmt.lower(),
                "rows_count": len(df),
                "columns_count": len(df.columns),
                "result_id": saved["result_id"],
                "message": f"Salvato {len(df)} righe in {saved['output_filename']}"
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
        from app.shared.resources.metadata import ResourceMetadata
        graph = canvas_graph or CanvasGraph.from_canvas(canvas)
        for cn in graph.nodes:
            raw = cn.get_field('columns')
            if not raw:
                continue
            cols_dicts = [
                {
                    "name": c.get("name") if isinstance(c, dict) else getattr(c, "name", None),
                    "type": c.get("type", "") if isinstance(c, dict) else getattr(c, "type", ""),
                }
                for c in raw
            ]
            meta = ResourceMetadata.from_columns_list(cols_dicts, source_kind="file")
            if meta.crs_epsg:
                return meta.crs_epsg
        return None

    # MAPPA TIPI CANVAS

    def _costruisci_mappa_tipi_canvas(
        self,
        canvas: CanvasState,
        *,
        canvas_graph: Optional[CanvasGraph] = None,
    ) -> Dict[str, str]:
        """Costruisce mappa colonna->tipo dal canvas. Include suffissi _L/_R per JOIN."""
        type_map: Dict[str, str] = {}
        graph = canvas_graph or CanvasGraph.from_canvas(canvas)
        for node in graph.nodes:
            columns = node.get_field('columns')
            if columns:
                for col in columns:
                    if isinstance(col, dict):
                        column_name = col.get('alias') or col.get('name')
                        column_type, original_name = col.get('type', 'str'), col.get('name')
                    else:
                        column_name = col.alias if hasattr(col, 'alias') and col.alias else col.name
                        column_type = col.type if hasattr(col, 'type') and col.type else "str"
                        original_name = col.name if hasattr(col, 'name') else None
                    if column_name:
                        type_map[column_name] = type_map[f"{column_name}_L"] = type_map[f"{column_name}_R"] = column_type
                    if original_name and original_name != column_name:
                        type_map[original_name] = type_map[f"{original_name}_L"] = type_map[f"{original_name}_R"] = column_type

            calculated = node.get_field('calculated_columns')
            if calculated:
                for calc in calculated:
                    column_name = calc.get("name") if isinstance(calc, dict) else getattr(calc, 'name', None)
                    column_type = (calc.get("output_type", "str") if isinstance(calc, dict)
                                   else getattr(calc, 'output_type', 'str'))
                    if column_name:
                        type_map[column_name] = type_map[f"{column_name}_L"] = type_map[f"{column_name}_R"] = column_type
        return type_map

    def _costruisci_colonne_con_tipi(
        self,
        df: pd.DataFrame,
        canvas_type_map: Dict[str, str],
    ) -> List[Dict[str, str]]:
        """Costruisce lista colonne con tipi. Lookup: esatto → lower → base _L/_R → dtype → WKT."""
        columns = []
        lower_map = {k.lower(): v for k, v in canvas_type_map.items()}
        for col in df.columns:
            column_type = canvas_type_map.get(col) or lower_map.get(col.lower())
            if column_type is None and (col.endswith('_L') or col.endswith('_R')):
                base = col[:-2]
                column_type = canvas_type_map.get(base) or lower_map.get(base.lower())
            
            # Se il tipo è str ma il nome suggerisce geometry, verifica il contenuto
            if column_type == 'str' and col.lower() in ('geometry', 'geom', 'the_geom', 'shape'):
                column_type = self._rileva_geometria_da_contenuto(df, col)
            
            if column_type is None:
                dtype = str(df[col].dtype)
                if 'int' in dtype: column_type = 'int'
                elif 'float' in dtype: column_type = 'real'
                elif 'bool' in dtype: column_type = 'bool'
                elif 'datetime' in dtype: column_type = 'date'
                elif 'geometry' in dtype.lower(): column_type = 'geometry'  # GeoDataFrame dtype
                else: column_type = self._rileva_geometria_da_contenuto(df, col)
            columns.append({'name': col, 'type': column_type})
        return columns

    def _rileva_geometria_da_contenuto(self, df: pd.DataFrame, col_name: str) -> str:
        """Rileva geometria WKT o oggetti Shapely dal contenuto di una colonna."""
        try:
            # Check nome colonna comune per geometry
            if col_name.lower() in ('geometry', 'geom', 'the_geom', 'shape'):
                sample = df[col_name].dropna().head(1)
                if not sample.empty:
                    sample_value = sample.iloc[0]
                    # Se ha geom_type o è WKT, è geometry
                    if hasattr(sample_value, 'geom_type'):
                        return 'geometry'
                    sample_text = str(sample_value).upper().strip()
                    if sample_text and sample_text[0] in 'PLMG':  # Quick check for WKT prefixes
                        return 'geometry'
            
            sample = df[col_name].dropna().head(1)
            if sample.empty:
                return 'str'
            sample_value = sample.iloc[0]
            
            # Controlla se è un oggetto Shapely
            if hasattr(sample_value, 'geom_type'):
                return 'geometry'
            
            # Controlla se è WKT string
            sample_text = str(sample_value).upper().strip()
            prefixes = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT',
                        'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')
            return 'geometry' if any(sample_text.startswith(p) for p in prefixes) else 'str'
        except Exception as _geo_probe_err:
            logger.debug(
                f"Rilevamento geometria da contenuto fallito per "
                f"colonna '{col_name}', fallback a 'str': {_geo_probe_err}"
            )
            return 'str'
