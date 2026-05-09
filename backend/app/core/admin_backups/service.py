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


class AdminBackupDependencyError(RuntimeError):
    """Raised quando una dipendenza AWS runtime manca (es. botocore[crt]).

    Bug #4 (2026-04-23): distingue l'errore "dep mancante" (503 Service
    Unavailable con detail operativo) da altri fallimenti transitori (auth
    S3, network, ecc.) che continuano a restituire [] via except Exception.
    """


class AdminBackupCredentialsError(RuntimeError):
    """Raised quando le credenziali AWS sono scadute o non disponibili.

    Runtime-bugs RB.2 (2026-04-27): distingue il caso "AWS SSO scaduto"
    (operatore deve eseguire `aws sso login`) dagli altri errori transitori
    su S3. Il router lo cattura -> 503 con detail strutturato e messaggio
    operativo, evitando il fallback silenzioso a lista vuota che faceva
    apparire l'endpoint come "success" senza backup elencati.

    Cause riconosciute (vedi `app.infrastructure.storage.cloud_backends.aws_credentials_error_message`):
      - LoginRefreshRequired / TokenRetrievalError / UnauthorizedSSOTokenError
      - NoCredentialsError / SSOTokenLoadError / CredentialRetrievalError
      - ClientError AccessDeniedException con "refresh token" / "CreateOAuth2Token"
    """


class AdminBackupDisabledError(RuntimeError):
    """Raised quando admin_backups e' chiamato ma `OBJECT_STORAGE_ENABLED=false`.

    la capability globale object storage e' off
    in questo deployment. Il router cattura -> 503 con detail "Feature
    non disponibile in questa configurazione" invece di provare e
    fallire con messaggio AWS confuso.
    """


