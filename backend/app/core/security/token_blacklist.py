"""
JWT blacklist su PostgreSQL + cache in-memory: revocation list per jti.

Il backend e' il KeyValueStore (PostgreSQL, tabella kv_store). La lettura
`is_jti_blacklisted` e' sul percorso
auth per-richiesta: davanti a PG c'e' una cache in-memory (process-local) che
memorizza sia i revocati ("1") sia i validi ("0") con TTL breve, cosi' il caso
comune (token validi) e' servito dalla RAM e resta nel budget di latenza.

Trade-off cache negativa: un jti revocato via logout puo' restare accettato
fino al TTL della cache negativa (default 5s, JWT_BLACKLIST_CACHE_TTL_SEC) sui
processi che lo avevano gia' visto come valido. Stesso processo: la write
sovrascrive subito la cache, revoca immediata.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from app.shared.contracts.payloads import IntMap, JsonObject
from app.shared.kv import InMemoryKeyValueStore, KeyValueStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BLACKLIST_KEY_PREFIX = "plenora:jwt_blacklist:"
# Chiave sentinel per l'healthcheck: un exists O(1) senza side effect.
_HEALTHCHECK_KEY = f"{_BLACKLIST_KEY_PREFIX}healthcheck"

_DEFAULT_CACHE_TTL_SEC = 5

_client_lock = threading.Lock()
# Backend KeyValueStore (sorgente di verita', PostgreSQL in produzione).
_client: Optional[KeyValueStore] = None
# Se True, `_get_client` ritorna `_client` cosi' com'e' (iniezione test).
_client_explicit: bool = False

# Cache process-local davanti al backend: assorbe la hot path (read auth).
_cache: InMemoryKeyValueStore = InMemoryKeyValueStore()

_metrics_lock = threading.Lock()
_metrics: IntMap = {
    "write_ok": 0,
    "write_fail": 0,
    "read_ok": 0,
    "read_fail": 0,
    "client_missing": 0,
}
_last_ok_epoch: Optional[float] = None


def _record_metric(name: str, mark_ok: bool = False) -> None:
    global _last_ok_epoch
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + 1
        if mark_ok:
            _last_ok_epoch = time.time()


def _cache_ttl_seconds() -> int:
    """TTL della cache (positiva e negativa). Configurabile via settings."""
    try:
        from app.config import settings

        ttl = int(settings.get("JWT_BLACKLIST_CACHE_TTL_SEC"))
        return ttl if ttl > 0 else _DEFAULT_CACHE_TTL_SEC
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_TTL_SEC


def _get_client() -> Optional[KeyValueStore]:
    """Lazy init del backend KeyValueStore (PostgreSQL). None se fallisce.

    Non rialza all'init: il caller decide la policy (fail-open/fail-loud)."""
    global _client
    if _client_explicit:
        return _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client_explicit or _client is not None:
            return _client
        try:
            from app.shared.kv import PgKeyValueStore

            _client = PgKeyValueStore()
            return _client
        except Exception as e:
            logger.warning("KeyValueStore blacklist init failed: %s", e)
            return None


def _key(jti: str) -> str:
    return f"{_BLACKLIST_KEY_PREFIX}{jti}"


def blacklist_jti(jti: str, ttl_seconds: int) -> bool:
    """Aggiunge `jti` alla blacklist (PostgreSQL) per `ttl_seconds`.

    Ritorna True se scritto, False se input invalido / backend giu'.
    Il caller (/auth/logout) trasforma False in 503. La cache locale viene
    popolata a "revocato" (sovrascrive un'eventuale entry negativa) cosi' la
    revoca e' immediata nel processo corrente.
    """
    if not jti or ttl_seconds <= 0:
        return False
    client = _get_client()
    if client is None:
        _record_metric("client_missing")
        _record_metric("write_fail")
        return False
    try:
        client.set(_key(jti), "1", ttl_seconds=ttl_seconds)
        _cache.set(_key(jti), "1", ttl_seconds=_cache_ttl_seconds())
        _record_metric("write_ok", mark_ok=True)
        return True
    except Exception as e:
        logger.warning("Blacklist write failed (jti=%s): %s", jti, e)
        _record_metric("write_fail")
        return False


