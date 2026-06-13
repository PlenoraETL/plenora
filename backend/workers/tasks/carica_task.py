"""
Task Celery per il modulo CARICA — esecuzione asincrona con progresso Redis.

Runs background execution while preserving backend runtime contracts.
"""

import time as _time
import traceback
import uuid as _uuid
from typing import Any, Optional, cast

from app.shared.contracts.payloads import JsonObject
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.config import settings
from app.core.artifacts import (
    ArtifactResolver,
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    PipelineArtifact,
    SourceLoader,
    SourceLoadingError,
    SourceRef,
)
from app.core.auth.async_user_recheck import recheck_user_authorized
from app.core.carica import dispatch as carica_dispatch
from app.core.carica.dispatch import CaricaTaskResultPayload
from app.core.carica.target_lock import (
    CaricaTargetLockHandle,
    acquire_carica_target_lock,
    release_carica_target_lock,
    resolve_carica_target_lock,
)
from app.core.dataframes.frame_io import (
    load_dataframe_from_artifact,
    load_dataframe_from_file,
)
from app.core.intermediate.cleanup import close_worker_connection
from app.core.intermediate.run_idempotency import is_already_completed
from app.core.orchestrator.task_progress import report_task_progress
from app.core.projects.runtime_context import get_project_runtime_context
from app.core.projects.service import ProjectService
from app.db.pg_connection import close_connection as _close_config_connection
from app.runtime import services as runtime_services_module
from app.utils.debug_payload_builder import DebugPayloadBuilder

from workers.celery_app import celery_app, BaseTaskWithRetry


logger = get_logger("workers.carica_task")




