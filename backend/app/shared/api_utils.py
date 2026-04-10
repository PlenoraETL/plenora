"""
app/shared/api_utils.py
=======================
Logica HTTP/API condivisa tra api_connector.py e enrichment_service.py.

Funzioni estratte per eliminare il dual-code-path che richiedeva
di applicare ogni fix in due posti separati.

Usato da:
  - app/core/arricchisci/api_connector.py
  - app/core/arricchisci/enrichment_service.py
"""

import base64
import json
import logging
import time
import threading
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Cache thread-safe per token ArcGIS (un token per (token_url, username))
# ──────────────────────────────────────────────────────────────────────
_ARCGIS_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_ARCGIS_TOKEN_LOCK = threading.Lock()


# ──────────────────────────────────────────────────────────────────────
# Sanificazione log per evitare leak di credenziali
# ──────────────────────────────────────────────────────────────────────

# Pattern di chiavi che indicano valori sensibili (case-insensitive, match parziale)
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


def geojson_to_wkt(geom: dict) -> str:
    """Converte un dict geometry GeoJSON in WKT usando shapely."""
    try:
        from shapely.geometry import shape
        return shape(geom).wkt
    except Exception:
        # Fallback: JSON string (compatibile con _geometry_to_wkt di CARICA)
        return json.dumps(geom, ensure_ascii=False)


_DOT_PLACEHOLDER = "\x01"  # Marker interno per proteggere i `.` dentro `[...]`
                           # dalla successiva .split(".")


def _tokenize_path(path: str) -> list:
    """Tokenizza un path `extract_json_value` proteggendo il contenuto delle
    parentesi quadre dal successivo split sui punti.

    Gestisce correttamente:
      - `features[properties.origine=asc3_2021].properties.p1`
        → ["features", "properties.origine=asc3_2021", "properties", "p1"]
      - `features[0].attributes.X` → ["features", "0", "attributes", "X"]
      - `list[*].field` → ["list", "*", "field"]

    Il marker `_DOT_PLACEHOLDER` (U+0001) sostituisce i `.` dentro le parentesi
    prima dello split, e viene ripristinato dopo.
    """
    out_chars = []
    depth = 0
    for ch in path:
        if ch == "[":
            depth += 1
            out_chars.append(ch)
        elif ch == "]":
            depth -= 1
            out_chars.append(ch)
        elif ch == "." and depth > 0:
            out_chars.append(_DOT_PLACEHOLDER)
        else:
            out_chars.append(ch)
    protected = "".join(out_chars)
    parts = protected.replace("]", "").replace("[", ".").split(".")
    return [p.replace(_DOT_PLACEHOLDER, ".") for p in parts]


def extract_json_value(data: Any, path: str) -> Any:
    """
    Estrae un valore da strutture JSON/dict usando dot notation annidata.

    Sintassi path:
      - "field.nested.value"                        → accesso annidato
      - "array[0].field"                            → indice array
      - "list[*].field"                             → wildcard: primo elemento
      - "list[key=value].field"                     → filtro top-level
      - "list[nested.key=value].field"              → filtro con chiave annidata
                                                       (es. features[properties.origine=asc3_2021])
      - ""  oppure  None                            → ritorna data intero

    **Filtri con chiavi annidate**: la chiave del filtro può essere un path dotted
    (es. `properties.origine`) e viene risolta ricorsivamente su ogni elemento
    della lista. Utile per GeoJSON FeatureCollection dove i discriminanti
    stanno sotto `properties`.

    **Chiavi dict con punti nel nome** (es. `"UniqueValue.Pixel Value"` tipico
    di ArcGIS raster): il parser usa **longest-first greedy match** — quando
    una parte non è presente nel dict corrente, prova a unirla con le successive
    (dalla più lunga alla più corta) finché trova una chiave che esiste.

    Le geometrie GeoJSON (dict con chiave "type" in Point/Polygon/...) vengono
    automaticamente convertite in WKT per compatibilità con DataFrame.
    """
    if not path:
        return data

    parts = _tokenize_path(path)
    current = data
    i = 0
    n = len(parts)

    while i < n:
        part = parts[i]
        if not part:
            i += 1
            continue
        if current is None:
            return None

        if part == "*":
            # Wildcard: usa il primo elemento se lista, altrimenti None
            if isinstance(current, list) and len(current) > 0:
                current = current[0]
            elif isinstance(current, list):
                return None
            i += 1
        elif "=" in part:
            # Filtro "key=value": primo elem della lista con elem[key]==value.
            # La chiave può essere un path annidato (es. "properties.origine")
            # risolta ricorsivamente tramite extract_json_value.
            if not isinstance(current, list):
                return None
            key, _, expected = part.partition("=")
            key = key.strip()
            expected = expected.strip()
            nested = "." in key
            found = None
            for item in current:
                if not isinstance(item, dict):
                    continue
                val = extract_json_value(item, key) if nested else item.get(key)
                if val is None:
                    continue
                if str(val) == expected:
                    found = item
                    break
                # Fallback numerico: gestisce layerId=0 (int) vs expected="0" (str)
                try:
                    if float(val) == float(expected):
                        found = item
                        break
                except (ValueError, TypeError):
                    pass
            current = found
            i += 1
        elif part.isdigit():
            idx = int(part)
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
            i += 1
        elif isinstance(current, dict):
            # Longest-first greedy match: prova a unire con le parti successive
            # per gestire chiavi con punti nel nome (es. "Raster.Value",
            # "UniqueValue.Pixel Value" tipiche dei layer raster ArcGIS).
            matched = False
            for j in range(n, i, -1):
                joined = ".".join(parts[i:j])
                if joined in current:
                    current = current[joined]
                    i = j
                    matched = True
                    break
            if not matched:
                return None
        else:
            return None

    # Converti geometry GeoJSON → WKT
    if isinstance(current, dict) and current.get("type") in [
        "Point", "Polygon", "MultiPolygon", "LineString",
        "MultiLineString", "MultiPoint", "GeometryCollection",
    ]:
        return geojson_to_wkt(current)

    return current


