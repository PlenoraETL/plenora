"""
app/shared/api_utils.py

Logica HTTP/API condivisa tra api_connector.py e enrichment_service.py.
"""

import base64
import functools
import time
import threading
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter

from app.utils.logger import get_logger

logger = get_logger(__name__)


# HTTP Session pool condivisa (riuso connessioni TCP

_shared_session: Optional[requests.Session] = None
_shared_session_lock = threading.Lock()


def get_shared_session() -> requests.Session:
    """Ritorna una Session requests condivisa con connection pooling.

    Riutilizza connessioni TCP invece di aprirne una nuova per ogni chiamata:
    riduce latenza (no handshake TLS ripetuto) e carico sul server.

    Thread-safe: requests.Session è thread-safe per l'uso (lettura/write
    concorrente su endpoint diversi). Per singola request con Session
    condivisa non servono lock.
    """
    global _shared_session
    if _shared_session is None:
        with _shared_session_lock:
            if _shared_session is None:
                sess = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=50,
                    max_retries=0,  # retry gestito a livello applicativo
                    pool_block=False,
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                _shared_session = sess
    return _shared_session


# Anti-SSRF: validazione URL esterni


@functools.lru_cache(maxsize=1)
def _warn_urby_loopback_alias_once() -> None:
    """Emette un warning una sola volta per processo quando viene letto
    l'alias deprecato ``URBY_ALLOW_LOOPBACK_OUTBOUND`` senza il canonical
    ``PLENORA_ALLOW_LOOPBACK_OUTBOUND``.

    Idempotente via ``functools.lru_cache(maxsize=1)``: chiamate successive
    sono cache hit silenti. Per i test, ``_warn_urby_loopback_alias_once.
    cache_clear`` permette di re-osservare l'emit (vedi fixture in
    ``tests2/security/unit/test_validate_external_url_contract.py``).

    R-21 (2026-05-01): emette anche un Python ``DeprecationWarning``
    standard, oltre al ``logger.warning``. Il warning Python e'
    intercettabile da ``pytest.deprecated_call`` (test contract) e dai
    filterwarnings, mentre il logger e' per l'osservabilita' ops.

    Removal target: 2026-Q4 (release post-stabilization). Removal block:
    nessun riferimento a ``URBY_ALLOW_LOOPBACK_OUTBOUND`` nei deploy
    profile / runbook / docs operative attivi.
    """
    import warnings as _warnings
    msg = (
        "[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND e' un alias retro-compat. "
        "Migrare a PLENORA_ALLOW_LOOPBACK_OUTBOUND. "
        "L'alias verra' rimosso entro 2026-Q4."
    )
    logger.warning(msg)
    _warnings.warn(msg, DeprecationWarning, stacklevel=2)


