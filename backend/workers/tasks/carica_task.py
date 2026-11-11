"""
Task Celery per il modulo CARICA — esecuzione asincrona con progresso Redis.

Runs background execution while preserving backend runtime contracts.
"""

import time as _time
import traceback
from app.shared.execution.progress_messages import progress_text
import uuid as _uuid
from typing import Any, Mapping, Optional, cast

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
    CaricaTargetLockHeartbeat,
    CaricaTargetLockHandle,
    acquire_carica_target_lock,
    require_carica_target_lock,
    release_carica_target_lock,
    start_carica_target_lock_heartbeat,
)
from app.core.dataframes.frame_io import (
    load_dataframe_from_artifact,
    load_dataframe_from_file,
)
from app.core.intermediate.cleanup import close_worker_connection
from app.core.intermediate import run_lifecycle as intermediate_run_lifecycle
from app.core.intermediate import runs_db as intermediate_runs_db
from app.core.intermediate.run_idempotency import is_already_completed
from app.core.orchestrator.task_progress import TaskRevoked, report_task_progress
from app.core.orchestrator.execution_contract import CaricaExecutionContract
from app.shared.execution.runtime_identity import RuntimeIdentityError, is_test_run_trigger, route_upstream_source
from app.core.projects.upstream_identity import source_identity_need
from app.core.projects.service import ProjectService
from app.db.pg_connection import close_connection as _close_config_connection
from app.runtime import services as runtime_services_module
from app.utils.debug_payload_builder import DebugPayloadBuilder


logger = get_logger("workers.carica_task")


def _close_worker_connections(*, stage: str = "") -> None:
    """Rilascia entrambe le connessioni thread-local del task CARICA."""
    suffix = f" {stage}" if stage else ""
    try:
        close_worker_connection()
    except Exception as cleanup_exc:
        logger.debug(
            f"close_connection intermediate fallito{suffix}: "
            f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        )
    try:
        _close_config_connection()
    except Exception as cleanup_exc:
        logger.debug(
            f"close_connection config fallito{suffix}: "
            f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        )


