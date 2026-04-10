"""
Servizio per la gestione dei log di esecuzione dello Scheduler.

Architettura
============
I log vengono salvati su filesystem organizzati per data:

    data/scheduler_logs/
    └── YYYY-MM-DD/
        ├── sched_{schedule_id}_{HHMMSS}.json
        ├── sched_{schedule_id}_{HHMMSS}.json
        └── ...

Ogni file JSON è un ScheduleLogEntry completo con:
    - Metadati esecuzione (schedule_id, trigger, timestamp)
    - Step della pipeline (ESTRAI, MANIPOLA, ARRICCHISCI, OUTPUT, CARICA)
    - Durata, righe processate, errori

Operazioni:
    - write_log(): scrive un nuovo log entry su filesystem
    - get_logs(): lista log per uno schedule (paginata, ordinata)
    - get_log(): singolo log entry per ID
    - delete_logs_before(): cancella log più vecchi di una data
    - get_latest_log(): ultimo log per uno schedule
"""

import json
from pathlib import Path
from datetime import datetime, date, timezone
from typing import List, Optional
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

from app.core.scheduler.models import (
    ScheduleLogEntry,
    ScheduleLogList,
    LogStep
)


# Directory per i log di esecuzione scheduler
# Usa settings.SCHEDULER_LOGS_DIR → data/scheduler_logs/
# (diverso da settings.LOGS_DIR che è ./logs per i log applicazione)