def _check_outbound_address(
    url: str, allow_loopback_opt_in: bool = True
) -> Optional[str]:
    """Anti-SSRF primitive: valida che l'URL sia http(s) verso un
    host pubblico. Ritorna None se OK, altrimenti una stringa col
    motivo del rifiuto (return-string contract).

    Single source of truth per i 2 wrapper:
      - `validate_external_url` (raise contract)
      - `validate_external_url_silent` (return-string contract pubblico)
    e per il shim REST_FILE strict (`api_connection_source_loading.
    _validate_outbound_url`) che chiama con
    `allow_loopback_opt_in=False`.

    Args:
      url: URL da validare.
      allow_loopback_opt_in: se True, l'env var
        `PLENORA_ALLOW_LOOPBACK_OUTBOUND=1` (canonical) o
        `URBY_ALLOW_LOOPBACK_OUTBOUND=1` (alias retro-compat,
        deprecato) sblocca loopback (`127.0.0.0/8`, `::1`) per i
        test funzionali. Gli IP privati di altre reti (10.x,
        172.16.x, 192.168.x, IPv6 ULA `fc00::/7`), link-local e
        reserved restano bloccati. Se False (REST_FILE strict),
        loopback resta bloccato anche con env settata.

    Guard: in ambienti hardened (`ENVIRONMENT in {production, prod,
    staging, stage}`, simmetrico a `config.py _DOTENV_DISABLED_ENVS`
    e `crypto.py` guard) il flag opt-in e' IGNORATO indipendentemente
    dal valore. Chiude S6 del runtime_config_audit.md: anche se il
    flag finisse per errore in un worker prod/staging, la validazione
    continua a bloccare il loopback.

    B3-B.8 bugfix (2026-04-27, security):
      - DNS resolution via `socket.getaddrinfo(host, None)` invece
        di `gethostbyname_ex` per coprire ANCHE i record AAAA (IPv6).
      - Aggiunto blocco `ip.is_unspecified` per `0.0.0.0` / `::`.
    """
    from urllib.parse import urlparse
    import ipaddress
    import os
    import socket

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return "URL non valido: solo schemi http/https sono permessi"
    host = parsed.hostname
    if not host:
        return "URL non valido: hostname mancante"

    # Determina allow_loopback effettivo. REST_FILE strict
    # `allow_loopback_opt_in=False`) salta interamente il blocco env
    # loopback resta bloccato
    if allow_loopback_opt_in:
        # Guard: in ambienti hardened il flag e' disabilitato by design
        # simmetrico a config.py:26 _DOTENV_DISABLED_ENVS e crypto.py
        # guard
        _env = os.environ.get(
            "ENVIRONMENT", os.environ.get("ENV", "development")
        ).lower()
        if _env in ("production", "prod", "staging", "stage"):
            allow_loopback = False
        else:
            plenora_set = os.environ.get(
                "PLENORA_ALLOW_LOOPBACK_OUTBOUND", ""
            ).strip() == "1"
            urby_set = os.environ.get(
                "URBY_ALLOW_LOOPBACK_OUTBOUND", ""
            ).strip() == "1"
            allow_loopback = plenora_set or urby_set
            # A3: warning loud one-shot quando si usa SOLO l'alias
            # deprecato. Nessuna semantica cambia: l'alias resta
            # funzionante. Pattern functools.lru_cache → cache hit
            # silente dalla 2a chiamata in poi
            if urby_set and not plenora_set:
                _warn_urby_loopback_alias_once()
    else:
        allow_loopback = False

    # B3-B.8: getaddrinfo copre IPv4 (A) + IPv6 (AAAA). Dedup via set —
    # uno stesso IP puo' apparire piu' volte (es. multipli protocolli
    # SOCK_STREAM/SOCK_DGRAM/SOCK_RAW
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"Host non risolvibile: {host}"
    addr_list = list({info[4][0] for info in infos})
    for addr in addr_list:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if allow_loopback and ip.is_loopback:
            continue  # opt-in di test
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"URL bloccato: l'host risolve a un IP privato/interno ({addr})"
    return None


def validate_external_url(url: str) -> None:
    """Anti-SSRF: valida che l'URL sia http(s) verso un host pubblico.

    Wrapper raise sopra `_check_outbound_address` con
    `allow_loopback_opt_in=True` (comportamento pre-fix invariato).

    Solleva ValueError se URL malformato o l'host risolve a un
    IP privato/loopback/link-local/riservato/unspecified.

    Per il contratto return-string (no eccezione), vedi
    `validate_external_url_silent`. Per il REST_FILE strict-no-opt-in,
    vedi `api_connection_source_loading._validate_outbound_url`
    (shim che chiama la primitive con `allow_loopback_opt_in=False`).
    """
    motivo = _check_outbound_address(url, allow_loopback_opt_in=True)
    if motivo is not None:
        raise ValueError(motivo)


def validate_external_url_silent(url: str) -> Optional[str]:
    """Anti-SSRF return-string contract: come `validate_external_url`
    ma ritorna None se OK, motivo string altrimenti. Stesso comportamento
    sul loopback opt-in (env PLENORA_*/URBY_* + hardened env guard)."""
    return _check_outbound_address(url, allow_loopback_opt_in=True)


# Cache thread-safe per token ArcGIS (un token per (token_url, username
_ARCGIS_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_ARCGIS_TOKEN_LOCK = threading.Lock()


# Cache thread-safe per token OAuth2 (lifecycle basato su expires_at
# Sostituisce il pattern "rinnova ogni N righe" che era sbagliato
# il counter non ha nulla a che vedere con il tempo di vita reale del token
# Pattern: double-checked locking con refresh se expires_at - now < 60s
# Thread-safe per uso da thread pool (sync
# Per uso async, threading.Lock funziona comunque perche e non-bloccante
# nel fast path (token valido); si blocca solo durante il refresh

_OAUTH2_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_OAUTH2_TOKEN_LOCK = threading.Lock()
_OAUTH2_REFRESH_THRESHOLD_SEC = 60  # rinnova se manca meno di 60s


def _oauth2_cache_key(auth: Dict) -> str:
    """Chiave cache univoca per un set di credenziali OAuth2."""
    auth_type = auth.get("type", "")
    token_url = auth.get("token_url", "")
    if auth_type == "oauth2_password":
        identifier = auth.get("username", "")
    else:  # client_credentials
        identifier = auth.get("client_id", "")
    return f"{auth_type}|{token_url}|{identifier}"


