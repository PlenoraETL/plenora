"""
Router API per il modulo Scheduler PLENORA-ETL.

Owns the HTTP boundary and delegates business logic to services or helpers.
"""

import json
import re
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import Response

from app.core.auth.dependencies import (
    require_admin,
    require_not_esecutore,
)
from app.core.auth.owner_dependencies import require_schedule_owner
from app.core.auth.ownership import assert_group_access
from app.core.auth.resource_access import assert_project_access
from app.core.io.http_streaming import safe_download_filename
from app.core.results import store as _results_store
from app.core.scheduler import db as scheduler_db
from app.core.scheduler.healthcheck_service import (
    _as_aware_datetime as _healthcheck_as_aware_datetime,
    get_beat_healthcheck,
)
from app.core.scheduler.log_service import ScheduleLogService
from app.core.scheduler.models import (
    BeatHealthcheck,
    ScheduleCatchupUpdate,
    ScheduleConfig,
    ScheduleCreate,
    ScheduleList,
    ScheduleLogList,
    ScheduleToggle,
    ScheduleUpdate,
)
from app.core.scheduler.runs_catalog import ScheduleRunsCatalog
from app.core.scheduler.runtime_status import RuntimeStatus
from app.core.scheduler.schedule_service import ScheduleService
from app.observability.audit_action import audit_action
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser, group_filter
from app.shared.error_envelope import raise_typed_http
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _download_result_file(
    *,
    result_id: str,
    project_id: str,
    schedule_id: str,
    group_id: str | None,
) -> Response:
    result_row = _results_store.get_result_row(result_id)
    if result_row is None or result_row.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Nessun output disponibile per lo schedule '{schedule_id}'. "
                f"Esegui lo schedule almeno una volta per generare un output."
            ),
        )
    result_bytes = _results_store.get_result_data(result_id, group_id=group_id)
    if result_bytes is None:
        raise HTTPException(
            status_code=404,
            detail=f"Blob risultato '{result_id}' non disponibile",
        )
    filename = safe_download_filename(
        result_row.filename or f"{schedule_id}.bin",
        fallback="result.bin",
    )
    return Response(
        content=result_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _as_aware_datetime(value: object):
    return _healthcheck_as_aware_datetime(value)


@router.get("/healthcheck", response_model=BeatHealthcheck)
def beat_healthcheck(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Verifica se Celery Beat (PlenoraBeatScheduler) e' attivo."""
    return get_beat_healthcheck()


@router.get("", response_model=ScheduleList)
def list_schedules(
    project_id: Optional[str] = Query(None, description="Filtra per progetto"),
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Lista tutti gli schedule, ordinati per data aggiornamento.
    
    Query params:
        project_id: Filtra per progetto specifico (opzionale)
    """
    try:
        service = ScheduleService()
        gid = group_filter(current_user)
        return service.list_schedules(
            project_id=project_id,
            group_id=gid,
            limit=limit,
            offset=offset,
        )
        
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista schedule: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("", response_model=ScheduleConfig, status_code=201)
@audit_action(action="schedule.create")
def create_schedule(data: ScheduleCreate, current_user: CurrentUser = Depends(require_admin)):
    """
    Crea un nuovo schedule dai dati del wizard (6 step). Solo admin/superadmin.
    
    Verifica che il progetto esista e ne legge nome e tipo.
    """
    try:
        schedule_service = ScheduleService()

        return schedule_service.create_schedule_for_user(
            schedule_data=data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore creazione schedule: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}", response_model=ScheduleConfig)
def get_schedule(schedule_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Dettaglio di uno schedule specifico."""
    try:
        service = ScheduleService()
        row = scheduler_db.get_schedule_row(schedule_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )
        assert_group_access(row, current_user, f"Schedule '{schedule_id}'")
        schedule = service.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )
        return schedule
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore lettura schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.put("/{schedule_id}", response_model=ScheduleConfig)
@audit_action(action="schedule.update", target_param="schedule_id")
def update_schedule(schedule_id: str, data: ScheduleUpdate, current_user: CurrentUser = Depends(require_schedule_owner)):
    """
    Modifica uno schedule esistente (wizard precompilato).

    Solo owner o admin/superadmin. Solo i campi non-null vengono aggiornati.
    """
    try:
        service = ScheduleService()

        # Se cambia progetto, verifica che esista E appartenga al gruppo
        # dell'utente (altrimenti un owner di uno schedule del gruppo A
        # puo' ricollegarlo a un progetto del gruppo B
        if data.project_id:
            assert_project_access(data.project_id, current_user)
        
        schedule = service.update_schedule(schedule_id, data)
        
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )
        
        return schedule
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore modifica schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.delete("/{schedule_id}", status_code=204)
@audit_action(action="schedule.delete", target_param="schedule_id")
def delete_schedule(schedule_id: str, current_user: CurrentUser = Depends(require_schedule_owner)):
    """Elimina uno schedule e i suoi log. Solo owner o admin/superadmin."""
    try:
        service = ScheduleService()
        service.delete_schedule(schedule_id)
        # DELETE risponde 204 No Content, body vuoto.
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore eliminazione schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.patch("/{schedule_id}/toggle", response_model=ScheduleConfig)
@audit_action(action="schedule.toggle", target_param="schedule_id")
def toggle_schedule(schedule_id: str, data: ScheduleToggle, current_user: CurrentUser = Depends(require_schedule_owner)):
    """Abilita o disabilita uno schedule. Solo owner o admin/superadmin."""
    try:
        service = ScheduleService()
        # Verifica prima se esiste
        existing = service.get_schedule(schedule_id)
        if not existing:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )
        schedule = service.toggle_schedule(schedule_id, data.enabled)
        if not schedule:
            raise HTTPException(
                status_code=400,
                detail=f"Impossibile modificare schedule '{schedule_id}' (schedule orfano non può essere abilitato)"
            )
        return schedule
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore toggle schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.patch("/{schedule_id}/catchup", response_model=ScheduleConfig)
@audit_action(action="schedule.catchup", target_param="schedule_id")
def update_schedule_catchup(
    schedule_id: str,
    data: ScheduleCatchupUpdate,
    current_user: CurrentUser = Depends(require_schedule_owner),
):
    """Abilita/disabilita il catchup dei missed run per uno schedule
    e/o modifica la grace window. Solo owner o admin/superadmin."""
    try:
        service = ScheduleService()
        result = service.update_catchup_config(
            schedule_id, enabled=data.enabled, grace_hours=data.grace_hours,
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore update catchup {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/{schedule_id}/run")
@audit_action(action="schedule.run_now", target_param="schedule_id")
def run_schedule_now(schedule_id: str, current_user: CurrentUser = Depends(require_schedule_owner)):
    """
    Esegui uno schedule immediatamente (manuale).
    
    1. Setta subito runtime.status = "queued" (il frontend lo vede al primo poll)
    2. Invia task a Celery
    3. Salva task_id nel runtime
    """
    service = ScheduleService()
    schedule = service.get_schedule(schedule_id)
    
    if not schedule:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.SCHEDULE_NOT_FOUND,
                "message": f"Schedule '{schedule_id}' non trovato",
            },
        )
    
    try:
        # Runtime status via enum; il payload JSON resta plain string per FE.
        service.set_runtime_status(schedule_id, status=RuntimeStatus.QUEUED)

        from app.core.scheduler.job_dispatcher import enqueue_schedule_run

        result = enqueue_schedule_run(
            schedule_id,
            triggered_by="manual",
            retry_attempt=0,
        )

        # Salva task_id per tracking
        service.set_runtime_status(
            schedule_id, status=RuntimeStatus.QUEUED, current_task_id=result.id
        )

        return {
            "status": "queued",
            "schedule_id": schedule_id,
            "task_id": result.id,
            "message": "Esecuzione manuale avviata"
        }

    except Exception as e:
        service.set_runtime_status(schedule_id, status=RuntimeStatus.IDLE)
        logger.error(f"[GESTITO] Errore invio task manuale {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}/download")