@celery_app.task(
    bind=True,
    base=BaseTaskWithRetry,
    name="carica.execute",
    # Database writes are fail-fast: automatic retries can duplicate partial
    # append+per_batch writes when no unique constraint is present.
    max_retries=0,
    soft_time_limit=settings.CELERY_TASK_TIMEOUT,
    time_limit=settings.CELERY_TASK_HARD_LIMIT,
)
def execute_carica_task(
    self,
    project_id: str,
    canvas: JsonObject,
    carica_node_data: JsonObject,
    output_format: str = "geojson",
    force_recalculate: bool = False,
    triggered_by: str = "manual",
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> CaricaTaskResultPayload:
    """Task asincrono per eseguire il caricamento su database.

    Retry policy: `max_retries=0` (fail-fast). Un retry automatico
    su un write in append+per_batch senza unique constraints
    duplicherebbe righe. Preferiamo task fallito + rilancio manuale
    dall'operatore dopo verifica, invece di retry silenzioso non
    idempotent. Gli errori transient di rete non sono retryati —
    sollevano eccezione visibile che viene persistita nei log.

    Idempotency: se `run_id` viene passato dall'esterno
    (scheduler, retry-handler UI) e in intermediate.runs esiste gia'
    un record con status='completed' per quel run_id, il task SKIPPA
    l'esecuzione e ritorna un risultato cached. Copre il caso di
    retry manuale accidentale o replay da backup. Se run_id non
    viene passato → genera fresh (behavior legacy, no idempotency).

    Args:
        project_id: ID del progetto
        canvas: Canvas state completo con nodi e edges
        carica_node_data: Dati del nodo CARICA (configurazione)
        output_format: Formato output del nodo Result upstream
        force_recalculate: Se True, riesegue la pipeline upstream
        triggered_by: "manual" o "cron"
        run_id: Chiave di idempotency opzionale. Se None, generato
            fresh. Se passato e gia' completato → task skippato.

    Returns:
        Dict con risultato dell'esecuzione (WriteResult.to_dict)
        o status='skipped_idempotent' se il run_id era gia' completo.
    """
    task_id = self.request.id
    if run_id is not None:
        if is_already_completed(run_id):
            logger.info(
                f"[IDEMPOTENCY] Task CARICA {task_id}: run_id {run_id} "
                f"gia' completato con successo, skip."
            )
            return {
                "status": "skipped_idempotent",
                "success": True,
                "message": f"Run {run_id} gia' completato",
                "run_id": run_id,
                "rows_written": 0,
            }
    else:
        run_id = str(_uuid.uuid4())

    _run_start = _time.time()

    logger.info(f"Task CARICA {task_id} avviato per progetto {project_id} (run_id={run_id})")

    _revoked = recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, run_id=run_id, task_name="CARICA",
    )
    if _revoked is not None:
        _revoked.update({"run_id": run_id, "task_id": task_id, "rows_written": 0})
        return cast(CaricaTaskResultPayload, _revoked)

    _group_id = ""
    try:
        _meta = ProjectService().get_project_metadata(project_id)
        _group_id = getattr(_meta, "group_id", "") if _meta else ""
    except Exception as e:
        logger.debug(f"group_id lookup fallito: {type(e).__name__}: {e}")

    open_run(
        run_id=run_id,
        project_id=project_id,
        group_id=_group_id,
        module="carica",
        triggered_by=triggered_by,
        user_id=user_id,
    )
    set_run_context(run_id=run_id, project_id=project_id)

    _update_progress(self, 0, "Inizializzazione CARICA...")

    # Astrae lo stato Celery per il dispatch (carica/dispatch.py, Celery-free).
    def progress(percent: int, message: str) -> None:
        _update_progress(self, percent, message)

    target_lock_handle: CaricaTargetLockHandle | None = None
    try:
        write_branch = carica_dispatch.select_carica_write_branch(carica_node_data)
        target_lock_handle = _acquire_target_lock_or_return_busy(
            carica_node_data=carica_node_data,
            project_id=project_id,
            group_id=_group_id,
            task_id=task_id,
            run_id=run_id,
            write_branch=write_branch,
        )
        if target_lock_handle is None:
            lock_target = resolve_carica_target_lock(
                node_data=carica_node_data,
                project_id=project_id,
                group_id=_group_id,
                write_branch=write_branch,
            )
            if lock_target is not None:
                elapsed_ms = int((_time.time() - _run_start) * 1000)
                message = (
                    "Destinazione CARICA gia' in scrittura da un altro task; "
                    "riprova al termine della run corrente."
                )
                _update_progress(self, -1, message)
                close_run(
                    run_id=run_id,
                    status="failure",
                    duration_ms=elapsed_ms,
                    error_module="carica",
                    error_message=message,
                )
                return {
                    "status": "conflict",
                    "success": False,
                    "error": "carica_target_locked",
                    "message": message,
                    "error_message": message,
                    "rows_written": 0,
                    "total_rows": 0,
                }

        _update_progress(self, 5, "Caricamento dati dal nodo Output...")

        # Multi-layer (es. IMDF bundle): se l'artefatto upstream e' riconosciuto
        # da un adapter registrato, il sink ArcGIS scrive N layer in una-run,
        # senza passare dal DataFrame singolo. CARICA resta generico: interroga
        # il registry, non conosce i formati verticali (la direzione di
        # accoppiamento e' verticale->orizzontale, l'adapter lo registra il
        # composition root).
        result = None
        if write_branch == 'arcgis':
            result = carica_dispatch.maybe_multilayer_arcgis_write(
                project_id, canvas, carica_node_data, task_id, progress=progress,
            )

        if result is None:
            df = _load_upstream_data(project_id, canvas, force_recalculate, output_format)

            if df is None:
                logger.warning("Nessun dato dal nodo upstream — DataFrame None")
                close_run(
                    run_id=run_id, status="failure",
                    duration_ms=int((_time.time() - _run_start) * 1000),
                    error_module="carica",
                    error_message="Nessun dato disponibile dal nodo Output upstream.",
                )
                return {
                    "status": "validation_error",
                    "success": False,
                    "error_message": "Nessun dato disponibile dal nodo Output upstream.",
                    "rows_written": 0,
                    "total_rows": 0,
                }

            if len(df) == 0:
                # 0 righe è un caso legittimo — completato con successo senza scrivere nulla
                logger.info("Dataset vuoto (0 righe) — CARICA completato senza scritture")
                close_run(
                    run_id=run_id, status="success",
                    rows_out=0,
                    duration_ms=int((_time.time() - _run_start) * 1000),
                )
                return {
                    "status": "completed",
                    "success": True,
                    "message": "Dataset vuoto: nessuna riga da scrivere.",
                    "rows_written": 0,
                    "total_rows": 0,
                }

            logger.info(f"DataFrame caricato: {len(df)} righe, {len(df.columns)} colonne")
            _update_progress(
                self, 10,
                f"Dati caricati: {len(df)} righe. Avvio scrittura..."
            )

            if write_branch == 'datasource':
                result = carica_dispatch.execute_datasource_write(
                    df, carica_node_data, project_id, task_id, progress=progress)
            elif write_branch == 'rest':
                result = carica_dispatch.execute_rest_write(
                    df, carica_node_data, project_id, task_id, progress=progress)
            elif write_branch == 'arcgis':
                result = carica_dispatch.execute_arcgis_write(
                    df, carica_node_data, project_id, task_id, progress=progress)
            else:
                result = carica_dispatch.execute_db_write(
                    df, carica_node_data, project_id, task_id, triggered_by, run_id, progress=progress)

        is_success = result.get("success", False)
        rows_written = result.get("rows_written", 0) or 0
        elapsed_ms = int((_time.time() - _run_start) * 1000)

        # P1 (2026-05-26): emit `append_step` SOLO per i branch
        # non-db. Il path `db` ha gia' un emit interno in
        # `CaricaExecutor.execute()` che mappa correttamente lo
        # status PARTIAL (qui invece si collassava a "failure").
        # Pre-fix: doppia entry "carica" per ogni esecuzione db (run
        # log e dashboard UI mostravano due righe per la stessa
        # operazione).
        if write_branch != 'db':
            append_step({
                "module":      "carica",
                "status":      "success" if is_success else "failure",
                "rows_out":    rows_written,
                "duration_ms": elapsed_ms,
                "error":       result.get("error_message") if not is_success else None,
            })
        close_run(
            run_id=run_id,
            status="success" if is_success else "failure",
            rows_out=rows_written,
            duration_ms=elapsed_ms,
            steps=[],   # steps già nel context thread-local non accessibili qui — opzionale
            error_module="carica" if not is_success else None,
            error_message=result.get("error_message") if not is_success else None,
        )
        return result

    except Exception as e:
        # error_msg_internal: canale admin/diagnostics (log_runs DB via close_run
        # progress UI via _update_progress, log_service.create_log_entry
        # Contiene str(e) intenzionalmente per postmortem admin
        error_msg_internal = f"Errore task CARICA: {str(e)}"
        public_error_message = "Errore task CARICA (consulta log)"
        public_error_code = "carica_task_fatal"
        if isinstance(e, ValueError) and "[UNSAFE WRITE CONFIG]" in str(e):
            public_error_message = str(e)
            public_error_code = "carica_unsafe_write_config"
        error_trace = traceback.format_exc()
        elapsed_ms = int((_time.time() - _run_start) * 1000)
        logger.error(f"[CRASH] {error_msg_internal}\n{error_trace}")

        # debug_payload --- (diagnostic canonico, str(e) intenzionale
        try:
            debug_payload = DebugPayloadBuilder().build(
                run_id=run_id,
                project_id=project_id,
                steps=[],
                failure={
                    "node_id":        None,
                    "node_type":      "carica",
                    "node_label":     "CARICA",
                    "module":         "carica",
                    "error_type":     type(e).__name__,
                    "error_message":  str(e),
                    "traceback":      error_trace,
                    "occurred_at_ms": elapsed_ms,
                },
            )
        except Exception as e:
            logger.debug(f"DebugPayloadBuilder fallito: {type(e).__name__}: {e}")
            debug_payload = None

        # Canali admin preservano str(e) per postmortem (log_runs DB / progress UI
        close_run(
            run_id=run_id,
            status="failure",
            duration_ms=elapsed_ms,
            error_module="carica",
            error_message=error_msg_internal,
            debug_payload=debug_payload,
        )
        _update_progress(self, -1, public_error_message)
        # intent: boundary top-level task Celery / outcome: fail-closed sanitizzato
        # Il return dict finisce in Redis via AsyncResult → FE: niente str(e
        # error_details=error_trace preservato come canale diagnostic separato
        return {
            "status": "write_error",
            "success": False,
            "error": public_error_code,
            "message": public_error_message,
            "error_message": public_error_message,
            "error_details": error_trace,
            "rows_written": 0,
            "total_rows": 0,
        }
    finally:
        if target_lock_handle is not None:
            try:
                released = release_carica_target_lock(target_lock_handle)
                if not released:
                    logger.warning(
                        "CARICA target lock non rilasciato: owner non piu' corrente "
                        f"(task_id={task_id}, run_id={run_id})"
                    )
            except Exception as e:
                logger.warning(
                    f"release CARICA target lock fallito: {type(e).__name__}: {e}"
                )
        # FIX: chiudi le connessioni PostgreSQL thread-local del worker Celery
        # Storia: la migrazione SQLite -> PostgreSQL ha mantenuto lo stesso
        # pattern thread-local, quindi il cleanup resta necessario. Senza
        # questa pulizia, ogni esecuzione lascia 2 connessioni aperte
        # config DB + intermediate DB) nel thread.local, che vengono
        # riusate dal worker pool finche' il file descriptor si esaurisce
        # o il pool resta saturo. Pre-migrazione si manifestava come
        # SQLITE_BUSY; post-migrazione come PG pool exhaustion
        try:
            close_worker_connection()
        except Exception as e:
            logger.debug(f"close_connection intermediate fallito: {type(e).__name__}: {e}")
        try:
            _close_config_connection()
        except Exception as e:
            logger.debug(f"close_connection config fallito: {type(e).__name__}: {e}")




