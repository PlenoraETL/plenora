"""
Client REST per ArcGIS Server / Enterprise / Online.

Architettura
============
Client HTTP puro che comunica con le REST API di ArcGIS senza dipendenze
dal pacchetto arcgis Python. Usa solo `requests` per le chiamate HTTP.

Funzionalità:
- Autenticazione token-based (ArcGIS Server, Enterprise, Online)
- Browse catalogo servizi e folder
- Lista layer di un Feature Service
- Schema campi di un layer
- Query features con filtri WHERE, bounding box, paginazione
- Scrittura: addFeatures, updateFeatures, deleteFeatures
- Conversione Esri JSON ↔ GeoDataFrame

Gestione token (C12)
====================
- Il token viene generato al primo utilizzo e cachato con margine 60s
  rispetto alla scadenza reportata da ArcGIS.
- Passaggio come `Authorization: Bearer {token}` header — NON piu'
  come `?token=` query param (pre-C12 era in params/data). Rationale
  sicurezza per un client backend server-to-server:
    * log reverse-proxy / upstream ArcGIS non catturano piu' il token
      nell'URL;
    * observability HTTP (tracing lato server) non lo espone;
    * eventuali log applicativi che catturano request URL/params fuori
      da questo modulo non leakano il segreto.
  (Browser history / Referer non sono vettori per questo client
  server-to-server, ma il fix li copre comunque per igiene.)
- Retry automatico (via `_request_with_token`):
    * HTTP 401 o body `error.code` in {498, 499} → invalida cache,
      refresh + retry una volta;
    * HTTP 429 → exponential backoff con jitter (rispetta Retry-After
      se presente), max 3 tentativi;
  Pre-C12 la docstring prometteva retry automatica ma non era
  implementata — il batch writer autenticava una volta e se il token
  scadeva a meta' batch falliva tutto.
- Supporta token URL custom o auto-rilevato dal tipo di server.
- Compatibilita' server: Bearer e' supportato da ArcGIS Online e
  ArcGIS Enterprise >= 10.6 (2018). Versioni precedenti non sono piu'
  supportate da Esri.

Paginazione:
- La query rispetta il maxRecordCount del server
- Se i risultati superano il limite, esegue query multiple con resultOffset
- Il chiamante può specificare un limite massimo di features

Uso:
    client = ArcGISClient()
    token = client.authenticate(connection)
    services = client.list_services(connection)
    layers = client.list_layers(connection, service_url)
    schema = client.get_layer_schema(connection, layer_url)
    rows, cols = client.query_features(connection, layer_url, where="POP > 1000")
"""

import time
import json
from typing import List, Dict, Any, Optional, Tuple, Protocol
from dataclasses import dataclass, field

import requests
import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)



def _mask_secret(value: str, visible: int = 4) -> str:
    """Mostra solo i primi N caratteri di un segreto nei log."""
    if not value:
        return "(vuoto)"
    return value[:visible] + "***" if len(value) > visible else "***"

# =============================================================================
# TIPI E PROTOCOLLI
# =============================================================================


@dataclass
class ArcGISConnectionInfo:
    """
    Configurazione minima per una connessione ArcGIS.
    
    Versione leggera usata dal client (senza metadati come nome, date).
    Analogo a DatabaseConnection nel query_executor.py.
    """
    server_url: str         # URL base (es. https://services.arcgis.com/xxx)
    server_type: str        # arcgis_online, arcgis_enterprise, arcgis_server
    auth_type: str          # token, oauth2, public
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    token_url: str = ""     # URL custom per generazione token


class HttpClientProtocol(Protocol):
    """Protocollo per il client HTTP — iniettabile per test."""
    def get(self, url: str, params: dict = None, timeout: int = 30, headers: dict = None) -> Any: ...
    def post(self, url: str, data: dict = None, timeout: int = 30, headers: dict = None) -> Any: ...


# =============================================================================
# HTTP CLIENT (default — usa requests)
# =============================================================================

class DefaultHttpClient:
    """
    Client HTTP di default basato su requests con Session pool condivisa.

    Sostituibile con mock nei test per evitare chiamate HTTP reali.
    """

    def get(self, url: str, params: dict = None, timeout: int = 30, headers: dict = None) -> requests.Response:
        from app.shared.api_utils import get_shared_session
        return get_shared_session().get(url, params=params, timeout=timeout, headers=headers)

    def post(self, url: str, data: dict = None, timeout: int = 30, headers: dict = None) -> requests.Response:
        from app.shared.api_utils import get_shared_session
        return get_shared_session().post(url, data=data, timeout=timeout, headers=headers)


# =============================================================================
# MAPPING TIPI ESRI → PLENORA
# =============================================================================

class EsriTypeMapper:
    """
    Mappa i tipi di campo Esri nei tipi interni PLENORA.
    
    Analogo a TypeMapper in database_inspector.py.
    
    Mapping:
    - esriFieldTypeOID → int
    - esriFieldTypeInteger → int
    - esriFieldTypeSmallInteger → int
    - esriFieldTypeDouble → real
    - esriFieldTypeSingle → real
    - esriFieldTypeString → str
    - esriFieldTypeDate → date
    - esriFieldTypeGUID → str
    - esriFieldTypeGlobalID → str
    - esriFieldTypeGeometry → geometry
    - esriFieldTypeBlob → str
    """
    
    ESRI_TO_PLENORA = {
        "esriFieldTypeOID": "int",
        "esriFieldTypeInteger": "int",
        "esriFieldTypeSmallInteger": "int",
        "esriFieldTypeDouble": "real",
        "esriFieldTypeSingle": "real",
        "esriFieldTypeString": "str",
        "esriFieldTypeDate": "date",
        "esriFieldTypeGUID": "str",
        "esriFieldTypeGlobalID": "str",
        "esriFieldTypeGeometry": "geometry",
        "esriFieldTypeBlob": "str",
        "esriFieldTypeXML": "str",
    }
    
    ESRI_GEOM_TO_PLENORA = {
        "esriGeometryPoint": "geometry:point",
        "esriGeometryMultipoint": "geometry:point",
        "esriGeometryPolyline": "geometry:line",
        "esriGeometryPolygon": "geometry:polygon",
        "esriGeometryEnvelope": "geometry:polygon",
    }
    
    @classmethod
    def map_field_type(cls, esri_type: str) -> str:
        """Converte un tipo di campo Esri in tipo PLENORA."""
        return cls.ESRI_TO_PLENORA.get(esri_type, "str")
    
    @classmethod
    def map_geometry_type(cls, esri_geom_type: str) -> str:
        """Converte un tipo di geometria Esri in tipo PLENORA con sottotipo."""
        return cls.ESRI_GEOM_TO_PLENORA.get(esri_geom_type, "geometry")


# =============================================================================
# CONVERSIONI ESRI JSON ↔ GEODATAFRAME
# =============================================================================