def get_oauth2_token(auth: Dict) -> Optional[str]:
    """
    Ottiene token OAuth2 con resource owner password grant.

    Args:
        auth: dict con chiavi token_url, username, password
    """
    token_url = auth.get("token_url", "")
    username = auth.get("username", "")
    password = auth.get("password", "")

    if not token_url or not username:
        logger.warning("OAuth2 password: token_url o username mancante")
        return None

    try:
        response = requests.post(
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
            token = response.json().get("access_token")
            masked = f"{username[:3]}{'*' * max(0, len(username) - 3)}"
            logger.info(f"OAuth2 password token ottenuto per user={masked}")
            return token
        logger.warning(
            f"OAuth2 password token fallito: HTTP {response.status_code} — {response.text[:200]}"
        )
        return None
    except Exception as e:
        logger.error(f"[CRASH] Errore OAuth2 password grant: {e}", exc_info=True)
        return None


def get_oauth2_client_credentials_token(auth: Dict) -> Optional[str]:
    """
    Ottiene token OAuth2 con client_credentials flow.

    Args:
        auth: dict con chiavi token_url, client_id, client_secret, scope (opzionale)
    """
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

        response = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
            verify=True,
        )

        if response.status_code == 200:
            masked = f"{client_id[:4]}{'*' * max(0, len(client_id) - 4)}"
            logger.info(f"OAuth2 CC token ottenuto per client={masked}")
            return response.json().get("access_token")

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
            - token_url:  URL completo del generateToken endpoint
            - username:   utente
            - password:   password
            - client:     'requestip' (default), 'referer', o 'ip'
            - expiration: minuti (default 60, max impostato dal server)
            - referer:    obbligatorio se client='referer'
            - ip:         obbligatorio se client='ip'

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

    # Check cache (con margine di 60s per evitare token "appena scaduti")
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

    verify_ssl = auth.get("verify_ssl", False)
    try:
        # Silenzia l'InsecureRequestWarning quando l'utente disabilita
        # esplicitamente la verifica SSL (alcuni server ArcGIS interni hanno
        # certificati self-signed o catene non riconosciute).
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

        # `expires` è un timestamp in millisecondi (può essere stringa o int)
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
      - none                      → solo Accept + User-Agent
      - api_key                   → chiave in header (location='header')
      - basic_auth                → Authorization: Basic base64(user:pass)
      - oauth2_password           → Bearer token (password grant)
      - oauth2_client_credentials → Bearer token (client_credentials grant)
      - arcgis_token              → token NON in header (va in query string,
                                    iniettato dal connector che chiama l'API)

    Args:
        auth:           dict configurazione autenticazione
        custom_headers: headers aggiuntivi da mergiare (sovrascrivono i default)
    """
    headers: Dict = {
        "Accept": "application/json",
        "User-Agent": "URBY-ETL/1.0",
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
        token = get_oauth2_token(auth)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "oauth2_client_credentials":
        token = get_oauth2_client_credentials_token(auth)
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
        # Il token ArcGIS viene iniettato come query param (?token=...)
        # direttamente dal chiamante (enrichment_service._call_api_with_retry).
        # Qui non aggiungiamo nulla agli headers — lasciamo solo i default.
        pass

    return headers
