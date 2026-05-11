"""
Router API per il modulo Scheduler PLENORA-ETL.

Owns the HTTP boundary and delegates business logic to services or helpers.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime, timezone
from typing import Optional
from app.config import settings
from app.db import pg_connection as db
from app.utils.logger import get_logger
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_admin,
)
from app.core.auth.owner_dependencies import require_schedule_owner
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import assert_group_access
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.observability.audit_action import audit_action
from app.core.auth.resource_access import assert_project_access
from app.core.scheduler import db as scheduler_db

logger = get_logger(__name__)

from app.core.scheduler.models import (
    ScheduleConfig,
    ScheduleCreate,
    ScheduleUpdate,
    ScheduleList,
    ScheduleToggle,
    ScheduleCatchupUpdate,
    ScheduleLogList,
    BeatHealthcheck,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _as_aware_datetime(value: object) -> "datetime":
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        raise TypeError(f"expected datetime or ISO string, got {type(value).__name__}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/healthcheck", response_model=BeatHealthcheck)
async def beat_healthcheck(current_user: CurrentUser = Depends(require_not_esecutore)):
    """
    Verifica se Celery Beat (PlenoraBeatScheduler) è attivo.
    Legge il heartbeat dalla tabella beat_heartbeat nel DB.
    """
    try:
        row = db.fetchone("SELECT ts FROM beat_heartbeat WHERE id = 'beat'")

        if row and row["ts"]:
            beat_time = _as_aware_datetime(row["ts"])
            age = (datetime.now(timezone.utc) - beat_time).total_seconds()

            # Conta schedule attivi
            schedules_count = db.fetchone("SELECT COUNT(*) as n FROM schedules WHERE is_enabled = TRUE")["n"]

            if age < 180:
                # Check task bloccate
                hard_limit = settings.CELERY_SCHEDULE_HARD_LIMIT
                stuck_count = db.fetchone(
                    "SELECT COUNT(*) as n FROM schedules "
                    "WHERE last_status = 'running' "
                    "AND last_run IS NOT NULL "
                    "AND last_run < NOW() - INTERVAL '%s seconds'",
                    (hard_limit,)
                )["n"]
                stuck_msg = f" — {stuck_count} task bloccate" if stuck_count > 0 else ""
                beat_status = "warning" if stuck_count > 0 else "ok"
                return BeatHealthcheck(
                    status=beat_status,
                    last_beat=beat_time,
                    message=f"Beat attivo ({int(age)}s fa) — {schedules_count} schedule attivi{stuck_msg}"
                )
            else:
                return BeatHealthcheck(
                    status="down",
                    last_beat=beat_time,
                    message=f"Beat non risponde da {int(age)}s"
                )

        return BeatHealthcheck(
            status="unknown",
            message="Nessun heartbeat trovato. Attendi ~60s dopo l'avvio di Beat."
        )

    except Exception as e:
        logger.error(f"[GESTITO] Errore healthcheck Beat: {e}", exc_info=True)
        return BeatHealthcheck(
            status="down",
            message=f"Errore: {str(e)}"
        )


@router.get("", response_model=ScheduleList)
async def list_schedules(project_id: Optional[str] = Query(None, description="Filtra per progetto"), current_user: CurrentUser = Depends(require_not_esecutore)):
    """
    Lista tutti gli schedule, ordinati per data aggiornamento.
    
    Query params:
        project_id: Filtra per progetto specifico (opzionale)
    """
    from app.core.scheduler.schedule_service import ScheduleService
    
    try:
        service = ScheduleService()
        gid = group_filter(current_user)
        return service.list_schedules(project_id=project_id, group_id=gid)
        
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista schedule: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("", response_model=ScheduleConfig, status_code=201)
@audit_action(action="schedule.create")
async def create_schedule(data: ScheduleCreate, current_user: CurrentUser = Depends(require_admin)):
    """
    Crea un nuovo schedule dai dati del wizard (6 step). Solo admin/superadmin.
    
    Verifica che il progetto esista e ne legge nome e tipo.
    """
    from app.core.scheduler.schedule_service import ScheduleService
    from app.core.projects.service import ProjectService
    
    try:
        project_service = ProjectService()
        schedule_service = ScheduleService()
        
        # Verifica progetto
        metadata = project_service.get_project_metadata(data.project_id, group_id=current_user.group_id)
        if not metadata:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{data.project_id}' non trovato",
                },
            )
        
        # Valida cron expression prima di creare
        try:
            from croniter import croniter
            if not croniter.is_valid(data.cron_expression):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cron expression non valida: '{data.cron_expression}'"
                )
        except ImportError:
            pass  # croniter non disponibile, skip validazione

        # Crea schedule
        schedule = schedule_service.create_schedule(
            schedule_data=data,
            project_name=metadata.name,
            project_type=metadata.project_type,
            group_id=current_user.group_id,  # sempre il gruppo dell'utente che crea
            created_by=current_user.user_id,  # owner per require_schedule_owner
        )
        
        return schedule
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore creazione schedule: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}", response_model=ScheduleConfig)
async def get_schedule(schedule_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Dettaglio di uno schedule specifico."""
    from app.core.scheduler.schedule_service import ScheduleService
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
async def update_schedule(schedule_id: str, data: ScheduleUpdate, current_user: CurrentUser = Depends(require_schedule_owner)):
    """
    Modifica uno schedule esistente (wizard precompilato).

    Solo owner o admin/superadmin. Solo i campi non-null vengono aggiornati.
    """
    from app.core.scheduler.schedule_service import ScheduleService

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
async def delete_schedule(schedule_id: str, current_user: CurrentUser = Depends(require_schedule_owner)):
    """Elimina uno schedule e i suoi log. Solo owner o admin/superadmin."""
    from app.core.scheduler.schedule_service import ScheduleService
    try:
        service = ScheduleService()
        service.delete_schedule(schedule_id)
        # F2-B1 (2026-04-24): DELETE risponde 204 No Content, body vuoto
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore eliminazione schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.patch("/{schedule_id}/toggle", response_model=ScheduleConfig)
@audit_action(action="schedule.toggle", target_param="schedule_id")
async def toggle_schedule(schedule_id: str, data: ScheduleToggle, current_user: CurrentUser = Depends(require_schedule_owner)):
    """Abilita o disabilita uno schedule. Solo owner o admin/superadmin."""
    from app.core.scheduler.schedule_service import ScheduleService
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
async def update_schedule_catchup(
    schedule_id: str,
    data: ScheduleCatchupUpdate,
    current_user: CurrentUser = Depends(require_schedule_owner),
):
    """Abilita/disabilita il catchup dei missed run per uno schedule
    e/o modifica la grace window. Solo owner o admin/superadmin."""
    from app.core.scheduler.schedule_service import ScheduleService
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
async def run_schedule_now(schedule_id: str, current_user: CurrentUser = Depends(require_schedule_owner)):
    """
    Esegui uno schedule immediatamente (manuale).
    
    1. Setta subito runtime.status = "queued" (il frontend lo vede al primo poll)
    2. Invia task a Celery
    3. Salva task_id nel runtime
    """
    from app.core.scheduler.schedule_service import ScheduleService
    
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
        # Setta subito "queued" — metodo leggero, non incrementa contatori
        service.set_runtime_status(schedule_id, status="queued")
        
        from workers.tasks.schedule_task import schedule_pipeline_task
        
        result = schedule_pipeline_task.apply_async(
            args=[schedule_id],
            kwargs={"triggered_by": "manual", "retry_attempt": 0}
        )
        
        # Salva task_id per tracking
        service.set_runtime_status(schedule_id, status="queued", current_task_id=result.id)
        
        return {
            "status": "queued",
            "schedule_id": schedule_id,
            "task_id": result.id,
            "message": "Esecuzione manuale avviata"
        }
        
    except Exception as e:
        service.set_runtime_status(schedule_id, status="idle")
        logger.error(f"[GESTITO] Errore invio task manuale {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}/download")
