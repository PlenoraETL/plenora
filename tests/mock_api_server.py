"""
mock_api_server.py — Plenora ARRICCHISCI Test Suite
Server FastAPI locale per simulare tutte le condizioni dei test A-01→A-74.

Avvio:
    uvicorn mock_api_server:app --port 8999 --reload

Reset contatori tra un test e l'altro:
    POST http://localhost:8999/mock/reset
"""

import asyncio
import time
import threading
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

app = FastAPI(title="Plenora Mock API Server", version="1.0.0")

# ─────────────────────────────────────────────────────────────────────────────
# STATO GLOBALE (reset via POST /mock/reset)
# ─────────────────────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self):
        self.reset()

    def reset(self):
        # Contatori chiamate per endpoint (usati da A-49 cache dedup, A-67 parallel)
        self.call_counts: dict[str, int] = defaultdict(int)
        # Chiamate attive simultanee (peak per A-67)
        self.active_calls: int = 0
        self.peak_concurrent: int = 0
        self._lock = threading.Lock()
        # Flaky: per ogni "session_id" tiene il conteggio dei fallimenti già emessi
        self.flaky_hits: dict[str, int] = defaultdict(int)
        # OAuth2: token emessi
        self.tokens_issued: int = 0
        # Token scaduto simulato (A-71 parziale)
        self.force_token_expired: bool = False

state = ServerState()


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def _check_bearer(authorization: Optional[str]):
    """Verifica che sia presente un Bearer token valido."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Bearer token")

def _check_api_key(x_api_key: Optional[str]):
    """Verifica che sia presente l'API key corretta."""
    if x_api_key != "test-api-key-valid":
        raise HTTPException(status_code=401, detail="Invalid API Key")

def _check_basic_auth(authorization: Optional[str]):
    """Verifica Basic Auth (user:password = testuser:testpass in base64)."""
    import base64
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Missing Basic Auth")
    try:
        decoded = base64.b64decode(authorization[6:]).decode()
        user, pwd = decoded.split(":", 1)
        if user != "testuser" or pwd != "testpass":
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed Basic Auth")


# ─────────────────────────────────────────────────────────────────────────────
# RESET
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/mock/reset", summary="Reset tutti i contatori e lo stato del server")
def reset_state():
    state.reset()
    return {"reset": True}


