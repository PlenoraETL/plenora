"""
Task Celery per arricchimento dati asincrono.

Runs background execution while preserving backend runtime contracts.
"""

from app.core.projects.schema_inference import SchemaInferenceService
from app.core.projects.canvas_models import CanvasState
from app.shared.execution.api_mapping import mapping_destination

import csv as csv_mod
import json as json_mod
import time
import traceback as _tb
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, List
from app.core.artifacts import resolver as artifact_resolver_module
from app.core.auth import async_user_recheck as async_user_recheck_module
from app.core.arricchisci import api_connector as api_connector_module
from app.core.arricchisci import canvas_orchestrator as canvas_orchestrator_module
from app.core.arricchisci import enrichment_run_mode
from app.core.arricchisci import execution_context as execution_context_module
from app.core.arricchisci import parallel as parallel_module
from app.core.dataframes.values import is_nonempty_dataframe
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili import storage as eseg_storage
from app.core.files import service as files_service
from app.core.intermediate import run_idempotency as run_idempotency_module
from app.core.intermediate import snapshot as intermediate_snapshot
from app.core.intermediate import writer as intermediate_writer
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.task_progress import TaskRevoked, report_task_progress
from app.core.projects import service as project_service_module
from app.core.resources.bounded_reader import read_stream_bounded
from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.core.results import blob_writer as result_blob_writer
from app.core.results import store as result_store
from app.shared.contracts.payloads import JsonObject
from app.shared.execution import carica_dispatcher
from app.shared.execution.canvas_graph import CanvasGraph
from app.shared.execution.runtime_identity import assert_identity_resolved
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.utils import debug_payload_builder as debug_payload_builder_module
from app.core.arricchisci.enrichment_service import (
    build_enrichment_executor,
    generate_error_report,
)

logger = get_logger(__name__)


def _close_worker_connection(*, stage: str = "") -> None:
    """Rilascia la connessione thread-local anche sui ritorni pre-run."""
    try:
        from app.core.intermediate.cleanup import close_worker_connection

        close_worker_connection()
    except Exception as cleanup_exc:
        suffix = f" {stage}" if stage else ""
        logger.debug(
            f"close_connection enrichment fallito{suffix}: "
            f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        )


