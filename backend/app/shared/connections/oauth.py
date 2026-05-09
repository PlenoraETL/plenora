"""
OAuth and token management helpers shared by API connectors.
"""

import base64
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

from app.utils.logger import get_logger

logger = get_logger(__name__)


# Cache thread-safe per token ArcGIS (un token per token_url/username).
_ARCGIS_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_ARCGIS_TOKEN_LOCK = threading.Lock()


# Cache thread-safe per token OAuth2 (lifecycle basato su expires_at).
_OAUTH2_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_OAUTH2_TOKEN_LOCK = threading.Lock()
_OAUTH2_REFRESH_THRESHOLD_SEC = 60


def _get_shared_session() -> requests.Session:
    from app.shared.api_utils import get_shared_session

    return get_shared_session()


def _oauth2_cache_key(auth: Dict) -> str:
    """Chiave cache univoca per un set di credenziali OAuth2."""
    auth_type = auth.get("type", "")
    token_url = auth.get("token_url", "")
    if auth_type == "oauth2_password":
        identifier = auth.get("username", "")
    else:
        identifier = auth.get("client_id", "")
    return f"{auth_type}|{token_url}|{identifier}"


def get_cached_oauth2_token(auth: Dict) -> Optional[str]:
    """Ritorna un token OAuth2 valido, rinnovandolo solo se sta per scadere."""
    auth_type = auth.get("type", "")
    if auth_type not in ("oauth2_password", "oauth2_client_credentials"):
        return None

    cache_key = _oauth2_cache_key(auth)
    now = time.time()

    cached = _OAUTH2_TOKEN_CACHE.get(cache_key)
    if cached and (cached["expires_at"] - now) > _OAUTH2_REFRESH_THRESHOLD_SEC:
        return cached["token"]

    with _OAUTH2_TOKEN_LOCK:
        cached = _OAUTH2_TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and (cached["expires_at"] - now) > _OAUTH2_REFRESH_THRESHOLD_SEC:
            return cached["token"]

        if auth_type == "oauth2_password":
            result = _fetch_oauth2_password_token(auth)
        else:
            result = _fetch_oauth2_client_credentials_token(auth)

        if not result or not result.get("access_token"):
            return None

        token = result["access_token"]
        expires_in = int(result.get("expires_in", 3600))
        _OAUTH2_TOKEN_CACHE[cache_key] = {
            "token": token,
            "expires_at": time.time() + expires_in,
        }
        logger.info(
            f"OAuth2 token rinnovato ({auth_type}, valido per {expires_in}s)"
        )
        return token


def invalidate_oauth2_token(auth: Dict) -> None:
    """Forza invalidazione cache per un set di credenziali."""
    cache_key = _oauth2_cache_key(auth)
    with _OAUTH2_TOKEN_LOCK:
        _OAUTH2_TOKEN_CACHE.pop(cache_key, None)


def _fetch_oauth2_password_token(auth: Dict) -> Optional[Dict]:
    """Fetch diretto di un token password grant."""
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("OAuth2 password: token_url o username mancante")
        return None

    try:
        response = _get_shared_session().post(
            token_url,
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            masked = f"{username[:3]}{'*' * max(0, len(username) - 3)}"
            logger.info(f"OAuth2 password token ottenuto per user={masked}")
            return data
        logger.warning(
            f"OAuth2 password token fallito: HTTP {response.status_code} - {response.text[:200]}"
        )
        return None
    except Exception as e:
        logger.error(f"[CRASH] Errore OAuth2 password grant: {e}", exc_info=True)
        return None


def _fetch_oauth2_client_credentials_token(auth: Dict) -> Optional[Dict]:
    """Fetch diretto di un token client_credentials grant."""
    token_url = auth.get("token_url", "")
    client_id = auth.get("client_id", "")
    client_secret = auth.get("client_secret", "")
    scope = auth.get("scope", "")

    if not token_url or not client_id:
        return None

    try:
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            data["scope"] = scope

        response = _get_shared_session().post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            verify=True,
        )

        if response.status_code == 200:
            masked = f"{client_id[:4]}{'*' * max(0, len(client_id) - 4)}"
            logger.info(f"OAuth2 CC token ottenuto per client={masked}")
            return response.json()

        logger.warning(f"OAuth2 CC token fallito: HTTP {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"[CRASH] Errore OAuth2 client_credentials: {e}", exc_info=True)
        return None


def get_arcgis_token(auth: Dict) -> Optional[str]:
    """
    Ottiene un token di autenticazione ArcGIS Token Service (ESRI).
    """
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("ArcGIS token: token_url o username mancante")
        return None

    cache_key = f"{token_url}|{username}"

    with _ARCGIS_TOKEN_LOCK:
        cached = _ARCGIS_TOKEN_CACHE.get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time() + 60:
            return cached["token"]

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

    verify_ssl = auth.get("verify_ssl", True)
    try:
        if not verify_ssl:
            try:
                from urllib3.exceptions import InsecureRequestWarning
                import urllib3

                urllib3.disable_warnings(InsecureRequestWarning)
            except Exception:
                pass

        response = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            verify=verify_ssl,
        )
        if response.status_code != 200:
            logger.warning(
                f"ArcGIS token fallito: HTTP {response.status_code} - {response.text[:200]}"
            )
            return None

        data = response.json()
        if "error" in data:
            logger.warning(f"ArcGIS token errore: {data.get('error')}")
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

        with _ARCGIS_TOKEN_LOCK:
            _ARCGIS_TOKEN_CACHE[cache_key] = {
                "token": token,
                "expires_at": expires_at,
            }

        masked = f"{username[:3]}{'*' * max(0, len(username) - 3)}"
        ttl_min = (expires_at - time.time()) / 60
        logger.info(f"ArcGIS token ottenuto per user={masked}, valido {ttl_min:.0f} min")
        return token

    except Exception as e:
        logger.error(f"[CRASH] Errore richiesta ArcGIS token: {e}", exc_info=True)
        return None


def prepare_headers(
    auth: Dict,
    custom_headers: Dict = None,
    *,
    oauth2_token_getter: Callable[[Dict], Optional[str]] = get_cached_oauth2_token,
) -> Dict:
    """
    Prepara headers HTTP per chiamata API REST.
    """
    headers: Dict = {
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