def _acquire_target_lock_or_return_busy(
    *,
    carica_node_data: JsonObject,
    project_id: str,
    group_id: str,
    task_id: str,
    run_id: str,
    write_branch: str,
) -> CaricaTargetLockHandle | None:
    """Acquire the distributed destination lock, or None when busy/unknown."""
    target_lock = resolve_carica_target_lock(
        node_data=carica_node_data,
        project_id=project_id,
        group_id=group_id,
        write_branch=write_branch,
    )
    if target_lock is None:
        logger.info(
            "CARICA target lock skipped: destinazione non identificabile "
            f"(branch={write_branch}, project_id={project_id})"
        )
        return None

    ttl_seconds = int(
        settings.get(
            "CARICA_TARGET_LOCK_TTL_SECONDS",
            settings.CELERY_TASK_HARD_LIMIT + 300,
        )
    )
    owner_token = f"task={task_id};run={run_id}"
    handle = acquire_carica_target_lock(
        target=target_lock,
        owner_token=owner_token,
        ttl_seconds=ttl_seconds,
    )
    if handle is None:
        logger.warning(
            "CARICA target lock busy "
            f"(kind={target_lock.kind}, key={target_lock.key}, project_id={project_id})"
        )
        return None

    logger.info(
        "CARICA target lock acquired "
        f"(kind={target_lock.kind}, key={target_lock.key}, ttl={ttl_seconds}s)"
    )
    return handle