def is_jti_blacklisted(jti: Optional[str]) -> bool:
    """True se `jti` e' in blacklist. Cache-first, fail-open su backend giu'.

    Cache hit ("1"=revocato / "0"=valido) -> risposta dalla RAM. Miss ->
    lettura PG, poi cache del risultato (positiva o negativa) con TTL breve.

    SECURITY TRADE-OFF: fail-open deliberato (backend giu' -> token accettato),
    per non trasformare un'outage del backend in DoS totale dell'auth. Errore
    loggato [SECURITY] ERROR + metrica read_fail. La cache NON viene popolata
    sul fail-open (si ritenta al prossimo check).
    """
    if not jti:
        return False
    key = _key(jti)
    cached = _cache.get(key)
    if cached is not None:
        return cached == "1"
    client = _get_client()
    if client is None:
        _record_metric("client_missing")
        _record_metric("read_fail")
        return False
    try:
        revoked = bool(client.exists(key))
        _cache.set(key, "1" if revoked else "0", ttl_seconds=_cache_ttl_seconds())
        _record_metric("read_ok", mark_ok=True)
        return revoked
    except Exception as e:
        logger.error(
            "[SECURITY] Blacklist read failed — fail-open attivo "
            "(token revocati restano accettati fino a TTL): jti=%s, "
            "error=%s: %s",
            jti, type(e).__name__, e,
        )
        _record_metric("read_fail")
        return False


def get_metrics() -> JsonObject:
    """Snapshot dei contatori in-process + timestamp last_ok."""
    with _metrics_lock:
        snapshot = dict(_metrics)
        last_ok = _last_ok_epoch
    return {
        "counters": snapshot,
        "last_ok_epoch": last_ok,
    }


def get_blacklist_status(stale_threshold_seconds: int = 1800) -> JsonObject:
    """Health probe: tenta un exists su una chiave sentinel del backend PG.

    status in {ok, warn, unknown, error}. Graceful: mai solleva, sempre dict.
    """
    client = _get_client()
    if client is None:
        return {
            "status": "unknown",
            "message": "Backend KeyValueStore non inizializzato",
            "counters": get_metrics()["counters"],
            "last_ok_epoch": _last_ok_epoch,
            "minutes_since_last_ok": None,
        }

    try:
        client.exists(_HEALTHCHECK_KEY)
        check_ok = True
        check_error = None
    except Exception as e:
        check_ok = False
        check_error = str(e)[:200]
        logger.warning("Blacklist healthcheck exists failed: %s", e)

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
                f"Backend risponde ma nessuna operazione blacklist riuscita "
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
        "message": "Backend raggiungibile ma exists fallito",
        "error": check_error,
        "counters": get_metrics()["counters"],
        "last_ok_epoch": _last_ok_epoch,
        "minutes_since_last_ok": minutes_since_last_ok,
    }


def _reset_metrics_for_tests() -> None:
    """Test-only: azzera contatori e last_ok."""
    global _last_ok_epoch
    with _metrics_lock:
        for k in list(_metrics.keys()):
            _metrics[k] = 0
        _last_ok_epoch = None


def _set_client_for_tests(client: Optional[KeyValueStore]) -> None:
    """Test-only: inietta un KeyValueStore backend (es. InMemoryKeyValueStore)
    o None per simulare il backend giu'. Setta `_client_explicit` e azzera la
    cache locale per isolare i test."""
    global _client, _client_explicit
    with _client_lock:
        _client = client
        _client_explicit = True
    _cache.__init__()  # reset cache process-local


def _reset_client_for_tests() -> None:
    """Test-only: torna allo stato default (lazy-init) e azzera la cache."""
    global _client, _client_explicit
    with _client_lock:
        _client = None
        _client_explicit = False
    _cache.__init__()