class EsriJsonConverter:
    """
    Converte tra formato Esri JSON e GeoDataFrame/liste Python.
    
    Esri JSON features hanno struttura:
    {
        "attributes": {"FIELD1": "val1", "FIELD2": 42},
        "geometry": {"x": 11.0, "y": 44.0}  // o rings/paths per polygon/line
    }
    
    La conversione a GeoDataFrame usa shapely per le geometrie.
    """
    
    @staticmethod
    def features_to_rows(
        features: List[dict],
        fields: List[dict],
        include_geometry: bool = True,
        geometry_type: str = ""
    ) -> Tuple[List[tuple], List[str]]:
        """
        Converte features Esri JSON in righe + colonne (formato execute_raw).
        
        Args:
            features: lista di features Esri JSON
            fields: lista definizioni campi dal layer schema
            include_geometry: se True, aggiunge colonna geometria WKT
            geometry_type: tipo geometria Esri per conversione corretta
            
        Returns:
            (righe, colonne) — stesso formato di QueryExecutor.execute_raw
        """
        if not features:
            return [], []
        
        # Nomi colonne attributi (escludi campo geometria Esri)
        attr_columns = [f["name"] for f in fields if f.get("type") != "esriFieldTypeGeometry"]
        
        # Colonne output: attributi + eventuale geometry
        columns = list(attr_columns)
        if include_geometry:
            columns.append("geometry")
        
        rows = []
        for feature in features:
            attrs = feature.get("attributes", {})
            row = [attrs.get(col) for col in attr_columns]
            
            if include_geometry:
                geom = feature.get("geometry")
                wkt = EsriJsonConverter._geometry_to_wkt(geom, geometry_type) if geom else None
                row.append(wkt)
            
            rows.append(tuple(row))
        
        return rows, columns
    
    @staticmethod
    def _geometry_to_wkt(geom: dict, geometry_type: str = "") -> Optional[str]:
        """
        Converte una geometria Esri JSON in WKT.
        
        Supporta:
        - Point: {"x": 11.0, "y": 44.0} → POINT(11.0 44.0)
        - Multipoint: {"points": [[x,y], ...]} → MULTIPOINT(...)
        - Polyline: {"paths": [[[x,y], ...], ...]} → LINESTRING/MULTILINESTRING(...)
        - Polygon: {"rings": [[[x,y], ...], ...]} → POLYGON/MULTIPOLYGON(...)
        """
        if geom is None:
            return None
        
        try:
            # Point
            if "x" in geom and "y" in geom:
                x, y = geom["x"], geom["y"]
                if x is None or y is None:
                    return None
                return f"POINT({x} {y})"
            
            # Multipoint
            if "points" in geom:
                points = geom["points"]
                if not points:
                    return None
                pts_wkt = ", ".join(f"{p[0]} {p[1]}" for p in points)
                return f"MULTIPOINT({pts_wkt})"
            
            # Polyline
            if "paths" in geom:
                paths = geom["paths"]
                if not paths:
                    return None
                if len(paths) == 1:
                    coords = ", ".join(f"{p[0]} {p[1]}" for p in paths[0])
                    return f"LINESTRING({coords})"
                else:
                    lines = []
                    for path in paths:
                        coords = ", ".join(f"{p[0]} {p[1]}" for p in path)
                        lines.append(f"({coords})")
                    return f"MULTILINESTRING({', '.join(lines)})"
            
            # Polygon
            if "rings" in geom:
                rings = geom["rings"]
                if not rings:
                    return None
                # Esri: primo ring = esterno, successivi = buchi
                ring_wkts = []
                for ring in rings:
                    coords = ", ".join(f"{p[0]} {p[1]}" for p in ring)
                    ring_wkts.append(f"({coords})")
                return f"POLYGON({', '.join(ring_wkts)})"
            
            return None
            
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"Errore conversione geometria a WKT: {e}")
            return None
    
    @staticmethod
    def geodataframe_to_esri_features(
        gdf,
        geometry_column: str = "geometry",
        object_id_field: str = None
    ) -> List[dict]:
        """
        Converte un GeoDataFrame in lista di features Esri JSON.
        
        Args:
            gdf: GeoDataFrame sorgente
            geometry_column: nome colonna geometria
            object_id_field: se presente, include nell'output per update
            
        Returns:
            Lista di features nel formato Esri JSON
        """
        features = []
        
        for idx, row in gdf.iterrows():
            feature = {"attributes": {}}
            
            for col in gdf.columns:
                if col == geometry_column:
                    continue
                val = row[col]
                # Converti tipi numpy/pandas in tipi Python nativi
                if pd.isna(val):
                    feature["attributes"][col] = None
                elif hasattr(val, 'item'):
                    feature["attributes"][col] = val.item()
                else:
                    feature["attributes"][col] = val
            
            # Converti geometria
            if geometry_column in gdf.columns and hasattr(gdf, 'geometry'):
                geom = row[geometry_column]
                if geom is not None and not geom.is_empty:
                    feature["geometry"] = EsriJsonConverter._shapely_to_esri(geom)
            
            features.append(feature)
        
        return features
    
    @staticmethod
    def _shapely_to_esri(geom) -> Optional[dict]:
        """
        Converte una geometria Shapely in formato Esri JSON.
        
        Args:
            geom: oggetto geometria Shapely
            
        Returns:
            Dizionario Esri JSON della geometria
        """
        try:
            geom_type = geom.geom_type
            
            if geom_type == "Point":
                return {"x": geom.x, "y": geom.y}
            
            elif geom_type == "MultiPoint":
                return {"points": [[p.x, p.y] for p in geom.geoms]}
            
            elif geom_type == "LineString":
                return {"paths": [list(geom.coords)]}
            
            elif geom_type == "MultiLineString":
                return {"paths": [list(line.coords) for line in geom.geoms]}
            
            elif geom_type == "Polygon":
                rings = [list(geom.exterior.coords)]
                for interior in geom.interiors:
                    rings.append(list(interior.coords))
                return {"rings": rings}
            
            elif geom_type == "MultiPolygon":
                rings = []
                for poly in geom.geoms:
                    rings.append(list(poly.exterior.coords))
                    for interior in poly.interiors:
                        rings.append(list(interior.coords))
                return {"rings": rings}
            
            else:
                logger.warning(f"Tipo geometria non supportato per Esri JSON: {geom_type}")
                return None
                
        except Exception as e:
            logger.warning(f"Errore conversione geometria Shapely -> Esri JSON: {e}")
            return None


# =============================================================================
# CLIENT PRINCIPALE
# =============================================================================