def get_cached_oauth2_token(auth: Dict) -> Optional[str]:
    """Ritorna un token OAuth2 valido, rinnovandolo solo se sta per scadere.

    Sostituisce il pattern legacy "rinnova ogni N righe". Il refresh avviene
    automaticamente quando `expires_at - now < 60s`, leggendo `expires_in`
    dalla risposta del token endpoint (fallback a 3600s se non presente).

    Thread-safe: il refresh e protetto da lock globale con double-check.
    Se due chiamanti trovano il token scaduto, solo uno fara la richiesta.
    """
    auth_type = auth.get("type", "")
    if auth_type not in ("oauth2_password", "oauth2_client_credentials"):
        return None

    cache_key = _oauth2_cache_key(auth)
    now = time.time()

    # Fast path: cache hit con token ancora valido
    cached = _OAUTH2_TOKEN_CACHE.get(cache_key)
    if cached and (cached["expires_at"] - now) > _OAUTH2_REFRESH_THRESHOLD_SEC:
        return cached["token"]

    # Slow path: serve refresh, acquisisci lock
    with _OAUTH2_TOKEN_LOCK:
        # Double-check: altro thread potrebbe aver rinnovato mentre aspettavamo
        cached = _OAUTH2_TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and (cached["expires_at"] - now) > _OAUTH2_REFRESH_THRESHOLD_SEC:
            return cached["token"]

        # Fetch nuovo token
        if auth_type == "oauth2_password":
            result = _fetch_oauth2_password_token(auth)
        else:
            result = _fetch_oauth2_client_credentials_token(auth)

        if not result or not result.get("access_token"):
            return None

        token = result["access_token"]
        expires_in = int(result.get("expires_in", 3600))  # default 1h
        _OAUTH2_TOKEN_CACHE[cache_key] = {
            "token": token,
            "expires_at": time.time() + expires_in,
        }
        logger.info(
            f"OAuth2 token rinnovato ({auth_type}, valido per {expires_in}s)"
        )
        return token


def invalidate_oauth2_token(auth: Dict) -> None:
    """Forza invalidazione cache per un set di credenziali.

    Utile quando l'API ritorna 401 — il token potrebbe essere stato revocato
    lato server prima della scadenza.
    """
    cache_key = _oauth2_cache_key(auth)
    with _OAUTH2_TOKEN_LOCK:
        _OAUTH2_TOKEN_CACHE.pop(cache_key, None)


# Sanificazione log per evitare leak di credenziali

# Pattern di chiavi che indicano valori sensibili (case-insensitive, match parziale
_SENSITIVE_KEY_PATTERNS = (
    "token", "api_key", "apikey", "password", "passwd", "secret",
    "authorization", "auth_key", "access_key", "client_secret",
    "private_key", "x-api-key",
)

# Placeholder usato nei log al posto del valore reale
_REDACTED = "***REDACTED***"


def sanitize_url_for_log(url: str) -> str:
    """Restituisce l'URL con i valori della query string sensibili oscurati.

    Mantiene scheme/host/path/keys della query string, ma sostituisce i valori
    delle chiavi sensibili (vedi _SENSITIVE_KEY_PATTERNS) con _REDACTED.
    Se l'URL contiene basic auth (`user:pass@host`), oscura anche quella.

    Implementazione regex-based per evitare il percent-encoding del marker
    REDACTED che farebbe parse_qsl/urlencode.
    """
    if not isinstance(url, str) or not url:
        return url
    if "?" not in url and "@" not in url:
        return url

    import re as _re

    # Redact basic auth: scheme://user:pass@host → scheme://***REDACTED***@host
    url = _re.sub(
        r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@",
        lambda m: f"{m.group(1)}{_REDACTED}@",
        url,
    )

    # Redact query string sensitive params: ?key=val&key2=val2 → ?key=***REDACTED***
    if "?" in url:
        sensitive_pattern = "|".join(_re.escape(k) for k in _SENSITIVE_KEY_PATTERNS)
        qs_redact_re = _re.compile(
            r"(?i)([?&])((?:" + sensitive_pattern + r")=)[^&\s'\"]+"
        )
        url = qs_redact_re.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", url)

    return url


def sanitize_for_log(data: Any, auth: Optional[Dict[str, Any]] = None) -> Any:
    """Restituisce una copia di `data` con i valori sensibili oscurati.

    Pensata per loggare params/headers/config di chiamate API senza esporre
    token, api_key, password ecc. nei file di log.

    - Riconosce le chiavi via `_SENSITIVE_KEY_PATTERNS` (match parziale, case-insensitive).
    - Se passato `auth` che dichiara `key_name` (es. api_key in query), oscura
      anche quella specifica chiave (anche se non matcha i pattern generici).
    - Funziona ricorsivamente su dict e liste.
    - Non muta l'input.
    """
    extra_keys = set()
    if isinstance(auth, dict):
        kn = auth.get("key_name")
        if kn:
            extra_keys.add(str(kn).lower())

    def _is_sensitive(key: str) -> bool:
        k = key.lower()
        if k in extra_keys:
            return True
        return any(p in k for p in _SENSITIVE_KEY_PATTERNS)

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: (_REDACTED if _is_sensitive(str(k)) else _walk(v))
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return type(obj)(_walk(v) for v in obj)
        return obj

    return _walk(data)


