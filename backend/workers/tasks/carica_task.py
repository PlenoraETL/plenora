"""
Task Celery per il modulo CARICA — esecuzione asincrona con progresso Redis.

Runs background execution while preserving backend runtime contracts.
"""

import time as _time
import traceback
import uuid as _uuid
from pathlib import PurePosixPath
from typing import Any, Mapping, Optional, cast
from typing_extensions import TypedDict

from app.shared.contracts.payloads import HeadersMap, JsonObject
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.config import settings
from app.core.carica.target_lock import (
    CaricaTargetLockHandle,
    acquire_carica_target_lock,
    release_carica_target_lock,
    resolve_carica_target_lock,
)

from workers.celery_app import celery_app, BaseTaskWithRetry


logger = get_logger("workers.carica_task")


def create_default_arcgis_feature_writer() -> Any:
    """Lazy wrapper kept patchable without importing ArcGIS services at registration."""
    from app.core.carica.arcgis_write_boundary import (
        create_default_arcgis_feature_writer as _create_default,
    )

    return _create_default()


def execute_carica_arcgis_write(**kwargs: Any) -> Any:
    """Lazy wrapper kept patchable without importing ArcGIS services at registration."""
    from app.core.carica.arcgis_write_boundary import (
        execute_carica_arcgis_write as _execute_boundary,
    )

    return _execute_boundary(**kwargs)


class CaricaTaskResultPayload(TypedDict, total=False):
    status: str
    success: bool
    message: str
    error: str
    error_message: str
    project_id: str
    task_id: str
    run_id: str
    result_id: str
    output_file: str
    output_filename: str
    rows_written: int
    rows_count: int
    columns_count: int
    total_rows: int


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
        from app.core.intermediate.run_idempotency import is_already_completed
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

    from app.core.auth.async_user_recheck import recheck_user_authorized
    _revoked = recheck_user_authorized(
        user_id=user_id, project_id=project_id,
        task_id=task_id, run_id=run_id, task_name="CARICA",
    )
    if _revoked is not None:
        _revoked.update({"run_id": run_id, "task_id": task_id, "rows_written": 0})
        return cast(CaricaTaskResultPayload, _revoked)

    _group_id = ""
    try:
        from app.core.projects.service import ProjectService as _PS
        _meta = _PS().get_project_metadata(project_id)
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

    target_lock_handle: CaricaTargetLockHandle | None = None
    try:
        write_branch = _select_carica_write_branch(carica_node_data)
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
            result = _execute_datasource_write(self, df, carica_node_data, project_id, task_id)
        elif write_branch == 'rest':
            result = _execute_rest_write(self, df, carica_node_data, project_id, task_id)
        elif write_branch == 'arcgis':
            result = _execute_arcgis_write(self, df, carica_node_data, project_id, task_id)
        else:
            result = _execute_db_write(self, df, carica_node_data, project_id, task_id, triggered_by, run_id)

        is_success = result.get("success", False)
        rows_written = result.get("rows_written", 0) or 0
        elapsed_ms = int((_time.time() - _run_start) * 1000)

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
            from app.utils.debug_payload_builder import DebugPayloadBuilder
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
            from app.core.intermediate.cleanup import close_worker_connection
            close_worker_connection()
        except Exception as e:
            logger.debug(f"close_connection intermediate fallito: {type(e).__name__}: {e}")
        try:
            from app.db.pg_connection import close_connection as _close_cfg
            _close_cfg()
        except Exception as e:
            logger.debug(f"close_connection config fallito: {type(e).__name__}: {e}")


def _select_carica_write_branch(carica_node_data: JsonObject) -> str:
    """Return the write branch for a CARICA node without executing writes."""
    node_type = carica_node_data.get('type', '')
    dest_type = carica_node_data.get('dest_type', 'db')

    if node_type == 'datasource_node':
        return 'datasource'
    if dest_type == 'rest':
        return 'rest'
    if dest_type == 'arcgis' or node_type == 'carica_arcgis':
        return 'arcgis'
    return 'db'


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