class ArcGISClient:
    """
    Client REST per ArcGIS Feature Service.
    
    Dependency Injection:
    - http_client: sostituibile con mock per test (evita chiamate HTTP reali)
    
    Uso in produzione:
        client = ArcGISClient()
        
    Uso nei test:
        mock_http = MockHttpClient()
        client = ArcGISClient(http_client=mock_http)
    """
    
    # Batch size per operazioni di scrittura (limite Esri: max 2000)
    WRITE_BATCH_SIZE = 1000
    
    # Timeout per richieste HTTP
    DEFAULT_TIMEOUT = 30
    
    def __init__(self, http_client: HttpClientProtocol = None):
        self._http = http_client or DefaultHttpClient()
        self._token_cache: Dict[str, dict] = {}  # url → {token, expires}

    # =========================================================================
    # C12: HELPER CENTRALIZZATO TOKEN-AWARE
    # =========================================================================

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
            Oggetto requests.Response. Il caller chiama `.json()`.

        NB: questo helper DOES NOT raise su 498/499 ritornati dal body
        — il caller deve fare `if "error" in data` come prima. Il
        refresh si attiva automaticamente prima di cedere la response
        finale al caller, cosi' il caller vede solo l'esito del retry.
        """
        import random

        # Difesa in profondita': rimuovi token da params/data se qualche
        # caller ha ancora passato la vecchia forma. L'header e' l'unica
        # fonte autoritativa.
        if params and "token" in params:
            params = {k: v for k, v in params.items() if k != "token"}
        if data and "token" in data:
            data = {k: v for k, v in data.items() if k != "token"}

        cache_key = f"{conn.server_url}:{conn.username or conn.client_id}"
        attempt = 0
        retry_on_401_done = False
        backoff_429_count = 0

        while True:
            token = self.authenticate(conn)
            # Merge header: extra_headers del caller (es. Referer per
            # compat ArcGIS) + Authorization Bearer
            headers = dict(extra_headers) if extra_headers else {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if not headers:
                headers = None  # evita dict vuoto → comportamento di requests

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
                raise ValueError(
                    f"Metodo HTTP non supportato: {method_upper}"
                )

            attempt += 1

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

            # Caso 2: HTTP 401 → token rifiutato lato HTTP layer
            # (alcuni endpoint ArcGIS rispondono 401 invece che 200+error)
            if response.status_code == 401 and not retry_on_401_done:
                logger.info(
                    f"[ARCGIS] 401 sul token corrente, invalido cache e "
                    f"ritento con token fresh. URL: {url}"
                )
                self._token_cache.pop(cache_key, None)
                retry_on_401_done = True
                continue

            # Caso 3: response 200 con body `error.code` in {498, 499}
            # (il pattern Esri standard per token scaduti/invalidi)
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
                            self._token_cache.pop(cache_key, None)
                            retry_on_401_done = True
                            continue

            # Response finale — successo o errore non gestito dal retry
            return response

    # =========================================================================
    # AUTENTICAZIONE
    # =========================================================================
    
    def authenticate(self, conn: ArcGISConnectionInfo) -> Optional[str]:
        """
        Ottiene un token di autenticazione per la connessione.
        
        Supporta:
        - public: nessun token
        - oauth2: client_credentials flow (client_id + client_secret)
        - token: username/password → generateToken (con fallback strategie)
        
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
            return self._authenticate_oauth2(conn, cache_key)
        
        # Token classico (username/password)
        return self._authenticate_token(conn, cache_key)
    
    def _authenticate_oauth2(self, conn: ArcGISConnectionInfo, cache_key: str) -> str:
        """
        Autenticazione OAuth2 con client_credentials.
        
        POST /sharing/rest/oauth2/token
        grant_type=client_credentials&client_id=xxx&client_secret=xxx
        """
        from urllib.parse import urlparse
        parsed = urlparse(conn.server_url)
        oauth_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest/oauth2/token"
        
        token_data = {
            "client_id": conn.client_id,
            "client_secret": conn.client_secret,
            "grant_type": "client_credentials",
            "f": "json",
        }
        
        try:
            response = self._http.post(oauth_url, data=token_data, timeout=self.DEFAULT_TIMEOUT)
            result = response.json()
            
            logger.info(f"OAuth2 request a {oauth_url} - status: {response.status_code}")
            
            if "access_token" in result:
                token = result["access_token"]
                expires_in = result.get("expires_in", 7200)  # default 2 ore
                
                self._token_cache[cache_key] = {
                    "token": token,
                    "expires": time.time() + expires_in - 60  # margine 60s
                }
                
                logger.info(f"Token OAuth2 ottenuto per {conn.server_url} (expires_in={expires_in}s)")
                return token
            else:
                error = result.get("error", {})
                error_desc = error.get("message", result.get("error_description", "Errore OAuth2"))
                raise ConnectionError(f"OAuth2 fallito: {error_desc}")
                
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione OAuth2: {e}")
    
    def _authenticate_token(self, conn: ArcGISConnectionInfo, cache_key: str) -> str:
        """
        Autenticazione classica username/password → generateToken.
        
        Prova diverse strategie (referer, requestip) per compatibilità
        con diversi tipi di server.
        """
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
                response = self._http.post(token_url, data=token_data, timeout=self.DEFAULT_TIMEOUT)
                result = response.json()
                
                logger.debug(f"Token request a {token_url} - client={_mask_secret(str(strategy.get('client', '')))} - status: {response.status_code}")
                
                if "token" in result:
                    token = result["token"]
                    expires = result.get("expires", 0)
                    expires_sec = expires / 1000 if expires > 1e10 else expires
                    
                    self._token_cache[cache_key] = {
                        "token": token,
                        "expires": expires_sec - 60
                    }
                    
                    logger.debug(f"Token ottenuto per {conn.server_url} (client={_mask_secret(str(strategy.get('client', '')))})")
                    return token
                else:
                    error_obj = result.get("error", {})
                    last_error = error_obj.get("message", "Errore sconosciuto")
                    error_details = error_obj.get("details", [])
                    logger.warning(f"Token fallito con client={_mask_secret(str(strategy.get('client', '')))}: {last_error}")
                    continue
                    
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(f"Token request fallita con client={_mask_secret(str(strategy.get('client', '')))}: {e}")
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
        # Usa il dominio dell'organizzazione (es. orgname.maps.arcgis.com)
        # invece di forzare www.arcgis.com
        if "arcgis.com" in base.lower():
            from urllib.parse import urlparse
            parsed = urlparse(base)
            # Usa il dominio originale per il token (supporta portali org-specifici)
            return f"{parsed.scheme}://{parsed.netloc}/sharing/rest/generateToken"
        
        if conn.server_type == "arcgis_online":
            from urllib.parse import urlparse
            parsed = urlparse(base)
            if "arcgis.com" in parsed.netloc.lower():
                return f"{parsed.scheme}://{parsed.netloc}/sharing/rest/generateToken"
            return "https://www.arcgis.com/sharing/rest/generateToken"
        
        # Auto-discovery: chiedi al server dove sta il token endpoint
        # GET /rest/info?f=json → {"authInfo": {"tokenServicesUrl": "..."}}
        discovered_url = self._discover_token_url(base)
        if discovered_url:
            logger.info(f"Token URL auto-discovered: {discovered_url}")
            return discovered_url
        
        # Fallback
        if conn.server_type == "arcgis_enterprise":
            if "/portal" in base.lower():
                portal_base = base[:base.lower().index("/portal") + 7]
            else:
                portal_base = base
            return f"{portal_base}/sharing/rest/generateToken"
        else:
            if "/rest/services" in base:
                server_base = base[:base.index("/rest/services")]
            elif "/arcgis" in base.lower():
                server_base = base[:base.lower().index("/arcgis") + 7]
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
            candidates.append(base_url[:base_url.index("/rest/services")] + "/rest/info")
        if "/rest" in base_url:
            candidates.append(base_url[:base_url.index("/rest")] + "/rest/info")
        candidates.append(base_url.rstrip("/") + "/rest/info")
        
        # Deduplica mantenendo ordine
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)
        
        for info_url in unique_candidates:
            try:
                response = self._http.get(info_url, params={"f": "json"}, timeout=10)
                data = response.json()
                token_url = data.get("authInfo", {}).get("tokenServicesUrl", "")
                if token_url:
                    return token_url
            except Exception:
                continue
        
        return None
    
    # =========================================================================
    # BROWSE: LISTA SERVIZI
    # =========================================================================
    
    def list_services(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
        _depth: int = 0,
        _max_depth: int = 5
    ) -> List[dict]:
        """
        Lista i Feature Service disponibili.
        
        Per ArcGIS Server/Enterprise: naviga il catalogo REST.
        Per ArcGIS Online: usa la Content API per cercare i servizi dell'utente.
        
        Args:
            conn: configurazione connessione
            folder: sotto-folder da esplorare (vuoto = root)
            
        Returns:
            Lista di dict con {name, url, folder}
        """
        # Detect ArcGIS Online → usa Content API
        if self._is_arcgis_online(conn):
            return self._list_services_online(conn, folder=folder)
        
        # ArcGIS Server/Enterprise → catalogo REST tradizionale
        return self._list_services_server(conn, folder=folder, _depth=_depth, _max_depth=_max_depth)
    
    def _is_arcgis_online(self, conn: ArcGISConnectionInfo) -> bool:
        """Verifica se la connessione è ArcGIS Online."""
        url = conn.server_url.lower()
        return (
            "arcgis.com" in url 
            or conn.server_type == "arcgis_online"
        )
    
    # -------------------------------------------------------------------------
    # ArcGIS Online: Content API
    # -------------------------------------------------------------------------
    
    def _list_services_online(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
    ) -> List[dict]:
        """
        Lista i Feature Service dell'utente su ArcGIS Online.
        
        Usa la Content API per cercare tutti i servizi dell'utente
        (root + tutti i folder).
        
        Flusso:
        1. Autentica → token
        2. GET /sharing/rest/content/users/{username}[/{folder}] → items
        3. Filtra per type = "Feature Service"
        4. Ricorsione su ogni folder
        """
        token = self.authenticate(conn)
        username = conn.username
        
        # OAuth2 client_credentials potrebbe non avere username
        # In quel caso, usa la Search API per l'organizzazione
        if not username:
            logger.info("Nessun username — uso Search API per l'organizzazione")
            return self.search_services_online(conn, query="", scope="org")
        
        # Usa il dominio dell'organizzazione (non www.arcgis.com)
        from urllib.parse import urlparse
        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        
        try:
            # Costruisci URL per la folder
            if folder:
                content_url = f"{base_url}/content/users/{username}/{folder}"
            else:
                content_url = f"{base_url}/content/users/{username}"
            
            params = {
                "f": "json",
                "num": 100,
            }
            # C12: token va nell'header Authorization via _request_with_token
            response = self._request_with_token(
                "GET", content_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()
            
            if "error" in data:
                error_msg = data["error"].get("message", "Errore Content API")
                raise ConnectionError(f"Errore ArcGIS Online: {error_msg}")
            
            services = []
            
            # Estrai Feature Service dagli items
            for item in data.get("items", []):
                if item.get("type") == "Feature Service":
                    item_url = item.get("url", "")
                    if not item_url:
                        continue
                    
                    # Non normalizzare qui — list_layers gestisce il fallback
                    logger.info(f"Content API item: {item.get('title')} -> {item_url}")
                    
                    services.append({
                        "name": item.get("title", item.get("name", "Senza nome")),
                        "full_name": item.get("name", item.get("title", "")),
                        "url": item_url,
                        "folder": folder or item.get("ownerFolder", ""),
                        "service_type": "FeatureServer",
                        "item_id": item.get("id", ""),
                        "owner": item.get("owner", username),
                        "num_views": item.get("numViews", 0),
                        "modified": item.get("modified", 0),
                    })
            
            # Ricorsione sui folder (solo dal root)
            if not folder:
                for sub_folder in data.get("folders", []):
                    folder_id = sub_folder.get("id", "")
                    folder_title = sub_folder.get("title", folder_id)
                    if folder_id:
                        sub_services = self._list_services_online(conn, folder=folder_id)
                        # Aggiorna il folder name per display
                        for svc in sub_services:
                            svc["folder"] = folder_title
                        services.extend(sub_services)
            
            logger.info(f"ArcGIS Online: trovati {len(services)} Feature Service per {username}")
            return services
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione ArcGIS Online: {e}")
    
    def list_folders(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """
        Lista le cartelle dell'utente su ArcGIS Online/Enterprise.

        Returns:
            Lista di dict con {id, title} — include anche la root (id="").
        """
        if self._is_arcgis_online(conn):
            return self._list_folders_online(conn)
        return self._list_folders_server(conn)

    def _list_folders_online(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """Lista cartelle utente su ArcGIS Online via Content API."""
        token = self.authenticate(conn)
        username = conn.username
        if not username:
            return [{"id": "", "title": "(root)"}]

        from urllib.parse import urlparse
        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        content_url = f"{base_url}/content/users/{username}"

        params = {"f": "json"}

        try:
            response = self._request_with_token(
                "GET", content_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()
            if "error" in data:
                logger.warning(f"Errore lista folders: {data['error']}")
                return [{"id": "", "title": "(root)"}]

            folders = [{"id": "", "title": "(root)"}]
            for f in data.get("folders", []):
                folders.append({
                    "id": f.get("id", ""),
                    "title": f.get("title", f.get("id", "")),
                })
            return folders
        except requests.RequestException as e:
            logger.warning(f"Errore lista folders: {e}")
            return [{"id": "", "title": "(root)"}]

    def _list_folders_server(self, conn: ArcGISConnectionInfo) -> List[dict]:
        """Lista cartelle su ArcGIS Server/Enterprise via catalogo REST."""
        catalog_url = f"{conn.server_url.rstrip('/')}/rest/services"

        params = {"f": "json"}

        try:
            response = self._request_with_token(
                "GET", catalog_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            folders = [{"id": "", "title": "(root)"}]
            for f in data.get("folders", []):
                folders.append({"id": f, "title": f})
            return folders
        except requests.RequestException as e:
            logger.warning(f"Errore lista folders server: {e}")
            return [{"id": "", "title": "(root)"}]

    def search_services_online(
        self,
        conn: ArcGISConnectionInfo,
        query: str = "",
        scope: str = "user",
    ) -> List[dict]:
        """
        Cerca Feature Service su ArcGIS Online.
        
        Permette di cercare servizi dell'utente, dell'organizzazione,
        o pubblici.
        
        Args:
            conn: configurazione connessione
            query: termini di ricerca aggiuntivi
            scope: "user" (solo miei), "org" (organizzazione), "all" (pubblici)
            
        Returns:
            Lista di dict con {name, url, folder, ...}
        """
        token = self.authenticate(conn)
        username = conn.username
        from urllib.parse import urlparse
        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        
        # Costruisci query di ricerca
        search_parts = ['type:"Feature Service"']
        
        if scope == "user" and username:
            search_parts.append(f'owner:{username}')
        elif scope == "org":
            search_parts.append('orgid:self')
        
        if query:
            search_parts.append(query)
        
        search_query = " ".join(search_parts)
        
        try:
            all_results = []
            start = 1
            
            while True:
                params = {
                    "q": search_query,
                    "f": "json",
                    "num": 100,
                    "start": start,
                    "sortField": "modified",
                    "sortOrder": "desc",
                }
                response = self._request_with_token(
                    "GET", f"{base_url}/search", conn,
                    params=params, timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()

                if "error" in data:
                    error_msg = data["error"].get("message", "Errore ricerca")
                    raise ConnectionError(f"Errore ricerca ArcGIS Online: {error_msg}")
                
                results = data.get("results", [])
                if not results:
                    break
                
                for item in results:
                    item_url = item.get("url", "")
                    if not item_url:
                        continue
                    
                    all_results.append({
                        "name": item.get("title", item.get("name", "Senza nome")),
                        "full_name": item.get("name", item.get("title", "")),
                        "url": item_url,
                        "folder": "",
                        "service_type": "FeatureServer",
                        "item_id": item.get("id", ""),
                        "owner": item.get("owner", ""),
                        "num_views": item.get("numViews", 0),
                        "modified": item.get("modified", 0),
                    })
                
                # Paginazione
                next_start = data.get("nextStart", -1)
                if next_start == -1 or next_start <= start:
                    break
                start = next_start
            
            logger.info(f"Ricerca ArcGIS Online '{search_query}': {len(all_results)} risultati")
            return all_results
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore ricerca ArcGIS Online: {e}")
    
    # -------------------------------------------------------------------------
    # ArcGIS Server/Enterprise: catalogo REST
    # -------------------------------------------------------------------------
    
    def _list_services_server(
        self,
        conn: ArcGISConnectionInfo,
        folder: str = "",
        _depth: int = 0,
        _max_depth: int = 5
    ) -> List[dict]:
        """
        Lista i Feature Service dal catalogo REST di ArcGIS Server/Enterprise.
        
        Naviga il catalogo REST e restituisce solo i FeatureServer.
        Scansiona ricorsivamente i folder.
        """
        base = self._get_rest_services_url(conn)

        catalog_url = f"{base}/{folder}" if folder else base

        params = {"f": "json"}

        try:
            response = self._request_with_token(
                "GET", catalog_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            if "error" in data:
                raise ConnectionError(f"Errore catalogo: {data['error'].get('message', '')}")
            
            services = []
            
            # Servizi nella cartella corrente
            for svc in data.get("services", []):
                if svc.get("type") == "FeatureServer":
                    svc_name = svc["name"]
                    # Rimuovi il prefisso folder dal nome se presente
                    display_name = svc_name.split("/")[-1] if "/" in svc_name else svc_name
                    services.append({
                        "name": display_name,
                        "full_name": svc_name,
                        "url": f"{base}/{svc_name}/FeatureServer",
                        "folder": folder,
                        "service_type": "FeatureServer",
                    })
            
            # Scansiona sotto-folder ricorsivamente (con limite profondità)
            if _depth < _max_depth:
                for sub_folder in data.get("folders", []):
                    folder_path = f"{folder}/{sub_folder}" if folder else sub_folder
                    sub_services = self._list_services_server(
                        conn, folder=folder_path, 
                        _depth=_depth + 1, _max_depth=_max_depth
                    )
                    services.extend(sub_services)
            
            return services
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione al catalogo: {e}")
    
    def _get_rest_services_url(self, conn: ArcGISConnectionInfo) -> str:
        """
        Determina l'URL base del catalogo REST services.
        
        Gestisce vari formati di URL input:
        - https://server.com/arcgis/rest/services → usa così com'è
        - https://server.com/arcgis → aggiunge /rest/services
        - https://services.arcgis.com/xxx → aggiunge /rest/services
        """
        base = conn.server_url.rstrip("/")
        
        if base.endswith("/rest/services"):
            return base
        elif base.endswith("/rest"):
            return f"{base}/services"
        elif "/rest/services" in base:
            # URL contiene già il path completo, potrebbe avere altro dopo
            idx = base.index("/rest/services")
            return base[:idx + len("/rest/services")]
        else:
            return f"{base}/rest/services"
    
    # =========================================================================
    # BROWSE: LISTA LAYER
    # =========================================================================
    
    def list_layers(
        self,
        conn: ArcGISConnectionInfo,
        service_url: str
    ) -> List[dict]:
        """
        Lista i layer di un Feature Service.
        
        Gestisce vari formati di URL ArcGIS Online:
        - URL diretti (services.arcgis.com/...)
        - URL proxy (utility.arcgis.com/usrsvcs/...)
        - MapServer → FeatureServer fallback
        
        Returns:
            Lista di layer con {id, name, geometry_type, feature_count, url}
        """
        token = self.authenticate(conn)
        
        # Costruisci lista di URL da provare
        urls_to_try = self._build_layer_urls(service_url)
        
        logger.info(f"list_layers: URL candidati: {urls_to_try}")
        
        params = {"f": "json"}

        last_error = ""

        for url in urls_to_try:
            try:
                logger.info(f"list_layers: provo {url}")
                response = self._request_with_token(
                    "GET", url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()
                
                if "error" in data:
                    error_msg = data['error'].get('message', '')
                    logger.warning(f"list_layers fallito per {url}: {error_msg}")
                    last_error = error_msg
                    continue
                
                # Successo! Estrai layer
                layers = []
                
                for layer in data.get("layers", []):
                    layer_info = {
                        "id": layer["id"],
                        "name": layer.get("name", f"Layer {layer['id']}"),
                        "geometry_type": layer.get("geometryType", ""),
                        "min_scale": layer.get("minScale", 0),
                        "max_scale": layer.get("maxScale", 0),
                        "url": f"{url}/{layer['id']}",
                    }
                    layer_info["feature_count"] = layer.get("count", None)
                    layers.append(layer_info)
                
                for table in data.get("tables", []):
                    layers.append({
                        "id": table["id"],
                        "name": table.get("name", f"Table {table['id']}"),
                        "geometry_type": "",
                        "url": f"{url}/{table['id']}",
                        "feature_count": None,
                    })
                
                logger.info(f"list_layers: {len(layers)} layer trovati per {url}")
                return layers
                
            except requests.RequestException as e:
                logger.warning(f"list_layers request fallita per {url}: {e}")
                last_error = str(e)
                continue
        
        raise ConnectionError(f"Errore servizio: {last_error}")
    
    def _build_layer_urls(self, service_url: str) -> List[str]:
        """
        Costruisce lista di URL candidati per il browse layer.
        
        Per URL ArcGIS Online, prova sia FeatureServer che MapServer
        perché alcuni servizi proxy supportano solo MapServer.
        """
        urls = []
        url = service_url.rstrip("/")
        
        if url.endswith("/FeatureServer"):
            urls.append(url)
            # Fallback: MapServer
            urls.append(url.replace("/FeatureServer", "/MapServer"))
        elif url.endswith("/MapServer"):
            # Prova prima FeatureServer (preferibile per query)
            urls.append(url.replace("/MapServer", "/FeatureServer"))
            urls.append(url)
        else:
            # Nessun suffisso — prova entrambi
            urls.append(url + "/FeatureServer")
            urls.append(url + "/MapServer")
        
        return urls
    
    # =========================================================================
    # BROWSE: SCHEMA LAYER (campi e metadata)
    # =========================================================================
    
    def get_layer_schema(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str
    ) -> dict:
        """
        Recupera lo schema completo di un layer (campi, tipi, capabilities).
        
        Equivalente a DatabaseInspector.get_tables_and_columns() ma per un
        singolo layer. Restituisce campi con tipo PLENORA mappato.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer (es. .../FeatureServer/0)
            
        Returns:
            Dict con schema completo: {layer_id, name, geometry_type,
            spatial_reference, fields, feature_count, capabilities}
        """
        params = {"f": "json"}

        try:
            response = self._request_with_token(
                "GET", layer_url, conn, params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()

            if "error" in data:
                raise ConnectionError(f"Errore layer: {data['error'].get('message', '')}")
            
            # Mappa i campi in formato PLENORA
            fields = []
            for f in data.get("fields", []):
                esri_type = f.get("type", "")
                fields.append({
                    "name": f.get("name", ""),
                    "alias": f.get("alias", f.get("name", "")),
                    "field_type": esri_type,
                    "urby_type": EsriTypeMapper.map_field_type(esri_type),
                    "length": f.get("length"),
                    "nullable": f.get("nullable", True),
                    "editable": f.get("editable", True),
                })
            
            # Feature count (richiede query separata se non disponibile).
            # C12: token non piu' passato (gestito internamente dal helper);
            # lasciamo None per retrocompatibilita' della signature.
            feature_count = data.get("count", None)
            if feature_count is None:
                feature_count = self._get_feature_count(conn, layer_url, None)
            
            geometry_type = data.get("geometryType", "")
            
            # Spatial reference con wkid
            spatial_ref = data.get("extent", {}).get("spatialReference", 
                                     data.get("sourceSpatialReference", {}))
            wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid")
            
            # Tipo geometria PLENORA con SRID (es. "geometry:polygon:3857")
            urby_geom = EsriTypeMapper.map_geometry_type(geometry_type)
            if wkid and urby_geom != "geometry":
                urby_geom = f"{urby_geom}:{wkid}"
            
            # Mappa le relazioni (Related Tables)
            relationships = []
            base_service_url = layer_url.rsplit("/", 1)[0]  # rimuovi /N → .../FeatureServer
            for rel in data.get("relationships", []):
                cardinality = rel.get("cardinality", "")
                card_label = {
                    "esriRelCardinalityOneToOne": "1:1",
                    "esriRelCardinalityOneToMany": "1:N",
                    "esriRelCardinalityManyToMany": "N:N",
                }.get(cardinality, cardinality)
                
                role = rel.get("role", "")
                role_label = "origin" if "Origin" in role else "destination" if "Destination" in role else role
                
                related_table_id = rel.get("relatedTableId")
                related_url = f"{base_service_url}/{related_table_id}" if related_table_id is not None else ""
                
                relationships.append({
                    "id": rel.get("id"),
                    "name": rel.get("name", ""),
                    "related_table_id": related_table_id,
                    "related_url": related_url,
                    "cardinality": card_label,
                    "key_field": rel.get("keyField", ""),
                    "key_field_in_relationship_table": rel.get("keyFieldInRelationshipTable", ""),
                    "role": role_label,
                    "composite": rel.get("composite", False),
                })
            
            # Colonne in formato canvas-ready (come DatabaseInspector)
            # Mappa field_type Esri → tipo PLENORA per ogni campo
            columns = []
            for f in fields:
                columns.append({
                    "name": f["name"],
                    "type": f["urby_type"],
                })
            # Aggiungi colonna geometria con SRID
            if geometry_type:
                columns.append({
                    "name": "geometry",
                    "type": urby_geom,
                })
            
            return {
                "layer_id": data.get("id", 0),
                "name": data.get("name", ""),
                "geometry_type": geometry_type,
                "geometry_type_urby": urby_geom,
                "spatial_reference": spatial_ref,
                "fields": fields,
                "columns": columns,
                "feature_count": feature_count,
                "capabilities": data.get("capabilities", ""),
                "max_record_count": data.get("maxRecordCount", 1000),
                "description": data.get("description", ""),
                "relationships": relationships,
            }
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore connessione al layer: {e}")
    
    def _get_feature_count(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        token: str = None  # C12: non piu' usato ma mantenuto per compat caller
    ) -> Optional[int]:
        """Query count delle features di un layer."""
        params = {
            "where": "1=1",
            "returnCountOnly": "true",
            "f": "json",
        }
        try:
            response = self._request_with_token(
                "GET", f"{layer_url}/query", conn,
                params=params, timeout=self.DEFAULT_TIMEOUT,
            )
            data = response.json()
            return data.get("count")
        except Exception:
            return None
    
    # =========================================================================
    # QUERY FEATURES
    # =========================================================================
    
    def query_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        where: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        geometry_filter: dict = None,
        result_offset: int = 0,
        result_record_count: int = None,
        order_by: str = None,
        out_sr: int = None,
        max_features: int = None,
    ) -> Tuple[List[tuple], List[str]]:
        """
        Query features da un layer — restituisce (righe, colonne).
        
        Formato output identico a QueryExecutor.execute_raw() per
        compatibilità con il resto del sistema ESTRAI.
        
        Gestisce la paginazione automatica se i risultati superano
        il maxRecordCount del server.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer
            where: clausola WHERE (default: tutti)
            out_fields: campi da restituire (* = tutti)
            return_geometry: include geometria nella risposta
            geometry_filter: filtro spaziale (bounding box)
            result_offset: offset per paginazione
            result_record_count: numero massimo per pagina
            order_by: ordinamento (es. "OBJECTID ASC")
            out_sr: SRID di output
            max_features: limite massimo totale di features
            
        Returns:
            (righe, colonne) — formato compatibile con QueryExecutor
        """
        token = self.authenticate(conn)
        
        # Recupera schema per conoscere i campi e il maxRecordCount
        schema = self.get_layer_schema(conn, layer_url)
        max_record_count = schema.get("max_record_count", 1000)
        geometry_type = schema.get("geometry_type", "")
        fields_def = schema.get("fields", [])
        
        # Determina il campo OID per la paginazione
        oid_field = self._find_oid_field(fields_def)
        
        all_features = []
        current_offset = result_offset
        page_size = result_record_count or max_record_count
        
        while True:
            params = {
                "where": where,
                "outFields": out_fields,
                "returnGeometry": "true" if return_geometry else "false",
                "resultOffset": current_offset,
                "resultRecordCount": page_size,
                "f": "json",
            }
            
            if geometry_filter:
                params["geometry"] = json.dumps(geometry_filter.get("geometry", {}))
                params["geometryType"] = geometry_filter.get("geometryType", "esriGeometryEnvelope")
                params["spatialRel"] = geometry_filter.get("spatialRel", "esriSpatialRelIntersects")
                params["inSR"] = geometry_filter.get("inSR", "")

            if order_by:
                params["orderByFields"] = order_by
            elif oid_field:
                params["orderByFields"] = f"{oid_field} ASC"

            if out_sr:
                params["outSR"] = out_sr

            try:
                response = self._request_with_token(
                    "GET", f"{layer_url}/query", conn,
                    params=params, timeout=self.DEFAULT_TIMEOUT,
                )
                data = response.json()
                
                if "error" in data:
                    error_msg = data["error"].get("message", "Errore query")
                    raise RuntimeError(f"Errore query Feature Service: {error_msg}")
                
                features = data.get("features", [])
                
                if not features:
                    break
                
                all_features.extend(features)
                
                # Controlla se abbiamo raggiunto il limite richiesto
                if max_features and len(all_features) >= max_features:
                    all_features = all_features[:max_features]
                    break
                
                # Controlla se ci sono altre pagine
                exceeded = data.get("exceededTransferLimit", False)
                if not exceeded or len(features) < page_size:
                    break
                
                current_offset += len(features)
                
            except requests.RequestException as e:
                raise ConnectionError(f"Errore query: {e}")
        
        # Filtra fields_def in base a out_fields (se specificati)
        if out_fields and out_fields != "*":
            requested_fields = set(f.strip() for f in out_fields.split(","))
            fields_def_filtered = [
                f for f in fields_def 
                if f["name"] in requested_fields
            ]
        else:
            fields_def_filtered = fields_def
        
        # Converti in formato (righe, colonne)
        return EsriJsonConverter.features_to_rows(
            all_features, fields_def_filtered, 
            include_geometry=return_geometry,
            geometry_type=geometry_type
        )
    
    def _find_oid_field(self, fields: List[dict]) -> Optional[str]:
        """Trova il campo ObjectID nel layer."""
        for f in fields:
            if f.get("field_type") == "esriFieldTypeOID":
                return f["name"]
        # Fallback: cerca per nome comune
        for f in fields:
            if f["name"].upper() in ("OBJECTID", "OID", "FID"):
                return f["name"]
        return None
    
    # =========================================================================
    # SCRITTURA: ADD / UPDATE / DELETE
    # =========================================================================
    
    def add_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """
        Aggiunge features a un layer.
        
        Le features vengono inviate in batch di WRITE_BATCH_SIZE per
        rispettare i limiti del server.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer
            features: lista features in formato Esri JSON
            
        Returns:
            {success, added_count, errors}
        """
        return self._write_batch(conn, layer_url, "addFeatures", features, batch_size=batch_size)
    
    def update_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """
        Aggiorna features esistenti in un layer.
        
        Ogni feature deve includere il campo ObjectID negli attributes.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer
            features: lista features con ObjectID
            
        Returns:
            {success, updated_count, errors}
        """
        return self._write_batch(conn, layer_url, "updateFeatures", features, batch_size=batch_size)
    
    def delete_features(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        object_ids: List[int] = None,
        where: str = None,
    ) -> dict:
        """
        Elimina features da un layer.
        
        Può eliminare per ObjectID o per clausola WHERE.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer
            object_ids: lista di ObjectID da eliminare
            where: clausola WHERE alternativa
            
        Returns:
            {success, deleted_count, errors}
        """
        referer_headers = {"Referer": conn.server_url}

        data = {"f": "json"}

        if object_ids:
            data["objectIds"] = ",".join(str(oid) for oid in object_ids)
        elif where:
            data["where"] = where
        else:
            raise ValueError("Specificare object_ids o where per l'eliminazione")

        try:
            response = self._request_with_token(
                "POST", f"{layer_url}/deleteFeatures", conn,
                data=data, timeout=self.DEFAULT_TIMEOUT,
                extra_headers=referer_headers,
            )
            result = response.json()
            
            if "error" in result:
                return {
                    "success": False,
                    "deleted_count": 0,
                    "errors": [result["error"]],
                }
            
            delete_results = result.get("deleteResults", [])
            successes = sum(1 for r in delete_results if r.get("success"))
            errors = [r for r in delete_results if not r.get("success")]
            
            return {
                "success": len(errors) == 0,
                "deleted_count": successes,
                "errors": errors,
            }
            
        except requests.RequestException as e:
            return {
                "success": False,
                "deleted_count": 0,
                "errors": [{"message": str(e)}],
            }
    
    def _write_batch(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        operation: str,
        features: List[dict],
        batch_size: int = None,
    ) -> dict:
        """
        Esegue un'operazione di scrittura in batch.
        
        Args:
            conn: configurazione connessione
            layer_url: URL del layer
            operation: "addFeatures" o "updateFeatures"
            features: lista features
            
        Returns:
            {success, count, errors}
        """
        # C12: auth avviene dentro _request_with_token per-batch.
        # Rimosso il `token = self.authenticate(conn)` pre-loop: non
        # serviva piu' (il helper gestisce token + refresh mid-batch).
        referer_headers = {"Referer": conn.server_url}

        total_success = 0
        all_errors = []
        
        # Invia in batch
        _batch_size = batch_size if batch_size and batch_size > 0 else self.WRITE_BATCH_SIZE
        for i in range(0, len(features), _batch_size):
            batch = features[i:i + _batch_size]
            
            data = {
                "features": json.dumps(batch),
                "f": "json",
            }

            try:
                batch_num = i // _batch_size + 1
                logger.debug(f"Batch {batch_num}: invio {len(batch)} features a {layer_url}/{operation}")

                # C12: ogni batch via _request_with_token → refresh token
                # mid-batch automatico su 498/499, backoff su 429.
                # Pre-fix il token era autenticato UNA volta fuori loop,
                # scadenza a meta' batch = tutto il resto falliva.
                response = self._request_with_token(
                    "POST", f"{layer_url}/{operation}", conn,
                    data=data, timeout=60,
                    extra_headers=referer_headers,
                )
                try:
                    result = response.json()
                except Exception as json_err:
                    logger.error(f"[CRASH] Batch {batch_num}: risposta non JSON ({response.status_code}): {response.text[:200]}")
                    all_errors.append({"message": f"JSON parse error: {json_err}"})
                    continue
                
                
                if "error" in result:
                    logger.error(f"[GESTITO] Batch {batch_num} errore: {result['error']}")
                    all_errors.append(result["error"])
                    continue
                
                # addResults o updateResults
                result_key = "addResults" if operation == "addFeatures" else "updateResults"
                results = result.get(result_key, [])
                
                successes = sum(1 for r in results if r.get("success"))
                errors = [r for r in results if not r.get("success")]
                
                logger.debug(f"Batch {batch_num}: {successes} ok, {len(errors)} errori")
                if errors:
                    logger.error(f"[GESTITO] Batch {batch_num} errori (primi 2): {errors[:2]}")
                
                total_success += successes
                all_errors.extend(errors)
                
            except requests.RequestException as e:
                all_errors.append({"message": str(e)})
        
        count_key = "added_count" if operation == "addFeatures" else "updated_count"
        
        # Se non abbiamo scritto nulla ma avevamo features → considera fallimento
        total_attempted = len(features)
        has_errors = len(all_errors) > 0
        write_success = (total_success > 0 or total_attempted == 0) and not has_errors

        return {
            "success": write_success,
            count_key: total_success,
            "errors": all_errors,
        }
    
    # =========================================================================
    # CONVERSIONE: DataFrame → Esri JSON
    # =========================================================================
    
    @staticmethod
    def dataframe_to_esri_features(
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        operation: str = "add",
    ) -> List[dict]:
        """
        Converte un DataFrame/GeoDataFrame in una lista di features Esri JSON.
        
        Supporta:
        - GeoDataFrame con colonna geometry (shapely)
        - DataFrame con colonna geometry (WKT string)
        
        Args:
            df: DataFrame o GeoDataFrame
            geometry_column: nome colonna geometria (default: geometry)
            srid: SRID della geometria (default: 4326)
            
        Returns:
            Lista di features in formato Esri JSON:
            [{"attributes": {...}, "geometry": {...}}, ...]
        """
        import pandas as pd
        
        has_geometry = False
        is_geodataframe = False
        
        try:
            import geopandas as gpd
            if isinstance(df, gpd.GeoDataFrame) and df.geometry is not None:
                is_geodataframe = True
                has_geometry = True
                if df.crs:
                    srid = df.crs.to_epsg() or srid
        except ImportError:
            pass
        
        if not is_geodataframe and geometry_column in df.columns:
            has_geometry = True
        
        spatial_ref = {"wkid": srid}
        features = []
        
        for idx, row in df.iterrows():
            feature = {"attributes": {}}
            
            # Attributi (tutto tranne geometria e campi auto-generati da ArcGIS)
            # Per update/delete: OBJECTID serve come chiave, non va escluso
            skip_cols = {geometry_column, "geometry"}
            if operation in ("add", "create_service"):
                skip_cols.update({"OBJECTID", "objectid", "GLOBALID", "globalid", "GlobalID"})
            for col in df.columns:
                if col in skip_cols:
                    continue
                val = row[col]
                # Converti tipi numpy/pandas a tipi Python nativi
                if pd.isna(val):
                    feature["attributes"][col] = None
                elif hasattr(val, 'item'):  # numpy scalar
                    feature["attributes"][col] = val.item()
                else:
                    feature["attributes"][col] = val
            
            # Geometria
            if has_geometry:
                geom = None
                if is_geodataframe:
                    geom = row.geometry
                elif geometry_column in df.columns and pd.notna(row[geometry_column]):
                    from shapely import wkt
                    try:
                        geom = wkt.loads(str(row[geometry_column]))
                    except Exception:
                        geom = None
                
                if geom is not None and not geom.is_empty:
                    feature["geometry"] = ArcGISClient._shapely_to_esri(geom, spatial_ref)
            
            features.append(feature)
        
        logger.debug(f"Convertite {len(features)} features (has_geometry={has_geometry}, srid={srid})")
        return features
    
    @staticmethod
    def _shapely_to_esri(geom, spatial_ref: dict) -> dict:
        """
        Converte una geometria Shapely in formato Esri JSON.
        
        Supporta Point, MultiPoint, LineString, MultiLineString,
        Polygon, MultiPolygon.
        """
        from shapely.geometry import mapping
        
        geom_type = geom.geom_type
        
        if geom_type == "Point":
            return {
                "x": geom.x,
                "y": geom.y,
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiPoint":
            return {
                "points": [[p.x, p.y] for p in geom.geoms],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "LineString":
            return {
                "paths": [list(geom.coords)],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiLineString":
            return {
                "paths": [list(line.coords) for line in geom.geoms],
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "Polygon":
            rings = [list(geom.exterior.coords)]
            for interior in geom.interiors:
                rings.append(list(interior.coords))
            return {
                "rings": rings,
                "spatialReference": spatial_ref,
            }
        
        elif geom_type == "MultiPolygon":
            rings = []
            for polygon in geom.geoms:
                rings.append(list(polygon.exterior.coords))
                for interior in polygon.interiors:
                    rings.append(list(interior.coords))
            return {
                "rings": rings,
                "spatialReference": spatial_ref,
            }
        
        else:
            logger.warning(f"Tipo geometria non supportato per Esri JSON: {geom_type}")
            return None
    
    @staticmethod
    def _esri_geometry_type(geom_type: str) -> str:
        """Converte tipo geometria shapely/urby → esriGeometryType."""
        mapping = {
            "Point": "esriGeometryPoint",
            "MultiPoint": "esriGeometryMultipoint",
            "LineString": "esriGeometryPolyline",
            "MultiLineString": "esriGeometryPolyline",
            "Polygon": "esriGeometryPolygon",
            "MultiPolygon": "esriGeometryPolygon",
            # Formati PLENORA
            "geometry:point": "esriGeometryPoint",
            "geometry:line": "esriGeometryPolyline",
            "geometry:polygon": "esriGeometryPolygon",
        }
        return mapping.get(geom_type, "esriGeometryPolygon")
    
    @staticmethod
    def _python_type_to_esri_field(col_name: str, dtype) -> dict:
        """Converte tipo colonna pandas → definizione campo Esri."""
        dtype_str = str(dtype)
        
        if "int" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeInteger", "alias": col_name}
        elif "float" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeDouble", "alias": col_name}
        elif "datetime" in dtype_str:
            return {"name": col_name, "type": "esriFieldTypeDate", "alias": col_name}
        else:
            return {"name": col_name, "type": "esriFieldTypeString", "alias": col_name, "length": 255}
    
    @staticmethod
    def _default_drawing_info(geom_type: str) -> dict:
        """Genera drawingInfo con stile di default visibile per il tipo di geometria."""
        # Colore fill: blu semitrasparente — outline: blu scuro
        fill_color = [51, 136, 255, 130]    # #3388FF ~50% alpha
        outline_color = [20, 72, 168, 255]  # #1448A8 opaco
        line_color = [51, 136, 255, 255]    # #3388FF opaco
        marker_color = [51, 136, 255, 255]

        if geom_type in ("esriGeometryPoint", "esriGeometryMultipoint"):
            return {
                "renderer": {
                    "type": "simple",
                    "symbol": {
                        "type": "esriSMS",
                        "style": "esriSMSCircle",
                        "color": marker_color,
                        "size": 8,
                        "outline": {
                            "type": "esriSLS",
                            "style": "esriSLSSolid",
                            "color": outline_color,
                            "width": 1,
                        },
                    },
                }
            }

        if geom_type == "esriGeometryPolyline":
            return {
                "renderer": {
                    "type": "simple",
                    "symbol": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": line_color,
                        "width": 2,
                    },
                }
            }

        # Polygon (default)
        return {
            "renderer": {
                "type": "simple",
                "symbol": {
                    "type": "esriSFS",
                    "style": "esriSFSSolid",
                    "color": fill_color,
                    "outline": {
                        "type": "esriSLS",
                        "style": "esriSLSSolid",
                        "color": outline_color,
                        "width": 1,
                    },
                },
            }
        }

    # =========================================================================
    # CREAZIONE FEATURE SERVICE (ArcGIS Online)
    # =========================================================================

    def create_service(
        self,
        conn: ArcGISConnectionInfo,
        service_name: str,
        layer_name: str,
        df,
        geometry_column: str = "geometry",
        srid: int = 4326,
        folder_id: str = "",
    ) -> dict:
        """
        Crea un nuovo Feature Service su ArcGIS Online con layer e schema
        derivati dal DataFrame.
        
        Flusso:
        1. createService → crea il servizio vuoto
        2. addToDefinition → aggiunge layer con schema
        3. addFeatures → carica i dati
        
        Args:
            conn: configurazione connessione
            service_name: nome del nuovo servizio
            layer_name: nome del layer
            df: DataFrame con i dati
            geometry_column: colonna geometria
            srid: SRID
            
        Returns:
            {success, service_url, layer_url, feature_count}
        """
        import pandas as pd
        
        token = self.authenticate(conn)
        username = conn.username
        
        if not username:
            raise ValueError("Username richiesto per creare un servizio")
        
        from urllib.parse import urlparse
        parsed = urlparse(conn.server_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/sharing/rest"
        
        # ---- Step 1: Crea il servizio vuoto ----
        
        # Unita basate su SRID
        units = "esriDecimalDegrees" if srid == 4326 else "esriMeters"

        create_params = {
            "name": service_name,
            "hasStaticData": False,
            "maxRecordCount": 2000,
            "supportedQueryFormats": "JSON",
            "capabilities": "Create,Delete,Query,Update,Editing",
            "allowGeometryUpdates": True,
            "units": units,
            "spatialReference": {"wkid": srid},
            "initialExtent": {
                "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
                "spatialReference": {"wkid": srid},
            },
            "xssPreventionInfo": {"xssPreventionEnabled": True},
        }
        
        create_data = {
            "createParameters": json.dumps(create_params),
            "outputType": "featureService",
            "f": "json",
        }

        # Header Referer necessario per token client=referer
        referer_headers = {"Referer": conn.server_url}

        try:
            if folder_id:
                create_url = f"{base_url}/content/users/{username}/{folder_id}/createService"
            else:
                create_url = f"{base_url}/content/users/{username}/createService"
            logger.debug(f"createService URL: {create_url}")
            logger.debug(f"createService params: {json.dumps(create_params, indent=2)}")
            logger.debug(f"Token presente: {bool(token)}, lunghezza: {len(token) if token else 0}")
            logger.debug(f"Referer: {conn.server_url}")

            response = self._request_with_token(
                "POST", create_url, conn,
                data=create_data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()
            logger.debug(f"createService risposta completa: {json.dumps(result, indent=2, default=str)}")
            
            if not result.get("success"):
                error = result.get("error", {})
                error_msg = error.get("message", "") if isinstance(error, dict) else str(error)
                error_code = error.get("code", "") if isinstance(error, dict) else ""
                full_msg = f"Errore creazione servizio: code={error_code}, message={error_msg}, full_response={result}"
                logger.error(full_msg)
                raise ConnectionError(full_msg)
            
            service_url = result.get("serviceurl", "")
            service_item_id = result.get("itemId", "")
            
            logger.info(f"Servizio creato: {service_name} -> {service_url}")
            
        except requests.RequestException as e:
            raise ConnectionError(f"Errore creazione servizio: {e}")
        
        # ---- Step 2: Aggiungi la definizione del layer ----
        
        # Determina tipo geometria dal DataFrame.
        # Usiamo il tipo MAGGIORITARIO (value_counts) invece della prima
        # feature: se il df ha una feature stray di tipo diverso (es. 1
        # Point in mezzo a migliaia di Polygon per errore di caricamento),
        # usare la prima potrebbe creare il servizio col tipo sbagliato →
        # tutte le altre fallirebbero in addFeatures.
        geom_type = "esriGeometryPolygon"  # default
        try:
            import geopandas as gpd
            if isinstance(df, gpd.GeoDataFrame) and df.geometry is not None and len(df) > 0:
                geom_types = df.geometry.dropna().geom_type
                if len(geom_types) > 0:
                    majority_type = geom_types.value_counts().idxmax()
                    geom_type = self._esri_geometry_type(majority_type)
                    # Warning se c'e' mix di tipi (ArcGIS non li supporta)
                    unique_types = set(geom_types)
                    if len(unique_types) > 1:
                        logger.warning(
                            f"create_service: DataFrame contiene {len(unique_types)} "
                            f"tipi di geometria diversi ({unique_types}), usato il "
                            f"maggioritario '{majority_type}'. Le feature di tipo "
                            f"diverso potrebbero fallire in addFeatures."
                        )
                if df.crs:
                    srid = df.crs.to_epsg() or srid
        except (ImportError, IndexError):
            pass
        
        if not geom_type.startswith("esri") and geometry_column in df.columns:
            # Prova dal WKT
            from shapely import wkt
            sample = df[geometry_column].dropna().iloc[0] if len(df) > 0 else None
            if sample:
                try:
                    geom = wkt.loads(str(sample))
                    geom_type = self._esri_geometry_type(geom.geom_type)
                except Exception as _e:
                    logger.debug(f"Impossibile inferire geom_type da WKT sample: {_e}")
        
        # Costruisci definizione campi
        fields = [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "editable": False},
        ]
        for col in df.columns:
            if col in (geometry_column, "geometry", "OBJECTID", "objectid"):
                continue
            fields.append(self._python_type_to_esri_field(col, df[col].dtype))
        
        # Calcola extent dai dati
        extent = {
            "xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90,
            "spatialReference": {"wkid": srid},
        }
        try:
            import geopandas as gpd
            if isinstance(df, gpd.GeoDataFrame) and df.geometry is not None and len(df) > 0:
                bounds = df.geometry.dropna().total_bounds  # [minx, miny, maxx, maxy]
                if len(bounds) == 4 and all(abs(b) < 1e15 for b in bounds):
                    extent = {
                        "xmin": float(bounds[0]),
                        "ymin": float(bounds[1]),
                        "xmax": float(bounds[2]),
                        "ymax": float(bounds[3]),
                        "spatialReference": {"wkid": srid},
                    }
                    logger.debug(f"Extent calcolato dai dati: {extent}")
            elif geometry_column in df.columns:
                from shapely import wkt as _wkt
                geom_series = df[geometry_column].dropna()
                if len(geom_series) > 0:
                    geoms = [_wkt.loads(str(g)) for g in geom_series.head(1000)]
                    from shapely.geometry import box as _box
                    from shapely.ops import unary_union as _union
                    combined = _union(geoms)
                    b = combined.bounds  # (minx, miny, maxx, maxy)
                    extent = {
                        "xmin": float(b[0]), "ymin": float(b[1]),
                        "xmax": float(b[2]), "ymax": float(b[3]),
                        "spatialReference": {"wkid": srid},
                    }
                    logger.debug(f"Extent calcolato da WKT: {extent}")
        except Exception as _e:
            logger.warning(f"Impossibile calcolare extent, uso default: {_e}")

        # Renderer di default basato sul tipo di geometria
        drawing_info = self._default_drawing_info(geom_type)

        layer_def = {
            "layers": [{
                "id": 0,
                "name": layer_name,
                "type": "Feature Layer",
                "geometryType": geom_type,
                "objectIdField": "OBJECTID",
                "fields": fields,
                "extent": extent,
                "drawingInfo": drawing_info,
                "hasAttachments": False,
                "capabilities": "Create,Delete,Query,Update,Editing",
            }]
        }
        
        # L'URL admin per aggiungere la definizione
        admin_url = service_url.replace("/rest/services/", "/rest/admin/services/")
        
        field_names = [f["name"] for f in fields]
        logger.debug(f"addToDefinition: {len(fields)} campi: {field_names}")
        logger.debug(f"addToDefinition URL: {admin_url}/addToDefinition")
        
        add_def_data = {
            "addToDefinition": json.dumps(layer_def),
            "f": "json",
        }

        try:
            response = self._request_with_token(
                "POST", f"{admin_url}/addToDefinition", conn,
                data=add_def_data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()
            logger.debug(f"addToDefinition risposta: {json.dumps(result, indent=2, default=str)[:500]}")

            if not result.get("success"):
                logger.error(f"[GESTITO] Errore addToDefinition: {result}")
                # Non blocchiamo — il servizio è creato
            else:
                logger.debug(f"Layer definizione aggiunta: {layer_name}")
                
        except requests.RequestException as e:
            logger.error(f"[GESTITO] Errore addToDefinition: {e}")
        
        # ---- Step 3: Carica i dati ----
        
        layer_url = f"{service_url}/0"
        features = self.dataframe_to_esri_features(df, geometry_column, srid, operation="create_service")
        
        # Log sample feature per debug
        if features:
            sample = features[0]
            sample_geom = sample.get("geometry", {})
            sample_attrs = list(sample.get("attributes", {}).keys())[:5]
            logger.debug(f"Sample feature — geometry keys: {list(sample_geom.keys()) if sample_geom else 'NONE'}")
            logger.debug(f"Sample feature — geometry snippet: {json.dumps(sample_geom, default=str)[:300]}")
            logger.debug(f"Sample feature — attribute keys (primi 5): {sample_attrs}")
        
        write_result = self.add_features(conn, layer_url, features)
        
        logger.debug(f"addFeatures result: added={write_result.get('added_count', 0)}, "
                     f"errors={len(write_result.get('errors', []))}")
        if write_result.get("errors"):
            logger.error(f"[GESTITO] addFeatures errori (primi 3): {write_result['errors'][:3]}")
        
        # ---- Step 4: Aggiorna extent del layer con i dati reali ----
        try:
            update_def = {
                "layers": [{
                    "id": 0,
                    "extent": extent,
                }]
            }
            update_data = {
                "updateDefinition": json.dumps(update_def),
                "f": "json",
            }
            response = self._request_with_token(
                "POST", f"{admin_url}/updateDefinition", conn,
                data=update_data,
                timeout=60,
                extra_headers=referer_headers,
            )
            upd_result = response.json()
            if upd_result.get("success"):
                logger.debug(f"Extent aggiornato sul servizio")
            else:
                logger.warning(f"updateDefinition extent non riuscito: {upd_result}")
        except Exception as _e:
            logger.warning(f"Impossibile aggiornare extent: {_e}")

        return {
            "success": write_result.get("added_count", 0) > 0,
            "service_url": service_url,
            "layer_url": layer_url,
            "item_id": service_item_id,
            "feature_count": write_result.get("added_count", 0),
            "errors": write_result.get("errors", []),
        }
    
    # =========================================================================
    # TEST CONNESSIONE
    # =========================================================================
    
    def test_connection(self, conn: ArcGISConnectionInfo) -> dict:
        """
        Testa la connessione ArcGIS.
        
        Verifica:
        1. Raggiungibilità del server
        2. Autenticazione (se richiesta)
        3. Accesso al catalogo servizi
        
        Returns:
            {success, message, details}
        """
        try:
            # Step 1: Autenticazione
            if conn.auth_type != "public":
                token = self.authenticate(conn)
                if not token:
                    return {
                        "success": False,
                        "message": "Autenticazione fallita: nessun token ricevuto",
                    }
            
            # Step 2: Accesso catalogo
            services = self.list_services(conn)
            
            return {
                "success": True,
                "message": f"Connessione riuscita. {len(services)} Feature Service trovati.",
                "details": {
                    "server_url": conn.server_url,
                    "server_type": conn.server_type,
                    "feature_services_count": len(services),
                    "services": [s["name"] for s in services[:10]],  # max 10 per preview
                }
            }
            
        except ConnectionError as e:
            return {
                "success": False,
                "message": f"Errore connessione: {str(e)}",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Errore: {str(e)}",
            }


    # =========================================================================
    # STILI / RENDERER
    # =========================================================================

    def update_renderer(
        self,
        conn: ArcGISConnectionInfo,
        layer_url: str,
        drawing_info: dict,
    ) -> dict:
        """
        Aggiorna il renderer (stile) di un layer tramite updateDefinition.

        Args:
            conn: configurazione connessione
            layer_url: URL del layer (es. .../FeatureServer/0)
            drawing_info: dict con la definizione drawingInfo Esri

        Returns:
            {success, message}
        """
        token = self.authenticate(conn)
        referer_headers = {"Referer": conn.server_url}

        # Ricava admin URL dal layer URL
        admin_url = layer_url.replace("/rest/services/", "/rest/admin/services/")

        # Estrai layer ID dall'URL (ultimo segmento numerico)
        parts = layer_url.rstrip("/").split("/")
        layer_id = int(parts[-1]) if parts[-1].isdigit() else 0

        update_def = {
            "layers": [{
                "id": layer_id,
                "drawingInfo": drawing_info,
            }]
        }

        # L'admin URL è il service, non il layer
        service_admin_url = "/".join(admin_url.split("/")[:-1]) if parts[-1].isdigit() else admin_url

        data = {
            "updateDefinition": json.dumps(update_def),
            "f": "json",
        }

        try:
            response = self._request_with_token(
                "POST", f"{service_admin_url}/updateDefinition", conn,
                data=data,
                timeout=60,
                extra_headers=referer_headers,
            )
            result = response.json()

            if result.get("success"):
                logger.info(f"Renderer aggiornato per layer {layer_id}")
                return {"success": True, "message": "Renderer aggiornato"}
            else:
                error = result.get("error", {})
                msg = error.get("message", str(result))
                logger.error(f"[GESTITO] updateDefinition renderer fallito: {msg}")
                return {"success": False, "message": msg}

        except requests.RequestException as e:
            return {"success": False, "message": str(e)}

    # =========================================================================

# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_client(http_client: HttpClientProtocol = None) -> ArcGISClient:
    """Factory function per creare un ArcGISClient con Dependency Injection."""
    return ArcGISClient(http_client=http_client)