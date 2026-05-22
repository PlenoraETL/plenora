"""ArcGIS authentication, token cache, and token-aware request helpers."""

from __future__ import annotations

from urllib.parse import urlparse
import random

import threading
import time
from typing import Any, Optional, Protocol
from typing_extensions import TypedDict

import requests

from app.infrastructure.http.session import get_shared_session
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo
from app.shared.connections.oauth import (
    _get_or_refresh_token_record,
    _invalidate_token_record,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TokenRecord(TypedDict):
    token: str
    expires_at: float


def _mask_secret(value: str, visible: int = 4) -> str:
    """Mostra solo i primi N caratteri di un segreto nei log."""
    if not value:
        return "(vuoto)"
    return value[:visible] + "***" if len(value) > visible else "***"


class HttpClientProtocol(Protocol):
    """Protocollo per il client HTTP - iniettabile per test."""

    def get(
        self,
        url: str,
        params: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> Any: ...

    def post(
        self,
        url: str,
        data: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> Any: ...


class DefaultHttpClient:
    """
    Client HTTP di default basato su requests con Session pool condivisa.

    Sostituibile con mock nei test per evitare chiamate HTTP reali.
    """

    def get(
        self,
        url: str,
        params: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> requests.Response:
        return get_shared_session().get(
            url, params=params, timeout=timeout, headers=headers
        )

    def post(
        self,
        url: str,
        data: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> requests.Response:
        return get_shared_session().post(
            url, data=data, timeout=timeout, headers=headers
        )


class ArcGISAuthMixin:
    """Token cache, authentication, and authenticated request helpers."""

    DEFAULT_TIMEOUT = 30

    def _token_cache_lock(self) -> threading.Lock:
        lock = getattr(self, "_token_cache_thread_lock", None)
        if lock is None:
            lock = threading.Lock()
            setattr(self, "_token_cache_thread_lock", lock)
        return lock

    def _store_token_record(
        self,
        cache_key: str,
        token: str,
        expires_at: float,
    ) -> str:
        self._token_cache[cache_key] = {
            "token": token,
            "expires": expires_at,
            "expires_at": expires_at,
        }
        return token

    def _invalidate_cached_auth_token(
        self,
        conn: ArcGISConnectionInfo,
        cache_key: str,
    ) -> None:
        namespace = (
            "arcgis-client-oauth2"
            if conn.auth_type == "oauth2"
            else "arcgis-client-token"
        )
        _invalidate_token_record(
            namespace=namespace,
            cache_key=cache_key,
            local_cache=self._token_cache,
            local_lock=self._token_cache_lock(),
        )

    def _request_with_token(
        self,
        method: str,
        url: str,
        conn: "ArcGISConnectionInfo",
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries_429: int = 3,
        extra_headers: Optional[dict] = None,
    ) -> "requests.Response":
        """Esegue una request autenticata verso ArcGIS con:

        1. Token in `Authorization: Bearer <token>` header (non in
           params/data). Evita leak in log reverse-proxy, observability
           tracing, e log applicativi che catturano URL o request body.
        2. Su HTTP 401 o response body `error.code` in {498, 499}
           (token expired/invalid), invalida la cache token per questa
           connection e ritenta UNA volta con un token fresh.
        3. Su HTTP 429 (rate limit), rispetta `Retry-After` header se
           presente altrimenti exponential backoff con jitter, fino a
           `max_retries_429` tentativi. Oltre quel limite, lascia
           ritornare la 429 al caller.

        Compatibilita' ArcGIS: Authorization Bearer e' supportato da
        ArcGIS Online e ArcGIS Enterprise >= 10.6 (rilascio 2018).
        Versioni piu' vecchie non sono piu' supportate da Esri.

        Args:
            method: "GET" o "POST" (case-insensitive).
            url: URL completo dell'endpoint.
            conn: ArcGISConnectionInfo per refresh token su 498/499.
            params: query string parameters (token rimosso).
            data: form-encoded body (token rimosso).
            timeout: HTTP timeout in secondi.
            max_retries_429: max ritentativi su 429. Default 3.

        Returns:
            Oggetto requests.Response. Il caller chiama `.json`.

        NB: questo helper DOES NOT raise su 498/499 ritornati dal body
        - il caller deve fare `if "error" in data` come prima. Il
        refresh si attiva automaticamente prima di cedere la response
        finale al caller, cosi' il caller vede solo l'esito del retry.
        """

        # Difesa in profondita': rimuovi token da params/data se qualche
        # caller ha ancora passato la vecchia forma. L'header e' l'unica
        # fonte autoritativa.
        if params and "token" in params:
            params = {k: v for k, v in params.items() if k != "token"}
        if data and "token" in data:
            data = {k: v for k, v in data.items() if k != "token"}

        cache_key = f"{conn.server_url}:{conn.username or conn.client_id}"
        retry_on_401_done = False
        backoff_429_count = 0

        while True:
            token = self.authenticate(conn)
            headers = dict(extra_headers) if extra_headers else {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if not headers:
                headers = None  # evita dict vuoto -> comportamento di requests

            method_upper = method.upper()
            if method_upper == "GET":
                response = self._http.get(
                    url, params=params, timeout=timeout, headers=headers
                )
            elif method_upper == "POST":
                response = self._http.post(
                    url, data=data, timeout=timeout, headers=headers
                )
            else:
                raise ValueError(f"Metodo HTTP non supportato: {method_upper}")

            # Caso 1: HTTP 429 rate limit
            if response.status_code == 429:
                if backoff_429_count >= max_retries_429:
                    logger.warning(
                        f"[ARCGIS] 429 dopo {backoff_429_count} retry, "
                        f"abbandono. URL: {url}"
                    )
                    return response
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait_s = float(retry_after)
                    except (TypeError, ValueError):
                        wait_s = 2.0 ** backoff_429_count
                else:
                    # Exponential backoff: 2^n secondi con jitter +-25%
                    wait_s = (2.0 ** backoff_429_count) * (
                        1.0 + (random.random() - 0.5) * 0.5
                    )
                # Cap a 30s per non bloccare troppo a lungo
                wait_s = min(wait_s, 30.0)
                logger.warning(
                    f"[ARCGIS] 429 rate limited, backoff {wait_s:.1f}s "
                    f"(retry {backoff_429_count + 1}/{max_retries_429})"
                )
                time.sleep(wait_s)
                backoff_429_count += 1
                continue

            # Caso 2: HTTP 401 -> token rifiutato lato HTTP layer
            # alcuni endpoint ArcGIS rispondono 401 invece che 200+error
            if response.status_code == 401 and not retry_on_401_done:
                logger.info(
                    f"[ARCGIS] 401 sul token corrente, invalido cache e "
                    f"ritento con token fresh. URL: {url}"
                )
                self._invalidate_cached_auth_token(conn, cache_key)
                retry_on_401_done = True
                continue

            # Caso 3: response 200 con body `error.code` in {498, 499}
            # il pattern Esri standard per token scaduti/invalidi
            if response.status_code == 200 and not retry_on_401_done:
                try:
                    body = response.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    err = body.get("error")
                    if isinstance(err, dict):
                        code = err.get("code")
                        if code in (498, 499):
                            logger.info(
                                f"[ARCGIS] error.code={code} (token "
                                f"scaduto/invalido), refresh + retry. "
                                f"URL: {url}"
                            )
                            self._invalidate_cached_auth_token(conn, cache_key)
                            retry_on_401_done = True
                            continue

            # Response finale - successo o errore non gestito dal retry
            return response

    def authenticate(self, conn: ArcGISConnectionInfo) -> Optional[str]:
        """
        Ottiene un token di autenticazione per la connessione.

        Supporta:
        - public: nessun token
        - oauth2: client_credentials flow (client_id + client_secret)
        - token: username/password -> generateToken (con fallback strategie)

        Returns:
            Token string o None per connessioni pubbliche
        """
        if conn.auth_type == "public":
            return None

        # Cleanup token scaduti
        now = time.time()
        expired = [k for k, v in self._token_cache.items() if v["expires"] <= now]
        for k in expired:
            del self._token_cache[k]

        # Controlla cache
        cache_key = f"{conn.server_url}:{conn.username or conn.client_id}"
        cached = self._token_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return cached["token"]

        # OAuth2 client_credentials
        if conn.auth_type == "oauth2":
            return _get_or_refresh_token_record(
                namespace="arcgis-client-oauth2",
                cache_key=cache_key,
                local_cache=self._token_cache,
                local_lock=self._token_cache_lock(),
                refresh_threshold_sec=60,
                fetch_token_record=lambda: self._fetch_oauth2_token_record(conn),
            )

        # Token classico (username/password)
        return _get_or_refresh_token_record(
            namespace="arcgis-client-token",
            cache_key=cache_key,
            local_cache=self._token_cache,
            local_lock=self._token_cache_lock(),
            refresh_threshold_sec=60,
            fetch_token_record=lambda: self._fetch_classic_token_record(conn),
        )

    def _authenticate_oauth2(
        self,
        conn: ArcGISConnectionInfo,
        cache_key: str,
    ) -> str:
        """
        Autenticazione OAuth2 con client_credentials.

        POST /sharing/rest/oauth2/token
        grant_type=client_credentials&client_id=xxx&client_secret=xxx
        """
        record = self._fetch_oauth2_token_record(conn)
        if not record:
            raise ConnectionError("OAuth2 fallito: nessun token ricevuto")
        return self._store_token_record(
            cache_key,
            str(record["token"]),
            float(record["expires_at"]),
        )

    def _fetch_oauth2_token_record(
        self,
        conn: ArcGISConnectionInfo,
    ) -> TokenRecord:
        """Fetch del token OAuth2 ArcGIS.

        Delega a `oauth._fetch_oauth2_client_credentials_token` (shared)
        per evitare duplicazione del grant flow. Pre-fix
        c'era un'implementazione parallela in questo file con cache
        separata (`self._token_cache` vs `_OAUTH2_TOKEN_CACHE` globale)
        che potevano restituire token diversi per la stessa connessione.
        L'`extra_form_data={"f": "json"}` preserva il quirk ArcGIS
        (forza JSON response sull'endpoint legacy `/sharing/rest/...`).
        """
        from app.shared.connections.oauth import (
            _fetch_oauth2_client_credentials_token,
        )

        parsed = urlparse(conn.server_url)
        oauth_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest/oauth2/token"

        result = _fetch_oauth2_client_credentials_token(
            {
                "token_url": oauth_url,
                "client_id": conn.client_id,
                "client_secret": conn.client_secret,
            },
            extra_form_data={"f": "json"},
        )

        if not result or "access_token" not in result:
            error = (result or {}).get("error", {}) if isinstance(result, dict) else {}
            error_desc = (
                error.get("message")
                if isinstance(error, dict)
                else None
            ) or (result or {}).get("error_description") or "Errore OAuth2"
            raise ConnectionError(f"OAuth2 fallito: {error_desc}")

        expires_in = result.get("expires_in", 7200)  # default 2 ore
        logger.info(
            f"Token OAuth2 ottenuto per {conn.server_url} "
            f"(expires_in={expires_in}s)"
        )
        return {
            "token": result["access_token"],
            "expires_at": time.time() + expires_in,
        }

    def _authenticate_token(
        self,
        conn: ArcGISConnectionInfo,
        cache_key: str,
    ) -> str:
        """
        Autenticazione classica username/password -> generateToken.

        Prova diverse strategie (referer, requestip) per compatibilita'
        con diversi tipi di server.
        """
        record = self._fetch_classic_token_record(conn)
        if not record:
            raise ConnectionError("Autenticazione fallita: nessun token ricevuto")
        return self._store_token_record(
            cache_key,
            str(record["token"]),
            float(record["expires_at"]),
        )

    def _fetch_classic_token_record(
        self,
        conn: ArcGISConnectionInfo,
    ) -> TokenRecord:
        """Fetch diretto generateToken ArcGIS con strategie legacy."""
        token_url = self._resolve_token_url(conn)

        is_online = "arcgis.com" in conn.server_url.lower()

        if is_online:
            strategies = [
                {"client": "referer", "referer": conn.server_url},
                {"client": "requestip"},
            ]
        else:
            strategies = [
                {"client": "referer", "referer": conn.server_url},
            ]

        last_error = "Autenticazione fallita"

        for strategy in strategies:
            token_data = {
                "username": conn.username,
                "password": conn.password,
                "f": "json",
                "expiration": 60,
                **strategy,
            }

            try:
                response = self._http.post(
                    token_url, data=token_data, timeout=self.DEFAULT_TIMEOUT
                )
                result = response.json()

                logger.debug(
                    f"Token request a {token_url} - "
                    f"client={_mask_secret(str(strategy.get('client', '')))} - "
                    f"status: {response.status_code}"
                )

                if "token" in result:
                    token = result["token"]
                    expires = result.get("expires", 0)
                    # ArcGIS API ritorna `expires` in millisecondi-since-epoch
                    # (legacy) o secondi (alcune versioni nuove). Pre-fix
                    # l'heuristic `> 1e10` ne distingueva l'unita': 1e10 sec
                    # = anno 2286 → qualsiasi valore sopra e' "in ms". Bug
                    # noto: in futuro lontano (post-2286) la heuristic si
                    # rompe. Mitigazione: sanity cap su ttl (max ~1 anno)
                    # per non accettare token con scadenza assurda.
                    expires_sec = expires / 1000 if expires > 1e10 else expires
                    _MAX_REASONABLE_TTL = 366 * 24 * 3600  # 1 anno
                    now_ts = time.time()
                    if expires_sec - now_ts > _MAX_REASONABLE_TTL:
                        logger.warning(
                            "Token ArcGIS con TTL sospetto (%.0fs > 1 anno): "
                            "ricomputo come `now + 1h`. Verifica formato "
                            "campo `expires` (ms vs sec) del server.",
                            expires_sec - now_ts,
                        )
                        expires_sec = now_ts + 3600

                    logger.debug(
                        f"Token ottenuto per {conn.server_url} "
                        f"(client={_mask_secret(str(strategy.get('client', '')))})"
                    )
                    return {
                        "token": token,
                        "expires_at": expires_sec,
                    }
                error_obj = result.get("error", {})
                last_error = error_obj.get("message", "Errore sconosciuto")
                logger.warning(
                    f"Token fallito con "
                    f"client={_mask_secret(str(strategy.get('client', '')))}: "
                    f"{last_error}"
                )
                continue

            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(
                    f"Token request fallita con "
                    f"client={_mask_secret(str(strategy.get('client', '')))}: {e}"
                )
                continue

        raise ConnectionError(f"Autenticazione fallita: {last_error}")

    def _resolve_token_url(self, conn: ArcGISConnectionInfo) -> str:
        """
        Determina l'URL corretto per la generazione del token.

        Ordine:
        1. Token URL custom (se specificato dall'utente)
        2. Auto-detect ArcGIS Online dall'URL (*.arcgis.com)
        3. Auto-discovery dal /rest/info endpoint del server
        4. Fallback basato su server_type
        """
        if conn.token_url:
            return conn.token_url

        base = conn.server_url.rstrip("/")

        # Auto-detect ArcGIS Online dall'URL
        # Usa il dominio dell'organizzazione (es. orgname.maps.arcgis.com
        # invece di forzare www.arcgis.com).
        if "arcgis.com" in base.lower():

            parsed = urlparse(base)
            # Usa il dominio originale per il token (supporta portali org-specifici).
            return f"{parsed.scheme}://{parsed.netloc}/sharing/rest/generateToken"

        if conn.server_type == "arcgis_online":

            parsed = urlparse(base)
            if "arcgis.com" in parsed.netloc.lower():
                return f"{parsed.scheme}://{parsed.netloc}/sharing/rest/generateToken"
            return "https://www.arcgis.com/sharing/rest/generateToken"

        # Auto-discovery: chiedi al server dove sta il token endpoint
        # GET /rest/info?f=json -> {"authInfo": {"tokenServicesUrl": "..."}}
        discovered_url = self._discover_token_url(base)
        if discovered_url:
            logger.info(f"Token URL auto-discovered: {discovered_url}")
            return discovered_url

        # Fallback
        if conn.server_type == "arcgis_enterprise":
            if "/portal" in base.lower():
                portal_base = base[: base.lower().index("/portal") + 7]
            else:
                portal_base = base
            return f"{portal_base}/sharing/rest/generateToken"
        if "/rest/services" in base:
            server_base = base[: base.index("/rest/services")]
        elif "/arcgis" in base.lower():
            server_base = base[: base.lower().index("/arcgis") + 7]
        else:
            server_base = base
        return f"{server_base}/tokens/generateToken"

    def _discover_token_url(self, base_url: str) -> Optional[str]:
        """
        Tenta di scoprire il token URL dal /rest/info endpoint.

        ArcGIS Server espone authInfo.tokenServicesUrl che indica
        l'URL esatto per la generazione token, evitando di indovinare
        il path (es. /arcgis vs /arcgisserver).
        """
        # Prova diverse varianti di /rest/info
        # base potrebbe essere .../rest/services o .../arcgisserver
        candidates = []
        if "/rest/services" in base_url:
            candidates.append(
                base_url[: base_url.index("/rest/services")] + "/rest/info"
            )
        if "/rest" in base_url:
            candidates.append(base_url[: base_url.index("/rest")] + "/rest/info")
        candidates.append(base_url.rstrip("/") + "/rest/info")

        # Deduplica mantenendo ordine
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                unique_candidates.append(candidate)

        for info_url in unique_candidates:
            try:
                response = self._http.get(
                    info_url, params={"f": "json"}, timeout=10
                )
                data = response.json()
                token_url = data.get("authInfo", {}).get("tokenServicesUrl", "")
                if token_url:
                    return token_url
            except Exception:
                continue

        return None