class ScheduleLogService:
    """
    Servizio per scrittura e lettura dei log di esecuzione scheduler.
    
    Ogni esecuzione produce un file JSON in data/scheduler_logs/YYYY-MM-DD/.
    Il log_id è generato come: sched_{schedule_id}_{HHMMSS}.
    
    Operazioni:
    - write: crea directory giornaliera + scrive JSON
    - list: scansiona directory, filtra per schedule_id, pagina
    - get: carica singolo log da JSON
    - delete: rimuove log prima di una data
    - latest: ultimo log per uno schedule (per aggiornare runtime)
    """
    
    def __init__(self):
        self.logs_dir = settings.SCHEDULER_LOGS_DIR
    
    def _get_day_dir(self, dt: datetime) -> Path:
        """Ottieni path directory giornaliera (YYYY-MM-DD)."""
        day_str = dt.strftime("%Y-%m-%d")
        return self.logs_dir / day_str
    
    def _generate_log_id(self, schedule_id: str, timestamp: datetime) -> str:
        """
        Genera log_id univoco: sched_{short_id}_{HHMMSS_ffffff}.

        Include i microsecondi per evitare collisioni se trigger manuale
        e trigger cron scattano nello stesso secondo sullo stesso schedule.
        """
        time_str = timestamp.strftime("%H%M%S_%f")
        # Usa solo la parte breve dello schedule_id per leggibilità
        short_id = schedule_id.replace("schedule_", "")[:12]
        return f"sched_{short_id}_{time_str}"
    
    def _find_log_file(self, log_id: str) -> Optional[Path]:
        """Cerca un log file per ID scandendo le directory giornaliere."""
        if not self.logs_dir.exists():
            return None
        for day_dir in sorted(self.logs_dir.iterdir(), reverse=True):
            if day_dir.is_dir():
                log_path = day_dir / f"{log_id}.json"
                if log_path.exists():
                    return log_path
        return None
    
    # =========================================================================
    # SCRITTURA
    # =========================================================================
    
    def write_log(self, log_entry: ScheduleLogEntry) -> str:
        """
        Scrive un log entry su filesystem.
        
        Crea la directory giornaliera se non esiste.
        Ritorna il percorso relativo del file (es. "2026-02-07/sched_abc123_030000.json").
        """
        try:
            day_dir = self._get_day_dir(log_entry.timestamp)
            day_dir.mkdir(parents=True, exist_ok=True)
            
            log_path = day_dir / f"{log_entry.log_id}.json"
            
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(log_entry.model_dump_json(indent=2))
            
            # Percorso relativo per salvarlo in runtime.last_execution.log_file
            relative_path = f"{day_dir.name}/{log_path.name}"
            
            return relative_path
            
        except Exception as e:
            logger.error(f"[CRASH] Errore scrittura log {log_entry.log_id}: {e}", exc_info=True)
            raise
    
    # =========================================================================
    # LETTURA
    # =========================================================================
    
    def get_log(self, log_id: str) -> Optional[ScheduleLogEntry]:
        """Carica un singolo log entry per ID."""
        log_path = self._find_log_file(log_id)
        if not log_path:
            return None
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                return ScheduleLogEntry.model_validate_json(f.read())
        except Exception as e:
            logger.error(f"[CRASH] Errore lettura log {log_id}: {e}", exc_info=True)
            return None
    
    def get_logs(
        self, 
        schedule_id: str, 
        page: int = 1, 
        page_size: int = 20,
        status_filter: Optional[str] = None
    ) -> ScheduleLogList:
        """
        Lista log per uno schedule, paginata e ordinata (più recenti prima).
        
        Scandisce le directory giornaliere in ordine decrescente,
        filtra per schedule_id e opzionalmente per status.
        """
        all_logs: List[ScheduleLogEntry] = []
        
        if not self.logs_dir.exists():
            return ScheduleLogList(logs=[], total=0, page=page, page_size=page_size)
        
        # Scandisci directory giornaliere in ordine decrescente
        day_dirs = sorted(
            [d for d in self.logs_dir.iterdir() if d.is_dir()],
            reverse=True
        )
        
        for day_dir in day_dirs:
            for log_file in sorted(day_dir.glob("sched_*.json"), reverse=True):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        entry = ScheduleLogEntry.model_validate_json(f.read())
                    
                    # Filtra per schedule_id
                    if entry.schedule_id != schedule_id:
                        continue
                    
                    # Filtra per status (opzionale)
                    if status_filter and entry.event not in (
                        f"PIPELINE_{status_filter.upper()}",
                    ):
                        continue
                    
                    all_logs.append(entry)
                    
                except Exception as e:
                    logger.error(f"[CRASH] Errore lettura log {log_file.name}: {e}", exc_info=True)
        
        # Paginazione
        total = len(all_logs)
        start = (page - 1) * page_size
        end = start + page_size
        page_logs = all_logs[start:end]
        
        return ScheduleLogList(
            logs=page_logs,
            total=total,
            page=page,
            page_size=page_size
        )
    
    def get_latest_log(self, schedule_id: str) -> Optional[ScheduleLogEntry]:
        """
        Ultimo log per uno schedule.
        
        Usato dall'execution_manager per aggiornare runtime.last_execution.
        """
        result = self.get_logs(schedule_id, page=1, page_size=1)
        return result.logs[0] if result.logs else None
    
    # =========================================================================
    # CANCELLAZIONE
    # =========================================================================
    
    def delete_logs_before(self, before_date: date) -> int:
        """
        Cancella tutti i log prima di una data.
        
        Rimuove intere directory giornaliere più vecchie di before_date.
        Ritorna il numero di file eliminati.
        """
        deleted_count = 0
        
        if not self.logs_dir.exists():
            return 0
        
        for day_dir in list(self.logs_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            try:
                dir_date = date.fromisoformat(day_dir.name)
                if dir_date < before_date:
                    # Conta file prima di eliminarli
                    files = list(day_dir.glob("*.json"))
                    deleted_count += len(files)
                    
                    # Elimina tutti i file nella directory
                    for f in files:
                        f.unlink()
                    
                    # Rimuovi directory vuota
                    day_dir.rmdir()
                    
                    logger.info(f"Eliminata directory log {day_dir.name} ({len(files)} file)")
                    
            except (ValueError, OSError) as e:
                logger.error(f"[CRASH] Errore eliminazione directory {day_dir.name}: {e}", exc_info=True)
        
        return deleted_count
    
    def delete_schedule_logs(self, schedule_id: str) -> int:
        """
        Cancella tutti i log di uno specifico schedule.

        Usato quando si elimina uno schedule.
        Filtra per nome file (sched_{short_id}_*) evitando di aprire
        ogni JSON — più veloce con molti log.
        """
        deleted_count = 0

        if not self.logs_dir.exists():
            return 0

        # Ricostruisce il short_id con la stessa logica di _generate_log_id
        short_id = schedule_id.replace("schedule_", "")[:12]
        pattern = f"sched_{short_id}_*.json"

        for day_dir in self.logs_dir.iterdir():
            if not day_dir.is_dir():
                continue
            for log_file in day_dir.glob(pattern):
                try:
                    log_file.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[CRASH] Errore eliminazione log {log_file.name}: {e}", exc_info=True)

            # Rimuovi directory se vuota
            try:
                if day_dir.exists() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
            except OSError:
                pass

        logger.info(f"Eliminati {deleted_count} log per schedule {schedule_id}")
        return deleted_count
    
    # =========================================================================
    # HELPER — Creazione log entry
    # =========================================================================
    
    def create_log_entry(
        self,
        schedule_id: str,
        event: str,
        triggered_by: str = "cron",
        level: str = "INFO",
        steps: Optional[List[LogStep]] = None,
        total_duration_ms: int = 0,
        error_message: Optional[str] = None,
        schedule_info: Optional[dict] = None,
        pipeline_info: Optional[dict] = None,
        execution_info: Optional[dict] = None
    ) -> ScheduleLogEntry:
        """
        Helper per creare un ScheduleLogEntry con log_id e timestamp generati.
        
        Uso tipico:
            entry = log_service.create_log_entry(
                schedule_id="schedule_abc123",
                event="PIPELINE_COMPLETED",
                steps=[...],
                total_duration_ms=71000
            )
            log_service.write_log(entry)
        """
        now = datetime.now(timezone.utc)
        log_id = self._generate_log_id(schedule_id, now)
        
        return ScheduleLogEntry(
            log_id=log_id,
            schedule_id=schedule_id,
            timestamp=now,
            level=level,
            event=event,
            triggered_by=triggered_by,
            steps=steps or [],
            total_duration_ms=total_duration_ms,
            error_message=error_message,
            schedule_info=schedule_info,
            pipeline_info=pipeline_info,
            execution_info=execution_info
        )