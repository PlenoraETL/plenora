"""Configurazione della dashboard Grafana (regola config boundary).

Segreti SOLO in .env, letti con ``app.environment.get_env`` e senza default:
``DASHBOARD_PASS_SECRET`` (firma lasciapassare e cookie di sessione),
``GRAFANA_DATA_SECRET`` (intestazione ``X-Grafana-Secret`` di Infinity),
``GRAFANA_SERVICE_TOKEN`` (token del service account Plenora in Grafana).
Tutto il resto in ``app.config.Settings.DEFAULTS``.
"""

from __future__ import annotations

from app.config import settings
from app.environment import get_env


def flag_enabled() -> bool:
    """Il solo interruttore ``GRAFANA_ENABLED`` (DEFAULTS, override da env)."""
    value = settings.get("GRAFANA_ENABLED")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def enabled() -> bool:
    """Funzione attiva = ``GRAFANA_ENABLED`` E ``GRAFANA_SERVICE_TOKEN``
    presente (il token esiste solo dopo il bootstrap di Grafana fatto
    dall'installer). Altrimenti nessuna chiamata a Grafana, creazione
    rifiutata, «Apri dashboard» non offerto."""
    return flag_enabled() and bool(service_token())


def api_url() -> str:
    return str(settings.get("GRAFANA_URL") or "").rstrip("/")


def public_path() -> str:
    return "/" + str(settings.get("GRAFANA_PUBLIC_PATH") or "/grafana").strip("/")


def data_url() -> str:
    return str(settings.get("GRAFANA_DATA_URL") or "").rstrip("/")


def datasource_uid() -> str:
    return str(settings.get("GRAFANA_DATASOURCE_UID") or "plenora-infinity")


def timeout_seconds() -> float:
    return float(settings.get("GRAFANA_TIMEOUT_SECONDS") or 5)


def warnings_timeout_seconds() -> float:
    return float(settings.get("GRAFANA_WARNINGS_TIMEOUT_SECONDS") or 1.5)


def warnings_cache_seconds() -> float:
    return float(settings.get("GRAFANA_WARNINGS_CACHE_SECONDS") or 60)


def access_sync_ttl_seconds() -> float:
    return float(settings.get("GRAFANA_ACCESS_SYNC_TTL_SECONDS") or 300)


def folder_resync_interval_seconds() -> float:
    return float(settings.get("GRAFANA_FOLDER_RESYNC_INTERVAL_SECONDS") or 900)


def data_purge_interval_seconds() -> float:
    return float(settings.get("DASHBOARD_DATA_PURGE_INTERVAL_SECONDS") or 300)


def session_ttl_seconds() -> int:
    return int(settings.get("GRAFANA_SESSION_TTL_SECONDS") or 1800)


def data_ttl_seconds() -> int:
    return int(settings.get("DASHBOARD_DATA_TTL_SECONDS") or 1800)


def pass_ttl_seconds() -> int:
    return int(settings.get("DASHBOARD_PASS_TTL_SECONDS") or 1800)


def data_max_rows() -> int:
    return int(settings.get("DASHBOARD_DATA_MAX_ROWS") or 50_000)


def data_max_bytes() -> int:
    return int(settings.get("DASHBOARD_DATA_MAX_BYTES") or 64 * 1024 * 1024)


def data_rate_per_minute() -> int:
    return int(settings.get("DASHBOARD_DATA_RATE_PER_MINUTE") or 120)


def _secret(name: str) -> str:
    return (get_env(name, "") or "").strip()


def pass_secret() -> str:
    return _secret("DASHBOARD_PASS_SECRET")


def data_secret() -> str:
    return _secret("GRAFANA_DATA_SECRET")


def service_token() -> str:
    return _secret("GRAFANA_SERVICE_TOKEN")


__all__ = [
    "access_sync_ttl_seconds",
    "api_url",
    "data_max_bytes",
    "data_max_rows",
    "data_purge_interval_seconds",
    "data_rate_per_minute",
    "data_secret",
    "data_ttl_seconds",
    "data_url",
    "datasource_uid",
    "enabled",
    "flag_enabled",
    "folder_resync_interval_seconds",
    "pass_secret",
    "pass_ttl_seconds",
    "public_path",
    "service_token",
    "session_ttl_seconds",
    "timeout_seconds",
    "warnings_cache_seconds",
    "warnings_timeout_seconds",
]
