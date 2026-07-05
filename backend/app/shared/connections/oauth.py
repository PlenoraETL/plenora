"""
OAuth and token management helpers shared by API connectors.
"""

import base64
import hashlib
import json
import threading
import time
import uuid
import warnings as _warnings
from typing import Any, Callable, MutableMapping, Optional

import requests
from urllib3.exceptions import InsecureRequestWarning

from app import config as app_config
from app.core.security.outbound_url import validate_external_url
from app.shared.security.tls_policy import resolve_tls_verify
from app.infrastructure.http.session import get_shared_session
from app.shared.contracts.payloads import HeadersMap, JsonObject, StringMap
from app.utils.logger import get_logger

logger = get_logger(__name__)


# Cache thread-safe per token ArcGIS (un token per token_url/username).
_ARCGIS_TOKEN_CACHE: dict[str, JsonObject] = {}
_ARCGIS_TOKEN_LOCK = threading.Lock()


# Cache thread-safe per token OAuth2 (lifecycle basato su expires_at).
_OAUTH2_TOKEN_CACHE: dict[str, JsonObject] = {}
_OAUTH2_TOKEN_LOCK = threading.Lock()
_OAUTH2_REFRESH_THRESHOLD_SEC = 60


_TOKEN_CACHE_REDIS_KEY_PREFIX = "plenora:connections:token"
_TOKEN_CACHE_LOCK_KEY_PREFIX = "plenora:connections:token_lock"
_TOKEN_CACHE_DEFAULT_REDIS_TIMEOUT_SEC = 1
_TOKEN_CACHE_DEFAULT_LOCK_TTL_SEC = 30
_TOKEN_CACHE_DEFAULT_WAIT_TIMEOUT_SEC = 5
_TOKEN_CACHE_POLL_INTERVAL_SEC = 0.1

_TOKEN_CACHE_CLIENT_LOCK = threading.Lock()
_TOKEN_CACHE_CLIENT: Optional[Any] = None
_TOKEN_CACHE_CLIENT_EXPLICIT = False
_TOKEN_CACHE_WARNED_FALLBACK = False


def _get_shared_session() -> requests.Session:
    return get_shared_session()


def _setting_int(name: str, default: int) -> int:
    try:
        value = int(app_config.settings.get(name, default))
        return value if value > 0 else default
    except Exception:
        return default


def _setting_float(name: str, default: float) -> float:
    try:
        value = float(app_config.settings.get(name, default))
        return value if value > 0 else default
    except Exception:
        return default


def _get_token_cache_store() -> Optional[Any]:
    """Backend del token cache distribuito (Plenora 2.0 Fase 4: KeyValueStore
    su PostgreSQL al posto di Redis). None se non inizializzabile -> fallback
    locale (cache in-process)."""
    global _TOKEN_CACHE_CLIENT
    if _TOKEN_CACHE_CLIENT_EXPLICIT:
        return _TOKEN_CACHE_CLIENT
    if _TOKEN_CACHE_CLIENT is not None:
        return _TOKEN_CACHE_CLIENT
    with _TOKEN_CACHE_CLIENT_LOCK:
        if _TOKEN_CACHE_CLIENT_EXPLICIT:
            return _TOKEN_CACHE_CLIENT
        if _TOKEN_CACHE_CLIENT is not None:
            return _TOKEN_CACHE_CLIENT
        try:
            from app.shared.kv import PgKeyValueStore

            _TOKEN_CACHE_CLIENT = PgKeyValueStore()
            return _TOKEN_CACHE_CLIENT
        except Exception as exc:
            _warn_token_cache_fallback_once(type(exc).__name__)
            return None


def _warn_token_cache_fallback_once(reason: str) -> None:
    global _TOKEN_CACHE_WARNED_FALLBACK
    if _TOKEN_CACHE_WARNED_FALLBACK:
        return
    _TOKEN_CACHE_WARNED_FALLBACK = True
    logger.warning(
        "OAuth/API token Redis cache non disponibile (%s); fallback locale attivo",
        reason,
    )


