"""JWT blacklist via Redis — revocation list per jti.

Quando un client fa `POST /auth/logout`, il suo `jti` viene scritto
in Redis con TTL = `exp - now`. Ad ogni richiesta autenticata
`get_current_user` verifica se il jti e' in blacklist → 401.

Dopo la scadenza naturale del token la chiave si auto-rimuove (no GC
manuale necessario).

Redis URL
  Leggiamo `settings.CELERY_BROKER_URL` (canonica unica in tutta l'app:
  la stessa istanza Redis fa da broker Celery, backend status checks e
  blacklist JWT). Nessuna variabile dedicata: un solo servizio Redis,
  una sola env var.

  Convivenza con Celery sullo stesso DB 0: le chiavi blacklist sono
  prefissate `plenora:jwt_blacklist:` e non collidono con il keyspace
  celery (`celery`, `_kombu.binding.*`, `unacked*`).

Policy fail-open sulla READ (check):
  Se Redis e' down, `is_jti_blacklisted()` ritorna False (no blocco).
  Rationale: JWT exp + signature restano enforced → un attaccante non
  puo' comunque forgiare/estendere. La finestra di esposizione di un
  token compromesso ma non ancora scaduto e' limitata dal TTL (default
  8h). Compromesso disponibilita' vs. security-strict, monitorato
  tramite alerting su Redis uptime.

Policy fail-loud sulla WRITE (logout):
  Se Redis e' down, `/auth/logout` ritorna 503. Il client deve sapere
  se la revoca e' effettiva — confermare falsamente un logout cambia
  la security posture percepita dall'utente.

Alternative valutate:
  - Blacklist in Postgres: scrittura/lettura hot path → overhead DB
    e locking inutili per dato TTL-based puro.
  - Fail-closed sulla read: Redis giu' => tutti i token invalidi =>
    blast radius dell'intera app. Troppo fragile per single-instance
    Redis.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_BLACKLIST_KEY_PREFIX = "plenora:jwt_blacklist:"
# Chiave di healthcheck: un EXISTS su questa chiave costa O(1) e non
# scrive nulla (no side effect). Usata da `get_blacklist_status` per
# sondare connettivita' + autorizzazioni Redis.
_HEALTHCHECK_KEY = f"{_BLACKLIST_KEY_PREFIX}healthcheck"

_client_lock = threading.Lock()
_client: Optional[Any] = None  # redis.Redis-like instance
# Se True, `_get_client()` ritorna `_client` cosi' com'e' senza tentare
# il lazy-init. Usato dai test per iniettare fakeredis oppure forzare
# `None` (simulazione Redis down) senza che il codice provi a ricollegarsi
# al Redis locale.
_client_explicit: bool = False

# METRICHE (contatori in-process)
#
# Contatori sincroni protetti da lock. Esposti via get_metrics() →
# admin_system_status.get_blacklist_status → dashboard admin.
#
# Scelta in-process vs. Redis INCR:
#   - in-process: zero overhead sulla hot path, azzerato a ogni restart
#     del backend. Ok per "health at a glance" nella dashboard.
#   - Redis INCR: persistente cross-restart ma aggiunge 1 round-trip
#     per ogni check di blacklist (raddoppia la latenza dell'auth hot
#     path). Non vale la pena: se serve storia persistente, meglio log
#     aggregation (Grafana/Loki) sui warning.
_metrics_lock = threading.Lock()
_metrics: Dict[str, int] = {
    "write_ok":       0,  # blacklist_jti ha scritto in Redis
    "write_fail":     0,  # blacklist_jti ha fallito (Redis down/error)
    "read_ok":        0,  # is_jti_blacklisted ha completato (qualunque risultato)
    "read_fail":      0,  # is_jti_blacklisted ha fallito (exception catturata)
    "client_missing": 0,  # _get_client() ha restituito None (redis-py o server)
}
# Timestamp dell'ultima operazione andata a buon fine (write OR read).
# Serve per l'alert "blacklist inoperante da X minuti": se ora - last_ok
# supera la soglia, la dashboard mostra warning. Epoch seconds UTC.
_last_ok_epoch: Optional[float] = None


def _record_metric(name: str, mark_ok: bool = False) -> None:
    """Incrementa un contatore, opzionalmente aggiorna `last_ok_epoch`."""
    global _last_ok_epoch
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + 1
        if mark_ok:
            _last_ok_epoch = time.time()


def _get_client() -> Optional[Any]:
    """Lazy init + cache del client Redis. None se init fallisce.

    Non rialzamo all'init: il caller decide cosa fare (fail-open/
    fail-loud). Il log e' a livello warning perche' in dev e' comune
    avere Redis off senza che sia un problema.
    """
    global _client
    if _client_explicit:
        return _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client_explicit:
            return _client
        if _client is not None:
            return _client
        try:
            import redis  # type: ignore
        except ImportError:
            logger.warning("redis-py non installato: blacklist disabilitata")
            return None
        try:
            # Import locale: evita import circolare all'import di
            # app.shared (config potrebbe a sua volta importare shared).
            from app.config import settings
            # Timeout configurabile via env (default 1s). Alzarlo in deploy
            # con Redis remoto / network latente; mantenerlo basso in
            # single-host per non bloccare il request handler se Redis
            # va giu' (fail-open kicks in dopo il timeout).
            # BE-CONFIG-001C: default tipato in `Settings.DEFAULTS`
            # (`JWT_BLACKLIST_REDIS_TIMEOUT_SEC=1`). Guard `<= 0` resta
            # safety per valori legali ma non sensati.
            try:
                timeout = int(settings.get("JWT_BLACKLIST_REDIS_TIMEOUT_SEC"))
                if timeout <= 0:
                    timeout = 1
            except (TypeError, ValueError):
                timeout = 1
            _client = redis.from_url(
                settings.CELERY_BROKER_URL,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                decode_responses=True,
            )
            return _client
        except Exception as e:
            logger.warning("Redis blacklist client init failed: %s", e)
            return None


def _key(jti: str) -> str:
    return f"{_BLACKLIST_KEY_PREFIX}{jti}"


def blacklist_jti(jti: str, ttl_seconds: int) -> bool:
    """Aggiunge `jti` alla blacklist per `ttl_seconds`.

    Ritorna True se scritto in Redis, False se jti vuoto / ttl<=0 /
    Redis non raggiungibile. Il caller (/auth/logout) trasforma
    False in 503.

    TTL idealmente = `exp - now` del token. Dopo quel tempo la
    chiave scade da sola e non sprechiamo RAM per token gia'
    naturalmente invalidi.
    """
    if not jti or ttl_seconds <= 0:
        # Input invalido: non lo contiamo come write_fail (non e' colpa
        # di Redis), torniamo False silenziosamente.
        return False
    client = _get_client()
    if client is None:
        _record_metric("client_missing")
        _record_metric("write_fail")
        return False
    try:
        client.setex(_key(jti), ttl_seconds, "1")
        _record_metric("write_ok", mark_ok=True)
        return True
    except Exception as e:
        logger.warning("Redis blacklist write failed (jti=%s): %s", jti, e)
        _record_metric("write_fail")
        return False


def is_jti_blacklisted(jti: Optional[str]) -> bool:
    """True se `jti` e' presente in blacklist. Fail-open su Redis giu'."""
    if not jti:
        # jti vuoto = token legacy: non incrementiamo metriche (non e'
        # un'operazione vera).
        return False
    client = _get_client()
    if client is None:
        _record_metric("client_missing")
        _record_metric("read_fail")
        return False
    try:
        result = bool(client.exists(_key(jti)))
        _record_metric("read_ok", mark_ok=True)
        return result
    except Exception as e:
        logger.warning("Redis blacklist read failed (jti=%s): %s", jti, e)
        _record_metric("read_fail")
        return False


# METRICHE + HEALTH


def get_metrics() -> Dict[str, Any]:
    """Snapshot dei contatori in-process + timestamp last_ok.

    Esposto via `admin_system_status.get_blacklist_status()` alla
    dashboard admin. Azzerato a ogni restart del backend (in-process,
    non persistente — per lo storico serve log aggregation).
    """
    with _metrics_lock:
        snapshot = dict(_metrics)
        last_ok = _last_ok_epoch
    return {
        "counters": snapshot,
        "last_ok_epoch": last_ok,
    }


def get_blacklist_status(stale_threshold_seconds: int = 1800) -> Dict[str, Any]:
    """Health probe: tenta un EXISTS su una chiave sentinel.

    Ritorna un dict con `status` ∈ {ok, unknown, error} + diagnostica:
      - ok: Redis ha risposto, blacklist operativa
      - unknown: redis-py non installato (dev env / container senza dep),
        oppure client non ancora inizializzato → None
      - error: Redis raggiungibile ma l'EXISTS ha sollevato (auth/ACL,
        timeout, disconnessione transitoria)

    Include `minutes_since_last_ok` calcolato su `_last_ok_epoch`
    (ultima write/read riuscita). Se il valore supera
    `stale_threshold_seconds` (default 30 min) il caller puo' alzare un
    alert "blacklist inoperante": anche se il check sentinel passa, il
    fatto che nessuna operazione reale sia riuscita recentemente e'
    sospetto (es. Redis risponde a PING ma rigetta i client auth).

    Design graceful degradation (contratto comune con admin_system_status):
    mai solleva, sempre ritorna dict.
    """
    client = _get_client()
    if client is None:
        try:
            import redis  # type: ignore  # noqa: F401
            message = "Client Redis non inizializzato"
        except ImportError:
            message = "redis-py non installato — blacklist disabilitata"
        return {
            "status": "unknown",
            "message": message,
            "counters": get_metrics()["counters"],
            "last_ok_epoch": _last_ok_epoch,
            "minutes_since_last_ok": None,
        }

    # Sentinel EXISTS: non crea la chiave, non modifica stato.
    try:
        client.exists(_HEALTHCHECK_KEY)
        check_ok = True
        check_error = None
    except Exception as e:
        check_ok = False
        check_error = str(e)[:200]
        logger.warning("Blacklist healthcheck EXISTS failed: %s", e)

    minutes_since_last_ok: Optional[float] = None
    if _last_ok_epoch is not None:
        minutes_since_last_ok = round((time.time() - _last_ok_epoch) / 60.0, 1)

    if check_ok:
        stale = (
            minutes_since_last_ok is not None
            and minutes_since_last_ok * 60.0 > stale_threshold_seconds
        )
        status = "warn" if stale else "ok"
        if stale:
            message = (
                f"Redis risponde ma nessuna operazione blacklist riuscita "
                f"da {minutes_since_last_ok} minuti "
                f"(soglia {stale_threshold_seconds // 60} min)"
            )
        else:
            message = "Blacklist operativa"
        return {
            "status": status,
            "message": message,
            "counters": get_metrics()["counters"],
            "last_ok_epoch": _last_ok_epoch,
            "minutes_since_last_ok": minutes_since_last_ok,
        }

    return {
        "status": "error",
        "message": "Redis raggiungibile ma EXISTS fallito",
        "error": check_error,
        "counters": get_metrics()["counters"],
        "last_ok_epoch": _last_ok_epoch,
        "minutes_since_last_ok": minutes_since_last_ok,
    }


def _reset_metrics_for_tests() -> None:
    """Test-only: azzera contatori e last_ok per isolare i test."""
    global _last_ok_epoch
    with _metrics_lock:
        for k in list(_metrics.keys()):
            _metrics[k] = 0
        _last_ok_epoch = None


def _set_client_for_tests(client: Optional[Any]) -> None:
    """Test-only: inietta un client custom (es. fakeredis) o None per
    simulare Redis down. Setta il flag `_client_explicit` cosi' il
    lazy-init non sovrascrive l'iniezione: passare None qui significa
    davvero "Redis non disponibile", non "non ancora inizializzato".
    """
    global _client, _client_explicit
    with _client_lock:
        _client = client
        _client_explicit = True


def _reset_client_for_tests() -> None:
    """Test-only: torna allo stato default (lazy-init al prossimo uso)."""
    global _client, _client_explicit
    with _client_lock:
        _client = None
        _client_explicit = False
