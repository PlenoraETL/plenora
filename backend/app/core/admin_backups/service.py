"""Service layer admin backups — boto3 (S3 list) + DB (plenora.backup_runs)."""

from __future__ import annotations

from datetime import timedelta
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from typing_extensions import TypedDict

from app.config import settings
from app.core.admin_backups.backup_run_store import (
    fetch_latest_backup_run,
    fetch_latest_backup_started_at,
    fetch_manual_backup_lock,
    release_manual_backup_lock,
    try_acquire_manual_backup_lock,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BackupObjectPayload(TypedDict):
    key: str
    size_bytes: int
    size_mb: float
    last_modified: str
    storage_class: str


class LastBackupRunStatus(TypedDict):
    result: str
    exit_code: int | None
    started_at: str | None
    finished_at: str | None
    duration_sec: float | None
    bucket_key: str | None
    size_bytes: int | None
    error_message: str | None
    log_tail: list[str]


class BackupScheduleInfo(TypedDict):
    timer_active: bool
    next_run: str | None
    last_run: str | None
    schedule_cron: str
    description: str
    timezone: str


class ManualBackupTriggerResult(TypedDict):
    started: bool
    pid: int | None
    task_id: str | None
    message: str


class AdminBackupDependencyError(RuntimeError):
    """Raised quando una dipendenza AWS runtime manca (es. botocore[crt]).

    Distingue l'errore "dep mancante" (503 Service Unavailable con detail
    operativo) da altri fallimenti transitori (auth S3, network, ecc.).
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

from app.core.io.object_storage import (
    ObjectStorageDisabledError,
    aws_credentials_error_message,
    get_backup_object_storage,
)
from app.infrastructure.storage.cloud_backends import (
    _resolve_backup_bucket as _resolve_backup_bucket_env,
)

# P1 (2026-05-26): rimosso fallback hardcoded con AWS account ID reale
# (`plenora-backup-112011134642-eu-south-1-an`). L'account ID era
# rintracciabile via grep su backend/ ed esponeva infrastruttura prod.
# In assenza di BACKUP_BUCKET la lista/restore restituiranno errore
# strutturato invece di puntare in modo silenzioso a una risorsa
# specifica.
BUCKET = _resolve_backup_bucket_env() or ""
REGION = settings.get("AWS_REGION", "eu-south-1")

# Lock distribuito Postgres per evitare trigger concorrenti via API.
# Pre-fix: TTL = 7200s (2h). Sufficiente a coprire un backup lungo ma se
# il worker Celery crashava (OOM, kill -9, disconnect broker) il lock
# restava orfano per 2h prima di sbloccarsi via TTL → ogni superadmin
# che provava a triggerare un backup nel frattempo riceveva "gia' in
# corso". Post-fix: TTL breve (300s = 5min) + heartbeat dal task Celery
# che estende `expires_at` ogni 120s. Se il worker muore, l'heartbeat
# si ferma, TTL scade in ≤5min, il prossimo try_acquire riacquisisce.
# Blast radius su worker morto: da 2h a 5min.
MANUAL_LOCK_TTL_SEC = 300
MANUAL_LOCK_HEARTBEAT_SEC = 120


# ── S3: lista backup ────────────────────────────────────────────────────────


def list_backups() -> list[BackupObjectPayload]:
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
        if isinstance(e, ImportError):
            logger.error(
                f"list_backups fallito per dep runtime mancante: {e}",
                exc_info=True,
            )
            raise AdminBackupDependencyError(str(e)) from e
        try:
            from botocore.exceptions import MissingDependencyException
            if isinstance(e, MissingDependencyException):
                logger.error(
                    f"list_backups fallito per dep runtime mancante: {e}",
                    exc_info=True,
                )
                raise AdminBackupDependencyError(str(e)) from e
        except ImportError as import_error:
            logger.debug(
                "list_backups: botocore non importabile durante la "
                f"classificazione errore backup: {import_error}"
            )
        # RB.2 (2026-04-27): credenziali AWS scadute / SSO refresh required
        # > 503 con detail strutturato. Niente lista vuota fuorviante
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


def get_last_run_status() -> LastBackupRunStatus:
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
    out: LastBackupRunStatus = {
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
        row = fetch_latest_backup_run()
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


def get_schedule_info() -> BackupScheduleInfo:
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
            candidate += timedelta(days=1)
        next_run_iso = candidate.isoformat()

    # last_run: started_at dell'ultima row in backup_runs
    last_run_iso: Optional[str] = None
    try:
        row = fetch_latest_backup_started_at()
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


def manual_backup_trigger_enabled() -> bool:
    """Return True when the manual backup API trigger is enabled."""

    return bool(settings.get("ADMIN_BACKUP_MANUAL_TRIGGER_ENABLED", True))


def is_manual_backup_running() -> bool:
    """Verifica se esiste un lock DB valido di un trigger manuale."""
    try:
        lock = fetch_manual_backup_lock()
    except Exception as e:
        logger.warning(f"is_manual_backup_running: query lock fallita: {e}")
        return False

    if not lock:
        return False

    try:
        expires_at = _to_datetime(lock["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            release_manual_backup_lock(owner_token=lock["owner_token"])
            return False
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        logger.warning(f"is_manual_backup_running: lock non parsabile: {e}")
        return True

    try:
        latest_run = fetch_latest_backup_run()
    except Exception as e:
        logger.warning(f"is_manual_backup_running: query backup_runs fallita: {e}")
        return True

    if not latest_run:
        return True

    started = latest_run.get("started_at")
    if not started:
        return True

    try:
        acquired_at = _to_datetime(lock["acquired_at"])
        started_at = _to_datetime(started)
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, AttributeError) as e:
        logger.warning(
            "is_manual_backup_running: timestamp non parsabile "
            f"(lock={lock!r}, latest_run={latest_run!r}): {e}"
        )
        return True

    if started_at < acquired_at:
        return True

    if latest_run.get("finished_at") is None:
        return True

    release_manual_backup_lock(owner_token=lock["owner_token"])
    return False


def trigger_manual_backup() -> ManualBackupTriggerResult:
    """Accoda il backup manuale su Celery.

    Returns:
        {"started": bool, "pid": None, "task_id": str | None, "message": str}
    """
    if not manual_backup_trigger_enabled():
        return {
            "started": False,
            "pid": None,
            "task_id": None,
            "message": (
                "Trigger backup manuale disabilitato in questa "
                "configurazione. Usa lo scheduler/job di deployment."
            ),
        }

    owner_token = uuid.uuid4().hex
    try:
        lock = try_acquire_manual_backup_lock(
            owner_token=owner_token,
            ttl_seconds=MANUAL_LOCK_TTL_SEC,
        )
    except Exception as e:
        logger.error(f"trigger_manual_backup: lock DB non acquisibile: {e}", exc_info=True)
        return {
            "started": False,
            "pid": None,
            "task_id": None,
            "message": f"Impossibile acquisire lock backup manuale: {str(e)[:200]}",
        }

    if lock is None:
        # `try_acquire_manual_backup_lock` e' gia' atomica: usa
        # `INSERT ... ON CONFLICT DO UPDATE WHERE expires_at <= now()`,
        # quindi il lock viene rinegoziato in una singola operazione SQL
        # che vede solo lock vivi.
        #
        # Pre-fix questo branch faceva `is_manual_backup_running()` +
        # `try_acquire_manual_backup_lock` come due query separate. La
        # finestra tra le due era una race TOCTOU: due admin che
        # cliccavano "Backup ora" nello stesso istante potevano entrambi
        # superare il gate e accodare due task Celery duplicati.
        #
        # Fix in profondita': nessun retry "diagnostico". Se il primo
        # tentativo (atomico) ritorna None, il lock e' davvero detenuto
        # → rispondi "gia' in corso" senza ulteriori query.
        return {
            "started": False,
            "pid": None,
            "task_id": None,
            "message": "Un backup manuale e' gia' in corso. Attendi che finisca.",
        }

    try:
        from workers.tasks.admin_backup_task import run_manual_backup_task

        async_result = run_manual_backup_task.apply_async(
            kwargs={"owner_token": owner_token}
        )
        logger.info("Backup manuale Celery accodato: task_id=%s", async_result.id)
        return {
            "started": True,
            "pid": None,
            "task_id": async_result.id,
            "message": (
                f"Backup accodato su Celery (task_id={async_result.id}). "
                "Controlla la lista S3 tra qualche secondo."
            ),
        }
    except Exception as e:
        try:
            release_manual_backup_lock(owner_token=owner_token)
        except Exception:
            logger.warning("cleanup lock manual backup fallita", exc_info=True)
        logger.error(f"trigger_manual_backup fallito: {e}", exc_info=True)
        return {"started": False, "pid": None, "task_id": None, "message": str(e)}
