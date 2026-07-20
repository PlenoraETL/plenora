"""Operational startup validation boundary.

`app.config.Settings` owns configuration values only. Filesystem cleanup
and broker (RabbitMQ) reachability probing live here so importing the
settings singleton never implies operational behavior.

La probe di reachability a startup pinga RabbitMQ via
``DRAMATIQ_BROKER_URL`` senza side effect operativi.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse
import socket

import logging
from pathlib import Path
from typing import Any

from app.environment import environment_name


logger = logging.getLogger(__name__)

_PRODUCTION_GATE_ENVS = frozenset({"production", "prod", "staging", "stage"})
_TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
_CLUSTER_FLAG_KEYS = (
    "CLUSTER_MODE",
    "PLENORA_CLUSTER_MODE",
    "MULTI_POD_MODE",
    "PLENORA_MULTI_POD",
)
_BROKER_URL_KEYS = (
    "DRAMATIQ_BROKER_URL",
)
# Default broker RabbitMQ (allineato a settings.DRAMATIQ_BROKER_URL /
# workers.dramatiq_app.broker_url()). amqp://...:5672, vhost di default "/".
_DEFAULT_BROKER_URL = "amqp://guest:guest@rabbitmq:5672"
_DEFAULT_BROKER_PORT = 5672
_DATA_PLANE_EXECUTION_MODES = frozenset({"inline", "subprocess"})


def _env_name() -> str:
    return environment_name()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY_VALUES


def production_gates_required(settings: Any) -> bool:
    """Return True when this process must satisfy multi-pod gates."""

    if _env_name() in _PRODUCTION_GATE_ENVS:
        return True
    for key in _CLUSTER_FLAG_KEYS:
        if _as_bool(settings.get(key, False)):
            return True
    return False


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().strip("[]").rstrip(".")
    if normalized in {"localhost", "0.0.0.0"}:
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _url_uses_loopback_host(url: Any) -> bool:
    parsed = urlparse(str(url or ""))
    return _is_loopback_host(parsed.hostname)


def _safe_url_host_label(url: Any) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme and not parsed.hostname:
        return "<non-url>"
    host = parsed.hostname or "<host-mancante>"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'url'}://{host}{port}"


def validate_production_gates(settings: Any, errors: list[str]) -> None:
    """Fail-fast gates for production/staging and explicit cluster mode.

    Dev/test keep the historical single-node defaults. Server and multi-pod
    deploys must use shared Redis, distributed object storage, fail-closed
    beat leadership, and fail-closed login rate limiting.
    """

    if not production_gates_required(settings):
        return

    # PLENORA_SINGLE_NODE=true (installer nativo Windows, deployment
    # single-box dichiarato): il broker sulla stessa macchina e'
    # legittimo -> si sopprime SOLO il gate anti-loopback. Gli altri
    # gates restano: un single-node in production deve comunque avere
    # object storage e rate-limiter distribuiti (su PG) attivi.
    single_node = _as_bool(settings.get("PLENORA_SINGLE_NODE", False))
    if not single_node:
        for key in _BROKER_URL_KEYS:
            value = settings.get(key)
            if value and _url_uses_loopback_host(value):
                errors.append(
                    f"{key} ({_safe_url_host_label(value)}) usa un host "
                    "loopback/localhost. "
                    "In production/staging o cluster usa un broker RabbitMQ "
                    "condiviso raggiungibile da tutti i pod/processi "
                    "(oppure dichiara PLENORA_SINGLE_NODE=true per un "
                    "deployment single-box)."
                )

    object_storage_enabled = _as_bool(settings.get("OBJECT_STORAGE_ENABLED", True))
    if not object_storage_enabled:
        errors.append(
            "OBJECT_STORAGE_ENABLED deve essere true in production/staging "
            "o cluster: i pod devono condividere result/file artifacts via "
            "object storage."
        )

    if not settings.get("OBJECT_STORAGE_BUCKET"):
        errors.append(
            "OBJECT_STORAGE_BUCKET e' obbligatorio in production/staging "
            "o cluster. Non usare fallback legacy su BACKUP_BUCKET per gli "
            "artifact runtime."
        )

    if _as_bool(settings.get("SCHEDULE_BEAT_LEADER_FAIL_OPEN", False)):
        errors.append(
            "SCHEDULE_BEAT_LEADER_FAIL_OPEN deve essere false in "
            "production/staging o cluster: se il lock leader su PostgreSQL "
            "non e' disponibile beat deve restare fail-closed per evitare "
            "doppio enqueue."
        )

    if not _as_bool(settings.get("LOGIN_RL_DISTRIBUTED_REQUIRED", False)):
        errors.append(
            "LOGIN_RL_DISTRIBUTED_REQUIRED deve essere true in production/staging "
            "o cluster: il fallback in-memory non e' coerente con piu' pod. "
            "(alias legacy accettato: LOGIN_RL_REDIS_REQUIRED)"
        )

    if not _as_bool(settings.get("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED", False)):
        errors.append(
            "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED deve essere true in "
            "production/staging o cluster: il rate limit verso provider "
            "esterni deve essere globale tra pod/worker. "
            "(alias legacy accettato: ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED)"
        )

    if not _as_bool(settings.get("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED", False)):
        errors.append(
            "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED deve essere true in "
            "production/staging o cluster: il fallback locale moltiplica "
            "le quote per numero di repliche. "
            "(alias legacy accettato: ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED)"
        )

def validate_data_plane_config(settings: Any, errors: list[str]) -> None:
    """Validate the portable data-plane process boundary before consuming jobs."""

    mode = str(
        settings.get("DATA_PLANE_EXECUTION_MODE", "subprocess") or "subprocess"
    ).strip().lower()
    if mode not in _DATA_PLANE_EXECUTION_MODES:
        errors.append(
            "DATA_PLANE_EXECUTION_MODE deve essere 'inline' o 'subprocess' "
            f"(ricevuto {mode!r})."
        )

    raw_timeout = settings.get("DATA_PLANE_CHILD_TIMEOUT_SECONDS", 3900)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        timeout = 0
    if timeout <= 0:
        errors.append(
            "DATA_PLANE_CHILD_TIMEOUT_SECONDS deve essere un intero positivo "
            f"(ricevuto {raw_timeout!r})."
        )

    raw_memory_limit = settings.get(
        "DATA_PLANE_CHILD_RSS_MAX_BYTES",
        settings.get("WORKER_RSS_MAX_BYTES", 1_500_000_000),
    )
    try:
        memory_limit = int(raw_memory_limit)
    except (TypeError, ValueError):
        memory_limit = 0
    if mode == "subprocess" and memory_limit <= 0:
        errors.append(
            "DATA_PLANE_CHILD_RSS_MAX_BYTES deve essere un intero positivo "
            "quando DATA_PLANE_EXECUTION_MODE='subprocess' "
            f"(ricevuto {raw_memory_limit!r})."
        )

    intermediate_mode = str(
        settings.get("INTERMEDIATE_PERSISTENCE_MODE", "metadata_only")
        or "metadata_only"
    ).strip().lower()
    if intermediate_mode in {
        "preview_only",
        "full_pg",
    }:
        errors.append(
            f"INTERMEDIATE_PERSISTENCE_MODE={intermediate_mode!r} e' legacy "
            "e salva righe in PostgreSQL. Usa 'metadata_only' o "
            "'full_artifact'."
        )

    if not _as_bool(settings.get("OBJECT_STORAGE_ENABLED", True)):
        errors.append(
            "OBJECT_STORAGE_ENABLED deve essere true: il data-plane salva "
            "tutti i payload su object storage."
        )
    if not settings.get("OBJECT_STORAGE_BUCKET"):
        errors.append(
            "OBJECT_STORAGE_BUCKET e' obbligatorio: PostgreSQL conserva "
            "soltanto metadata e reference."
        )


def validate_runtime_timing_config(settings: Any, errors: list[str]) -> None:
    """Reject lease/heartbeat combinations that make ownership unsafe."""

    def positive_int(key: str, default: int) -> int:
        raw_value = settings.get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            errors.append(
                f"{key} deve essere un intero positivo (ricevuto {raw_value!r})."
            )
        return value

    worker_heartbeat = positive_int("WORKER_HEARTBEAT_INTERVAL_SECONDS", 10)
    task_lease = positive_int("TASK_EXECUTION_LEASE_SECONDS", 45)
    readiness_age = positive_int(
        "READINESS_WORKER_HEARTBEAT_MAX_AGE_SECONDS", 45,
    )
    schedule_ttl = positive_int("SCHEDULE_LOCK_TTL_SECONDS", 600)
    schedule_heartbeat = positive_int(
        "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS", 180,
    )
    task_hard_limit = positive_int("TASK_HARD_LIMIT", 3900)
    schedule_hard_limit = positive_int("TASK_SCHEDULE_HARD_LIMIT", 3600)
    result_ttl = positive_int("TASK_RESULT_EXPIRES", 86400)
    positive_int("CARICA_TARGET_LOCK_TTL_SECONDS", task_hard_limit + 300)

    if task_lease and worker_heartbeat and task_lease < worker_heartbeat * 3:
        errors.append(
            "TASK_EXECUTION_LEASE_SECONDS deve essere almeno tre volte "
            "WORKER_HEARTBEAT_INTERVAL_SECONDS per tollerare tick mancati."
        )
    if readiness_age and worker_heartbeat and readiness_age < worker_heartbeat * 2:
        errors.append(
            "READINESS_WORKER_HEARTBEAT_MAX_AGE_SECONDS deve essere almeno "
            "due volte WORKER_HEARTBEAT_INTERVAL_SECONDS."
        )
    if schedule_ttl and schedule_heartbeat and schedule_ttl <= schedule_heartbeat * 2:
        errors.append(
            "SCHEDULE_LOCK_TTL_SECONDS deve essere maggiore del doppio di "
            "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS."
        )
    if result_ttl and result_ttl <= max(task_hard_limit, schedule_hard_limit):
        errors.append(
            "TASK_RESULT_EXPIRES deve superare i limiti massimi dei task, "
            "altrimenti metadata ed esito possono scadere durante la run."
        )


def ensure_broker(settings: Any, errors: list) -> None:
    """Verify RabbitMQ broker reachability with a bounded TCP connect.

    Il broker dei task e' RabbitMQ; un connect TCP all'host:porta ricavati da
    ``DRAMATIQ_BROKER_URL`` e' sufficiente a fail-fast su broker assente
    senza dipendere da pika a startup. Niente fallback WSL (era una
    convenienza dev-Windows per auto-avviare Redis, non applicabile a
    RabbitMQ)."""

    broker_url = settings.get("DRAMATIQ_BROKER_URL", _DEFAULT_BROKER_URL)
    parsed = urlparse(broker_url)
    host = parsed.hostname or "rabbitmq"
    port = parsed.port or _DEFAULT_BROKER_PORT

    try:
        with socket.create_connection((host, port), timeout=2):
            logger.info("Broker RabbitMQ raggiungibile su %s:%s", host, port)
            return
    except OSError:
        pass

    errors.append(
        f"Broker RabbitMQ non raggiungibile su {broker_url}. "
        "Avvia il servizio RabbitMQ configurato in DRAMATIQ_BROKER_URL "
        "prima di avviare worker/beat/API."
    )


def _validate_data_dir(settings: Any, errors: list) -> None:
    # Boundary: self-test scrivibilita' DATA_DIR via `io.local_filesystem`
    # (vedi razionale operational nel modulo). Touch + unlink, niente residui.
    from app.core.io.local_filesystem import (
        LocalFilesystemError,
        verify_local_writable,
    )

    try:
        verify_local_writable(settings.DATA_DIR)
    except LocalFilesystemError as exc:
        errors.append(f"DATA_DIR '{settings.get('DATA_DIR')}' non scrivibile: {exc}")
    except Exception as exc:  # difensivo: settings access errors
        errors.append(f"DATA_DIR '{settings.get('DATA_DIR')}' non scrivibile: {exc}")


def _cleanup_temp_dir(settings: Any) -> None:
    # Boundary: cleanup boot-time via `io.local_cleanup` (best-effort
    # con retry + chmod onerror). Sostituisce il pattern inline
    # shutil.rmtree(onerror=...) + entry.unlink() ripetuto qui.
    from app.core.io.local_cleanup import rmtree_safe

    try:
        temp_dir = Path(settings.TEMP_DIR)
        if not temp_dir.exists():
            return
        for entry in temp_dir.iterdir():
            rmtree_safe(entry, logger=logger)
    except Exception as exc:
        logger.debug("Pulizia cartelle temp fallita (non critico): %s", exc)


def validate_startup(settings: Any) -> None:
    """Run explicit startup side effects for the configured process."""
    errors: list[str] = []
    warnings: list[str] = []

    validate_production_gates(settings, errors)
    validate_data_plane_config(settings, errors)
    validate_runtime_timing_config(settings, errors)
    _validate_data_dir(settings, errors)
    _cleanup_temp_dir(settings)
    ensure_broker(settings, errors)

    for warning in warnings:
        print(f"[WARN] PLENORA-ETL: {warning}")

    if errors:
        print("\n[ERROR] PLENORA-ETL - Configurazione non valida, avvio interrotto:\n")
        for index, error in enumerate(errors, 1):
            print(f"  {index}. {error}")
        raise SystemExit(1)


__all__ = [
    "ensure_broker",
    "production_gates_required",
    "validate_data_plane_config",
    "validate_production_gates",
    "validate_runtime_timing_config",
    "validate_startup",
]