# Corpo task come funzione pura.
# `self` e' un self-shim fornito dall'actor Dramatiq (request.id/name); vedi
# workers/dramatiq_app.py. Retry/timeouts non sono piu' decorator Celery: la
# write fail-fast (no retry, niente doppie scritture append+per_batch) e'
# garantita dall'actor con max_retries=0.
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
    execution_contract: Optional[Mapping[str, Any]] = None,
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
    retry manuale accidentale o replay da backup. Se run_id non viene passato,
    genera fresh e non applica idempotency cross-run.

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
    contract: CaricaExecutionContract | None = None
    if execution_contract is not None:
        contract = CaricaExecutionContract.from_payload(
            execution_contract,
            expected_project_id=project_id,
        )
        canvas = dict(contract.canvas)
        carica_node_data = dict(contract.carica_node_data)
        output_format = contract.output_format
        force_recalculate = contract.force_recalculate
        triggered_by = contract.triggered_by
    if is_test_run_trigger(triggered_by):
        # Anche senza contratto (messaggi legacy): il prefisso delle prove lo
        # scrive solo il server per le prove, e una prova non avvia Carica.
        raise ValueError("triggered_by di prova non ammesso per CARICA")
    if run_id is None:
        run_id = str(_uuid.uuid4())

    _run_start = _time.time()

    logger.info(f"Task CARICA {task_id} avviato per progetto {project_id} (run_id={run_id})")

    _revoked = recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, run_id=run_id, task_name="CARICA",
    )
    if _revoked is not None:
        _revoked.update({"run_id": run_id, "task_id": task_id, "rows_written": 0})
        _close_worker_connections(stage="post-permission-revoke")
        return cast(CaricaTaskResultPayload, _revoked)

    # Il controllo autorizzativo deve precedere anche il fast-path
    # idempotente: un utente revocato non puo' osservare l'esito di run
    # precedenti semplicemente riusandone l'identificativo.
    if is_already_completed(run_id):
        logger.info(
            f"[IDEMPOTENCY] Task CARICA {task_id}: run_id {run_id} "
            f"gia' completato con successo, skip."
        )
        result: CaricaTaskResultPayload = {
            "status": "skipped_idempotent",
            "success": True,
            "message": f"Run {run_id} gia' completato",
            "run_id": run_id,
            "rows_written": 0,
        }
        _close_worker_connections(stage="post-idempotency")
        return result

    if contract is not None:
        _group_id = contract.group_id
    else:
        # Compatibility for messages queued before CARICA contracts. This
        # lookup is deliberately fail-closed: a write cannot proceed without
        # a tenant binding.
        _meta = ProjectService().get_project_metadata(project_id)
        _group_id = str(getattr(_meta, "group_id", "") or "") if _meta else ""
        if not _group_id:
            raise RuntimeError("group_id progetto non determinabile per CARICA")

    existing_intermediate_run = intermediate_runs_db.get_run(run_id)
    if existing_intermediate_run is not None:
        existing_status = str(existing_intermediate_run.get("status") or "")
        if existing_status in {"running", "completion_uncertain"}:
            message = (
                "Esiste una scrittura CARICA non terminale o incerta; "
                "verifica la destinazione prima di rilanciare."
            )
            _close_worker_connections(stage="pre-target-barrier")
            return {
                "status": "completion_uncertain",
                "success": False,
                "error": "carica_target_uncertain",
                "message": message,
                "error_message": message,
                "run_id": run_id,
                "project_id": project_id,
                "rows_written": 0,
                "total_rows": 0,
            }

    log_run_opened = False
    intermediate_run_finalized = False
    try:
        open_run(
            run_id=run_id,
            project_id=project_id,
            group_id=_group_id,
            module="carica",
            triggered_by=triggered_by,
            user_id=user_id,
        )
        log_run_opened = True
        set_run_context(run_id=run_id, project_id=project_id)
    except BaseException:
        if log_run_opened:
            try:
                close_run(
                    run_id=run_id,
                    status="failure",
                    duration_ms=int((_time.time() - _run_start) * 1000),
                    error_module="carica",
                    error_message="Inizializzazione run fallita",
                )
            except Exception:
                logger.warning("close_run CARICA fallito durante setup", exc_info=True)
        _close_worker_connections(stage="setup")
        raise

    # Astrae lo stato Celery per il dispatch (carica/dispatch.py, Celery-free).
    def progress(percent: int, message: str) -> None:
        _update_progress(self, percent, message)

    def committed_progress(percent: int, message: str) -> None:
        _update_progress(
            self,
            percent,
            message,
            check_revocation=False,
        )

    target_lock_handle: CaricaTargetLockHandle | None = None
    target_lock_heartbeat: CaricaTargetLockHeartbeat | None = None
    intermediate_run_persisted = existing_intermediate_run is not None
    write_started = False
    try:
        _update_progress(self, 0, progress_text("load_start"))
        # La porta unica (runtime_identity U1/U6) PRIMA di ogni lettura o
        # ricalcolo del risultato da caricare (anche multi-layer): un progetto
        # che ha bisogno dell'«Utente che esegue» (input proprio o a monte)
        # non ha mai un risultato salvato filtrato, e Carica non ha il piano
        # dell'esecuzione: si blocca col motivo, nessuna scrittura.
        try:
            route_upstream_source(
                source_project_id=str(project_id),
                need=source_identity_need(str(project_id), group_id=_group_id),
                isolated_upstreams=None,
            )
        except RuntimeIdentityError as identity_error:
            _update_progress(self, -1, identity_error.message)
            close_run(
                run_id=run_id, status="failure",
                duration_ms=int((_time.time() - _run_start) * 1000),
                error_module="carica", error_message=identity_error.message,
            )
            return {
                "status": "validation_error",
                "success": False,
                "error": identity_error.code,
                "message": identity_error.message,
                "error_message": identity_error.message,
                "rows_written": 0,
                "total_rows": 0,
            }
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
            elapsed_ms = int((_time.time() - _run_start) * 1000)
            message = progress_text("load_busy")
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

        if existing_intermediate_run is None:
            intermediate_run_lifecycle.create_run(
                run_id=run_id,
                canvas_id=project_id,
                group_id=_group_id,
                user_id=user_id,
                target_lock_key=target_lock_handle.target.key,
            )
            intermediate_run_persisted = True
        else:
            # ``allow_retry`` porta la run a failed; la stessa chiave viene
            # riaperta senza una seconda INSERT sulla PK.
            intermediate_runs_db.update_run(
                run_id,
                status="running",
                target_lock_key=target_lock_handle.target.key,
            )
            intermediate_run_persisted = True

        target_lock_heartbeat = start_carica_target_lock_heartbeat(
            target_lock_handle,
            ttl_seconds=_carica_target_lock_ttl_seconds(),
        )

        _update_progress(self, 5, progress_text("load_reading"))

        # Multi-layer (es. IMDF bundle): se l'artefatto upstream e' riconosciuto
        # da un adapter registrato, il sink ArcGIS scrive N layer in una-run,
        # senza passare dal DataFrame singolo. CARICA resta generico: interroga
        # il registry, non conosce i formati verticali (la direzione di
        # accoppiamento e' verticale->orizzontale, l'adapter lo registra il
        # composition root).
        result = None
        if write_branch == 'arcgis':
            write_started = True
            result = carica_dispatch.maybe_multilayer_arcgis_write(
                project_id, canvas, carica_node_data, task_id,
                progress=progress, committed_progress=committed_progress,
                expected_group_id=_group_id,
            )

        if result is None:
            df = _load_upstream_data(
                project_id,
                canvas,
                force_recalculate,
                output_format,
                expected_group_id=_group_id,
                execution_contract=contract,
            )

            if df is None:
                logger.warning("Nessun dato dal nodo upstream — DataFrame None")
                intermediate_runs_db.update_run(run_id, status="failed")
                intermediate_run_finalized = True
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
                intermediate_runs_db.update_run(run_id, status="completed")
                intermediate_run_finalized = True
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
                progress_text("load_read", rows=len(df)),
            )

            if write_branch == 'datasource':
                write_started = True
                result = carica_dispatch.execute_datasource_write(
                    df, carica_node_data, project_id, task_id,
                    progress=progress, committed_progress=committed_progress,
                    expected_group_id=_group_id)
            elif write_branch == 'rest':
                write_started = True
                result = carica_dispatch.execute_rest_write(
                    df, carica_node_data, project_id, task_id,
                    progress=progress, committed_progress=committed_progress,
                    expected_group_id=_group_id, user_id=user_id)
            elif write_branch == 'arcgis':
                write_started = True
                result = carica_dispatch.execute_arcgis_write(
                    df, carica_node_data, project_id, task_id,
                    progress=progress, committed_progress=committed_progress,
                    expected_group_id=_group_id)
            else:
                write_started = True
                result = carica_dispatch.execute_db_write(
                    df, carica_node_data, project_id, task_id, triggered_by, run_id,
                    progress=progress, committed_progress=committed_progress,
                    expected_group_id=_group_id)

        is_success = result.get("success", False)
        rows_written = result.get("rows_written", 0) or 0
        elapsed_ms = int((_time.time() - _run_start) * 1000)

        target_lock_heartbeat.stop()
        if target_lock_heartbeat.lost:
            message = (
                "Ownership del target lock persa durante la scrittura; "
                "verifica la destinazione prima di rilanciare."
            )
            logger.error(
                "[IDEMPOTENCY] CARICA target lock perso "
                f"(run={run_id}, error={target_lock_heartbeat.error})"
            )
            try:
                intermediate_runs_db.update_run(
                    run_id, status="completion_uncertain",
                )
                intermediate_run_finalized = True
            except Exception:
                logger.error(
                    "Marcatura intermediate CARICA completion_uncertain "
                    "fallita dopo perdita target lock",
                    exc_info=True,
                )
            close_run(
                run_id=run_id,
                status="failure",
                rows_out=rows_written,
                duration_ms=elapsed_ms,
                error_module="carica",
                error_message=message,
            )
            return {
                "status": "completion_uncertain",
                "success": False,
                "error": "carica_target_lock_lost",
                "message": message,
                "error_message": message,
                "rows_written": rows_written,
                "total_rows": result.get("total_rows", rows_written) or 0,
                "run_id": run_id,
                "project_id": project_id,
            }

        # I sink remoti possono aver accettato una richiesta anche quando la
        # risposta finale e' un errore o si perde in rete. Non autorizzare un
        # replay automatico: richiedi una decisione esplicita dell'operatore.
        if not is_success and write_branch in {"datasource", "rest", "arcgis"}:
            message = (
                "Esito della scrittura esterna non determinabile: verifica "
                "la destinazione prima di rilanciare."
            )
            intermediate_runs_db.update_run(
                run_id, status="completion_uncertain",
            )
            intermediate_run_finalized = True
            close_run(
                run_id=run_id,
                status="failure",
                rows_out=rows_written,
                duration_ms=elapsed_ms,
                error_module="carica",
                error_message=message,
            )
            return {
                "status": "completion_uncertain",
                "success": False,
                "error": "carica_external_write_uncertain",
                "message": message,
                "error_message": message,
                "rows_written": rows_written,
                "total_rows": result.get("total_rows", rows_written) or 0,
                "run_id": run_id,
                "project_id": project_id,
            }

        try:
            intermediate_runs_db.update_run(
                run_id,
                status="completed" if is_success else "failed",
            )
            intermediate_run_finalized = True
        except Exception as state_exc:
            if is_success:
                logger.error(
                    "[IDEMPOTENCY] Scrittura CARICA riuscita ma marker "
                    f"completed non persistito per run {run_id}: {state_exc}",
                    exc_info=True,
                )
                try:
                    intermediate_runs_db.update_run(
                        run_id, status="completion_uncertain",
                    )
                    intermediate_run_finalized = True
                except Exception:
                    logger.error(
                        "Marcatura intermediate CARICA completion_uncertain "
                        "fallita dopo errore del marker completed",
                        exc_info=True,
                    )
                message = (
                    "Scrittura completata, ma conferma idempotente non "
                    "persistita: verifica la destinazione prima di rilanciare."
                )
                close_run(
                    run_id=run_id,
                    status="failure",
                    rows_out=rows_written,
                    duration_ms=elapsed_ms,
                    error_module="carica",
                    error_message=message,
                )
                return {
                    "status": "completion_uncertain",
                    "success": False,
                    "error": "carica_completion_uncertain",
                    "message": message,
                    "error_message": message,
                    "rows_written": rows_written,
                    "total_rows": result.get("total_rows", rows_written) or 0,
                    "run_id": run_id,
                    "project_id": project_id,
                }
            raise

        # Emit `append_step` solo per branch non-db: il path `db` ha gia'
        # emit interno in `CaricaExecutor.execute()` e mappa correttamente
        # lo status PARTIAL.
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

    except TaskRevoked:
        if intermediate_run_persisted and not intermediate_run_finalized:
            try:
                intermediate_runs_db.update_run(
                    run_id,
                    status=(
                        "completion_uncertain" if write_started else "failed"
                    ),
                )
                intermediate_run_finalized = True
            except Exception:
                logger.warning(
                    "Marcatura intermediate CARICA revoked fallita",
                    exc_info=True,
                )
        try:
            close_run(
                run_id=run_id,
                status="revoked",
                duration_ms=int((_time.time() - _run_start) * 1000),
                error_module="carica",
                error_message="Task annullato",
            )
        except Exception as close_exc:
            logger.warning(
                "close_run CARICA fallito post-revoke: "
                f"{type(close_exc).__name__}: {close_exc}"
            )
        raise

    except Exception as e:
        # error_msg_internal: canale admin/diagnostics (log_runs DB via close_run
        # progress UI via _update_progress, log_service.create_log_entry
        # Contiene str(e) intenzionalmente per postmortem admin
        error_msg_internal = f"Errore task CARICA: {str(e)}"
        public_error_message = "Errore task CARICA (consulta log)"
        public_error_code = "carica_task_fatal"
        public_status = "write_error"
        if write_started:
            public_status = "completion_uncertain"
            public_error_code = "carica_external_write_uncertain"
            public_error_message = (
                "Scrittura avviata ma esito non determinabile: verifica la "
                "destinazione prima di rilanciare."
            )
        if isinstance(e, ValueError) and "[UNSAFE WRITE CONFIG]" in str(e):
            public_error_message = str(e)
            public_error_code = "carica_unsafe_write_config"
        error_trace = traceback.format_exc()
        elapsed_ms = int((_time.time() - _run_start) * 1000)
        logger.error(f"[CRASH] {error_msg_internal}\n{error_trace}")

        if intermediate_run_persisted and not intermediate_run_finalized:
            try:
                intermediate_runs_db.update_run(
                    run_id,
                    status=(
                        "completion_uncertain" if write_started else "failed"
                    ),
                )
                intermediate_run_finalized = True
            except Exception:
                logger.warning(
                    "Marcatura intermediate CARICA terminale non riuscita; "
                    "run lasciata non terminale per bloccare replay incerti",
                    exc_info=True,
                )

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
        _update_progress(
            self,
            -1,
            public_error_message,
            check_revocation=False,
        )
        # intent: boundary top-level task Celery / outcome: fail-closed sanitizzato
        # Il return dict finisce in Redis via AsyncResult → FE: niente str(e
        # error_details=error_trace preservato come canale diagnostic separato
        return {
            "status": public_status,
            "success": False,
            "error": public_error_code,
            "message": public_error_message,
            "error_message": public_error_message,
            "error_details": error_trace,
            "rows_written": 0,
            "total_rows": 0,
            "run_id": run_id,
            "project_id": project_id,
        }
    finally:
        if target_lock_heartbeat is not None:
            target_lock_heartbeat.stop()
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
        # Chiude le connessioni thread-local del worker: senza cleanup ogni
        # esecuzione mantiene aperte le connessioni config/intermediate nel
        # thread pool, fino a saturare i pool PostgreSQL.
        _close_worker_connections()




