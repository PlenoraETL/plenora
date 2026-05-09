"""Operational startup validation boundary.

`app.config.Settings` owns configuration values only. Filesystem cleanup,
Redis probing, and dev-only WSL fallback live here so importing the settings
singleton never implies operational behavior.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from app.config_env_loader import DOTENV_DISABLED_ENVS


logger = logging.getLogger(__name__)


def wsl_redis_fallback_allowed() -> bool:
    """Return True only for local Windows development."""
    import sys

    env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    is_dev = env_name not in DOTENV_DISABLED_ENVS
    is_windows = sys.platform.startswith("win")
    return is_dev and is_windows


def ensure_redis(settings: Any, errors: list, warnings: list) -> None:
    """Verify Redis reachability and optionally start WSL Redis in dev Windows."""
    import socket
    from urllib.parse import urlparse

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
    """Opportunistically start Redis through WSL for local Windows dev only."""
    import subprocess
    import time

    logger.info("Redis non raggiungibile - avvio via WSL (dev Windows)...")
    try:
        subprocess.Popen(
            ["wsl", "redis-server", "--daemonize", "yes"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(10):
            time.sleep(0.5)
            if redis_ping():
                logger.info("Redis avviato con successo via WSL")
                return
        warnings.append("Redis avviato via WSL ma non risponde dopo 5s")
    except FileNotFoundError:
        errors.append(
            "Redis non attivo e WSL non disponibile. "
            "Avvia Redis manualmente: wsl redis-server --daemonize yes"
        )
    except Exception as exc:
        errors.append(f"Impossibile avviare Redis via WSL: {exc}")


def _validate_data_dir(settings: Any, errors: list) -> None:
    try:
        data_dir = Path(settings.DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.touch()
        test_file.unlink()
    except Exception as exc:
        errors.append(f"DATA_DIR '{settings.get('DATA_DIR')}' non scrivibile: {exc}")


def _cleanup_temp_dir(settings: Any) -> None:
    try:
        import shutil
        import stat

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
        import shutil

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
    "validate_startup",
    "wsl_redis_fallback_allowed",
]
