"""
Router API per il modulo Scheduler URBY-ETL.

Architettura
============
Espone gli endpoint REST per gestire gli schedule automatici delle pipeline.

Endpoints (8 totali):
- GET    /api/schedules                → lista tutti gli schedule
- POST   /api/schedules               → crea nuovo schedule (dal wizard)
- GET    /api/schedules/healthcheck    → stato Celery Beat
- GET    /api/schedules/{id}           → dettaglio schedule
- PUT    /api/schedules/{id}           → modifica schedule (wizard precompilato)
- DELETE /api/schedules/{id}           → elimina schedule + log
- PATCH  /api/schedules/{id}/toggle    → abilita/disabilita
- POST   /api/schedules/{id}/run       → esegui ora (manuale)
- GET    /api/schedules/{id}/logs      → log esecuzioni (paginati)
- POST   /api/schedules/check-orphans  → verifica schedule orfani

Flusso wizard:
1. Frontend compila 6 step → POST /api/schedules con ScheduleCreate
2. Backend crea JSON + registra in RedBeat → ritorna ScheduleConfig
3. Frontend mostra nella lista → polling stato via GET /{id}

Registrazione: montato in main.py come:
    app.include_router(schedules_router)
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from app.utils.logger import get_logger
from app.core.auth.auth import get_current_user, CurrentUser, group_filter
import app.db.db_service as db

logger = get_logger(__name__)

from app.core.scheduler.models import (
    ScheduleConfig,
    ScheduleCreate,
    ScheduleUpdate,
    ScheduleList,
    ScheduleToggle,
    ScheduleLogList,
    BeatHealthcheck,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


# ==================== HEALTHCHECK (prima delle route con {id}) ====================

@router.get("/healthcheck", response_model=BeatHealthcheck)
async def beat_healthcheck():
    """
    Verifica se Celery Beat (UrbyBeatScheduler) è attivo.
    Legge il heartbeat dalla tabella beat_heartbeat in SQLite.
    """
    try:
        from datetime import datetime, timezone
        import app.db.db_service as db

        row = db.fetchone("SELECT ts FROM beat_heartbeat WHERE id = 'beat'")

        if row and row["ts"]:
            beat_time = datetime.fromisoformat(row["ts"])
            if beat_time.tzinfo is None:
                beat_time = beat_time.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - beat_time).total_seconds()

            # Conta schedule attivi
            schedules_count = db.fetchone("SELECT COUNT(*) as n FROM schedules WHERE is_enabled = 1")["n"]

            if age < 180:
                return BeatHealthcheck(
                    status="ok",
                    last_beat=beat_time,
                    message=f"Beat attivo ({int(age)}s fa) — {schedules_count} schedule attivi"
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


# ==================== LISTA + CREAZIONE ====================

@router.get("", response_model=ScheduleList)
async def list_schedules(project_id: Optional[str] = Query(None, description="Filtra per progetto"), current_user: CurrentUser = Depends(get_current_user)):
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
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.post("", response_model=ScheduleConfig)
async def create_schedule(data: ScheduleCreate, current_user: CurrentUser = Depends(get_current_user)):
    """
    Crea un nuovo schedule dai dati del wizard (6 step).
    
    Verifica che il progetto esista e ne legge nome e tipo.
    """
    from app.core.scheduler.schedule_service import ScheduleService
    from app.services.project_service import ProjectService
    
    try:
        project_service = ProjectService()
        schedule_service = ScheduleService()
        
        # Verifica progetto
        metadata = project_service.get_project_metadata(data.project_id, group_id=current_user.group_id)
        if not metadata:
            raise HTTPException(
                status_code=404,
                detail=f"Progetto '{data.project_id}' non trovato"
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
            group_id=current_user.group_id  # sempre il gruppo dell'utente che crea
        )
        
        return schedule
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore creazione schedule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


# ==================== SINGOLO SCHEDULE ====================

@router.get("/{schedule_id}", response_model=ScheduleConfig)
async def get_schedule(schedule_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Dettaglio di uno schedule specifico."""
    from app.core.scheduler.schedule_service import ScheduleService
    try:
        service = ScheduleService()
        row = db.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        if current_user.role != "superadmin" and row.get('group_id') != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")
        schedule = service.get_schedule(schedule_id)
        return schedule
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore lettura schedule {schedule_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.put("/{schedule_id}", response_model=ScheduleConfig)
async def update_schedule(schedule_id: str, data: ScheduleUpdate, current_user: CurrentUser = Depends(get_current_user)):
    """
    Modifica uno schedule esistente (wizard precompilato).
    
    Solo i campi non-null vengono aggiornati.
    """
    from app.core.scheduler.schedule_service import ScheduleService
    
    try:
        service = ScheduleService()
        
        # Verifica ownership
        row = db.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        if current_user.role != "superadmin" and row.get('group_id') != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")
        
        # Se cambia progetto, verifica che esista
        if data.project_id:
            from app.services.project_service import ProjectService
            project_service = ProjectService()
            if not project_service.project_exists(data.project_id):
                raise HTTPException(
                    status_code=404,
                    detail=f"Progetto '{data.project_id}' non trovato"
                )
        
        schedule = service.update_schedule(schedule_id, data)
        
        if not schedule:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        
        return schedule
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore modifica schedule {schedule_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Elimina uno schedule e i suoi log."""
    from app.core.scheduler.schedule_service import ScheduleService
    try:
        service = ScheduleService()
        row = db.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        if current_user.role != "superadmin" and row.get('group_id') != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")
        service.delete_schedule(schedule_id)
        return {"status": "deleted", "schedule_id": schedule_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore eliminazione schedule {schedule_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


# ==================== TOGGLE + RUN MANUALE ====================

@router.patch("/{schedule_id}/toggle", response_model=ScheduleConfig)
async def toggle_schedule(schedule_id: str, data: ScheduleToggle, current_user: CurrentUser = Depends(get_current_user)):
    """Abilita o disabilita uno schedule."""
    from app.core.scheduler.schedule_service import ScheduleService
    try:
        service = ScheduleService()
        row = db.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        if current_user.role != "superadmin" and row.get('group_id') != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")
        # Verifica prima se esiste
        existing = service.get_schedule(schedule_id)
        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"Schedule '{schedule_id}' non trovato"
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
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.post("/{schedule_id}/run")
async def run_schedule_now(schedule_id: str, current_user: CurrentUser = Depends(get_current_user)):
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
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
    
    try:
        row = db.get_schedule(schedule_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
        if current_user.role != "superadmin" and row.get('group_id') != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")
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
        raise HTTPException(
            status_code=500,
            detail="Errore interno del server"
        )


# ==================== DOWNLOAD OUTPUT ====================

@router.get("/{schedule_id}/download")
async def download_schedule_output(
    schedule_id: str,
    run: str = Query(None, description="Timestamp specifico (YYYYMMDD_HHMMSS). Se omesso, scarica l'ultimo.")
):
    """
    Scarica il file output di uno schedule.
    
    - Senza parametri: scarica l'ultima esecuzione (file più recente in runs/)
    - Con ?run=20260208_104600: scarica una specifica esecuzione
    
    Se non ci sono runs archiviati, scarica il file dalla cartella base del progetto.
    """
    from app.core.scheduler.schedule_service import ScheduleService
    from app.config import settings
    from fastapi.responses import FileResponse
    import zipfile
    try:
        from app.db import results_db_service
        import io
        from fastapi.responses import StreamingResponse as _SR

        service = ScheduleService()
        schedule = service.get_schedule(schedule_id)

        if not schedule:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")

        project_id = schedule.pipeline.project_id

        # FIX: leggi da results.db invece del filesystem legacy
        result_meta = results_db_service.get_latest_result(project_id)
        if not result_meta:
            raise HTTPException(status_code=404, detail="Nessun risultato disponibile per questo schedule")

        result_data = results_db_service.get_result_data(result_meta["id"])
        if not result_data:
            raise HTTPException(status_code=404, detail="Dati risultato non disponibili")

        filename = result_meta.get("filename", f"{project_id}_output.csv")
        fmt = result_meta.get("format", "csv")
        media_types = {
            "csv":     "text/csv",
            "gpkg":    "application/geopackage+sqlite3",
            "geojson": "application/geo+json",
            "xlsx":    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        media_type = media_types.get(fmt, "application/octet-stream")

        return _SR(
            io.BytesIO(result_data),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore download output schedule {schedule_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@router.get("/{schedule_id}/runs")
async def list_schedule_runs(schedule_id: str):
    """
    Lista tutte le esecuzioni archiviate di uno schedule.
    
    Ritorna i timestamp delle esecuzioni con i file disponibili.
    """
    from app.core.scheduler.schedule_service import ScheduleService
    from app.config import settings
    try:
        service = ScheduleService()
        schedule = service.get_schedule(schedule_id)

        if not schedule:
            raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")

        project_id = schedule.pipeline.project_id
        schedule_short = schedule_id.replace("schedule_", "")[:12]
        runs_dir = settings.RESULTS_DIR / project_id / "runs" / schedule_short

        if not runs_dir.exists():
            return {"schedule_id": schedule_id, "runs": []}

        runs = []
        for run_folder in sorted(runs_dir.iterdir(), reverse=True):
            if run_folder.is_dir():
                files = [
                    {
                        "name": f.name,
                        "size_bytes": f.stat().st_size,
                        "size_human": _format_size(f.stat().st_size)
                    }
                    for f in run_folder.iterdir() if f.is_file()
                ]
                runs.append({
                    "timestamp": run_folder.name,
                    "files": files,
                    "total_size": sum(f["size_bytes"] for f in files)
                })
    
        return {
            "schedule_id": schedule_id,
            "project_id": project_id,
            "runs_count": len(runs),
            "runs": runs
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista runs schedule {schedule_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


def _format_size(size_bytes: int) -> str:
    """Formatta byte in stringa leggibile."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ==================== LOG ESECUZIONI ====================

@router.get("/{schedule_id}/logs", response_model=ScheduleLogList)
async def get_schedule_logs(
    schedule_id: str,
    page: int = Query(1, ge=1, description="Pagina"),
    page_size: int = Query(20, ge=1, le=100, description="Risultati per pagina"),
    status: Optional[str] = Query(None, description="Filtra per status: COMPLETED, PARTIAL, FAILED")
):
    """Log esecuzioni di uno schedule (paginati, più recenti prima)."""
    from app.core.scheduler.schedule_service import ScheduleService
    from app.core.scheduler.log_service import ScheduleLogService
    
    # Verifica schedule esiste
    schedule_service = ScheduleService()
    if not schedule_service.schedule_exists(schedule_id):
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' non trovato")
    
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
        raise HTTPException(status_code=500, detail="Errore interno del server")