def _acquire_target_lock_or_return_busy(
    *,
    carica_node_data: JsonObject,
    project_id: str,
    group_id: str,
    task_id: str,
    run_id: str,
    write_branch: str,
) -> CaricaTargetLockHandle | None:
    """Acquire the distributed destination lock, or None when busy."""
    target_lock = require_carica_target_lock(
        node_data=carica_node_data,
        project_id=project_id,
        group_id=group_id,
        write_branch=write_branch,
    )
    ttl_seconds = _carica_target_lock_ttl_seconds()
    # Il token coincide con la PK della run: lo store puo' distinguere la run
    # corrente dalle barriere lasciate da esecuzioni precedenti sul target.
    owner_token = run_id
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


def _carica_target_lock_ttl_seconds() -> int:
    return max(
        1,
        int(
            settings.get(
                "CARICA_TARGET_LOCK_TTL_SECONDS",
                settings.TASK_HARD_LIMIT + 300,
            )
        ),
    )







def _update_progress(
    task,
    percent: int,
    message: str,
    *,
    check_revocation: bool = True,
):
    """Aggiorna il progresso del task su PostgreSQL.

    Stato sempre 'PROGRESS' (mai 'FAILURE') per non far interpretare il task
    come terminato; il frontend determina lo stato reale da messaggio e
    percentuale (percent negativo = errore).

    La scrittura va solo su PG via report_task_progress (keyed sul task_id),
    che e' la fonte di verita' per il progress consumato dalla UI.
    """
    report_task_progress(
        getattr(getattr(task, "request", None), "id", None),
        percent,
        message,
        is_error=percent < 0,
        name="carica.execute",
        check_revocation=check_revocation,
    )