# Corpo task come funzione pura: `self` e' il self-shim dell'actor
# Dramatiq (request.id/name).
def execute_enrichment_task(
    self,
    project_id: str,
    canvas: JsonObject,
    output_format: str = "csv",
    force_recalculate: bool = False,
    cleanup_file_source_ids: Optional[List[str]] = None,
    is_eseguibile: bool = False,
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
    intermediate_mode_override: Optional[str] = None,
    execution_context: Optional[Mapping[str, Any]] = None,
    execution_contract: Optional[Mapping[str, Any]] = None,
) -> JsonObject:
    """
    Task asincrono per eseguire l'arricchimento dati.

    Idempotency: se `run_id` viene passato dall'esterno
    (scheduler, retry-handler UI) e in intermediate.runs esiste gia'
    un record con status='completed' per quel run_id, il task SKIPPA
    l'esecuzione e ritorna un risultato cached. Stesso pattern del
    carica_task. Se run_id non viene passato, genera fresh e non applica
    idempotency cross-run.

    Args:
        project_id: ID del progetto
        canvas: Canvas con nodi e edges
        output_format: Formato output (csv, xlsx, geojson, etc.)
        force_recalculate: Se True, riesegue anche se esiste cache
        cleanup_file_source_ids: Lista di file_source_id da eliminare al termine
            del task (sia in caso di successo che di errore). Usato dagli eseguibili
            per rimuovere i file temporanei creati durante l'esecuzione on-demand.
        is_eseguibile: True quando il task gira per un "eseguibile" pubblicato.
            In questa modalita' il task NON scrive nulla su DB (runs, log_runs,
            results, node_rows, output_contract): persiste l'output nello
            storage oggetti task-scoped e il frontend lo scarica via
            GET /api/eseguibili/{id}/download/{task_id}, che elimina il file
            dopo l'invio. Un cleanup periodico (celery beat) rimuove eventuali
            file orfani.
        run_id: Chiave di idempotency opzionale. Se passato e gia'
            completato in intermediate.runs → task skippato.

    Returns:
        Dict con risultato dell'esecuzione, oppure status='skipped_idempotent'
        se il run_id era gia' completo.
    """
    task_id = self.request.id
    parsed_contract: PipelineExecutionContract | None = None
    if execution_contract is not None:
        parsed_contract = PipelineExecutionContract.from_payload(
            execution_contract,
            expected_project_id=project_id,
        )
        if parsed_contract.project_type != "arricchisci":
            raise ValueError(
                "execution_contract.project_type non valido per enrichment"
            )
        subject = parsed_contract.executable_subject
        contract_is_eseguibile = subject is not None
        if is_eseguibile and not contract_is_eseguibile:
            raise ValueError(
                "execution_contract privo di execution_subject eseguibile"
            )
        is_eseguibile = contract_is_eseguibile
        canvas = dict(parsed_contract.canvas)
        runtime_context = parsed_contract.build_runtime_context(
            output_task_id=task_id if is_eseguibile else None,
        )
        if subject is not None:
            runtime_context.update(
                {
                    "eseguibile_id": str(subject.get("id") or project_id),
                    "version_id": str(subject.get("version_id") or ""),
                    "run_id": str(subject.get("run_id") or ""),
                }
            )
        execution_context = runtime_context
        if runtime_context.get("output_format"):
            output_format = str(runtime_context["output_format"])
        cleanup_ids = runtime_context.get("cleanup_file_source_ids")
        if cleanup_ids is not None:
            if not isinstance(cleanup_ids, list):
                raise ValueError(
                    "execution_contract.runtime_context.cleanup_file_source_ids "
                    "deve essere una lista"
                )
            cleanup_file_source_ids = [str(value) for value in cleanup_ids]

    executable_run_id = str((execution_context or {}).get("run_id") or "")
    executable_id = str(
        (execution_context or {}).get("eseguibile_id") or project_id
    )

    start_time = time.time()

    # #28-B: la run-mode incapsula le divergenze standard vs eseguibile
    # (auth recheck, logger project_id, group_id + intermediate run).
    run_mode = enrichment_run_mode.for_task(is_eseguibile)

    def complete_executable_run(
        *,
        success: bool,
        output_metadata: Optional[Mapping[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not executable_run_id:
            return
        try:
            eseguibili_service.complete_eseguibile_run(
                executable_run_id,
                success=success,
                output_metadata=dict(output_metadata or {}),
                error_message=error_message,
            )
        except Exception as run_exc:
            logger.warning(
                f"[Task {task_id}] Update run eseguibile fallito: {run_exc}"
            )

    def cancel_executable_run() -> None:
        if not executable_run_id:
            return
        try:
            eseguibili_service.cancel_eseguibile_run(
                executable_run_id,
                error_message="Task annullato",
            )
        except Exception as run_exc:
            logger.warning(
                f"[Task {task_id}] Cancellazione run eseguibile fallita: {run_exc}"
            )

    if is_eseguibile and execution_context:
        _revoked = async_user_recheck_module.recheck_eseguibile_authorized(
            user_id=user_id,
            eseguibile_id=executable_id,
            eseguibile_loader=eseguibili_service.get_eseguibile_for_access_check,
            task_id=task_id,
            run_id=executable_run_id,
        )
        if _revoked is not None:
            complete_executable_run(
                success=False,
                error_message="Permesso di esecuzione revocato",
            )
            _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
            _revoked.update({"task_id": task_id, "project_id": project_id})
            _close_worker_connection(stage="post-permission-revoke")
            return _revoked

    if run_mode.should_recheck_auth():
        _revoked = async_user_recheck_module.recheck_user_authorized(
            user_id=user_id, project_id=project_id,
            task_id=task_id, run_id=run_id or "", task_name="ENRICHMENT",
        )
        if _revoked is not None:
            _revoked.update({"task_id": task_id, "project_id": project_id})
            _close_worker_connection(stage="post-permission-revoke")
            return _revoked

    _log_pid = run_mode.log_project_id(project_id)

    logger.info(f"[Task {task_id}] Avvio enrichment per progetto {project_id}", project_id=_log_pid)

    # Aggiorna stato: STARTED. Il checkpoint puo' osservare una revoca prima
    # dell'apertura delle run: gli input temporanei vanno comunque ripuliti.
    try:
        update_task_progress(self, 0, "Inizializzazione...", task_id=task_id)
    except TaskRevoked:
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
        cancel_executable_run()
        _close_worker_connection(stage="pre-run")
        raise

    # Authorization and the cancellation checkpoint must precede idempotency
    # lookups, so revoked callers cannot observe metadata from earlier runs.
    if is_eseguibile:
        if executable_run_id:
            previous_run = eseguibili_service.get_eseguibile_run(executable_run_id)
            if previous_run and previous_run.get("status") == "success":
                output_metadata = previous_run.get("output_metadata") or {}
                if isinstance(output_metadata, str):
                    try:
                        output_metadata = json_mod.loads(output_metadata)
                    except ValueError:
                        output_metadata = {}
                output_file = (
                    output_metadata.get("filename")
                    if isinstance(output_metadata, Mapping)
                    else None
                )
                _close_worker_connection(stage="post-eseguibile-idempotency")
                return {
                    "status": "skipped_idempotent",
                    "success": True,
                    "project_id": project_id,
                    "task_id": task_id,
                    "run_id": run_id,
                    "result_id": None,
                    "output_file": output_file,
                    "download_url": f"/api/eseguibili/{project_id}/download/{task_id}",
                    "is_eseguibile": True,
                }
    elif run_id is not None and run_idempotency_module.is_already_completed(run_id):
        logger.info(
            f"[IDEMPOTENCY] Task ENRICHMENT {task_id}: run_id {run_id} "
            f"gia' completato con successo, skip."
        )
        result: JsonObject = {
            "status": "skipped_idempotent",
            "success": True,
            "message": f"Run {run_id} gia' completato",
            "run_id": run_id,
            "task_id": task_id,
        }
        _close_worker_connection(stage="post-idempotency")
        return result

    # Variabili per statistiche enrichment
    enrichment_stats = None
    # L'error report e' un result_blob secondario persistito via
    # save_result_blob_from_bytes; il task
    # result espone il filename canonico (display) + il result_id per il
    # download. Niente piu' path filesystem.
    error_report_file = None  # filename display, e.g. enrichment_errors_{task_id}.csv
    error_report_result_id = None
    # physical_name -> path dell'output_mapping API risolto a runtime (dalla
    # connessione, non dal nodo che puo' essere stale). Il `path` e' l'identita'
    # stabile della colonna API: consente a build_output_contract di riusare
    # l'id colonna attraverso un rename del nome fisico in output_mapping.
    source_names_by_column = {}  # column -> api source name (costruito sotto)

    # Apre run intermedio (non bloccante).
    # Per gli eseguibili saltiamo `intermediate.create_run` perche' l'eseguibile
    # non e' un progetto in `projects`, quindi un record runs con
    # canvas_id="exec_..." sarebbe orfano. MA il `_group_id` deve comunque
    # essere recuperato (dalla tabella `eseguibili`) per propagarlo al
    # tenant binding di `api_connection_service` piu' sotto: senza
    # `_load_*` / `_execute_rest_file` falliscono fail-closed con
    # "Contesto di sicurezza non determinabile (group_id mancante)"
    _run_start = time.time()
    _run_id: Optional[str] = None
    _group_id = ""
    _retention_n: Optional[int] = None
    log_run_opened = False
    try:
        _ctx = run_mode.begin_run(
            project_id=project_id,
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            canonical_group_id=(
                parsed_contract.group_id
                if parsed_contract is not None
                else str((execution_context or {}).get("group_id") or "")
            ),
        )
        _run_id = _ctx.run_id
        _group_id = _ctx.group_id
        _retention_n = _ctx.retention_n

        # Per eseguibili: niente log_runs/logs su DB
        if run_mode.logs_run():
            open_run(
                run_id=_run_id or task_id,
                project_id=project_id,
                group_id=_group_id,
                module="arricchisci",
                triggered_by="manual",
                user_id=user_id,
            )
            log_run_opened = True
            set_run_context(run_id=_run_id or task_id, project_id=project_id)
    except BaseException as setup_exc:
        if _run_id and run_mode.logs_run():
            try:
                intermediate_writer.close_run(
                    run_id=_run_id,
                    input_hash="",
                    output_hash="",
                    total_exec_time=time.time() - _run_start,
                    success=False,
                    retention_n=_retention_n,
                )
            except Exception:
                logger.warning(
                    "close_run intermediate fallito durante setup enrichment",
                    exc_info=True,
                )
        if log_run_opened:
            try:
                close_run(
                    run_id=_run_id or task_id,
                    status="failure",
                    duration_ms=int((time.time() - _run_start) * 1000),
                    error_module="arricchisci",
                    error_message="Inizializzazione run fallita",
                )
            except Exception:
                logger.warning(
                    "close_run ARRICCHISCI fallito durante setup",
                    exc_info=True,
                )
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
        complete_executable_run(
            success=False,
            error_message=f"Inizializzazione fallita: {type(setup_exc).__name__}",
        )
        _close_worker_connection(stage="setup")
        raise

    def finalize_standard_success(result: Mapping[str, Any]) -> None:
        """Commit idempotente sincrono prima dell'ACK del task."""
        elapsed = time.time() - _run_start
        rows_out = int(result.get("rows_processed", 0) or 0)
        if run_mode.logs_run():
            elapsed_ms = int(elapsed * 1000)
            append_step(
                {
                    "module": "arricchisci",
                    "status": "success",
                    "rows_out": rows_out,
                    "duration_ms": elapsed_ms,
                }
            )
            close_run(
                run_id=_run_id or task_id,
                status="success",
                rows_out=rows_out,
                duration_ms=elapsed_ms,
            )
        # Ultimo side effect fallibile: dopo status=completed un redelivery
        # deve vedere la barriera idempotente e non rieseguire l'enrichment.
        if _run_id and run_mode.logs_run():
            intermediate_writer.close_run(
                run_id=_run_id,
                input_hash="",
                output_hash="",
                total_exec_time=elapsed,
                success=True,
                retention_n=_retention_n,
            )

    try:
        # NOTE: istanziamo una NUOVA `ApiConnectionService` invece di
        # usare il singleton modulo-level. Il singleton ha stato per-execution
        # che e' soggetto a race condition cross-tenant fra task paralleli
        # sullo stesso worker process (Celery prefork riusa i fork
        # Un'istanza locale per task garantisce isolamento totale del tenant
        # binding
        api_connection_service = api_connector_module.ApiConnectionService()
        # — costruisci un
        # `ArricchisciExecutionContext` tipato esplicito; il context
        # viene threadato esplicitamente ai 4 mixin callsite
        # `_load_source_project`, `_load_file_source`
        # `_load_static_values`, `_execute_rest_file`) tramite `ctx=ctx`.
        # Il worker NON muta `svc._current_group_id` / `svc._preview_limit`:
        # il ctx tipato e' l'unica via di tenant binding nel path worker.
        ctx = execution_context_module.ArricchisciExecutionContext(
            project_id=project_id,
            group_id=(
                _group_id if _group_id and _group_id != 'unknown' else None
            ),
            preview_limit=None,  # task celery non e' preview
            output_task_id=task_id if run_mode.is_eseguibile else None,
        )

        nodes = canvas.get("nodes", [])
        edges = canvas.get("edges", [])

        if not nodes:
            raise ValueError("Nessun nodo nel canvas")
        # Input «Utente che esegue» non risolto (avvio legacy, catene, avvio
        # non presidiato): la catena si blocca (shared/execution/runtime_identity).
        assert_identity_resolved(canvas)

        update_task_progress(self, 5, "Caricamento dati...", task_id=task_id)

        # Carica API connections
        api_connections = {}
        for api in api_connection_service.list_api_connections():
            full_api = api_connection_service.get_api_connection(api["id"], include_token=True)
            if full_api:
                api_connections[api["id"]] = full_api
        
        update_task_progress(self, 10, "API caricate, inizio elaborazione...", task_id=task_id)

        # Congela gli id prima delle chiamate API sul canvas effettivamente eseguito.
        column_plan = []
        try:
            typed_canvas = CanvasState.model_validate(canvas)
            inferred = SchemaInferenceService(
                project_service_module.ProjectService(), api_configs=api_connections,
            ).infer_all_schemas(project_id, typed_canvas)
            output_node = next((n for n in typed_canvas.nodes if n.type == "result"), None)
            column_plan = inferred.get(output_node.id, []) if output_node else []
        except Exception as exc:
            logger.warning("Piano identita non disponibile prima dell'arricchimento: %s", exc)

        execution_order = canvas_orchestrator_module.get_execution_order(nodes, edges)
        node_results = {}
        
        # compute_parallel_groups raggruppa api_enrich consecutivi che non
        # dipendono l'uno dall'output dell'altro (leggono solo colonne gia'
        # presenti nel DataFrame al momento del gruppo) → possono girare
        # concorrentemente. I nodi di tipo source/static/result restano
        # "single" (eseguiti uno alla volta
        parallel_groups = parallel_module.compute_parallel_groups(
            execution_order, nodes, edges,
            api_connections=api_connections,
        )
        logger.info(
            f"[Task {task_id}] Gruppi esecuzione: "
            f"{[(t, len(ids)) for t, ids in parallel_groups]}"
        )

        class _EarlyTaskReturn(Exception):
            """Usata dentro la closure per early termination con payload.

            Il loop esterno cattura questa eccezione e propaga il payload
            come return del task; e' l'equivalente async-safe di un
            `return` interno alla closure.
            """
            def __init__(self, payload: JsonObject) -> None:
                self.payload = payload

        # Esecuzione canvas via runner condiviso.
        # Il task delega a
        # `run_enrichment_canvas`, fornendo l'enricher avanzato (`enrich_fn`)
        # e gli hook progress/snapshot. Il parallelo e' gestito dal runner
        # (`execute_parallel_enrichers`).
        graph = CanvasGraph.from_canvas(canvas)

        _PROGRESS_MSG = {
            "project_source": "Caricamento sorgente dati...",
            "file_source": "Caricamento file...",
            "staticValues": "Processamento valori statici...",
        }

        def _node_base_progress(node_id_: str) -> int:
            try:
                idx = execution_order.index(node_id_)
            except ValueError:
                idx = 0
            return 10 + int((idx / max(len(execution_order), 1)) * 80)

        def _before_node(node_id_: str, node_type_: str) -> None:
            update_task_progress(
                self,
                _node_base_progress(node_id_),
                _PROGRESS_MSG.get(node_type_, f"Esecuzione {node_type_}..."),
                task_id=task_id,
            )

        def _after_node(node_id_: str, node_type_: str, result_: object) -> None:
            if not _run_id or node_type_ != "api_enrich":
                return
            try:
                _df = result_ if is_nonempty_dataframe(result_) else None
                if _df is not None:
                    intermediate_snapshot.record_checkpoint_snapshot(
                        run_id=_run_id,
                        module="arricchisci",
                        node_id=node_id_,
                        node_type="api_enrich",
                        df=_df,
                        exec_time=0.0,
                        status="ok",
                        mode_override=intermediate_mode_override,
                    )
            except Exception as _sne:
                logger.warning(
                    f"[Task {task_id}] Intermediate write nodo {node_id_} fallita: {_sne}"
                )

        def _task_enrich_fn(node_data, api_conns, input_df):
            nonlocal enrichment_stats, error_report_file, error_report_result_id
            api_id = node_data.get("api_id") or (node_data.get("data") or {}).get("api_id")
            api_config = api_conns.get(api_id)
            for _m in (api_config or {}).get("output_mapping") or []:
                if not isinstance(_m, dict):
                    continue
                _col = mapping_destination(_m, phase="explicit") or _m.get("target_column") or _m.get("name")
                _path = mapping_destination(_m, phase="source")
                if _col and _path:
                    source_names_by_column[str(_col)] = str(_path)
            if not api_config:
                logger.error(
                    f"[GESTITO] [Task {task_id}] API '{api_id}' non trovata nel "
                    f"canvas del progetto {project_id}"
                )
                raise canvas_orchestrator_module.CanvasRunError({
                    "success": False,
                    "task_id": task_id,
                    "project_id": project_id,
                    "error": (
                        f"Connessione API '{api_id}' non trovata. Verifica che la "
                        "connessione esista e non sia stata eliminata."
                    ),
                    "enrichment_stats": enrichment_stats,
                    "error_report": error_report_file,
                    "error_report_result_id": error_report_result_id,
                })
            api_name = api_config.get("name", api_id)
            if api_config.get("api_type", "enricher") == "generator" or input_df is None:
                return api_connection_service._execute_api_generator(api_config, input_df)

            merged = {
                **{
                    k: v for k, v in node_data.items()
                    if k not in ("id", "type", "position", "data", "config")
                },
                **(node_data.get("data") or {}),
            }

            def _progress(_percent: int, message: str):
                update_task_progress(self, 50, message, task_id=task_id)

            executor = build_enrichment_executor(
                merged, api_config, progress_callback=_progress
            )
            result, enrichment_result = executor.execute(input_df)
            enrichment_stats = enrichment_result.stats.to_dict()

            if not executor.options.continue_on_error and enrichment_result.stats.errors > 0:
                raise canvas_orchestrator_module.CanvasRunError({
                    "success": False,
                    "task_id": task_id,
                    "project_id": project_id,
                    "error": (
                        f"{enrichment_result.stats.errors} righe fallite. "
                        "Imposta continue_on_error=True per ignorare gli errori."
                    ),
                    "enrichment_stats": enrichment_stats,
                    "error_report": error_report_file,
                    "error_report_result_id": error_report_result_id,
                })
            threshold = executor.options.stop_on_error_threshold
            if threshold > 0 and enrichment_result.stats.errors >= threshold:
                raise canvas_orchestrator_module.CanvasRunError({
                    "success": False,
                    "task_id": task_id,
                    "project_id": project_id,
                    "error": (
                        f"Superato il limite massimo di errori ({threshold}). "
                        f"{enrichment_result.stats.errors} righe fallite su "
                        f"{enrichment_result.stats.total_rows}."
                    ),
                    "enrichment_stats": enrichment_stats,
                    "error_report": error_report_file,
                    "error_report_result_id": error_report_result_id,
                })

            if not executor.api_config.get("output_mapping") and executor.detected_column_types:
                detected = executor.detected_column_types
                new_schema = [
                    {"path": col, "name": col, "type": typ}
                    for col, typ in detected.items()
                ]
                for col in (result.columns if hasattr(result, "columns") else []):
                    if col not in detected:
                        new_schema.append({"path": col, "name": col, "type": "str"})
                try:
                    api_connection_service.update_api_connection(
                        api_id, {"schema_fields": new_schema}
                    )
                    logger.info(
                        f"[Task {task_id}] Schema auto-rilevato: {list(detected.keys())}"
                    )
                except Exception as _e:
                    logger.warning(f"[Task {task_id}] Aggiornamento schema fallito: {_e}")

            if executor.options.generate_error_report and enrichment_result.errors:
                error_report_bytes = generate_error_report(
                    enrichment_result.errors,
                    auth=executor.api_config.get("auth"),
                )
                if error_report_bytes:
                    error_report_file = f"enrichment_errors_{task_id}.csv"
                    if run_mode.is_eseguibile:
                        internal_name = eseg_storage.internal_artifact_filename(
                            "error-report",
                            api_id,
                            error_report_file,
                        )
                        eseg_storage.save_bytes(
                            task_id,
                            internal_name,
                            error_report_bytes,
                            group_id=_group_id,
                        )
                    else:
                        error_report_result_id = result_blob_writer.save_result_blob_from_bytes(
                            project_id=project_id,
                            data=error_report_bytes,
                            filename=error_report_file,
                            fmt="csv",
                            rows_count=len(enrichment_result.errors),
                            group_id=_group_id or None,
                        )
            logger.info(
                f"[Task {task_id}] {api_name}: "
                f"{enrichment_result.stats.success}/{enrichment_result.stats.total_rows} OK, "
                f"{enrichment_result.stats.cached} cache, "
                f"{enrichment_result.stats.errors} errori"
            )
            return result

        try:
            node_results = canvas_orchestrator_module.run_enrichment_canvas(
                api_connection_service,
                project_id,
                graph=graph,
                ctx=ctx,
                api_connections=api_connections,
                execution_order=execution_order,
                groups=parallel_groups,
                force_recalculate=force_recalculate,
                enrich_fn=_task_enrich_fn,
                hooks=canvas_orchestrator_module.CanvasRunHooks(
                    before_node=_before_node,
                    after_node=_after_node,
                ),
            )
        except canvas_orchestrator_module.CanvasRunError as _cre:
            raise _EarlyTaskReturn(_cre.payload)

        update_task_progress(self, 90, "Salvataggio risultati...", task_id=task_id)
        
        # Trova output finale
        result_node = next((n for n in nodes if n.get("type") == "result"), None)
        # Merge flat + data (stesso pattern usato per gli altri nodi
        if result_node:
            result_flat = {k: v for k, v in result_node.items() if k not in ("id", "type", "position", "data", "config")}
            result_data = {**result_flat, **(result_node.get("data") or {})}
        else:
            result_data = {}
        
        final_format = result_data.get("format", output_format) or output_format
        output_filename = result_data.get("fileName", "output") or "output"
        
        final_node_id = execution_order[-1] if execution_order else None
        final_result = node_results.get(final_node_id)
        
        # Cerca anche nel result node
        if final_result is None and result_node:
            final_result = node_results.get(result_node["id"])
        
        # Fallback: ultimo risultato non-passthrough
        if final_result is None:
            for nid in reversed(execution_order):
                r = node_results.get(nid)
                if r is not None and not (isinstance(r, dict) and r.get("_passthrough_file_source")):
                    final_result = r
                    break
        
        if final_result is None:
            raise ValueError("Nessun risultato finale")
        
        if isinstance(final_result, dict) and final_result.get("is_file_result"):

            # Il connector ha gia' persistito l'output come result_blob e restituisce
            # `result_id` + `filename` + `format` + `size_bytes`. Carichiamo
            # i bytes via il resolver per row counting / IMDF extraction
            # / eseguibili upload. Niente piu' `output_path` cross-process.
            upstream_result_id = final_result.get("result_id")
            upstream_storage_key = final_result.get("storage_key")
            upstream_filename = final_result.get("filename") or "output.bin"
            upstream_fmt = final_result.get("format") or "bin"
            if not upstream_result_id and not upstream_storage_key:
                raise ValueError(
                    "rest_file: riferimento artifact mancante nel risultato"
                )

            # Result_id propagation con metadata short-circuit:
            #   - Se NON e' IMDF (needs bundle bytes) E NON e' eseguibile
            #     (needs upload bytes), tenta lookup metadata via
            #     `store.get_result_row(result_id).rows_count`. Skip
            #     fetch_run_blob quando metadata e' sufficient.
            #   - Se metadata `rows_count` e' None (es. connector non lo
            #     ha registrato): fallback al fetch_run_blob completo.
            elapsed_time = time.time() - start_time
            update_task_progress(
                self,
                90,
                "Finalizzazione output...",
                task_id=task_id,
                check_revocation=False,
            )

            # Eventuale rename in base a output_filename del nodo Result.
            src_ext = "." + (upstream_fmt or "bin")
            final_name = upstream_filename
            if output_filename and output_filename != "output":
                named = Path(output_filename)
                final_name = named.stem + (named.suffix or src_ext)

            ext = (Path(final_name).suffix or src_ext).lower()
            rows_count = 0
            output_bytes: bytes = b""
            # `has_imdf_upstream` calcolato PRIMA del check perche' la
            # decisione di scaricare i bytes dipende anche dall'IMDF
            # extraction (richiede bundle bytes completi).
            has_imdf_upstream = any(
                (n.get("output_kind") == "imdf" or (n.get("data") or {}).get("output_kind") == "imdf")
                for n in nodes
                if n.get("type") == "api_enrich"
            )
            needs_full_bytes = run_mode.needs_full_bytes() or (
                # IMDF: output JSON plenora.indoor.bundle -> serve il body
                # completo per il parse.
                has_imdf_upstream and ext == ".json"
            )

            # Tenta metadata short-circuit prima del full fetch.
            metadata_rows_count: Optional[int] = None
            if not needs_full_bytes and upstream_result_id:
                try:
                    metadata_row = result_store.get_result_row(upstream_result_id)
                    if metadata_row is not None and metadata_row.rows_count is not None:
                        metadata_rows_count = int(metadata_row.rows_count)
                except Exception as _md_e:
                    logger.debug(
                        f"[Task {task_id}] result metadata lookup fallito, "
                        f"fallback fetch_run_blob: {_md_e}"
                    )

            # Il REST connector salva `rows_count=0` come placeholder iniziale.
            # Trattalo come "unknown/placeholder" e fai short-circuit SOLO se
            # `metadata_rows_count > 0`.
            if metadata_rows_count is not None and metadata_rows_count > 0:
                # Short-circuit: rows_count gia' nei metadata (e > 0),
                # skip download.
                rows_count = metadata_rows_count
                logger.info(
                    f"[Task {task_id}] rows_count={rows_count} via metadata "
                    f"short-circuit (skip fetch_run_blob)"
                )
            else:
                # Path classico: scarica i bytes per contare righe e/o
                # per IMDF extraction / eseguibili upload. Include il
                # caso `metadata_rows_count=0` (REST connector placeholder).
                if metadata_rows_count == 0:
                    logger.debug(
                        f"[Task {task_id}] metadata rows_count=0 "
                        f"(placeholder connector), fetch_run_blob per "
                        f"count reale"
                    )
                if upstream_storage_key:
                    stream = eseg_storage.open_task_stream(
                        task_id,
                        str(upstream_storage_key),
                        expected_group_id=_group_id,
                    )
                    output_bytes = read_stream_bounded(
                        stream["body"],
                        max_bytes=resolve_resource_max_bytes(),
                        label=upstream_filename,
                        stage="enrichment_executable_final_file",
                    )
                else:
                    blob = artifact_resolver_module.ArtifactResolver().fetch_run_blob(
                        result_id=upstream_result_id,
                        expected_project_id=project_id,
                    )
                    output_bytes = blob.data

                try:
                    if len(output_bytes) == 0:
                        raise ValueError("Il file di output è vuoto (0 bytes)")

                    if ext == '.csv':
                        text = output_bytes.decode('utf-8-sig')
                        rows_found = list(csv_mod.reader(text.splitlines()))
                        if len(rows_found) < 2:
                            raise ValueError("CSV vuoto o senza righe dati")
                        rows_count = len(rows_found) - 1

                    elif ext in ('.xlsx', '.xls'):
                        import io as _io
                        import openpyxl
                        wb = openpyxl.load_workbook(
                            _io.BytesIO(output_bytes),
                            read_only=True, data_only=True,
                        )
                        try:
                            ws = wb.active
                            rows_count = max(0, ws.max_row - 1)
                        finally:
                            wb.close()

                    elif ext == '.json':
                        data = json_mod.loads(output_bytes)
                        rows_count = len(data) if isinstance(data, list) else 1

                    elif ext in ('.geojson',):
                        data = json_mod.loads(output_bytes)
                        features = data.get('features', []) if isinstance(data, dict) else []
                        rows_count = len(features)

                except (UnicodeDecodeError, ValueError, Exception) as val_err:
                    raise ValueError(f"Output non valido ({ext}): {val_err}") from val_err

            # IMDF (plenora.indoor.bundle) NON ha piu' un path dedicato qui: e'
            # un arricchimento come gli altri. Il bundle JSON prodotto dal
            # convertitore viene salvato come normale result_blob del progetto;
            # il publish ArcGIS multi-layer avviene in-pipeline nel nodo
            # carica_arcgis downstream (vedi carica_task._maybe_multilayer_arcgis_write
            # + bundle_arcgis_adapter), in una sola run, senza persist dedicata.

            # Routing finale: eseguibili (S3) vs arricchisci (DB result_blob).
            result_id: Optional[str] = None

            if run_mode.is_eseguibile:
                # Upload su object storage (S3 via cloud_backends). Niente FS
                # locale persistente: backend+worker multi-host safe. Il
                # frontend scarica via GET /api/eseguibili/{id}/download/{task_id}
                # che streamma da S3 e cancella l'oggetto dopo l'invio.
                eseg_storage.save_bytes(
                    task_id,
                    final_name,
                    output_bytes,
                    group_id=_group_id,
                )
                logger.info(
                    f"[Task {task_id}] Output eseguibile su storage: "
                    f"eseguibili-outputs/{task_id}/{final_name}"
                )
            else:
                # Flusso arricchisci standard: il connector ha gia' salvato
                # come result_id. Decision tree:
                #   - rename necessario E bytes non disponibili (metadata
                #     short-circuit attivo): ri-fetch + ri-salva.
                #   - rename necessario E bytes disponibili: ri-salva.
                #   - no rename: propaga upstream_result_id (no re-save).
                # Solo `final_name != upstream_filename`
                # giustifica un re-save per via dell'API contract task
                # result dict.
                needs_resave_for_rename = final_name != upstream_filename
                if needs_resave_for_rename:
                    # Re-fetch + re-save sotto try/except: se il rename
                    # fallisce, propaga upstream_result_id con filename
                    # originale e registra un warning nel result envelope.
                    try:
                        if not output_bytes:
                            logger.info(
                                f"[Task {task_id}] rename richiesto, re-fetch "
                                f"output_bytes per re-persist con final_name='{final_name}'"
                            )
                            blob_for_resave = artifact_resolver_module.ArtifactResolver().fetch_run_blob(
                                result_id=upstream_result_id,
                                expected_project_id=project_id,
                            )
                            output_bytes = blob_for_resave.data
                        result_id = result_blob_writer.save_result_blob_from_bytes(
                            project_id=project_id,
                            data=output_bytes,
                            filename=final_name,
                            fmt=ext.lstrip(".") or "bin",
                            rows_count=rows_count,
                        )
                        logger.info(
                            f"[Task {task_id}] File result re-persistito con "
                            f"rename: {result_id}"
                        )
                    except Exception as _resave_e:
                        # Re-fetch / save fallito: il blob originale e' integro,
                        # propaga upstream_result_id + filename originale.
                        # La run NON viene segnata come failure.
                        logger.warning(
                            f"[Task {task_id}] Re-save con rename '{final_name}' "
                            f"fallito: {type(_resave_e).__name__}. "
                            f"Propago result_id originale con filename "
                            f"'{upstream_filename}'. "
                            f"Dettaglio errore nei log server-side.",
                            exc_info=True,
                        )
                        result_id = upstream_result_id
                        # Reset final_name al nome originale del blob persistito
                        # cosi' il task envelope e' coerente con la realta'
                        # dello storage.
                        final_name = upstream_filename
                else:
                    result_id = upstream_result_id
                    logger.info(
                        f"[Task {task_id}] File result propagato dal connector: {result_id}"
                    )

            result = {
                "success": True,
                "task_id": task_id,
                "project_id": project_id,
                # `result_id` propagato per il download via
                # `/api/tasks/{task_id}/download` che usa
                # `task_info.result["result_id"]` (vedi
                # orchestrator/router.py:580). Senza, 410 Gone.
                "result_id": result_id,
                "output_type": "file",
                "output_file": final_name,
                "output_filename": final_name,

                "rows_processed": rows_count,
                "columns": [],
                "output_format": ext.lstrip("."),
                "elapsed_time": round(elapsed_time, 2),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "execution_time_ms": final_result.get("execution_time_ms", 0),
                "enrichment_stats": None,
                "error_report": None,
                "error_report_result_id": None,
                "message": f"Processamento completato: {final_name}",
            }
            run_mode.decorate_result(result, project_id=project_id, task_id=task_id)

            logger.info(
                f"[Task {task_id}] Executable completato in "
                f"{elapsed_time:.2f}s - {final_name}"
            )

            if run_mode.triggers_downstream_carica():
                try:
                    carica_info = _trigger_carica_if_present(
                        project_id=project_id,
                        canvas=canvas,
                        output_format=ext.lstrip(".") or "bin",
                        user_id=user_id,
                        group_id=_group_id,
                    )
                    if carica_info:
                        result["carica"] = carica_info
                except Exception as _ce:
                    logger.error(
                        f"[GESTITO] [Task {task_id}] Errore avvio task CARICA dopo rest_file: {_ce}",
                        project_id=project_id, exc_info=True
                    )

            _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
            complete_executable_run(
                success=True,
                output_metadata={"task_id": task_id, "filename": final_name},
            )
            finalize_standard_success(result)
            update_task_progress(
                self,
                100,
                "Completato!",
                task_id=task_id,
                check_revocation=False,
            )
            return result
        
        final_df = final_result
        
        if final_df is None:
            raise ValueError("Nessun risultato finale")
        if hasattr(final_df, "empty") and final_df.empty:
            logger.info(f"[Task {task_id}] DataFrame vuoto (0 righe) — salvo output con solo header")
        
        # Salva DataFrame come bytes/object su destinazione finale
        # Niente DATA_DIR/temp staging: result pipeline ed eseguibili storage
        # gestiscono il dispatch internamente.
        final_filename = f"{output_filename}.{final_format}"
        elapsed_time = time.time() - start_time
        result_id, saved_path = run_mode.persist_dataframe_result(
            project_id=project_id,
            task_id=task_id,
            final_df=final_df,
            final_format=final_format,
            output_filename=output_filename,
            final_filename=final_filename,
            group_id=_group_id,
            source_names=source_names_by_column,
            column_plan=column_plan,
        )

        update_task_progress(
            self,
            95,
            "Finalizzazione output...",
            task_id=task_id,
            check_revocation=False,
        )

        result = {
            "success": True,
            "task_id": task_id,
            "project_id": project_id,
            # `result_id` propagato per il download via
            # `/api/tasks/{task_id}/download` (orchestrator/router.py:580
            # None nel ramo eseguibile: il frontend in quel caso usa
            # `download_url` → `/api/eseguibili/.../download/{task_id}`
            "result_id": result_id,
            "rows_processed": len(final_df),
            "columns": list(final_df.columns),
            "output_file": saved_path.name,
            "output_format": final_format,
            "elapsed_time": round(elapsed_time, 2),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            # Statistiche enrichment
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
            "error_report_result_id": error_report_result_id,
        }
        run_mode.decorate_result(result, project_id=project_id, task_id=task_id)

        logger.info(f"[Task {task_id}] Completato in {elapsed_time:.2f}s - {len(final_df)} righe", project_id=_log_pid)

        if _run_id and result_node and run_mode.logs_run():
            try:
                intermediate_snapshot.record_final_snapshot(
                    run_id=_run_id,
                    module="arricchisci",
                    node_id=result_node["id"],
                    node_type="result",
                    df=final_df,
                    exec_time=0.0,
                    status="ok",
                    mode_override=intermediate_mode_override,
                )
            except Exception as _sne:
                logger.warning(f"[Task {task_id}] Intermediate write result node fallita: {_sne}")

        try:
            carica_info = _trigger_carica_if_present(
                project_id=project_id,
                canvas=canvas,
                output_format=final_format,
                user_id=user_id,
                group_id=_group_id,
            )
            if carica_info:
                # Il frontend legge result.carica.tasks per fare polling sui task carica
                result["carica"] = carica_info
        except Exception as _ce:
            logger.error(
                f"[GESTITO] [Task {task_id}] Errore avvio task CARICA dopo arricchimento: {_ce}",
                project_id=project_id, exc_info=True
            )

        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)

        complete_executable_run(
            success=True,
            output_metadata={"task_id": task_id, "filename": saved_path.name},
        )

        finalize_standard_success(result)
        update_task_progress(
            self,
            100,
            "Completato!",
            task_id=task_id,
            check_revocation=False,
        )
        return result

    except TaskRevoked:
        if _run_id and run_mode.logs_run():
            try:
                intermediate_writer.close_run(
                    run_id=_run_id,
                    input_hash="",
                    output_hash="",
                    total_exec_time=time.time() - _run_start,
                    success=False,
                    retention_n=_retention_n,
                )
            except Exception as close_exc:
                logger.debug(
                    "close_run intermediate fallito post-revoke: "
                    f"{type(close_exc).__name__}: {close_exc}"
                )
        if run_mode.logs_run():
            try:
                close_run(
                    run_id=_run_id or task_id,
                    status="revoked",
                    duration_ms=int((time.time() - _run_start) * 1000),
                    error_module="arricchisci",
                    error_message="Task annullato",
                )
            except Exception as close_exc:
                logger.warning(
                    "close_run ARRICCHISCI fallito post-revoke: "
                    f"{type(close_exc).__name__}: {close_exc}"
                )
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
        cancel_executable_run()
        raise

    except Exception as e:
        # Catch dei _EarlyTaskReturn (sollevati dal blocco che adatta
        # CanvasRunError di `run_enrichment_canvas`). Questi portano un payload
        # gia' pronto per il return del task: niente logging CRASH, niente close_run
        # in failure mode, niente debug_payload — e' un fallimento "gestito"
        # Nota: _EarlyTaskReturn e' definita come classe locale dentro il try
        # quindi qui la riconosciamo via name matching sul __class__
        if type(e).__name__ == "_EarlyTaskReturn" and hasattr(e, "payload"):
            if _run_id and run_mode.logs_run():
                try:
                    intermediate_writer.close_run(
                        run_id=_run_id,
                        input_hash="",
                        output_hash="",
                        total_exec_time=time.time() - _run_start,
                        success=False,
                        retention_n=_retention_n,
                    )
                except Exception as e_inner:
                    logger.debug(f"close_run intermediate fallito (early return): {type(e_inner).__name__}: {e_inner}")
            if run_mode.logs_run():
                _elapsed_ms_er = int((time.time() - _run_start) * 1000)
                close_run(
                    run_id=_run_id or task_id,
                    status="failure",
                    duration_ms=_elapsed_ms_er,
                    error_module="arricchisci",
                    error_message=e.payload.get("error", "task early return"),
                )
            _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
            complete_executable_run(
                success=False,
                error_message=str(e.payload.get("error") or "Esecuzione fallita"),
            )
            return e.payload
        if _run_id and run_mode.logs_run():
            try:
                intermediate_writer.close_run(
                    run_id=_run_id,
                    input_hash="",
                    output_hash="",
                    total_exec_time=time.time() - _run_start,
                    success=False,
                    retention_n=_retention_n,
                )
            except Exception as e_inner:
                logger.debug(f"close_connection intermediate fallito (outer except): {type(e_inner).__name__}: {e_inner}")
        error_message = str(e)
        _elapsed_ms_f = int((time.time() - _run_start) * 1000)
        logger.error(
            f"[CRASH] [Task {task_id}] Errore: {error_message}\n{_tb.format_exc()}",
            project_id=_log_pid,
        )

        if run_mode.logs_run():
            try:
                _debug_payload = debug_payload_builder_module.DebugPayloadBuilder().build(
                    run_id=_run_id or task_id,
                    project_id=project_id,
                    steps=[],
                    failure={
                        "node_id":        None,
                        "node_type":      "api_enrich",
                        "node_label":     "ARRICCHISCI",
                        "module":         "arricchisci",
                        "error_type":     type(e).__name__,
                        "error_message":  error_message,
                        "traceback":      _tb.format_exc(),
                        "occurred_at_ms": _elapsed_ms_f,
                    },
                )
            except Exception as e:
                logger.debug(f"DebugPayloadBuilder fallito (enrichment): {type(e).__name__}: {e}")
                _debug_payload = None

            close_run(
                run_id=_run_id or task_id,
                status="failure",
                duration_ms=_elapsed_ms_f,
                error_module="arricchisci",
                error_message=error_message,
                debug_payload=_debug_payload,
            )
        
        # NON usare state="FAILURE" direttamente - causa problemi con Celery/Redis
        # Invece, aggiorniamo con progress negativo ma stato PROGRESS e is_error=True
        try:
            update_task_progress(
                self,
                0,
                f"Errore: {error_message}",
                is_error=True,
                task_id=task_id,
                check_revocation=False,
            )
        except Exception as e_prog:
            logger.debug(f"progress update fallito: {type(e_prog).__name__}: {e_prog}")

        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
        complete_executable_run(success=False, error_message=error_message)

        # Ritorna dict con errore (serializzabile
        return {
            "success": False,
            "task_id": task_id,
            "project_id": project_id,
            "error": error_message,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            # Statistiche parziali se disponibili
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
            "error_report_result_id": error_report_result_id,
        }

    finally:
        _close_worker_connection()


def _cleanup_temp_file_sources(file_source_ids: Optional[List[str]], task_id: str) -> None:
    """Elimina i file source temporanei creati per l'esecuzione (es. eseguibili).

    Non solleva mai eccezioni: in caso di errore logga e prosegue.
    """
    if not file_source_ids:
        return

    for fsid in file_source_ids:
        try:
            ok = files_service.file_source_service.delete_file_source(fsid)
            if ok:
                logger.info(f"[Task {task_id}] Cleanup: file source temp '{fsid}' eliminato")
            else:
                logger.warning(f"[Task {task_id}] Cleanup: file source temp '{fsid}' non trovato")
        except Exception as e:
            logger.debug(f"cleanup temp dir fallito: {type(e).__name__}: {e}")
            logger.warning(f"[Task {task_id}] Cleanup: errore eliminando '{fsid}': {e}")


def update_task_progress(
    task: object,
    progress: int,
    message: str,
    is_error: bool = False,
    *,
    task_id: Optional[str] = None,
    check_revocation: bool = True,
) -> None:
    """
    Aggiorna il progresso del task su PostgreSQL.

    Stato sempre 'PROGRESS' (mai 'FAILURE') per non far interpretare il task
    come terminato; il frontend determina lo stato reale dal flag is_error e
    dall'eventuale prefisso "Errore:" nel messaggio.

    `task_id` keyword obbligatorio quando la chiamata avviene da un thread
    spawned (es. ThreadPoolExecutor del loop parallelo): `self.request.id`
    di Celery e' thread-local e nei thread worker ricade su None. Va quindi
    passato esplicito (catturato a inizio task da `self.request.id`) perche'
    e' la chiave della riga task_results.

    Scrittura solo su PG via report_task_progress: e' la fonte di verita'
    per il progress consumato dalla UI.
    """
    report_task_progress(
        task_id or getattr(getattr(task, "request", None), "id", None),
        progress,
        message,
        is_error=is_error,
        name=getattr(task, "name", None),
        check_revocation=check_revocation,
    )


# Helper modulo-level rimossi: usavano il singleton globale
# `api_connection_service` con stato per-execution non isolato fra task
# paralleli. Ora il task celery (`execute_enrichment_task`) crea una
# propria istanza locale di `ApiConnectionService` e chiama direttamente
# `_get_execution_order` / `_load_source_project` / `_load_file_source` /
# `_load_static_values` su di essa. Questo garantisce isolamento del
# tenant binding (`_current_group_id`) fra task concorrenti sullo stesso
# worker process Celery (prefork riusa i fork
# I project_source downstream consumano solo latest artifact + output_contract.


def _trigger_carica_if_present(
    project_id: str,
    canvas: JsonObject,
    output_format: str = "csv",
    user_id: Optional[str] = None,
    *,
    group_id: str,
) -> Optional[JsonObject]:
    """Cerca nodi CARICA collegati all'output e lancia i relativi task.

    Delega al launcher canonico condiviso
    (``task_service.launch_carica_tasks_for_canvas``), lo STESSO usato dal path
    orchestrator (``OrchestratorService._execute_carica_if_present``): identica
    meccanica find/build/lancio via il wrapper ``create_carica_task``.
    Policy d'errore per-nodo resiliente (``resilient=True``): un nodo che non
    parte viene loggato e saltato, gli altri partono comunque.

    Returns: JSON object {"tasks": [{task_id, node_id}, ...], "message": ...} oppure None se
             nessun nodo carica trovato. Il frontend usa questo per fare polling sui task.
    """
    from app.core.orchestrator.task_service import task_service

    return task_service.launch_carica_tasks_for_canvas(
        project_id,
        canvas,
        output_contract_loader=(
            project_service_module.ProjectService().get_project_output_contract
        ),
        project_type="arricchisci",
        group_id=group_id,
        output_format=output_format,
        triggered_by="manual",
        user_id=user_id,
        resilient=True,
        message_suffix=" dopo arricchimento",
        on_unconnected=lambda node: logger.warning(
            f"Nodo {carica_dispatcher.node_type(node)} "
            f"({carica_dispatcher.node_id(node)}) non collegato al nodo Output, skip"
        ),
    )


# Nome registrato del task: soddisfa DelayTask.name (dispatch by-name)
# e combacia con l'actor Dramatiq.
execute_enrichment_task.name = "tasks.execute_enrichment"