async def download_schedule_output(
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
    from app.core.scheduler.schedule_service import ScheduleService
    from app.core.scheduler.runs_catalog import ScheduleRunsCatalog
    from fastapi.responses import FileResponse
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
            try:
                run_dir = catalog.get_run_dir(project_id, schedule_id, run)
            except ValueError:
                raise HTTPException(status_code=400, detail="Parametro 'run' non valido")
            except FileNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail=f"Run '{run}' non trovata per schedule '{schedule_id}'",
                )
            out_file = catalog.first_file_in(run_dir)
            if out_file is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Nessun file nella run '{run}'",
                )
            return FileResponse(
                path=str(out_file),
                filename=out_file.name,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{out_file.name}"'},
            )

        # Branch 2: senza parametro `run` → latest run archiviata di
        # QUESTO schedule. Niente `get_latest_result(project_id)`, che
        # ritornerebbe l'ultimo del progetto includendo anche altri
        # schedule o esecuzioni manuali (race cross-schedule
        latest_run = catalog.latest_run_dir(project_id, schedule_id)
        out_file = catalog.first_file_in(latest_run) if latest_run else None
        if out_file is not None:
            return FileResponse(
                path=str(out_file),
                filename=out_file.name,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{out_file.name}"'},
            )

        # Fallback: post-refactor i risultati pipeline sono blob in
        # `plenora.results` (no file filesystem). L'archive scheduler
        # materializza solo quando `ArtifactResolver` riesce a risolvere
        # il ref della run; per schedule senza destination CARICA il
        # ref puo' restare vuoto e nessun file viene archiviato.
        # Senza questo fallback il download tornava 404 anche se la
        # run e' completata e il blob esiste nel DB. Servimao quindi
        # l'ultimo blob risultato del progetto (best-effort: copre il
        # caso reale singolo-schedule; per progetti con schedule
        # multipli concorrenti il caller dovrebbe passare ?run=...).
        from fastapi.responses import Response
        from app.core.results import store as _results_store

        latest_row = _results_store.get_latest_result_row(project_id)
        latest_bytes = (
            _results_store.get_result_data(latest_row.id)
            if latest_row is not None
            else None
        )
        if latest_row is None or latest_bytes is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Nessun output disponibile per lo schedule '{schedule_id}'. "
                    f"Esegui lo schedule almeno una volta per generare un output."
                ),
            )
        filename = latest_row.filename or f"{schedule_id}.bin"
        return Response(
            content=latest_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore download output schedule {schedule_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{schedule_id}/runs")
async def list_schedule_runs(schedule_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """
    Lista tutte le esecuzioni archiviate di uno schedule.

    Ritorna i timestamp delle esecuzioni con i file disponibili.
    Logica core delegata a `ScheduleRunsCatalog` (R#5 Step B).
    """
    from app.core.scheduler.schedule_service import ScheduleService
    from app.core.scheduler.runs_catalog import ScheduleRunsCatalog
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
        runs = catalog.list_runs(project_id, schedule_id)

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
async def get_schedule_logs(
    schedule_id: str,
    page: int = Query(1, ge=1, description="Pagina"),
    page_size: int = Query(20, ge=1, le=100, description="Risultati per pagina"),
    status: Optional[str] = Query(None, description="Filtra per status: COMPLETED, PARTIAL, FAILED"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Log esecuzioni di uno schedule (paginati, più recenti prima)."""
    from app.core.scheduler.schedule_service import ScheduleService
    from app.core.scheduler.log_service import ScheduleLogService
    
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
