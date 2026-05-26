"""Operational startup validation boundary.

`app.config.Settings` owns configuration values only. Filesystem cleanup,
Redis probing, and dev-only WSL fallback live here so importing the settings
singleton never implies operational behavior.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse
import shutil
import socket
import stat
import sys

import logging
import os
from pathlib import Path
from typing import Any, Callable

from app.config_env_loader import DOTENV_DISABLED_ENVS
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
_REDIS_URL_KEYS = (
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "REDIS_URL",
)


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

    for key in _REDIS_URL_KEYS:
        value = settings.get(key)
        if value and _url_uses_loopback_host(value):
            errors.append(
                f"{key} ({_safe_url_host_label(value)}) usa un host "
                "loopback/localhost. "
                "In production/staging o cluster usa un Redis condiviso "
                "raggiungibile da tutti i pod/processi."
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
            "production/staging o cluster: se Redis/lock non e' disponibile "
            "beat deve restare fail-closed per evitare doppio enqueue."
        )

    if not _as_bool(settings.get("LOGIN_RL_REDIS_REQUIRED", False)):
        errors.append(
            "LOGIN_RL_REDIS_REQUIRED deve essere true in production/staging "
            "o cluster: il fallback in-memory non e' coerente con piu' pod."
        )

    if not _as_bool(settings.get("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", False)):
        errors.append(
            "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED deve essere true in "
            "production/staging o cluster: il rate limit verso provider "
            "esterni deve essere globale tra pod/worker."
        )

    if not _as_bool(settings.get("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", False)):
        errors.append(
            "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED deve essere true in "
            "production/staging o cluster: il fallback locale moltiplica "
            "le quote per numero di repliche."
        )

    storage_policy = (
        str(settings.get("STORAGE_POLICY", "hybrid") or "hybrid")
        .strip()
        .lower()
    )
    if storage_policy not in {"object_storage", "hybrid"}:
        errors.append(
            f"STORAGE_POLICY={storage_policy!r} non e' coerente con "
            "production/staging o cluster. Usa 'object_storage' o 'hybrid' "
            "con OBJECT_STORAGE_ENABLED=true e OBJECT_STORAGE_BUCKET "
            "configurato."
        )


def wsl_redis_fallback_allowed() -> bool:
    """Return True only for local Windows development."""

    is_dev = _env_name() not in DOTENV_DISABLED_ENVS
    is_windows = sys.platform.startswith("win")
    return is_dev and is_windows


def ensure_redis(settings: Any, errors: list, warnings: list) -> None:
    """Verify Redis reachability and optionally start WSL Redis in dev Windows."""

    broker_url = settings.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    parsed = urlparse(broker_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379

    def _redis_ping() -> bool:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    if _redis_ping():
        logger.info("Redis gia' attivo su %s:%s", host, port)
        return

    if wsl_redis_fallback_allowed():
        ensure_redis_wsl_fallback(_redis_ping, errors, warnings)
        return

    errors.append(
        f"Redis non raggiungibile su {broker_url}. "
        "Avvia il servizio Redis configurato in CELERY_BROKER_URL "
        "prima di avviare l'app."
    )


def ensure_redis_wsl_fallback(
    redis_ping: Callable[[], bool],
    errors: list,
    warnings: list,
) -> None:
    """Reject implicit process spawning; Redis must be started externally."""

    if redis_ping():
        return
    warnings.append(
        "Fallback WSL Redis disabilitato: lo startup non avvia processi figli."
    )
    errors.append(
        "Redis non attivo. Avvia Redis esternamente prima del backend "
        "(es. wsl redis-server --daemonize yes in sviluppo locale)."
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
    try:

        def _on_error(func, fpath, exc_info):
            try:
                os.chmod(fpath, stat.S_IWRITE)
                func(fpath)
            except Exception as exc:
                logger.debug("Cleanup temp: impossibile rimuovere %s: %s", fpath, exc)

        temp_dir = Path(settings.TEMP_DIR)
        if not temp_dir.exists():
            return
        for entry in temp_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, onerror=_on_error)
            else:
                try:
                    entry.unlink()
                except Exception as exc:
                    logger.debug("Cleanup temp: impossibile rimuovere %s: %s", entry, exc)
    except Exception as exc:
        logger.debug("Pulizia cartelle temp fallita (non critico): %s", exc)


def _cleanup_legacy_dirs(settings: Any) -> None:
    try:

        for legacy_dir in ("files", "results", "File", "Results"):
            legacy_path = Path(settings.DATA_DIR) / legacy_dir
            if legacy_path.exists() and legacy_path.is_dir():
                shutil.rmtree(legacy_path, ignore_errors=True)
    except Exception as exc:
        logger.debug("Rimozione cartelle legacy fallita (non critico): %s", exc)


def validate_startup(settings: Any) -> None:
    """Run explicit startup side effects for the configured process."""
    errors: list[str] = []
    warnings: list[str] = []

    validate_production_gates(settings, errors)
    _validate_data_dir(settings, errors)
    _cleanup_temp_dir(settings)
    _cleanup_legacy_dirs(settings)
    ensure_redis(settings, errors, warnings)

    for warning in warnings:
        print(f"[WARN] PLENORA-ETL: {warning}")

    if errors:
        print("\n[ERROR] PLENORA-ETL - Configurazione non valida, avvio interrotto:\n")
        for index, error in enumerate(errors, 1):
            print(f"  {index}. {error}")
        raise SystemExit(1)


__all__ = [
    "ensure_redis",
    "ensure_redis_wsl_fallback",
    "production_gates_required",
    "validate_production_gates",
    "validate_startup",
    "wsl_redis_fallback_allowed",
]