def download_schedule_output(
    schedule_id: str,
    run: str = Query(None, description="Timestamp specifico (YYYYMMDD_HHMMSS). Se omesso, scarica l'ultimo."),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Scarica il file output di uno schedule.

    - Senza parametri: scarica l'ultima esecuzione (file piu' recente in runs/)
    - Con ?run=20260208_104600: scarica una specifica esecuzione

    Logica core delegata a `ScheduleRunsCatalog` (R#5 Step B); qui restano
    solo: ACL HTTP (group_access), traduzione eccezioni in HTTPException,
    costruzione FileResponse.
    """
    try:
        service = ScheduleService()
        schedule = service.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )

        # SECURITY: verifica che lo schedule appartenga al gruppo dell'utente
        _sched_row = scheduler_db.get_schedule_row(schedule_id)
        if _sched_row:
            assert_group_access(_sched_row, current_user, f"Schedule '{schedule_id}'")

        project_id = schedule.pipeline.project_id
        catalog = ScheduleRunsCatalog()

        # Branch 1: run specifica via ?run=... — sanitize + lookup nel catalog
        if run:
            if not re.fullmatch(r"\d{8}_\d{6}", run):
                raise HTTPException(status_code=400, detail="Parametro 'run' non valido")
            try:
                selected_run = catalog.get_run(project_id, schedule_id, run)
            except ValueError:
                raise HTTPException(status_code=400, detail="Parametro 'run' non valido")
            except FileNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Run '{run}' non trovata per schedule '{schedule_id}'",
                )
            first_run_file = selected_run.files[0] if selected_run.files else None
            if first_run_file is not None and first_run_file.result_id:
                return _download_result_file(
                    result_id=first_run_file.result_id,
                    project_id=project_id,
                    schedule_id=schedule_id,
                    group_id=getattr(_sched_row, "group_id", None),
                )
            # Post-cleanup: i run nuovi passano via `result_id` -> DB/S3.
                # Le run vecchie su filesystem archive (`RESULTS_DIR/.../runs/`)
            # non sono piu' supportate dal download endpoint.
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Nessun result_id per la run '{run}' "
                    f"(archive filesystem non piu' supportato)"
                ),
            )

        # Branch 2: senza parametro `run` -> latest run di QUESTO
        # schedule via DB log + result_id.
        latest_run = catalog.latest_run(project_id, schedule_id)
        first_run_file = latest_run.files[0] if latest_run and latest_run.files else None
        if first_run_file is not None and first_run_file.result_id:
            return _download_result_file(
                result_id=first_run_file.result_id,
                project_id=project_id,
                schedule_id=schedule_id,
                group_id=getattr(_sched_row, "group_id", None),
            )

        # Blob-only schedules record the exact result_id in the schedule log.
        # Do not fall back to "latest project result": that can cross schedules.
        latest_logs = scheduler_db.list_schedule_logs(
            schedule_id=schedule_id,
            events=("PIPELINE_COMPLETED", "PIPELINE_PARTIAL"),
            limit=1,
        )
        execution_info = latest_logs[0].get("execution_info") if latest_logs else None
        if isinstance(execution_info, str):
            try:
                execution_info = json.loads(execution_info)
            except json.JSONDecodeError:
                execution_info = None
        result_id = (
            execution_info.get("result_id")
            if isinstance(execution_info, dict)
            else None
        )
        if not result_id:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Nessun file archiviato per lo schedule '{schedule_id}'. "
                    f"Le run precedenti non espongono un result_id scaricabile."
                ),
            )

        return _download_result_file(
            result_id=result_id,
            project_id=project_id,
            schedule_id=schedule_id,
            group_id=getattr(_sched_row, "group_id", None),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore download output schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}/runs")
def list_schedule_runs(
    schedule_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Lista tutte le esecuzioni archiviate di uno schedule.

    Ritorna i timestamp delle esecuzioni con i file disponibili.
    Logica core delegata a `ScheduleRunsCatalog` (R#5 Step B).
    """
    try:
        service = ScheduleService()
        schedule = service.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.SCHEDULE_NOT_FOUND,
                    "message": f"Schedule '{schedule_id}' non trovato",
                },
            )

        # SECURITY: group isolation check
        _sched_row = scheduler_db.get_schedule_row(schedule_id)
        if _sched_row:
            assert_group_access(_sched_row, current_user, f"Schedule '{schedule_id}'")

        project_id = schedule.pipeline.project_id
        catalog = ScheduleRunsCatalog()
        runs = catalog.list_runs(
            project_id, schedule_id, limit=limit, offset=offset,
        )

        # Serializza in JSON HTTP-friendly. `_format_size` resta nel router
        # perche' e' un dettaglio di presentazione, non logica core
        return {
            "schedule_id": schedule_id,
            "project_id": project_id,
            "runs_count": len(runs),
            "runs": [
                {
                    "timestamp": r.timestamp,
                    "files": [
                        {
                            "name": f.name,
                            "size_bytes": f.size_bytes,
                            "size_human": _format_size(f.size_bytes),
                        }
                        for f in r.files
                    ],
                    "total_size": r.total_size_bytes,
                }
                for r in runs
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista runs schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


def _format_size(size_bytes: int) -> str:
    """Formatta byte in stringa leggibile."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


@router.get("/{schedule_id}/logs", response_model=ScheduleLogList)
def get_schedule_logs(
    schedule_id: str,
    page: int = Query(1, ge=1, description="Pagina"),
    page_size: int = Query(20, ge=1, le=100, description="Risultati per pagina"),
    status: Optional[str] = Query(None, description="Filtra per status: COMPLETED, PARTIAL, FAILED"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Log esecuzioni di uno schedule (paginati, più recenti prima)."""
    # Verifica schedule esiste
    schedule_service = ScheduleService()
    if not schedule_service.schedule_exists(schedule_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.SCHEDULE_NOT_FOUND,
                "message": f"Schedule '{schedule_id}' non trovato",
            },
        )
    
    try:
        log_service = ScheduleLogService()
        return log_service.get_logs(
            schedule_id=schedule_id,
            page=page,
            page_size=page_size,
            status_filter=status
        )
        
    except Exception as e:
        logger.error(f"[GESTITO] Errore lettura log {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