def _distributed_token_cache_key(namespace: str, raw_key: str) -> str:
    digest = hashlib.sha256(f"{namespace}|{raw_key}".encode("utf-8")).hexdigest()
    return f"{_TOKEN_CACHE_REDIS_KEY_PREFIX}:{namespace}:{digest}"


def _distributed_token_lock_key(namespace: str, raw_key: str) -> str:
    digest = hashlib.sha256(f"{namespace}|{raw_key}".encode("utf-8")).hexdigest()
    return f"{_TOKEN_CACHE_LOCK_KEY_PREFIX}:{namespace}:{digest}"


def _token_expires_at(record: JsonObject) -> float:
    try:
        return float(record.get("expires_at", record.get("expires", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_token_record_valid(record: Optional[JsonObject], min_ttl_sec: int) -> bool:
    if not record or not record.get("token"):
        return False
    return (_token_expires_at(record) - time.time()) > min_ttl_sec


def _normalize_token_record(record: JsonObject) -> JsonObject:
    token = str(record.get("token") or record.get("access_token") or "")
    expires_at = _token_expires_at(record)
    return {
        "token": token,
        "expires_at": expires_at,
        "expires": expires_at,
    }


def _read_token_record(
    store: Any,
    store_key: str,
    min_ttl_sec: int,
) -> Optional[JsonObject]:
    raw_payload = store.get(store_key)  # KeyValueStore.get -> str | None
    if not raw_payload:
        return None
    try:
        payload = json.loads(str(raw_payload))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    record = _normalize_token_record(payload)
    if not _is_token_record_valid(record, min_ttl_sec):
        return None
    return record


def _write_token_record(store: Any, store_key: str, record: JsonObject) -> None:
    ttl_seconds = max(1, int(_token_expires_at(record) - time.time()))
    payload = json.dumps(
        {
            "token": record["token"],
            "expires_at": _token_expires_at(record),
        },
        separators=(",", ":"),
    )
    store.set(store_key, payload, ttl_seconds=ttl_seconds)


def _acquire_token_lock(store: Any, lock_key: str, owner: str) -> bool:
    lock_ttl = _setting_int(
        "OAUTH_TOKEN_CACHE_LOCK_TTL_SEC",
        _TOKEN_CACHE_DEFAULT_LOCK_TTL_SEC,
    )
    # owner-safe: il KeyValueStore memorizza l'owner; il release lo verifica.
    return bool(store.acquire_lock(lock_key, ttl_seconds=lock_ttl, owner=owner))


def _release_token_lock(store: Any, lock_key: str, owner: str) -> None:
    try:
        # release_lock(owner=...) cancella SOLO se siamo ancora owner
        # (owner-safe, ora nel protocol KeyValueStore).
        store.release_lock(lock_key, owner=owner)
    except Exception as exc:
        logger.debug(
            "OAuth/API token lock release failed: %s",
            type(exc).__name__,
        )


def _wait_for_token_record(
    store: Any,
    store_key: str,
    min_ttl_sec: int,
) -> Optional[JsonObject]:
    wait_timeout = _setting_float(
        "OAUTH_TOKEN_CACHE_WAIT_TIMEOUT_SEC",
        _TOKEN_CACHE_DEFAULT_WAIT_TIMEOUT_SEC,
    )
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        cached = _read_token_record(store, store_key, min_ttl_sec)
        if cached:
            return cached
        time.sleep(_TOKEN_CACHE_POLL_INTERVAL_SEC)
    return None


def _get_or_refresh_token_record(
    *,
    namespace: str,
    cache_key: str,
    local_cache: MutableMapping[str, JsonObject],
    local_lock: threading.Lock,
    refresh_threshold_sec: int,
    fetch_token_record: Callable[[], Optional[JsonObject]],
) -> Optional[str]:
    cached = local_cache.get(cache_key)
    if _is_token_record_valid(cached, refresh_threshold_sec):
        return str(cached["token"])

    store_key = _distributed_token_cache_key(namespace, cache_key)
    lock_key = _distributed_token_lock_key(namespace, cache_key)

    store = _get_token_cache_store()
    if store is not None:
        try:
            cached = _read_token_record(store, store_key, refresh_threshold_sec)
            if cached:
                with local_lock:
                    local_cache[cache_key] = cached
                return str(cached["token"])
        except Exception as exc:
            _warn_token_cache_fallback_once(type(exc).__name__)
            store = None

    with local_lock:
        cached = local_cache.get(cache_key)
        if _is_token_record_valid(cached, refresh_threshold_sec):
            return str(cached["token"])

        if store is not None:
            owner: Optional[str] = None
            try:
                cached = _read_token_record(store, store_key, refresh_threshold_sec)
                if cached:
                    local_cache[cache_key] = cached
                    return str(cached["token"])

                owner = uuid.uuid4().hex
                if not _acquire_token_lock(store, lock_key, owner):
                    owner = None
                    cached = _wait_for_token_record(
                        store,
                        store_key,
                        refresh_threshold_sec,
                    )
                    if cached:
                        local_cache[cache_key] = cached
                        return str(cached["token"])
                    _warn_token_cache_fallback_once(
                        "timeout attesa refresh distribuito"
                    )
            except Exception as exc:
                _warn_token_cache_fallback_once(type(exc).__name__)
                owner = None

            if owner is not None:
                return _refresh_token_with_lock(
                    store=store,
                    store_key=store_key,
                    lock_key=lock_key,
                    owner=owner,
                    local_cache=local_cache,
                    cache_key=cache_key,
                    refresh_threshold_sec=refresh_threshold_sec,
                    fetch_token_record=fetch_token_record,
                )

        fetched = fetch_token_record()
        if not fetched or not fetched.get("token"):
            return None
        record = _normalize_token_record(fetched)
        local_cache[cache_key] = record
        return str(record["token"])


def _refresh_token_with_lock(
    *,
    store: Any,
    store_key: str,
    lock_key: str,
    owner: str,
    local_cache: MutableMapping[str, JsonObject],
    cache_key: str,
    refresh_threshold_sec: int,
    fetch_token_record: Callable[[], Optional[JsonObject]],
) -> Optional[str]:
    try:
        cached = _read_token_record(store, store_key, refresh_threshold_sec)
        if cached:
            local_cache[cache_key] = cached
            return str(cached["token"])
    except Exception as exc:
        _warn_token_cache_fallback_once(type(exc).__name__)

    try:
        fetched = fetch_token_record()
        if not fetched or not fetched.get("token"):
            return None
        record = _normalize_token_record(fetched)
        local_cache[cache_key] = record
        try:
            _write_token_record(store, store_key, record)
        except Exception as exc:
            _warn_token_cache_fallback_once(type(exc).__name__)
        return str(record["token"])
    finally:
        _release_token_lock(store, lock_key, owner)


def _invalidate_token_record(
    *,
    namespace: str,
    cache_key: str,
    local_cache: Optional[MutableMapping[str, JsonObject]] = None,
    local_lock: Optional[threading.Lock] = None,
) -> None:
    if local_cache is not None:
        if local_lock is None:
            local_cache.pop(cache_key, None)
        else:
            with local_lock:
                local_cache.pop(cache_key, None)
    store = _get_token_cache_store()
    if store is not None:
        try:
            store.delete(_distributed_token_cache_key(namespace, cache_key))
        except Exception as exc:
            _warn_token_cache_fallback_once(type(exc).__name__)


def _oauth2_cache_key(auth: JsonObject) -> str:
    """Chiave cache univoca per un set di credenziali OAuth2."""
    auth_type = auth.get("type", "")
    token_url = auth.get("token_url", "")
    if auth_type == "oauth2_password":
        identifier = auth.get("username", "")
        secret_material = "|".join([
            str(auth.get("client_id", "")),
            str(auth.get("client_secret", "")),
            str(auth.get("password", "")),
            str(auth.get("scope", "")),
        ])
    else:
        identifier = auth.get("client_id", "")
        secret_material = "|".join([
            str(auth.get("client_secret", "")),
            str(auth.get("scope", "")),
        ])
    secret_hash = hashlib.sha256(secret_material.encode("utf-8")).hexdigest()[:16]
    return f"{auth_type}|{token_url}|{identifier}|{secret_hash}"


def _api_timeout_seconds() -> int:
    return _setting_int("API_TIMEOUT", 30)


def _validate_token_url(token_url: str) -> bool:
    try:
        validate_external_url(token_url)
        return True
    except ValueError as exc:
        logger.warning("Token URL bloccato dalla policy outbound: %s", exc)
        return False


def get_cached_oauth2_token(auth: JsonObject) -> Optional[str]:
    """Ritorna un token OAuth2 valido, rinnovandolo solo se sta per scadere."""
    auth_type = auth.get("type", "")
    if auth_type not in ("oauth2_password", "oauth2_client_credentials"):
        return None

    cache_key = _oauth2_cache_key(auth)

    def _fetch_record() -> Optional[JsonObject]:
        if auth_type == "oauth2_password":
            result = _fetch_oauth2_password_token(auth)
        else:
            result = _fetch_oauth2_client_credentials_token(auth)

        if not result or not result.get("access_token"):
            return None

        expires_in = int(result.get("expires_in", 3600))
        logger.info(
            "OAuth2 token rinnovato (%s, valido per %ss)",
            auth_type,
            expires_in,
        )
        return {
            "token": result["access_token"],
            "expires_at": time.time() + expires_in,
        }

    return _get_or_refresh_token_record(
        namespace="oauth2",
        cache_key=cache_key,
        local_cache=_OAUTH2_TOKEN_CACHE,
        local_lock=_OAUTH2_TOKEN_LOCK,
        refresh_threshold_sec=_OAUTH2_REFRESH_THRESHOLD_SEC,
        fetch_token_record=_fetch_record,
    )


def invalidate_oauth2_token(auth: JsonObject) -> None:
    """Forza invalidazione cache per un set di credenziali."""
    cache_key = _oauth2_cache_key(auth)
    _invalidate_token_record(
        namespace="oauth2",
        cache_key=cache_key,
        local_cache=_OAUTH2_TOKEN_CACHE,
        local_lock=_OAUTH2_TOKEN_LOCK,
    )


def _fetch_oauth2_password_token(auth: JsonObject) -> Optional[JsonObject]:
    """Fetch diretto di un token password grant."""
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")
    client_id = auth.get("client_id", "")
    client_secret = auth.get("client_secret", "")
    scope = auth.get("scope", "")

    if not token_url or not username:
        logger.warning("OAuth2 password: token_url o username mancante")
        return None
    if not _validate_token_url(token_url):
        return None

    try:
        payload: StringMap = {
            "grant_type": "password",
            "username": username,
            "password": password,
        }
        if client_id:
            payload["client_id"] = client_id
        if client_secret:
            payload["client_secret"] = client_secret
        if scope:
            payload["scope"] = scope

        response = _get_shared_session().post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_api_timeout_seconds(),
            allow_redirects=False,
        )
        if response.status_code == 200:
            data = response.json()
            masked = f"{username[:3]}{'*' * max(0, len(username) - 3)}"
            logger.info(f"OAuth2 password token ottenuto per user={masked}")
            return data
        logger.warning("OAuth2 password token fallito: HTTP %s", response.status_code)
        return None
    except Exception as e:
        # Defense-in-depth: il `f"{e}"` di alcune eccezioni `requests.*`
        # puo' contenere stringhe del payload form-urlencoded (password,
        # client_secret) anche se il `SanitizingFilter` lavora a posteriori.
        # Loggiamo solo il `type(e).__name__`; il dettaglio sanificato
        # resta nel traceback via `exc_info=True`.
        logger.error(
            "[CRASH] Errore OAuth2 password grant: %s",
            type(e).__name__,
            exc_info=True,
        )
        return None


def _fetch_oauth2_client_credentials_token(
    auth: JsonObject,
    *,
    extra_form_data: Optional[JsonObject] = None,
) -> Optional[JsonObject]:
    """Fetch diretto di un token client_credentials grant.

    `extra_form_data` consente ai caller provider-specific di aggiungere
    campi al body POST senza forkare la funzione (es. ArcGIS richiede
    `f=json` per forzare la response JSON sul vecchio endpoint
    `/sharing/rest/oauth2/token`). Pre-fix `arcgis_auth.py` aveva una
    implementazione duplicata di questa funzione con poche differenze.
    """
    token_url = auth.get("token_url", "")
    client_id = auth.get("client_id", "")
    client_secret = auth.get("client_secret", "")
    scope = auth.get("scope", "")

    if not token_url or not client_id:
        return None
    if not _validate_token_url(token_url):
        return None

    try:
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            data["scope"] = scope
        if extra_form_data:
            data.update(extra_form_data)

        response = _get_shared_session().post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_api_timeout_seconds(),
            verify=True,
            allow_redirects=False,
        )

        if response.status_code == 200:
            masked = f"{client_id[:4]}{'*' * max(0, len(client_id) - 4)}"
            logger.info(f"OAuth2 CC token ottenuto per client={masked}")
            return response.json()

        logger.warning(f"OAuth2 CC token fallito: HTTP {response.status_code}")
        return None
    except Exception as e:
        # Vedi commento equivalente in `_fetch_oauth2_password_token`:
        # l'eccezione puo' contenere il payload con `client_secret`,
        # loggiamo solo il tipo. Traceback sanitizzato via exc_info.
        logger.error(
            "[CRASH] Errore OAuth2 client_credentials: %s",
            type(e).__name__,
            exc_info=True,
        )
        return None


def get_arcgis_token(auth: JsonObject) -> Optional[str]:
    """
    Ottiene un token di autenticazione ArcGIS Token Service (ESRI).
    """
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("ArcGIS token: token_url o username mancante")
        return None
    if not _validate_token_url(token_url):
        return None

    secret_material = "|".join([
        str(password),
        str(auth.get("client", "requestip")),
        str(auth.get("referer", "")),
        str(auth.get("ip", "")),
    ])
    secret_hash = hashlib.sha256(secret_material.encode("utf-8")).hexdigest()[:16]
    cache_key = f"{token_url}|{username}|{secret_hash}"

    def _fetch_record() -> Optional[JsonObject]:
        client_mode = auth.get("client", "requestip")
        payload = {
            "username": username,
            "password": password,
            "client": client_mode,
            "expiration": int(auth.get("expiration", 60)),
            "f": "json",
        }
        if client_mode == "referer":
            payload["referer"] = auth.get("referer", "")
        elif client_mode == "ip":
            payload["ip"] = auth.get("ip", "")

        verify_ssl = resolve_tls_verify(
            auth.get("verify_ssl", True), context="ArcGIS token"
        )
        try:
            # Filtro warning SSL solo nello scope del POST (dev only, quando
            # verify_ssl=False sopravvive a _resolve_tls_verify). Niente
            # `urllib3.disable_warnings` globale — quello sopprimerebbe i
            # warning per l'intero processo e mascherebbe future regressioni
            # TLS in altri moduli.
            with _warnings.catch_warnings():
                if not verify_ssl:
                    _warnings.simplefilter("ignore", InsecureRequestWarning)
                response = requests.post(
                    token_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=_api_timeout_seconds(),
                    verify=verify_ssl,
                    allow_redirects=False,
                )
            if response.status_code != 200:
                logger.warning("ArcGIS token fallito: HTTP %s", response.status_code)
                return None

            data = response.json()
            if "error" in data:
                logger.warning("ArcGIS token errore: %s", data.get("error"))
                return None

            token = data.get("token")
            expires_raw = data.get("expires")
            if not token:
                logger.warning("ArcGIS token: nessun token nella risposta")
                return None

            try:
                expires_at = int(expires_raw) / 1000.0
            except (TypeError, ValueError):
                expires_at = time.time() + (int(auth.get("expiration", 60)) * 60)

            masked = f"{username[:3]}{'*' * max(0, len(username) - 3)}"
            ttl_min = (expires_at - time.time()) / 60
            logger.info(
                "ArcGIS token ottenuto per user=%s, valido %.0f min",
                masked,
                ttl_min,
            )
            return {
                "token": token,
                "expires_at": expires_at,
            }

        except Exception as e:
            # Pre-fix: `%s, e` propagava lo str(e) raw, che su errori HTTP
            # di urllib3/requests puo' contenere l'URL token con query
            # params (token, client_secret) o credenziali nei response body
            # inclusi nell'eccezione. Allineato a `_fetch_oauth2_password_token`
            # e `_fetch_oauth2_client_credentials_token` nello stesso file
            # (gia' fixati in sessione precedente).
            logger.error(
                "[CRASH] Errore richiesta ArcGIS token: %s",
                type(e).__name__,
                exc_info=True,
            )
            return None

    return _get_or_refresh_token_record(
        namespace="arcgis-token",
        cache_key=cache_key,
        local_cache=_ARCGIS_TOKEN_CACHE,
        local_lock=_ARCGIS_TOKEN_LOCK,
        refresh_threshold_sec=_OAUTH2_REFRESH_THRESHOLD_SEC,
        fetch_token_record=_fetch_record,
    )


def prepare_headers(
    auth: JsonObject,
    custom_headers: Optional[HeadersMap] = None,
    *,
    oauth2_token_getter: Callable[[JsonObject], Optional[str]] = get_cached_oauth2_token,
) -> HeadersMap:
    """
    Prepara headers HTTP per chiamata API REST.
    """
    headers: HeadersMap = {
        "Accept": "application/json",
        "User-Agent": "PLENORA-ETL/1.0",
    }

    if custom_headers:
        headers.update(custom_headers)

    auth_type = auth.get("type", "none")

    if auth_type in ("bearer", "token"):
        token = auth.get("token") or auth.get("access_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "oauth2_password":
        token = oauth2_token_getter(auth)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "oauth2_client_credentials":
        token = oauth2_token_getter(auth)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "api_key":
        if auth.get("location", "header") == "header":
            headers[auth.get("key_name", "X-API-Key")] = auth.get("key_value", "")

    elif auth_type in ("basic_auth", "basic"):
        username = auth.get("username", "")
        password = auth.get("password", "")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"

    elif auth_type == "arcgis_token":
        pass

    return headers


def _set_token_cache_client_for_tests(client: Optional[Any]) -> None:
    global _TOKEN_CACHE_CLIENT, _TOKEN_CACHE_CLIENT_EXPLICIT
    with _TOKEN_CACHE_CLIENT_LOCK:
        _TOKEN_CACHE_CLIENT = client
        _TOKEN_CACHE_CLIENT_EXPLICIT = True


def _reset_token_cache_state_for_tests() -> None:
    global _TOKEN_CACHE_CLIENT, _TOKEN_CACHE_CLIENT_EXPLICIT, _TOKEN_CACHE_WARNED_FALLBACK
    with _TOKEN_CACHE_CLIENT_LOCK:
        _TOKEN_CACHE_CLIENT = None
        _TOKEN_CACHE_CLIENT_EXPLICIT = False
    with _OAUTH2_TOKEN_LOCK:
        _OAUTH2_TOKEN_CACHE.clear()
    with _ARCGIS_TOKEN_LOCK:
        _ARCGIS_TOKEN_CACHE.clear()
    _TOKEN_CACHE_WARNED_FALLBACK = False
