"""Service layer admin backups — boto3 (S3 list) + DB (plenora.backup_runs)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Config — nomi canonical allineati a cloud_backends.py ─────────────────

BUCKET = (
    os.getenv("BACKUP_BUCKET")
    or os.getenv("BACKUP_S3_BUCKET")
    or "plenora-backup-112011134642-eu-south-1-an"
)
REGION = (
    os.getenv("AWS_REGION")
    or os.getenv("BACKUP_S3_REGION")
    or "eu-south-1"
)

# Lock file per evitare trigger concorrenti via API
MANUAL_LOCK_FILE = Path("/tmp/plenora-backup-manual.lock")
MANUAL_LOCK_TTL_SEC = 7200  # 2 ore: se un backup e' in corso, il lock scade dopo 2h

# Path dello script backup, risolto relativamente al backend root.
# service.py -> admin_backups -> core -> app -> backend
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = _BACKEND_ROOT / "scripts" / "backup_db.py"


# ── S3: lista backup ────────────────────────────────────────────────────────


def list_backups() -> List[Dict[str, Any]]:
    """Lista oggetti del bucket con prefix 'plenora_'.

    Returns:
        Lista di dict con key/size_bytes/last_modified/storage_class, ordinata
        dal piu' recente al piu' vecchio.
    """
    try:
        import boto3
    except ImportError:
        logger.error("boto3 non installato sul server")
        return []

    try:
        client = boto3.client("s3", region_name=REGION)
        response = client.list_objects_v2(Bucket=BUCKET, Prefix="plenora_")
        objects = response.get("Contents", [])
        result = [
            {
                "key": o["Key"],
                "size_bytes": o["Size"],
                "size_mb": round(o["Size"] / 1024 / 1024, 2),
                "last_modified": o["LastModified"].isoformat(),
                "storage_class": o.get("StorageClass", "STANDARD"),
            }
            for o in objects
        ]
        # Piu' recenti per primi
        result.sort(key=lambda o: o["last_modified"], reverse=True)
        return result
    except Exception as e:
        logger.error(f"list_backups fallito: {e}", exc_info=True)
        return []


# ── Stato ultimo run (DB-based, container-friendly) ─────────────────────────
# Fonte di verita': plenora.backup_runs. Scritta da scripts/backup_db.py a
# fine job. Container-safe: nessuna dipendenza da systemctl/journalctl del
# nodo dove gira il backend.


def get_last_run_status() -> Dict[str, Any]:
    """Stato dell'ultimo run leggendo plenora.backup_runs.

    Returns:
        {
          "result": "success" | "failed" | "running" | "unknown",
          "exit_code": int | None,
          "started_at": str (ISO) | None,
          "finished_at": str (ISO) | None,
          "duration_sec": float | None,
          "bucket_key": str | None,
          "size_bytes": int | None,
          "error_message": str | None,
          "log_tail": list[str]   # ultime righe catturate dallo script
        }
    """
    out: Dict[str, Any] = {
        "result": "unknown",
        "exit_code": None,
        "started_at": None,
        "finished_at": None,
        "duration_sec": None,
        "bucket_key": None,
        "size_bytes": None,
        "error_message": None,
        "log_tail": [],
    }

    try:
        from app.db.pg_connection import fetchone
        row = fetchone(
            "SELECT id, started_at, finished_at, exit_code, result, bucket_key, "
            "size_bytes, log_tail, error_message "
            "FROM plenora.backup_runs ORDER BY started_at DESC LIMIT 1"
        )
    except Exception as e:
        logger.warning(f"get_last_run_status query fallita: {e}")
        out["error_message"] = f"DB non accessibile: {str(e)[:200]}"
        return out

    if not row:
        return out  # Nessun run registrato: result resta 'unknown'

    out["result"] = row.get("result") or "unknown"
    out["exit_code"] = row.get("exit_code")
    out["bucket_key"] = row.get("bucket_key")
    out["size_bytes"] = row.get("size_bytes")
    out["error_message"] = row.get("error_message")

    started = row.get("started_at")
    finished = row.get("finished_at")
    if started:
        out["started_at"] = started.isoformat() if hasattr(started, "isoformat") else str(started)
    if finished:
        out["finished_at"] = finished.isoformat() if hasattr(finished, "isoformat") else str(finished)
    if started and finished:
        try:
            out["duration_sec"] = (finished - started).total_seconds()
        except (TypeError, AttributeError) as e:
            logger.warning(
                "Impossibile calcolare duration_sec (started=%r, finished=%r): %s: %s",
                started,
                finished,
                type(e).__name__,
                e,
            )

    log_tail = row.get("log_tail")
    if log_tail:
        out["log_tail"] = [l for l in str(log_tail).splitlines() if l.strip()]

    return out


def get_schedule_info() -> Dict[str, Any]:
    """Info sulla schedule del backup.

    La schedule e' gestita a livello di deployment (systemd timer
    `plenora-backup.timer` oggi, Celery beat / K8s CronJob in container).
    Qui ritorniamo metadati statici + next_run/last_run calcolati dal
    DB. `timer_active` e' sempre True: senza systemctl non possiamo
    distinguere "timer configurato e abilitato" da "timer assente" —
    assumiamo configurato. Se il run non parte, lo vediamo dall'ultimo
    backup mancato (get_last_run_status → age > soglia).

    Returns:
        timer_active (bool), next_run (ISO), last_run (ISO), schedule_cron,
        description, timezone.
    """
    schedule_cron = os.getenv("BACKUP_SCHEDULE_CRON", "0 2 * * *")
    description = os.getenv(
        "BACKUP_SCHEDULE_DESCRIPTION",
        "Daily 02:00 UTC (+ jitter fino a 5 min)",
    )

    # next_run: calcoliamo il prossimo 02:00 UTC dal cron hardcoded.
    # Supporto il default "0 2 * * *"; per cron custom restituiamo None.
    next_run_iso: Optional[str] = None
    if schedule_cron == "0 2 * * *":
        now = datetime.now(timezone.utc)
        candidate = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if candidate <= now:
            from datetime import timedelta
            candidate += timedelta(days=1)
        next_run_iso = candidate.isoformat()

    # last_run: started_at dell'ultima row in backup_runs.
    last_run_iso: Optional[str] = None
    try:
        from app.db.pg_connection import fetchone
        row = fetchone(
            "SELECT started_at FROM plenora.backup_runs "
            "ORDER BY started_at DESC LIMIT 1"
        )
        if row and row.get("started_at"):
            started = row["started_at"]
            last_run_iso = started.isoformat() if hasattr(started, "isoformat") else str(started)
    except Exception as e:
        logger.warning(f"query last_run fallita: {e}")

    return {
        "timer_active": True,
        "next_run": next_run_iso,
        "last_run": last_run_iso,
        "schedule_cron": schedule_cron,
        "description": description,
        "timezone": "UTC",
    }


# ── Trigger manuale ─────────────────────────────────────────────────────────


def is_manual_backup_running() -> bool:
    """Verifica se esiste un lock valido (non scaduto) di un trigger manuale."""
    if not MANUAL_LOCK_FILE.exists():
        return False
    try:
        age_sec = datetime.now(timezone.utc).timestamp() - MANUAL_LOCK_FILE.stat().st_mtime
        if age_sec > MANUAL_LOCK_TTL_SEC:
            # Lock scaduto, lo considero orfano
            return False
        # Verifica che il PID nel lock sia ancora vivo
        pid = MANUAL_LOCK_FILE.read_text().strip()
        if pid.isdigit():
            try:
                os.kill(int(pid), 0)  # signal 0 = verifica esistenza
                return True
            except OSError:
                return False
        return False
    except OSError:
        return False


def trigger_manual_backup() -> Dict[str, Any]:
    """Lancia backup_db.py come processo detached.

    Il processo eredita l'env del backend (che include DATABASE_URL caricata
    al boot da .env). Il processo sopravvive al riavvio del backend grazie a
    start_new_session=True.

    Returns:
        {"started": bool, "pid": int | None, "message": str}
    """
    if is_manual_backup_running():
        return {
            "started": False,
            "pid": None,
            "message": "Un backup manuale e' gia' in corso. Attendi che finisca.",
        }

    if not BACKUP_SCRIPT.exists():
        logger.error(f"Script backup non trovato: {BACKUP_SCRIPT}")
        return {
            "started": False,
            "pid": None,
            "message": f"Script non trovato: {BACKUP_SCRIPT}",
        }

    try:
        # start_new_session=True: detach dal backend, sopravvive al riavvio
        # stdout/stderr -> file dedicato (il trigger non passa da systemd
        # quindi niente journalctl automatico). Il run viene comunque
        # tracciato in plenora.backup_runs come ogni esecuzione dello script.
        log_file = Path("/tmp/plenora-backup-manual.log")
        with open(log_file, "a") as fp:
            proc = subprocess.Popen(
                ["python3", str(BACKUP_SCRIPT)],
                stdout=fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(_BACKEND_ROOT),
            )
        # Scrivi lock con PID
        MANUAL_LOCK_FILE.write_text(str(proc.pid))
        logger.info(f"Backup manuale avviato: pid={proc.pid}")
        return {
            "started": True,
            "pid": proc.pid,
            "message": f"Backup avviato (pid={proc.pid}). Controlla la lista S3 tra qualche secondo.",
        }
    except Exception as e:
        logger.error(f"trigger_manual_backup fallito: {e}", exc_info=True)
        return {"started": False, "pid": None, "message": str(e)}