# ─────────────────────────────────────────────────────────────────────────────
# AUTH — OAuth2 Client Credentials (A-04, A-23, A-71)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/auth/token", summary="OAuth2 Client Credentials grant")
async def oauth2_token(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")

    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")
    if client_id != "test-client" or client_secret != "test-secret":
        raise HTTPException(status_code=401, detail="invalid_client")

    state.tokens_issued += 1
    expires_in = 2 if state.force_token_expired else 3600

    return {
        "access_token": f"mock-access-token-{state.tokens_issued}",
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": "read"
    }


@app.post("/auth/token/expire", summary="Forza il prossimo token a scadere subito (A-71)")
def force_expire():
    state.force_token_expired = True
    return {"forced_expiry": True}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/SIMPLE — JSON piatto (A-01, A-05, A-13, A-42, A-49, A-73)
# ─────────────────────────────────────────────────────────────────────────────

COUNTRIES_DB = {
    "italy":       {"name": "Italy",       "capital": "Rome",      "population": 60317000, "area": 301340},
    "france":      {"name": "France",      "capital": "Paris",     "population": 67391000, "area": 551695},
    "germany":     {"name": "Germany",     "capital": "Berlin",    "population": 83240000, "area": 357114},
    "spain":       {"name": "Spain",       "capital": "Madrid",    "population": 47351000, "area": 505990},
    "portugal":    {"name": "Portugal",    "capital": "Lisbon",    "population": 10196700, "area": 92212},
    "netherlands": {"name": "Netherlands", "capital": "Amsterdam", "population": 17441000, "area": 41543},
    "belgium":     {"name": "Belgium",     "capital": "Brussels",  "population": 11590000, "area": 30528},
    "austria":     {"name": "Austria",     "capital": "Vienna",    "population": 9006000,  "area": 83871},
    "sweden":      {"name": "Sweden",      "capital": "Stockholm", "population": 10353000, "area": 450295},
    "poland":      {"name": "Poland",      "capital": "Warsaw",    "population": 37950000, "area": 312696},
}

@app.get("/mock/simple", summary="JSON piatto con dati paese — no auth (A-01, A-05, A-13, A-42, A-49)")
def mock_simple(country: str = Query(..., description="Nome paese in minuscolo")):
    state.call_counts["simple"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


@app.get("/mock/simple/bearer", summary="JSON piatto con Bearer auth (A-02, A-17)")
def mock_simple_bearer(
    country: str = Query(...),
    authorization: Optional[str] = Header(None)
):
    _check_bearer(authorization)
    state.call_counts["simple_bearer"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


@app.get("/mock/simple/apikey", summary="JSON piatto con API Key header (A-03, A-18)")
def mock_simple_apikey(
    country: str = Query(...),
    x_api_key: Optional[str] = Header(None)
):
    _check_api_key(x_api_key)
    state.call_counts["simple_apikey"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


@app.get("/mock/simple/basic", summary="JSON piatto con Basic Auth (A-06 — credenziali errate)")
def mock_simple_basic(
    country: str = Query(...),
    authorization: Optional[str] = Header(None)
):
    _check_basic_auth(authorization)
    state.call_counts["simple_basic"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


@app.post("/mock/simple/post", summary="POST body JSON (A-14)")
async def mock_simple_post(request: Request):
    state.call_counts["simple_post"] += 1
    body = await request.json()
    country = body.get("country", "").lower()
    city = body.get("city", "")
    if country not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    result = dict(COUNTRIES_DB[country])
    result["queried_city"] = city
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/NESTED — JSON annidato (A-47, A-63, A-64)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/nested", summary="Risposta REST-Countries style con path annidato (A-47)")
def mock_nested(country: str = Query(...)):
    """
    Struttura: lista di oggetti — accesso via [0].name.common, [0].capital[0], ecc.
    Riproduce la struttura reale di REST Countries v3.
    """
    state.call_counts["nested"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    d = COUNTRIES_DB[key]
    return [
        {
            "name": {"common": d["name"], "official": f"Republic of {d['name']}"},
            "capital": [d["capital"]],
            "population": d["population"],
            "area": d["area"],
            "latlng": [41.9, 12.5],
            "flags": {"png": f"https://flagcdn.com/{key[:2]}.png"}
        }
    ]


@app.get("/mock/nested/missing-field", summary="Risposta con path inesistente (A-63)")
def mock_nested_missing(country: str = Query(...)):
    """Il campo 'campoChNonEsiste' non è presente — testa gestione graceful."""
    state.call_counts["nested_missing"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return [{"name": COUNTRIES_DB[key]["name"], "population": COUNTRIES_DB[key]["population"]}]


@app.get("/mock/nested/object-not-array", summary="Oggetto singolo invece di array con path [0] (A-64)")
def mock_nested_object(country: str = Query(...)):
    """Restituisce oggetto {} non array — testa handling graceful di [0] su non-lista."""
    state.call_counts["nested_object"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    d = COUNTRIES_DB[key]
    return {"name": d["name"], "capital": d["capital"], "population": d["population"]}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/WILDCARD — Array con [*] wildcard (A-27, A-57)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/wildcard", summary="Array annidato features[*].properties (A-27)")
def mock_wildcard(country: str = Query(...)):
    state.call_counts["wildcard"] += 1
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": country, "value": i * 10, "tag": f"item-{i}"},
                "geometry": {"type": "Point", "coordinates": [12.5 + i, 41.9 + i]}
            }
            for i in range(3)
        ]
    }


@app.get("/mock/wildcard/empty", summary="Array vuoto [] (A-57)")
def mock_wildcard_empty(country: str = Query(...)):
    state.call_counts["wildcard_empty"] += 1
    return {"type": "FeatureCollection", "features": []}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/GEOMETRY — GeoJSON e WKT (A-19, A-43, A-69)
# ─────────────────────────────────────────────────────────────────────────────

CITY_COORDS = {
    "rome":      (41.9028, 12.4964),
    "paris":     (48.8566, 2.3522),
    "berlin":    (52.5200, 13.4050),
    "madrid":    (40.4168, -3.7038),
    "lisbon":    (38.7169, -9.1399),
    "amsterdam": (52.3676, 4.9041),
    "brussels":  (50.8503, 4.3517),
    "vienna":    (48.2082, 16.3738),
    "stockholm": (59.3293, 18.0686),
    "warsaw":    (52.2297, 21.0122),
}

@app.get("/mock/geometry", summary="Risposta con lat/lon → usato per WKT geometry (A-19, A-43, A-69)")
def mock_geometry(city: str = Query(...)):
    state.call_counts["geometry"] += 1
    key = city.lower()
    coords = CITY_COORDS.get(key, (45.0, 10.0))
    return {
        "city": city,
        "lat": coords[0],
        "lon": coords[1],
        "temperature": 18.5,
        "windspeed": 12.3,
        "geometry": {
            "type": "Point",
            "coordinates": [coords[1], coords[0]]
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/NULL-FIELDS — Campi null (A-55)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/null-fields", summary="Risposta con alcuni campi null (A-55)")
def mock_null_fields(country: str = Query(...)):
    state.call_counts["null_fields"] += 1
    key = country.lower()
    d = COUNTRIES_DB.get(key, {"name": country, "population": 0})
    return {
        "name": None,           # null intenzionale
        "capital": None,        # null intenzionale
        "population": d.get("population", 0),
        "area": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/WRONG-TYPES — Tipi inattesi (A-56)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/wrong-types", summary="Population come stringa invece di numero (A-56)")
def mock_wrong_types(country: str = Query(...)):
    state.call_counts["wrong_types"] += 1
    key = country.lower()
    d = COUNTRIES_DB.get(key, {"name": country, "capital": "Unknown", "population": 0})
    return {
        "name": d["name"],
        "capital": d["capital"],
        "population": str(d["population"]),   # stringa invece di int
        "area": "unknown",                     # stringa invece di numero
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/EMPTY-ARRAY — Array vuoto come risposta diretta (A-57)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/empty-array", summary="Risposta array vuoto [] (A-57)")
def mock_empty_array(country: str = Query(...)):
    state.call_counts["empty_array"] += 1
    return []


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/SLOW — Risposta lenta / timeout (A-07, A-32, A-58)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/slow", summary="Risposta con sleep configurabile — simula timeout (A-07, A-32, A-58)")
async def mock_slow(
    country: str = Query(...),
    delay: float = Query(default=60.0, description="Secondi di attesa prima della risposta")
):
    state.call_counts["slow"] += 1
    await asyncio.sleep(delay)
    return {"country": country, "delayed": True, "delay_seconds": delay}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/HTML-ERROR — 500 HTML invece di JSON (A-59)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/html-error", summary="500 con body HTML invece di JSON (A-59)")
def mock_html_error(country: str = Query(...)):
    state.call_counts["html_error"] += 1
    html_body = """<!DOCTYPE html>
<html><head><title>500 Internal Server Error</title></head>
<body><h1>Internal Server Error</h1>
<p>The server encountered an error and could not complete your request.</p>
</body></html>"""
    return HTMLResponse(content=html_body, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/FLAKY — N volte errore poi OK (A-16 retry su 503)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/flaky", summary="503 per i primi N tentativi poi 200 (A-16)")
def mock_flaky(
    country: str = Query(...),
    fail_times: int = Query(default=1, description="Quante volte rispondere 503 prima del 200"),
    session_id: str = Query(default="default", description="ID sessione per isolare i test")
):
    key = f"{session_id}:{country}"
    with state._lock:
        hits = state.flaky_hits[key]
        if hits < fail_times:
            state.flaky_hits[key] += 1
            raise HTTPException(status_code=503, detail="Service Temporarily Unavailable")
        state.call_counts["flaky"] += 1

    key_lower = country.lower()
    if key_lower not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key_lower]


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/404 — 404 per paese inesistente (A-48 continue_on_error)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/strict", summary="404 per paesi non presenti nel DB (A-48 continue_on_error)")
def mock_strict(country: str = Query(...)):
    state.call_counts["strict"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/RATE-LIMIT — 429 (A-15)
# ─────────────────────────────────────────────────────────────────────────────

_rate_limit_state = {"calls": 0, "window_start": time.time(), "limit": 1}

@app.get("/mock/rate-limited", summary="429 se superato rate limit (A-15)")
def mock_rate_limited(country: str = Query(...)):
    now = time.time()
    with state._lock:
        if now - _rate_limit_state["window_start"] > 1.0:
            _rate_limit_state["window_start"] = now
            _rate_limit_state["calls"] = 0
        _rate_limit_state["calls"] += 1
        if _rate_limit_state["calls"] > _rate_limit_state["limit"]:
            return Response(
                content='{"error":"rate limit exceeded"}',
                status_code=429,
                headers={"Retry-After": "1", "Content-Type": "application/json"}
            )
    state.call_counts["rate_limited"] += 1
    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/LARGE-PAYLOAD — Risposta >1MB (A-66)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/large-payload", summary="Risposta >1MB per test memory handling (A-66)")
def mock_large_payload(country: str = Query(...), size_kb: int = Query(default=1100)):
    state.call_counts["large_payload"] += 1
    key = country.lower()
    d = COUNTRIES_DB.get(key, {"name": country, "capital": "Unknown", "population": 0})
    padding = "x" * (size_kb * 1024)
    return {
        "name": d["name"],
        "capital": d["capital"],
        "population": d["population"],
        "large_field": padding
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/COUNTER — Conta chiamate simultanee (A-49 cache dedup, A-67 parallel)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/counter", summary="Conta chiamate totali e simultanee (A-49, A-67)")
async def mock_counter(
    country: str = Query(...),
    delay: float = Query(default=0.1, description="Delay artificiale per amplificare concorrenza")
):
    with state._lock:
        state.call_counts["counter"] += 1
        state.active_calls += 1
        if state.active_calls > state.peak_concurrent:
            state.peak_concurrent = state.active_calls

    await asyncio.sleep(delay)

    with state._lock:
        state.active_calls -= 1

    key = country.lower()
    if key not in COUNTRIES_DB:
        raise HTTPException(status_code=404, detail=f"Country '{country}' not found")
    return COUNTRIES_DB[key]


@app.get("/mock/counter/stats", summary="Statistiche contatore chiamate")
def mock_counter_stats():
    return {
        "total_calls": state.call_counts["counter"],
        "peak_concurrent": state.peak_concurrent,
        "active_now": state.active_calls,
        "all_counts": dict(state.call_counts),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/BATCH — Endpoint batch (A-53)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/mock/batch", summary="Endpoint batch: riceve {items:[...]} risponde array piatto (A-53)")
async def mock_batch(request: Request):
    state.call_counts["batch"] += 1
    body = await request.json()
    # Supporta sia {items: ["Italy", ...]} sia {items: [{"country": "Italy"}, ...]}
    items = body.get("items", body.get("countries", []))
    results = []
    for item in items:
        # item può essere stringa o dict con param_name
        if isinstance(item, str):
            key = item.lower()
        elif isinstance(item, dict):
            key = next(iter(item.values()), "").lower()
        else:
            key = ""
        if key in COUNTRIES_DB:
            results.append(COUNTRIES_DB[key])
        else:
            results.append({"name": key, "capital": None, "population": None, "area": None})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/STATIC-PARAM — Static parameters override (A-65)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/static-param", summary="Endpoint che riceve sia param dinamico che statico (A-65)")
def mock_static_param(
    country: str = Query(...),
    source: str = Query(default="dynamic", description="Sorgente del parametro: static o dynamic")
):
    state.call_counts["static_param"] += 1
    return {
        "country": country,
        "source_received": source,
        "note": "Se source=static il parametro statico ha vinto su quello dinamico"
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/PAGINATED — Paginazione (A-24, A-25, A-26, A-28)
# ─────────────────────────────────────────────────────────────────────────────

PAGINATED_DATA = [
    {"id": i, "name": f"Record {i}", "value": i * 100}
    for i in range(1, 31)  # 30 record totali → 3 pagine da 10
]

@app.get("/mock/paginated/offset", summary="Paginazione offset+limit (A-24)")
def mock_paginated_offset(
    offset: int = Query(default=0),
    limit: int = Query(default=10)
):
    state.call_counts["paginated_offset"] += 1
    slice_ = PAGINATED_DATA[offset: offset + limit]
    return {
        "data": slice_,
        "total": len(PAGINATED_DATA),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < len(PAGINATED_DATA)
    }


@app.get("/mock/paginated/cursor", summary="Paginazione cursor-based (A-25)")
def mock_paginated_cursor(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=10)
):
    state.call_counts["paginated_cursor"] += 1
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    slice_ = PAGINATED_DATA[start: start + limit]
    next_cursor = str(start + limit) if (start + limit) < len(PAGINATED_DATA) else None

    return {
        "data": slice_,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None
    }


@app.get("/mock/paginated/link-header", summary="Paginazione RFC 5988 via Link header (A-26)")
def mock_paginated_link_header(
    page: int = Query(default=1),
    per_page: int = Query(default=10)
):
    state.call_counts["paginated_link"] += 1
    start = (page - 1) * per_page
    slice_ = PAGINATED_DATA[start: start + per_page]
    total_pages = (len(PAGINATED_DATA) + per_page - 1) // per_page

    headers = {}
    links = []
    base = f"http://localhost:8999/mock/paginated/link-header?per_page={per_page}"
    if page < total_pages:
        links.append(f'<{base}&page={page + 1}>; rel="next"')
        links.append(f'<{base}&page={total_pages}>; rel="last"')
    if page > 1:
        links.append(f'<{base}&page={page - 1}>; rel="prev"')
        links.append(f'<{base}&page=1>; rel="first"')
    if links:
        headers["Link"] = ", ".join(links)

    return JSONResponse(content={"data": slice_, "page": page, "total_pages": total_pages}, headers=headers)


@app.get("/mock/paginated/empty-stop", summary="Paginazione che si ferma su array vuoto (A-28)")
def mock_paginated_empty_stop(
    page: int = Query(default=1),
    per_page: int = Query(default=10)
):
    state.call_counts["paginated_empty"] += 1
    start = (page - 1) * per_page
    slice_ = PAGINATED_DATA[start: start + per_page]
    # Quando non ci sono più dati restituisce array vuoto → il generator deve fermarsi
    return {"data": slice_}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/CHAIN — Catena enricher (A-50, A-68)
# ─────────────────────────────────────────────────────────────────────────────

WEATHER_BY_CITY = {
    "rome":      {"temperature": 22.1, "windspeed": 8.4,  "condition": "sunny"},
    "paris":     {"temperature": 14.3, "windspeed": 15.2, "condition": "cloudy"},
    "berlin":    {"temperature": 10.1, "windspeed": 20.1, "condition": "rainy"},
    "madrid":    {"temperature": 25.7, "windspeed": 6.3,  "condition": "sunny"},
    "lisbon":    {"temperature": 20.4, "windspeed": 11.0, "condition": "partly_cloudy"},
    "amsterdam": {"temperature": 12.8, "windspeed": 18.5, "condition": "windy"},
    "brussels":  {"temperature": 13.2, "windspeed": 14.7, "condition": "overcast"},
    "vienna":    {"temperature": 16.5, "windspeed": 9.8,  "condition": "clear"},
    "stockholm": {"temperature": 7.3,  "windspeed": 22.1, "condition": "cloudy"},
    "warsaw":    {"temperature": 9.8,  "windspeed": 16.3, "condition": "rainy"},
}

@app.get("/mock/weather", summary="Meteo per città — usato nella catena dopo REST Countries (A-50, A-68)")
def mock_weather(city: str = Query(...)):
    state.call_counts["weather"] += 1
    key = city.lower().strip()
    if key not in WEATHER_BY_CITY:
        # Risposta graceful per città non in DB
        return {"city": city, "temperature": None, "windspeed": None, "condition": "unknown"}
    w = WEATHER_BY_CITY[key]
    return {"city": city, **w}


# ─────────────────────────────────────────────────────────────────────────────
# MOCK/GENERATOR-NONE — Generator senza FileSource (A-51)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/mock/generator", summary="Endpoint generatore puro senza FileSource (A-51)")
def mock_generator(
    page: int = Query(default=1),
    limit: int = Query(default=5)
):
    state.call_counts["generator"] += 1
    start = (page - 1) * limit
    data = [
        {"id": i, "synthetic_name": f"SynthRecord_{i}", "score": round(i * 1.414, 2)}
        for i in range(start + 1, start + limit + 1)
    ]
    return {
        "page": page,
        "data": data,
        "has_more": page < 4  # max 4 pagine sintetiche
    }


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", summary="Health check")
def health():
    return {
        "status": "ok",
        "endpoints": [r.path for r in app.routes],
        "state": {
            "call_counts": dict(state.call_counts),
            "peak_concurrent": state.peak_concurrent,
            "tokens_issued": state.tokens_issued,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


# =============================================================================
# ARCGIS MOCK ENDPOINTS (C-11→C-15, C-24→C-26)
# =============================================================================

# Stato globale ArcGIS mock
class _ArcGISState:
    features: list = []
    token_calls: int = 0
    add_calls: int = 0
    update_calls: int = 0
    fail_after_batch: int = 0   # se > 0 fallisce dopo N batch

_agis = _ArcGISState()


@app.post("/arcgis/tokens/generateToken", summary="ArcGIS token mock")
async def arcgis_generate_token(request: Request):
    body = await request.form()
    username = body.get("username", "")
    password = body.get("password", "")
    _agis.token_calls += 1
    if username == "mock_user" and password == "mock_pass":
        return JSONResponse({
            "token": "mock-arcgis-token-123",
            "expires": 9999999999999,
            "ssl": False,
        })
    return JSONResponse({"error": {"code": 400, "message": "Invalid credentials"}}, status_code=400)


@app.get("/arcgis/rest/info", summary="ArcGIS rest info mock")
async def arcgis_rest_info():
    return {
        "currentVersion": 10.91,
        "authInfo": {
            "isTokenBasedSecurity": True,
            "tokenServicesUrl": "http://localhost:8999/arcgis/tokens/generateToken",
        }
    }


@app.get("/arcgis/rest/services/mock/FeatureServer", summary="ArcGIS FeatureServer info")
async def arcgis_feature_server_info():
    return {
        "currentVersion": 10.91,
        "serviceDescription": "Mock FeatureServer",
        "layers": [{"id": 0, "name": "MockLayer", "type": "Feature Layer"}],
    }


@app.get("/arcgis/rest/services/mock/FeatureServer/0", summary="ArcGIS Layer 0 info")
async def arcgis_layer_info():
    return {
        "currentVersion": 10.91,
        "id": 0,
        "name": "MockLayer",
        "type": "Feature Layer",
        "geometryType": "esriGeometryPoint",
        "spatialReference": {"wkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "nome",     "type": "esriFieldTypeString", "alias": "nome"},
            {"name": "valore",   "type": "esriFieldTypeDouble", "alias": "valore"},
        ],
        "capabilities": "Create,Delete,Query,Update,Editing",
        "maxRecordCount": 1000,
    }


@app.post("/arcgis/rest/services/mock/FeatureServer/0/addFeatures",
          summary="ArcGIS addFeatures mock")
async def arcgis_add_features(request: Request):
    # Leggi sempre il body raw — request.form() ha limiti di dimensione
    # che troncano payload grandi (es. 500 features JSON)
    features = []
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_json = json.loads(raw)
            features = body_json.get("features", body_json) if isinstance(body_json, dict) else body_json
        except Exception:
            features = []
    else:
        # application/x-www-form-urlencoded — parse manuale senza limiti
        try:
            import json as _json
            from urllib.parse import parse_qs, unquote_plus
            raw_str = raw.decode("utf-8")
            parsed = parse_qs(raw_str, keep_blank_values=True)
            features_json = parsed.get("features", [""])[0]
            if not features_json:
                for part in raw_str.split("&"):
                    if part.startswith("features="):
                        features_json = unquote_plus(part[9:])
                        break
            if features_json:
                features = _json.loads(features_json)
        except Exception as e:
            features = []

    _agis.add_calls += 1

    # Simula fallimento dopo N batch (per C-25 token scaduto)
    if _agis.fail_after_batch > 0 and _agis.add_calls > _agis.fail_after_batch:
        return JSONResponse({
            "error": {"code": 498, "message": "Token Expired"}
        }, status_code=498)

    # Simula errore su features senza geometry (C-24)
    null_geom = [f for f in features if not f.get("geometry")]
    if null_geom:
        return JSONResponse({
            "addResults": [
                {"objectId": -1, "success": False,
                 "error": {"code": 1000, "description": "Geometry is null"}}
                for _ in null_geom
            ]
        })

    # Successo
    start_oid = len(_agis.features) + 1
    added = []
    for i, feat in enumerate(features):
        oid = start_oid + i
        _agis.features.append({**feat, "objectId": oid})
        added.append({"objectId": oid, "success": True})

    return {"addResults": added}


@app.post("/arcgis/rest/services/mock/FeatureServer/0/updateFeatures",
          summary="ArcGIS updateFeatures mock")
async def arcgis_update_features(request: Request):
    raw = await request.body()
    try:
        from urllib.parse import parse_qs
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        features_json = parsed.get("features", ["[]"])[0]
        features = json.loads(features_json)
    except Exception:
        features = []

    _agis.update_calls += 1
    updated = []
    for feat in features:
        oid = feat.get("attributes", {}).get("OBJECTID", feat.get("objectId", -1))
        updated.append({"objectId": oid, "success": True})

    return {"updateResults": updated}


@app.get("/arcgis/rest/services/mock/FeatureServer/0/query",
         summary="ArcGIS query features mock")
async def arcgis_query_features(where: str = "1=1", outFields: str = "*",
                                 returnCountOnly: bool = False, f: str = "json"):
    if returnCountOnly:
        return {"count": len(_agis.features)}
    return {
        "features": _agis.features[:100],
        "exceededTransferLimit": len(_agis.features) > 100,
    }


@app.post("/arcgis/mock/reset", summary="Reset stato ArcGIS mock")
async def arcgis_reset(request: Request):
    _agis.features = []
    _agis.token_calls = 0
    _agis.add_calls = 0
    _agis.update_calls = 0
    _agis.fail_after_batch = 0
    return {"status": "reset"}


@app.post("/arcgis/mock/set-fail-after", summary="Imposta fallimento dopo N batch")
async def arcgis_set_fail_after(request: Request):
    body = await request.json()
    _agis.fail_after_batch = body.get("after", 0)
    return {"fail_after_batch": _agis.fail_after_batch}


@app.get("/arcgis/mock/stats", summary="Statistiche ArcGIS mock")
async def arcgis_stats():
    return {
        "features_count": len(_agis.features),
        "token_calls":    _agis.token_calls,
        "add_calls":      _agis.add_calls,
        "update_calls":   _agis.update_calls,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mock_api_server:app", host="0.0.0.0", port=8999, reload=True)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mock_api_server:app", host="0.0.0.0", port=8999, reload=True)
