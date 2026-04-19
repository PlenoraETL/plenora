"""Servizio per la gestione dei log di esecuzione dello Scheduler.

Architettura (FS-B3 post-migration)
====================================
I log vivono nella tabella `plenora.schedule_logs`. Zero FS. Benefici
rispetto all'implementazione pre-migration (`data/scheduler_logs/
YYYY-MM-DD/*.json`):

  - Container-safe: zero persistenza locale, qualsiasi pod backend
    legge lo stesso set
  - Paginazione indicizzata: list O(page_size) anche su anni di log
  - Transazionalita': `INSERT` atomico, no dir vuote orfane su crash
  - Retention via `DELETE WHERE timestamp < ?` (indexed)

API pubblica invariata rispetto alla versione FS:
    - write_log(entry) -> log_id (string, prima era relative file path)
    - get_log(log_id) -> ScheduleLogEntry | None
    - get_logs(schedule_id, page, page_size, status_filter) -> ScheduleLogList
    - get_latest_log(schedule_id) -> ScheduleLogEntry | None
    - delete_logs_before(date) -> int (righe eliminate)
    - delete_schedule_logs(schedule_id) -> int
    - create_log_entry(...) -> ScheduleLogEntry helper
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import List, Optional

from app.core.scheduler.models import (
    LogStep,
    ScheduleLogEntry,
    ScheduleLogList,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


class ScheduleLogService:
    """Servizio log scheduler — storage DB (`plenora.schedule_logs`).

    Stateless: ogni metodo apre/chiude la propria query. Safe da istanziare
    a basso costo in ogni handler (come gia' fanno router/schedule_service).
    """

    # =========================================================================
    # SCRITTURA
    # =========================================================================

    def write_log(self, log_entry: ScheduleLogEntry) -> str:
        """INSERT del log entry. Ritorna il log_id (identificatore canonico).

        Caller legacy aspettavano un "relative path" (es.
        "2026-02-07/sched_abc_030000.json"). Oggi ritorniamo il log_id:
        e' stabile, opaco, usabile come identificatore anywhere.
        """
        try:
            from app.db.pg_connection import execute
            execute(
                "INSERT INTO plenora.schedule_logs "
                "(log_id, schedule_id, timestamp, level, event, triggered_by, "
                " steps, total_duration_ms, error_message, schedule_info, "
                " pipeline_info, execution_info) "
                "VALUES (?,?,?,?,?,?,?::jsonb,?,?,?::jsonb,?::jsonb,?::jsonb)",
                (
                    log_entry.log_id,
                    log_entry.schedule_id,
                    log_entry.timestamp,
                    log_entry.level,
                    log_entry.event,
                    log_entry.triggered_by,
                    json.dumps([s.model_dump(mode="json") for s in log_entry.steps]),
                    log_entry.total_duration_ms,
                    log_entry.error_message,
                    json.dumps(log_entry.schedule_info) if log_entry.schedule_info else None,
                    json.dumps(log_entry.pipeline_info) if log_entry.pipeline_info else None,
                    json.dumps(log_entry.execution_info) if log_entry.execution_info else None,
                ),
            )
            return log_entry.log_id
        except Exception as e:
            logger.error(f"[CRASH] Errore scrittura log {log_entry.log_id}: {e}", exc_info=True)
            raise

    # =========================================================================
    # LETTURA
    # =========================================================================

    def _row_to_entry(self, row: dict) -> ScheduleLogEntry:
        """Mappa una row DB → ScheduleLogEntry (deserializza JSONB fields)."""
        def _jsonb(v):
            if v is None or v == "":
                return None
            if isinstance(v, (dict, list)):
                return v
            return json.loads(v)

        steps_raw = _jsonb(row.get("steps")) or []
        return ScheduleLogEntry(
            log_id=row["log_id"],
            schedule_id=row["schedule_id"],
            timestamp=row["timestamp"],
            level=row["level"],
            event=row["event"],
            triggered_by=row["triggered_by"],
            steps=[LogStep.model_validate(s) for s in steps_raw],
            total_duration_ms=row.get("total_duration_ms", 0) or 0,
            error_message=row.get("error_message"),
            schedule_info=_jsonb(row.get("schedule_info")),
            pipeline_info=_jsonb(row.get("pipeline_info")),
            execution_info=_jsonb(row.get("execution_info")),
        )

    def get_log(self, log_id: str) -> Optional[ScheduleLogEntry]:
        """Carica un singolo log entry per ID."""
        try:
            from app.db.pg_connection import fetchone
            row = fetchone(
                "SELECT * FROM plenora.schedule_logs WHERE log_id = ?",
                (log_id,),
            )
            if not row:
                return None
            return self._row_to_entry(row)
        except Exception as e:
            logger.error(f"[CRASH] Errore lettura log {log_id}: {e}", exc_info=True)
            return None

    def get_logs(
        self,
        schedule_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
    ) -> ScheduleLogList:
        """Lista log per uno schedule, paginata (timestamp DESC).

        Con l'indice `idx_schedule_logs_schedule_ts`, la query e' O(page_size)
        anche su anni di log (LIMIT/OFFSET su index scan).
        """
        # Validazione input (stesso comportamento della versione FS)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        # Filtro opzionale: la vecchia API acceptava status="success" etc.
        # → matchava `event == f"PIPELINE_{status.upper()}"`. Preserviamo.
        event_filter = None
        if status_filter:
            event_filter = f"PIPELINE_{status_filter.upper()}"

        try:
            from app.db.pg_connection import fetchall, fetchone

            if event_filter:
                where = "WHERE schedule_id = ? AND event = ?"
                args_count = (schedule_id, event_filter)
                args_page = (schedule_id, event_filter, page_size, offset)
            else:
                where = "WHERE schedule_id = ?"
                args_count = (schedule_id,)
                args_page = (schedule_id, page_size, offset)

            count_row = fetchone(
                f"SELECT COUNT(*) AS n FROM plenora.schedule_logs {where}",
                args_count,
            )
            total = int(count_row["n"]) if count_row else 0

            rows = fetchall(
                f"SELECT * FROM plenora.schedule_logs {where} "
                f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                args_page,
            )
            logs = [self._row_to_entry(r) for r in rows]

            return ScheduleLogList(
                logs=logs, total=total, page=page, page_size=page_size,
            )
        except Exception as e:
            logger.error(f"[CRASH] Errore list logs schedule={schedule_id}: {e}", exc_info=True)
            return ScheduleLogList(logs=[], total=0, page=page, page_size=page_size)

    def get_latest_log(self, schedule_id: str) -> Optional[ScheduleLogEntry]:
        """Ultimo log per uno schedule.

        Usato dall'execution_manager per aggiornare runtime.last_execution.
        """
        try:
            from app.db.pg_connection import fetchone
            row = fetchone(
                "SELECT * FROM plenora.schedule_logs WHERE schedule_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (schedule_id,),
            )
            if not row:
                return None
            return self._row_to_entry(row)
        except Exception as e:
            logger.error(f"[CRASH] Errore get_latest_log {schedule_id}: {e}", exc_info=True)
            return None

    # =========================================================================
    # CANCELLAZIONE
    # =========================================================================

    def delete_logs_before(self, before_date: date) -> int:
        """Cancella log con timestamp < before_date. Ritorna righe eliminate.

        Usato tipicamente da un beat job di retention. Indexed su timestamp.
        """
        try:
            from app.db.pg_connection import execute
            # Convert date -> datetime UTC per comparison con TIMESTAMPTZ
            before_dt = datetime.combine(before_date, datetime.min.time(), tzinfo=timezone.utc)
            cur = execute(
                "DELETE FROM plenora.schedule_logs WHERE timestamp < ?",
                (before_dt,),
            )
            deleted = cur.rowcount
            if deleted:
                logger.info(f"Eliminati {deleted} log prima di {before_date}")
            return deleted
        except Exception as e:
            logger.error(f"[CRASH] Errore delete_logs_before {before_date}: {e}", exc_info=True)
            return 0

    def delete_schedule_logs(self, schedule_id: str) -> int:
        """Cancella tutti i log di uno specifico schedule (cleanup on delete)."""
        try:
            from app.db.pg_connection import execute
            cur = execute(
                "DELETE FROM plenora.schedule_logs WHERE schedule_id = ?",
                (schedule_id,),
            )
            deleted = cur.rowcount
            logger.info(f"Eliminati {deleted} log per schedule {schedule_id}")
            return deleted
        except Exception as e:
            logger.error(f"[CRASH] Errore delete_schedule_logs {schedule_id}: {e}", exc_info=True)
            return 0

    # =========================================================================
    # HELPER — Creazione log entry
    # =========================================================================

    def _generate_log_id(self, schedule_id: str, timestamp: datetime) -> str:
        """Genera log_id univoco: sched_{short_id}_{HHMMSS_ffffff}.

        Include microsecondi per evitare collisioni su trigger concorrenti
        (cron + manuale nello stesso secondo).
        """
        time_str = timestamp.strftime("%H%M%S_%f")
        short_id = schedule_id.replace("schedule_", "")[:12]
        return f"sched_{short_id}_{time_str}"

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
        execution_info: Optional[dict] = None,
    ) -> ScheduleLogEntry:
        """Helper per creare un ScheduleLogEntry con log_id e timestamp generati."""
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
            execution_info=execution_info,
        )
