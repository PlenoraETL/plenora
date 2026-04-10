"""
Custom Celery Beat Scheduler — legge gli schedule da config.db (SQLite).

Broker/backend su Redis, ma la sorgente degli schedule resta SQLite (config.db).

Funzionamento:
- Ogni tick (default 60s) legge la tabella `schedules` dal DB
- Per ogni schedule attivo e scaduto, lancia schedule_pipeline_task
- Usa una tabella `beat_state` in SQLite per tracciare l'ultima esecuzione

Avvio:
    celery -A workers.celery_app beat -S workers.beat_scheduler.UrbyBeatScheduler --loglevel=info
"""

import time
from datetime import datetime, timezone
from typing import Optional
from celery.beat import Scheduler, ScheduleEntry
from celery.utils.log import get_logger

logger = get_logger(__name__)

# Intervallo minimo tra due check (secondi)
DEFAULT_MAX_INTERVAL = 60.0


class UrbyBeatScheduler(Scheduler):
    """
    Beat Scheduler custom che legge gli schedule da SQLite.
    
    Ogni max_interval secondi:
    1. Carica tutti gli schedule attivi dal DB
    2. Per ognuno verifica se è scaduto (cron/interval)
    3. Lancia il task Celery se necessario
    4. Aggiorna last_run nel DB
    5. Scrive heartbeat nel DB
    """

    max_interval = DEFAULT_MAX_INTERVAL

    def __init__(self, *args, **kwargs):
        self._db_loaded = False
        super().__init__(*args, **kwargs)
        logger.info("UrbyBeatScheduler avviato (schedule da SQLite, broker Redis)")

    # ------------------------------------------------------------------
    # Override principale: called ogni tick da Celery Beat
    # ------------------------------------------------------------------

    def tick(self, event_t=None, **kwargs):
        """Override tick per sincronizzare schedule dal DB prima di ogni ciclo."""
        self._sync_from_db()
        self._write_heartbeat()
        # event_t può essere None quando Celery chiama tick() senza argomenti
        # In quel caso non passarlo — Celery usa il default interno
        if event_t is not None:
            return super().tick(event_t=event_t, **kwargs)
        return super().tick(**kwargs)

    # ------------------------------------------------------------------
    # Sincronizzazione DB → schedule entries
    # ------------------------------------------------------------------

    def _sync_from_db(self):
        """Carica/aggiorna gli schedule attivi dal DB SQLite."""
        try:
            import app.db.db_service as db
            rows = db.fetchall(
                "SELECT id, name, cron_expression, project_id, is_enabled, last_run FROM schedules WHERE is_enabled = 1"
            )

            current_ids = set()
            for row in rows:
                sid = row["id"]
                current_ids.add(sid)
                entry_name = f"urby:schedule:{sid}"

                # Crea o aggiorna entry
                schedule = self._parse_cron(row["cron_expression"])
                if schedule is None:
                    continue

                last_run_at = self._parse_dt(row.get("last_run"))

                if entry_name not in self.data:
                    self.data[entry_name] = ScheduleEntry(
                        name=entry_name,
                        task="workers.tasks.schedule_task.schedule_pipeline_task",
                        schedule=schedule,
                        kwargs={"schedule_id": sid, "triggered_by": "cron"},
                        app=self.app,
                        last_run_at=last_run_at or self.app.now(),
                    )
                else:
                    # Aggiorna schedule se cambiato
                    entry = self.data[entry_name]
                    if str(entry.schedule) != str(schedule):
                        self.data[entry_name] = entry.update(schedule=schedule)

            # Rimuovi schedule disattivati
            to_remove = [
                name for name in list(self.data.keys())
                if name.startswith("urby:schedule:") and
                   name.split(":")[-1] not in current_ids
            ]
            for name in to_remove:
                del self.data[name]
                logger.info(f"Schedule rimosso: {name}")

        except Exception as e:
            logger.error(f"[CRASH] Errore sync schedule dal DB: {e}", exc_info=True)

    def _parse_cron(self, cron_expr: str):
        """Parsa espressione cron in un oggetto schedule Celery."""
        try:
            from celery.schedules import crontab
            parts = cron_expr.strip().split()
            if len(parts) != 5:
                logger.warning(f"Cron non valido: {cron_expr!r}")
                return None
            minute, hour, day_of_month, month_of_year, day_of_week = parts
            return crontab(
                minute=minute,
                hour=hour,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                day_of_week=day_of_week,
            )
        except Exception as e:
            logger.warning(f"Errore parsing cron {cron_expr!r}: {e}")
            return None

    def _parse_dt(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Converte stringa ISO in datetime aware."""
        if not dt_str:
            return None
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Heartbeat su SQLite
    # ------------------------------------------------------------------

    def _write_heartbeat(self):
        """Scrive il timestamp heartbeat nel DB (usato dall'healthcheck)."""
        try:
            import app.db.db_service as db
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT OR REPLACE INTO beat_heartbeat (id, ts) VALUES ('beat', ?)",
                (now,)
            )
        except Exception as e:
            logger.warning(f"Errore scrittura heartbeat: {e}")

    # ------------------------------------------------------------------
    # Persistenza — non usiamo file shelve, tutto su DB
    # ------------------------------------------------------------------

    def setup_schedule(self):
        """Inizializza schedule vuoto — verrà popolato dal DB al primo tick."""
        self.data = {}
        self._sync_from_db()
        logger.info(f"Beat avviato con {len(self.data)} schedule attivi")

    def sync(self):
        """Sync periodico richiesto da Celery — già gestito in tick()."""
        pass

    def close(self):
        """Cleanup alla chiusura."""
        logger.info("UrbyBeatScheduler chiuso")
        super().close()