def _to_datetime(value: Any) -> datetime:
    """Normalizza a `datetime` un valore proveniente da `plenora.backup_runs`.

    Il driver psycopg2 ritorna `started_at`/`finished_at` come `datetime`
    in modalita' default, ma alcuni adapter / cursor mode (RealDict in
    contesti specifici) possono restituirli come stringa ISO 8601 con TZ
    aware (es. ``"2026-04-27 02:02:45.362688+00:00"``). Idempotente:
    se gia' datetime, ritorna invariato.

    Raises ValueError se la stringa non e' parsabile come ISO 8601.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Postgres usa lo space separator, fromisoformat (3.11+) accetta
        # entrambi space e 'T'. Piu' robusto sostituire space con 'T' per
        # coerenza con interpreti precedenti, ma 3.12 lo gestisce nativo
        return datetime.fromisoformat(value)
    raise TypeError(f"Tipo non normalizzabile a datetime: {type(value).__name__}")


# ── Config — nomi canonical allineati a cloud_backends.py ─────────────────
# `BUCKET` -> delega a `cloud_backends._resolve_backup_bucket`
# DEPLOY_INFRA, env-only) che gia' centralizza la chain
# `BACKUP_BUCKET` -> legacy `BACKUP_S3_BUCKET`. Fallback hardcoded
# preservato come prima per deployment legacy senza env
# `REGION` -> single env read `AWS_REGION` (DEPLOY_INFRA). Il
# NOTE: legacy alias `BACKUP_S3_REGION` era un dead alias (zero altri
# consumer in repo) e viene retirato qui
# schedule cron / description -> `Settings.DEFAULTS` (APP_POLICY

from app.infrastructure.storage.cloud_backends import _resolve_backup_bucket as _resolve_backup_bucket_env

BUCKET = _resolve_backup_bucket_env() or "plenora-backup-112011134642-eu-south-1-an"
REGION = os.getenv("AWS_REGION", "eu-south-1")

# Lock file per evitare trigger concorrenti via API
MANUAL_LOCK_FILE = Path("/tmp/plenora-backup-manual.lock")
MANUAL_LOCK_TTL_SEC = 7200  # 2 ore: se un backup e' in corso, il lock scade dopo 2h

# Path dello script backup, risolto relativamente al backend root
# service.py -> admin_backups -> core -> app -> backend
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = _BACKEND_ROOT / "scripts" / "backup_db.py"


# ── S3: lista backup ────────────────────────────────────────────────────────


def list_backups() -> List[Dict[str, Any]]:
    """Lista oggetti del bucket backup con prefix 'plenora_'.

    passa per `get_backup_object_storage`
    invece di `boto3.client("s3")` diretto, cosi' la capability globale
    `OBJECT_STORAGE_ENABLED=false` e' rispettata. Senza questo, in un
    deployment senza object storage l'endpoint provava list_objects_v2
    e ritornava lista vuota, mascherando "feature disabilitata" come
    "nessun backup".

    Returns:
        Lista di dict con key/size_bytes/last_modified/storage_class, ordinata
        dal piu' recente al piu' vecchio.

    Raises:
        AdminBackupDisabledError: capability object storage off.
        AdminBackupDependencyError: botocore[crt] o altre dep AWS mancanti.
        AdminBackupCredentialsError: credenziali AWS scadute / SSO refresh.
    """
    from app.infrastructure.storage.cloud_backends import (
        ObjectStorageDisabledError,
        get_backup_object_storage,
    )

    try:
        storage = get_backup_object_storage()
    except ObjectStorageDisabledError as e:
        logger.info(
            "list_backups: object storage capability disabilitata, "
            "feature non disponibile."
        )
        raise AdminBackupDisabledError(str(e)) from e

    try:
        objects = storage.list_objects("plenora_")
        result = [
            {
                "key": o["key"],
                "size_bytes": o["size"],
                "size_mb": round(o["size"] / 1024 / 1024, 2),
                "last_modified": o["last_modified"].isoformat(),
                "storage_class": "STANDARD",
            }
            for o in objects
        ]
        result.sort(key=lambda o: o["last_modified"], reverse=True)
        return result
    except Exception as e:
        try:
            from botocore.exceptions import MissingDependencyException
            if isinstance(e, MissingDependencyException):
                logger.error(
                    f"list_backups fallito per dep runtime mancante: {e}",
                    exc_info=True,
                )
                raise AdminBackupDependencyError(str(e)) from e
        except ImportError:
            pass  # botocore non importabile: improbabile qui, degrada
        # RB.2 (2026-04-27): credenziali AWS scadute / SSO refresh required
        # > 503 con detail strutturato. Niente lista vuota fuorviante
        from app.infrastructure.storage.cloud_backends import aws_credentials_error_message
        hint = aws_credentials_error_message(e)
        if hint is not None:
            # R-1 (2026-04-27): log conciso. La causa e' classificata
            # credenziali AWS scadute = condizione operativa nota, non bug
            # applicativo) e gestita esplicitamente dal router con 503
            # strutturato. Un traceback botocore completo per ogni client
            # call durante il refresh expiry rumoreggia il journal senza
            # aggiungere informazione: il tipo di eccezione e' gia' loggato
            logger.warning(
                "list_backups: credenziali AWS non disponibili "
                f"({type(e).__name__}: {e})",
            )
            raise AdminBackupCredentialsError(hint) from e
        # Errori non classificati: traceback completo resta utile per debug
        logger.error(f"list_backups fallito: {e}", exc_info=True)
        return []


# ── Stato ultimo run (DB-based, container-friendly) ─────────────────────────
# Fonte di verita': plenora.backup_runs. Scritta da scripts/backup_db.py a
# fine job. Container-safe: nessuna dipendenza da systemctl/journalctl del
# nodo dove gira il backend


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
          "log_tail": list[str] # ultime righe catturate dallo script
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
            "FROM backup_runs ORDER BY started_at DESC LIMIT 1"
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
        # Driver PG puo' ritornare timestamp come datetime (psycopg2 default
        # o come stringa ISO (alcuni adapter / cursor mode). Normalizziamo
        # entrambi a datetime per la sottrazione. Idempotente: se gia' datetime
        # _to_datetime e' no-op
        try:
            started_dt = _to_datetime(started)
            finished_dt = _to_datetime(finished)
            out["duration_sec"] = (finished_dt - started_dt).total_seconds()
        except (TypeError, ValueError, AttributeError) as e:
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
    from app.config import settings
    schedule_cron = settings.get("BACKUP_SCHEDULE_CRON", "0 2 * * *")
    description = settings.get(
        "BACKUP_SCHEDULE_DESCRIPTION",
        "Daily 02:00 UTC (+ jitter fino a 5 min)",
    )

    # next_run: calcoliamo il prossimo 02:00 UTC dal cron hardcoded
    # Supporto il default "0 2 * * *"; per cron custom restituiamo None
    next_run_iso: Optional[str] = None
    if schedule_cron == "0 2 * * *":
        now = datetime.now(timezone.utc)
        candidate = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if candidate <= now:
            from datetime import timedelta
            candidate += timedelta(days=1)
        next_run_iso = candidate.isoformat()

    # last_run: started_at dell'ultima row in backup_runs
    last_run_iso: Optional[str] = None
    try:
        from app.db.pg_connection import fetchone
        row = fetchone(
            "SELECT started_at FROM backup_runs "
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
        # tracciato in plenora.backup_runs come ogni esecuzione dello script
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