def _load_upstream_data(
    project_id: str,
    canvas: JsonObject,
    force_recalculate: bool,
    output_format: str,
    *,
    expected_group_id: str,
    execution_contract: CaricaExecutionContract | None = None,
) -> Optional[Any]:
    """Carica il DataFrame dal nodo Result upstream.

    Strategia:
    1. Cerca nel DB PostgreSQL (via SourceLoader, fail-closed sul group_id)
    2. Se force_recalculate o non trova → riesegui la pipeline
    3. Dopo ricalcolo rilegge l'artefatto specifico della run
    """
    try:
        # Pre-cache check: il tenant viene dal contratto accodato, non da un
        # secondo lookup che potrebbe osservare uno stato progetto diverso.
        if not force_recalculate:
            try:
                df = SourceLoader().load(
                    SourceRef(project=LatestProjectArtifactRef(project_id=project_id)),
                    expected_group_id=expected_group_id,
                )
            except SourceLoadingError as _sle:
                logger.error(f"[SECURITY] CARICA pre-cache: {_sle}")
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

        if execution_contract is not None:
            result = orchestrator.execute_pipeline_from_contract(
                contract=execution_contract.to_pipeline_contract(),
                force_recalculate=force_recalculate,
                skip_carica=True,
            )
        else:
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


# Nome registrato del task.
# Soddisfa il protocol DelayTask.name usato da submit_task per il dispatch
# by-name e combacia con l'actor Dramatiq in workers/dramatiq_app.py.
execute_carica_task.name = "carica.execute"