def _execute_datasource_write(task, df, carica_node_data, project_id, task_id):
    """Scrive il DataFrame su un DataSource (local/FTP/SFTP/SharePoint)."""
    datasource_id = carica_node_data.get('datasource_id', '')
    dest_folder = carica_node_data.get('dest_folder', '') or ''
    dest_filename = carica_node_data.get('dest_filename', '') or f'output_{project_id}'
    dest_format = carica_node_data.get('dest_format', 'csv') or 'csv'

    if not datasource_id:
        return {
            'status': 'connection_error',
            'success': False,
            'error_message': 'datasource_id mancante nel nodo',
            'rows_written': 0,
            'total_rows': len(df),
        }

    try:
        from app.core.auth.visibility import is_resource_visible
        from app.core.projects.service import ProjectService

        metadata = ProjectService().get_project_metadata(project_id)
        group_id = getattr(metadata, "group_id", None) if metadata else None
        if not group_id or not is_resource_visible(
            "datasources",
            datasource_id,
            str(group_id),
            is_superadmin=False,
        ):
            return {
                'status': 'connection_error',
                'success': False,
                'error': 'datasource_not_visible',
                'message': 'DataSource non visibile al gruppo del progetto',
                'error_message': 'DataSource non visibile al gruppo del progetto',
                'rows_written': 0,
                'total_rows': len(df),
            }
    except Exception:
        logger.error('[SECURITY] Errore verifica visibilita DataSource', exc_info=True)
        return {
            'status': 'connection_error',
            'success': False,
            'error': 'datasource_visibility_check_failed',
            'message': 'Verifica DataSource fallita',
            'error_message': 'Verifica DataSource fallita',
            'rows_written': 0,
            'total_rows': len(df),
        }

    _update_progress(task, 20, f'Serializzazione DataFrame ({dest_format})...')

    # RAM-2 (2026-05-26): serializzazione minimal-copy via
    # `dataframe_to_export_bytes`. Sostituisce il pattern legacy
    # `df.to_csv().encode()` + `BytesIO.write()` + `getvalue()` che
    # aveva peak ~4-5x del df. Ora ~2x (df + bytes finali).
    from app.core.dataframes.export_writer import dataframe_to_export_bytes
    content = dataframe_to_export_bytes(df, dest_format)

    _update_progress(task, 50, f'Scrittura su DataSource {datasource_id}...')

    try:
        from app.core.datasources.service import DataSourceService
        ds_service = DataSourceService()

        # Componi path: dest_folder + dest_filename + .ext
        ext = f'.{dest_format}' if not dest_filename.endswith(f'.{dest_format}') else ''
        # Sanitizza per prevenire path traversal
        safe_folder = PurePosixPath(dest_folder).as_posix().replace('..', '').strip('/')
        safe_filename = PurePosixPath(dest_filename).name  # solo il nome, no directory
        path = f'{safe_folder}/{safe_filename}{ext}'.lstrip('/')

        ds_service.write_file(datasource_id, path, content)

        _update_progress(task, 100, f'Scrittura completata: {path}')
        logger.info(f'Task CARICA DS {task_id} completato: {path}')

        return {
            'status': 'success',
            'success': True,
            'rows_written': len(df),
            'rows_failed': 0,
            'total_rows': len(df),
            'datasource_id': datasource_id,
            'path': path,
            'message': f'Scritti {len(df)} righe su {path}',
        }

    except Exception as e:
        logger.error(f'[CRASH] Errore scrittura DataSource: {e}', exc_info=True)
        # intent: boundary branch DataSource write / outcome: fail-closed sanitizzato
        return {
            'status': 'connection_error',
            'success': False,
            'error': 'carica_datasource_write_failed',
            'message': 'Errore scrittura DataSource (consulta log)',
            'error_message': 'Errore scrittura DataSource (consulta log)',
            'rows_written': 0,
            'total_rows': len(df),
        }