def _fetch_oauth2_password_token(auth: Dict) -> Optional[Dict]:
    """Fetch diretto di un token password grant. Ritorna dict completo con expires_in.

    Args:
        auth: dict con chiavi token_url, username, password
    Returns:
        dict con chiavi access_token, expires_in, token_type — oppure None
    """
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("OAuth2 password: token_url o username mancante")
        return None

    try:
        response = get_shared_session().post(
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
            f"OAuth2 password token fallito: HTTP {response.status_code} — {response.text[:200]}"
        )
        return None
    except Exception as e:
        logger.error(f"[CRASH] Errore OAuth2 password grant: {e}", exc_info=True)
        return None


def _fetch_oauth2_client_credentials_token(auth: Dict) -> Optional[Dict]:
    """Fetch diretto di un token client_credentials grant. Ritorna dict completo."""
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

        response = get_shared_session().post(
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

    A differenza di OAuth2, ArcGIS:
      - usa un endpoint custom (es. /arcgis/admin/generateToken)
      - accetta form-data con username/password/client/expiration/f=json
      - risponde con {"token": "...", "expires": "<ms_since_epoch>"}
      - il token va passato come query param (?token=...) NON come Authorization header

    Args:
        auth: dict con chiavi:
            - token_url: URL completo del generateToken endpoint
            - username: utente
            - password: password
            - client: 'requestip' (default), 'referer', o 'ip'
            - expiration: minuti (default 60, max impostato dal server)
            - referer: obbligatorio se client='referer'
            - ip: obbligatorio se client='ip'

    Returns:
        Il token, oppure None in caso di errore. Il token è cachato per
        (token_url, username) finché non sta per scadere.
    """
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("ArcGIS token: token_url o username mancante")
        return None

    cache_key = f"{token_url}|{username}"

    # Check cache (con margine di 60s per evitare token "appena scaduti"
    with _ARCGIS_TOKEN_LOCK:
        cached = _ARCGIS_TOKEN_CACHE.get(cache_key)
        if cached and cached.get("expires_at", 0) > time.time() + 60:
            return cached["token"]

    # Richiedi un nuovo token
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

    # SECURITY: default cambiato da False a True. Prima, il default disabilitava
    # SSL, permettendo MITM su richieste ArcGIS token (username+password in chiaro
    # Gli utenti con server ArcGIS self-signed possono ancora impostare
    # verify_ssl=false nella configurazione del connettore
    verify_ssl = auth.get("verify_ssl", True)
    try:
        # Silenzia l'InsecureRequestWarning quando l'utente disabilita
        # esplicitamente la verifica SSL (alcuni server ArcGIS interni hanno
        # certificati self-signed o catene non riconosciute
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
                f"ArcGIS token fallito: HTTP {response.status_code} — {response.text[:200]}"
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

        # `expires` è un timestamp in millisecondi (può essere stringa o int
        try:
            expires_at = int(expires_raw) / 1000.0
        except (TypeError, ValueError):
            # Fallback: assume durata = expiration minuti dal momento attuale
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


def prepare_headers(auth: Dict, custom_headers: Dict = None) -> Dict:
    """
    Prepara headers HTTP per chiamata API REST.

    Tipi auth supportati:
      - none → solo Accept + User-Agent
      - api_key → chiave in header (location='header')
      - basic_auth → Authorization: Basic base64(user:pass)
      - oauth2_password → Bearer token (password grant)
      - oauth2_client_credentials → Bearer token (client_credentials grant)
      - arcgis_token → token NON in header (va in query string,
                                    iniettato dal connector che chiama l'API)

    Args:
        auth: dict configurazione autenticazione
        custom_headers: headers aggiuntivi da mergiare (sovrascrivono i default)
    """
    headers: Dict = {
        "Accept": "application/json",
        "User-Agent": "PLENORA-ETL/1.0",
    }

    if custom_headers:
        headers.update(custom_headers)

    auth_type = auth.get("type", "none")

    if auth_type in ("bearer", "token"):
        # Token Bearer statico — passato direttamente senza OAuth2 flow
        token = auth.get("token") or auth.get("access_token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "oauth2_password":
        token = get_cached_oauth2_token(auth)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "oauth2_client_credentials":
        token = get_cached_oauth2_token(auth)
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
        # Il token ArcGIS viene iniettato come query param (?token=
        # direttamente dal chiamante (enrichment_service._call_api_with_retry
        # Qui non aggiungiamo nulla agli headers — lasciamo solo i default
        pass

    return headers