def _update_progress(task, percent: int, message: str):
    """Aggiorna il progresso del task su PostgreSQL.

    Stato sempre 'PROGRESS' (mai 'FAILURE') per non far interpretare il task
    come terminato; il frontend determina lo stato reale da messaggio e
    percentuale (percent negativo = errore).

    Plenora 2.0 Fase 2 (F2.5): la scrittura va solo su PG via
    report_task_progress (keyed sul task_id). Il vecchio self.update_state
    verso il result backend Redis di Celery e' stato rimosso col flip della
    lettura su PG (F2.3): quello stato non veniva piu' letto da nessuno.
    """
    report_task_progress(
        getattr(getattr(task, "request", None), "id", None),
        percent,
        message,
        is_error=percent < 0,
        name="carica.execute",
    )


def _load_upstream_data(
    project_id: str,
    canvas: JsonObject,
    force_recalculate: bool,
    output_format: str,
) -> Optional[Any]:
    """Carica il DataFrame dal nodo Result upstream.

    Strategia:
    1. Cerca nel DB PostgreSQL (via SourceLoader, fail-closed sul group_id)
    2. Se force_recalculate o non trova → riesegui la pipeline
    3. Dopo ricalcolo rilegge l'artefatto specifico della run
    """
    try:
        # Pre-cache check: se il blob e' gia' presente, salta execute_pipeline
        # Il group_id viene letto dal progetto per chiudere il check
        # cross-tenant del resolver (defense-in-depth: il task CARICA
        # opera su un project_id arrivato dal payload Celery, garantito
        # dal layer HTTP a monte ma comunque verificato qui
        if not force_recalculate:
            try:
                _project_context = get_project_runtime_context(project_id)
                _project_group_id = (
                    _project_context.group_id if _project_context else None
                )
            except Exception as _ge:
                logger.warning(
                    f"[GESTITO] _load_upstream_data: lookup group_id "
                    f"per {project_id} fallito: {_ge}"
                )
                _project_group_id = None

            if not _project_group_id:
                logger.error(
                    f"[SECURITY] _load_upstream_data: group_id non "
                    f"determinabile per {project_id}, fail-closed sul "
                    f"pre-cache (procedo con execute_pipeline)"
                )
            else:
                try:
                    df = SourceLoader().load(
                        SourceRef(project=LatestProjectArtifactRef(project_id=project_id)),
                        expected_group_id=_project_group_id,
                    )
                except SourceLoadingError as _sle:
                    logger.error(
                        f"[SECURITY] CARICA pre-cache: {_sle}"
                    )
                    return None
                if df is not None:
                    logger.info(f"Caricamento output dal DB per {project_id}")
                    return df

        # Nessun risultato in cache o force_recalculate → riesegui pipeline
        # Runtime composition owns the executor graph. CARICA must not rely on
        # OrchestratorService fallback builders, otherwise the worker path
        # diverges from the API/data-plane contract wiring.
        orchestrator_stack = runtime_services_module.build_orchestrator_stack()
        orchestrator = orchestrator_stack.orchestrator

        result = orchestrator.execute_pipeline(
            project_id=project_id,
            force_recalculate=force_recalculate,
            skip_carica=True,
        )

        # Controlla se la pipeline ha fallito — errore gestito, non crash
        if not result or not result.get("success"):
            pipeline_error = (result or {}).get("message", "Pipeline fallita senza messaggio")
            logger.error(
                f"[GESTITO] Pipeline upstream fallita per {project_id}: {pipeline_error}. "
                f"Impossibile procedere con CARICA."
            )
            return None

        # Risoluzione artefatto centralizzata via PipelineArtifact +
        # ArtifactResolver: inline_df → result_id (verify project_id
        # hard fail su mismatch). Niente latest-by-project: due esecuzioni
        # parallele sullo stesso project si scambierebbero l'output
        artifact = PipelineArtifact.from_executor_result(result, project_id=project_id)
        if artifact.artifact_kind == "none":
            logger.error(
                f"[GESTITO] CARICA: execute_pipeline({project_id}) non ha "
                f"restituito ne' dataframe inline ne' result_id. "
                f"Impossibile determinare l'artefatto esatto — abort."
            )
            return None
        try:
            materialized = ArtifactResolver().materialize_run(
                artifact.to_run_artifact_ref()
            )
        except ArtifactResolutionError as _re:
            logger.error(f"[GESTITO] CARICA: {_re}")
            return None
        with materialized as m:
            if m.df is not None:
                return m.df
            if m.data is not None:
                return load_dataframe_from_artifact(
                    data=m.data, format_hint=m.format,
                )
            return load_dataframe_from_file(m.path)

    except ImportError as e:
        # Qualunque errore nel risolvere l'artefatto upstream e' fatale
        # meglio un task CARICA fallito e visibile che un caricamento
        # silente di un dataset sbagliato sul DB di destinazione (i due
        # `except` NON devono rifallback su latest-by-project del
        # progetto, sarebbe lo stesso race che il path principale evita
        logger.error(f"[CRASH] Import error in _load_upstream_data: {e}", exc_info=True)
        return None

    except Exception as e:
        logger.error(
            f"[CRASH] Errore caricamento dati upstream per {project_id}: {e} — "
            f"abort (nessun fallback a latest-by-project)",
            exc_info=True,
        )
        return None