def _execute_db_write(task, df, carica_node_data, project_id, task_id, triggered_by, run_id=None):
    """Esegue il caricamento su database (percorso originale).

    Args:
        run_id: propagato all'executor come self-defense layer (N15). Il task
            fa gia' il check idempotency a inizio prima di arrivare qui, quindi
            nel happy path il guard executor e' un no-op (ri-verifica lo stesso
            run_id). Lo passiamo per coerenza: tutti i callsite dell'executor
            opt-in al guard quando hanno un run_id.
    """
    from app.core.carica.executor import CaricaExecutor
    from app.core.connections.service import ConnectionService

    def progress_callback(batch_progress):
        overall_percent = 10 + int(batch_progress.percent * 0.85)
        _update_progress(task, overall_percent, batch_progress.message)

    # Passa connection_service per decriptare correttamente le password
    executor = CaricaExecutor(connection_service=ConnectionService())

    result = executor.execute(
        df=df,
        node_data=carica_node_data,
        project_id=project_id,
        progress_callback=progress_callback,
        triggered_by=triggered_by,
        run_id=run_id,
    )

    result_dict = result.to_dict()
    is_success = result.status.value in ('success', 'completed', 'partial_success')
    result_dict["success"] = is_success
    result_dict["rows_count"] = result.rows_written
    result_dict["columns_count"] = result_dict.get("columns_count") or len(df.columns)
    if not is_success and not result_dict.get("message"):
        result_dict["message"] = result_dict.get("error_message", f"Errore: {result.status.value}")
    
    _update_progress(task, 100 if is_success else -1, 
                     f"Completato: {result.rows_written} righe scritte" if is_success 
                     else result_dict.get("error_message", "Errore"))
    
    logger.info(f"Task CARICA DB {task_id} completato: {result.status.value}")
    return result_dict


def _execute_rest_write(
    task: Any,
    df: Any,
    carica_node_data: JsonObject,
    project_id: str,
    task_id: str,
) -> CaricaTaskResultPayload:
    """Coordinate REST write progress while the domain owns REST payload writes."""
    _update_progress(task, 15, "Preparazione scrittura REST...")

    try:
        from app.core.carica.rest_config_parser import parse_carica_rest_config
        from app.core.carica.rest_write_boundary import execute_carica_rest_write

        parsed_config = parse_carica_rest_config(carica_node_data)
        _update_progress(task, 25, "Scrittura REST...")
        outcome = execute_carica_rest_write(
            df=df,
            config=parsed_config.sink_config,
            http_client=create_default_rest_http_client(),
        )
        result = cast(CaricaTaskResultPayload, outcome.to_task_result())

        is_success = result.get("success", False)
        rows = result.get("rows_written", 0) or 0
        if not is_success:
            _update_progress(
                task,
                -1,
                result.get("error_message") or result.get("message") or "Errore scrittura REST",
            )
            return result

        _update_progress(task, 100, f"REST completato: {rows} righe scritte")
        logger.info(f"Task CARICA REST {task_id} completato: {rows} righe")
        return result

    except Exception as e:
        logger.error(f"[GESTITO] Errore scrittura REST: {e}", exc_info=True)
        return {
            "status": "write_error",
            "success": False,
            "error": "carica_rest_write_failed",
            "message": "Errore scrittura REST (consulta log)",
            "error_message": "Errore scrittura REST (consulta log)",
            "rows_written": 0,
            "total_rows": len(df),
        }


