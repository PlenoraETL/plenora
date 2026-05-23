"""Read-only runtime configuration snapshot for superadmins."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import shutil
import socket
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.config import API_VERSION, settings
from app.observability.health import (
    check_bootstrap_complete,
    check_migrations_aligned,
    check_object_storage,
    check_postgres,
    check_redis,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


_SECRET_NEEDLES = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "KEY",
    "CREDENTIAL",
    "ACCESS",
    # MINIO_ENDPOINT_URL e qualunque altra ENDPOINT espone topologia di
    # storage interno (es. `http://minio:9000`) utile per lateral movement
    # se un admin account viene compromesso. Trattato come secret.
    "ENDPOINT",
)


# Chiavi `*_URL` necessitano di trattamento dedicato: contengono spesso
# credenziali embedded (es. `postgresql://user:pwd@host:5432/db`) e
# topologia di rete interna. Pre-fix (P1 admin-api):
# `_is_secret_key("DATABASE_URL") → False` (nessuna needle nel nome) +
# `_redact_url(value)` no-op su URL senza credentials embedded → host:port
# interno esposto. Post-fix: redaction strutturata `_redact_url_strict`
# che preserva solo lo `scheme` + il `path` (db name / Redis DB number)
# e maschera netloc completo. L'admin distingue Postgres vs Redis ma
# NON vede host/port/credentials.
_URL_REDACT_KEYS = (
    "DATABASE_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)

_CONFIG_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Deployment",
        (
            "ENVIRONMENT",
            "DB_SCHEMA",
            "DATA_DIR",
            "CORS_ORIGINS",
            "CLUSTER_MODE",
            "PLENORA_CLUSTER_MODE",
            "MULTI_POD_MODE",
            "PLENORA_MULTI_POD",
        ),
    ),
    (
        "Database",
        (
            "DATABASE_URL",
            "DB_CONNECT_TIMEOUT",
            "DB_QUERY_TIMEOUT",
            "DB_SCHEMA_WARN_GB",
            "DB_SCHEMA_ERROR_GB",
        ),
    ),
    (
        "Celery / Redis",
        (
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "CELERY_QUEUE_DEFAULT",
            "CELERY_QUEUE_DATA_PLANE",
            "CELERY_QUEUE_CONTROL_PLANE",
            "CELERY_QUEUE_SCHEDULER",
            "CELERY_TASK_TIMEOUT",
            "CELERY_TASK_HARD_LIMIT",
            "CELERY_PREVIEW_TIMEOUT",
            "CELERY_PREVIEW_HARD_LIMIT",
            "CELERY_SCHEDULE_TIMEOUT",
            "CELERY_SCHEDULE_HARD_LIMIT",
        ),
    ),
    (
        "Storage",
        (
            "STORAGE_POLICY",
            "OBJECT_STORAGE_ENABLED",
            "OBJECT_STORAGE_BACKEND",
            "CLOUD_PROVIDER",
            "OBJECT_STORAGE_BUCKET",
            "BACKUP_BUCKET",
            "BACKUP_SCHEDULE_CRON",
            "ADMIN_BACKUP_MANUAL_TRIGGER_ENABLED",
            "MINIO_ENDPOINT_URL",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
        ),
    ),
    (
        "Limits",
        (
            "MAX_FILE_SIZE",
            "LOAD_FILE_MAX_BYTES",
            "WORKER_RSS_MAX_BYTES",
            "RESOURCE_MAX_BYTES",
            "ARTIFACT_PREVIEW_MAX_BYTES",
            "RESULT_DB_HARD_CAP_BYTES",
            "RESULT_S3_THRESHOLD_BYTES",
            "FILE_SOURCE_S3_THRESHOLD_BYTES",
            "CROSS_JOIN_MAX_OUTPUT_ROWS",
            "MULTI_JOIN_MAX_OUTPUT_ROWS",
            "MAX_PREVIEW_ROWS",
        ),
    ),
    (
        "Security",
        (
            "JWT_EXPIRE_HOURS",
            "JWT_SECRET_KEY",
            "PLENORA_ENCRYPTION_KEY",
            "LOGIN_MAX_ATTEMPTS",
            "LOGIN_WINDOW_SECONDS",
            "LOGIN_LOCKOUT_SECONDS",
            "LOGIN_RL_REDIS_REQUIRED",
            "JWT_BLACKLIST_REDIS_TIMEOUT_SEC",
            "JWT_BLACKLIST_STALE_THRESHOLD_SEC",
        ),
    ),
)

_FUTURE_EDIT_ALLOWLIST = frozenset(
    {
        "OBJECT_STORAGE_ENABLED",
        "OBJECT_STORAGE_BACKEND",
        "STORAGE_POLICY",
        "OBJECT_STORAGE_BUCKET",
        "BACKUP_BUCKET",
        "BACKUP_SCHEDULE_CRON",
        "LOAD_FILE_MAX_BYTES",
        "WORKER_RSS_MAX_BYTES",
        "RESOURCE_MAX_BYTES",
        "ARTIFACT_PREVIEW_MAX_BYTES",
        "RESULT_DB_HARD_CAP_BYTES",
        "RESULT_S3_THRESHOLD_BYTES",
        "FILE_SOURCE_S3_THRESHOLD_BYTES",
        "MAX_PREVIEW_ROWS",
        "JWT_EXPIRE_HOURS",
        "LOGIN_MAX_ATTEMPTS",
        "LOGIN_WINDOW_SECONDS",
        "LOGIN_LOCKOUT_SECONDS",
    }
)


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(needle in upper for needle in _SECRET_NEEDLES)


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except Exception:
        return value
    if not parts.scheme or not parts.netloc or "@" not in parts.netloc:
        return value
    host_part = parts.netloc.rsplit("@", 1)[1]
    user_part = parts.netloc.split("@", 1)[0]
    username = user_part.split(":", 1)[0] or "user"
    redacted = SplitResult(
        scheme=parts.scheme,
        netloc=f"{username}:<redacted>@{host_part}",
        path=parts.path,
        query=parts.query,
        fragment=parts.fragment,
    )
    return urlunsplit(redacted)


def _redact_url_strict(value: str) -> str:
    """Redaction full: maschera netloc completo, preserva scheme + path.

    Usata per chiavi in `_URL_REDACT_KEYS` (DATABASE_URL, broker, etc.)
    dove anche `host:port` interno e' info da non leakare. Esempi:
        postgresql://user:pwd@db.internal:5432/plenora → postgresql://<redacted>/plenora
        redis://:secret@redis.internal:6379/0 → redis://<redacted>/0
        amqp://celery:pwd@rabbit:5672/vhost → amqp://<redacted>/vhost
    """
    try:
        parts = urlsplit(value)
    except Exception:
        return "<redacted>"
    if not parts.scheme:
        return "<redacted>"
    redacted = SplitResult(
        scheme=parts.scheme,
        netloc="<redacted>",
        path=parts.path,
        query="",  # query string puo' contenere credentials in URL OAuth
        fragment="",
    )
    return urlunsplit(redacted)


def _serialize_value(key: str, value: Any) -> object:
    if value is None:
        return None
    if _is_secret_key(key):
        return "<redacted>"
    # Chiavi URL-sensitive: redaction strict che maschera netloc anche
    # quando l'URL non ha credenziali embedded (es. `postgresql://
    # internal-pg:5432/db` esponeva host:port interni pre-fix).
    if key.upper() in _URL_REDACT_KEYS:
        if isinstance(value, str) and "://" in value:
            return _redact_url_strict(value)
        return "<redacted>"
    if isinstance(value, str) and "://" in value:
        return _redact_url(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return value
    return str(value)


def _setting_entry(key: str) -> dict[str, object]:
    raw_value = os.getenv(key)
    value = settings.get(key)
    has_default = key in settings.DEFAULTS
    # Sanity log: una chiave in `_CONFIG_SECTIONS` che non e' ne' in
    # `settings.DEFAULTS` ne' in `os.environ` puo' indicare un drift
    # in cui qualcuno ha aggiunto la chiave alla sezione admin ma ha
    # dimenticato di aggiungerla ai default canonici. Senza questo log
    # la chiave usciva con `source="unset"` e `value=None` senza alcuna
    # tracciabilita' operativa.
    if raw_value is None and not has_default:
        logger.info(
            "[admin/runtime-config] key=%s NON in settings.DEFAULTS e non "
            "in os.environ: ritornata come source=unset. Verifica drift "
            "fra _CONFIG_SECTIONS e settings.DEFAULTS.",
            key,
        )
    return {
        "key": key,
        "value": _serialize_value(key, value),
        "source": "env" if raw_value is not None else ("default" if has_default else "unset"),
        "secret": _is_secret_key(key),
        "editable_later": key in _FUTURE_EDIT_ALLOWLIST,
        "requires_restart": _requires_restart(key),
    }


def _requires_restart(key: str) -> list[str]:
    if key.startswith("CELERY_") or key in {"WORKER_RSS_MAX_BYTES"}:
        return ["backend", "worker", "beat"]
    if key in {"DATABASE_URL", "DB_SCHEMA"}:
        return ["backend", "worker", "beat"]
    if key.startswith("OBJECT_STORAGE") or key in {
        "STORAGE_POLICY",
        "CLOUD_PROVIDER",
        "BACKUP_BUCKET",
        "MINIO_ENDPOINT_URL",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    }:
        return ["backend", "worker", "beat"]
    if key in {"JWT_SECRET_KEY", "PLENORA_ENCRYPTION_KEY", "CORS_ORIGINS"}:
        return ["backend", "worker", "beat"]
    return []


def _health_check(name: str, fn) -> dict[str, object]:
    try:
        ok, detail = fn()
        return {"name": name, "ok": bool(ok), "detail": detail}
    except Exception as exc:
        return {"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _worker_snapshot() -> dict[str, object]:
    try:
        from app.core.task_runtime.probes import inspect_celery_workers

        active, stats, queues = inspect_celery_workers(timeout=1.5)
        worker_names = sorted(set(stats.keys()) | set(active.keys()) | set(queues.keys()))
        return {
            "available": True,
            "workers_online": len(worker_names),
            "workers": [
                {
                    "name": name,
                    "active_tasks": len(active.get(name) or []),
                    "queues": [
                        q.get("name")
                        for q in (queues.get(name) or [])
                        if isinstance(q, dict) and q.get("name")
                    ],
                    "pool": (stats.get(name) or {}).get("pool", {}),
                    "total": (stats.get(name) or {}).get("total", {}),
                }
                for name in worker_names
            ],
        }
    except Exception as exc:
        return {
            "available": False,
            "workers_online": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _is_dev_env() -> bool:
    """True quando ENVIRONMENT/ENV identifica dev/test.

    Le informazioni di topologia (hostname, docker socket, compose name)
    sono utili in dev per debugging ma rappresentano un info leak in
    prod/staging anche se l'endpoint e' superadmin-only: un admin
    compromesso ottiene la mappa interna per lateral movement.
    """
    env_name = os.environ.get(
        "ENVIRONMENT", os.environ.get("ENV", "development")
    ).lower()
    return env_name not in ("production", "prod", "staging", "stage")


def _runtime_snapshot() -> dict[str, object]:
    """Snapshot del runtime visibile in `GET /admin/runtime-config`.

    Espone solo informazioni utili e a basso rischio in prod/staging
    (note + workers). I campi di topologia interna (hostname,
    docker_socket, compose_project, container_name) sono visibili
    SOLO in dev/test sotto chiave `_dev_topology` per non rompere il
    debug locale. Pre-fix questi campi erano sempre presenti nella
    risposta — info leak verso un admin compromesso.
    """
    snapshot: dict[str, object] = {
        "note": (
            "Restart e modifica configurazione non sono esposti da questo endpoint "
            "read-only."
        ),
    }
    if _is_dev_env():
        snapshot["_dev_topology"] = {
            "hostname": socket.gethostname(),
            "docker_cli_available": shutil.which("docker") is not None,
            "docker_socket_mounted": os.path.exists("/var/run/docker.sock"),
            "compose_project": os.getenv("COMPOSE_PROJECT_NAME"),
            "container_name": os.getenv("HOSTNAME"),
        }
    return snapshot


def get_runtime_config() -> dict[str, object]:
    sections = [
        {
            "name": section_name,
            "items": [_setting_entry(key) for key in keys],
        }
        for section_name, keys in _CONFIG_SECTIONS
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_version": API_VERSION,
        "mode": "read_only",
        "sections": sections,
        "health": [
            _health_check("postgres", check_postgres),
            _health_check("redis", check_redis),
            _health_check("migrations", check_migrations_aligned),
            _health_check("object_storage", check_object_storage),
            _health_check("bootstrap", check_bootstrap_complete),
        ],
        "runtime": _runtime_snapshot(),
        "workers": _worker_snapshot(),
    }