class _RequestsRestHttpClient:
    """Synchronous HTTP adapter for the Celery REST write edge."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: HeadersMap,
        json: Any,
        timeout: float,
    ) -> Any:
        import requests
        from app.core.connections.rest_sink import RestSinkHttpResponse

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        )
        response_body: Any
        try:
            response_body = response.json()
        except ValueError:
            response_body = None
        return RestSinkHttpResponse(
            status_code=response.status_code,
            body=response_body,
            text=response.text,
        )


def create_default_rest_http_client() -> _RequestsRestHttpClient:
    """Create the default HTTP client injected into the REST write boundary."""
    return _RequestsRestHttpClient()


def _execute_arcgis_write(
    task: Any,
    df: Any,
    carica_node_data: JsonObject,
    project_id: str,
    task_id: str,
) -> Mapping[str, object]:
    """Coordinate ArcGIS write progress while the domain owns provider writes."""
    from app.core.carica.arcgis_write_boundary import CaricaArcGISWriteRequest

    _update_progress(task, 15, "Preparazione scrittura ArcGIS...")

    operation = carica_node_data.get("arcgis_operation", "add")
    request = CaricaArcGISWriteRequest(
        project_id=project_id,
        connection_id=carica_node_data.get("arcgis_connection_id", ""),
        operation=operation,
        geometry_column=carica_node_data.get("geometry_column", "geometry"),
        source_srid=carica_node_data.get("source_srid"),
        batch_size=carica_node_data.get("batch_size"),
        column_mappings=carica_node_data.get("column_mappings", []),
        layer_url=carica_node_data.get("arcgis_layer_url", ""),
        match_column=carica_node_data.get("arcgis_match_column", ""),
        service_name=carica_node_data.get("arcgis_new_service_name", ""),
        layer_name=carica_node_data.get("arcgis_new_layer_name", "Layer 0"),
        folder_id=carica_node_data.get("arcgis_folder_id", ""),
    )

    _update_progress(task, 25, f"Scrittura ArcGIS ({operation})...")

    try:
        if operation == "create_service":
            _update_progress(task, 30, f"Creazione servizio '{request.service_name}'...")
        else:
            _update_progress(task, 30, f"Scrittura features ({operation})...")

        outcome = execute_carica_arcgis_write(
            df=df,
            request=request,
            feature_writer=create_default_arcgis_feature_writer(),
        )
        result = outcome.to_task_result()
        rows = outcome.rows_written

        if not outcome.success:
            _update_progress(task, -1, outcome.error_message or outcome.message)
            return result

        _update_progress(task, 100, f"ArcGIS completato: {rows} features")
        logger.info(f"Task CARICA ArcGIS {task_id} completato: {rows} features")
        return result

    except Exception as e:
        # Canale admin log: preserva str(e) per postmortem
        logger.error(f"[GESTITO] Errore scrittura ArcGIS: {e}", exc_info=True)
        # intent: boundary branch ArcGIS write / outcome: fail-closed sanitizzato
        return {
            "status": "write_error",
            "success": False,
            "error": "carica_arcgis_write_failed",
            "message": "Errore scrittura ArcGIS (consulta log)",
            "error_message": "Errore scrittura ArcGIS (consulta log)",
            "rows_written": 0,
            "total_rows": len(df),
        }


def _update_progress(task, percent: int, message: str):
    """Aggiorna il progresso del task in Redis.

    Usa sempre lo stato 'PROGRESS' (mai 'FAILURE') per evitare che Celery
    interpreti il task come terminato. Il frontend determina lo stato
    reale dal contenuto del messaggio e dalla percentuale.

    Un percent negativo indica errore.
    """
    try:
        task.update_state(
            state='PROGRESS',
            meta={
                'progress': max(0, percent),
                'message': message,
                'is_error': percent < 0,
            }
        )
    except Exception as e:
        logger.warning(f"Errore aggiornamento progresso: {e}")


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
        from app.core.dataframes.frame_io import load_dataframe_from_file
        from app.core.artifacts import (
            LatestProjectArtifactRef,
            SourceLoader,
            SourceLoadingError,
            SourceRef,
        )

        # Pre-cache check: se il blob e' gia' presente, salta execute_pipeline
        # Il group_id viene letto dal progetto per chiudere il check
        # cross-tenant del resolver (defense-in-depth: il task CARICA
        # opera su un project_id arrivato dal payload Celery, garantito
        # dal layer HTTP a monte ma comunque verificato qui
        if not force_recalculate:
            try:
                from app.core.projects.runtime_context import (
                    get_project_runtime_context,
                )
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

        from app.runtime.services import build_orchestrator_stack

        # Runtime composition owns the executor graph. CARICA must not rely on
        # OrchestratorService fallback builders, otherwise the worker path
        # diverges from the API/data-plane contract wiring.
        orchestrator_stack = build_orchestrator_stack(
            settings=settings,
        )
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
        from app.core.artifacts import (
            ArtifactResolver,
            ArtifactResolutionError,
            PipelineArtifact,
        )

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
