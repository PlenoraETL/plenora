"""Cross-cutting assertions: helper riusabili per controlli che si
applicano a TUTTI i test che fanno una certa azione, non solo a
quelli "specifici per il concern".

Filosofia: ogni POST scrive audit log -> tutti i test che fanno POST
verificano che l'audit log sia scritto, NON un singolo AUDIT-001.

Uso tipico (chiamato dentro un test esistente)::

    from runner.cross_cutting import (
        assert_audit_logged,
        assert_idempotent_post,
        assert_no_pii_in_response,
        assert_request_id_propagated,
    )

    def test_prep_004_create_postgres_connection(http_client, auth_headers, group_id):
        r = http_client.post("/api/connections", json=payload, headers=auth_headers)
        assert r.status_code == 201
        # cross-cutting opt-in
        assert_audit_logged(
            http_client, auth_headers,
            action="connection.create", resource_id=r.json()["id"],
        )
        # request_id e PII sono gia' applicati dalla fixture autouse

NB: il modulo NON tocca conftest.py. Le fixture autouse vengono
attivate da pytest per il fatto che il file e' importato (di solito
via `from runner.cross_cutting import _global_response_audit` in
conftest, oppure - alternativa adottata qui - tramite import
esplicito nel test che vuole usarle).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest


# --- API prefix (allineato con conftest.py) ---------------------------------

API_PREFIX = "/api"


# --- Utilita interne --------------------------------------------------------

def _safe_response_text(response: httpx.Response) -> str:
    """Estrae il testo della response in modo difensivo.

    httpx.Response puo' non avere `.text` se streaming non consumato; in quel
    caso ritorniamo stringa vuota. Non solleviamo: questo helper viene
    chiamato anche dentro asserzioni soft.
    """
    try:
        return response.text or ""
    except Exception:
        return ""


def _safe_response_json(response: httpx.Response) -> Any:
    """Decodifica JSON se il content-type lo dichiara, altrimenti None."""
    ctype = (response.headers.get("content-type") or "").lower()
    if "application/json" not in ctype:
        return None
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _is_browser_facing(response: httpx.Response) -> bool:
    """Heuristica: la response e' destinata a un browser?

    Returns True se il content-type e' html o se non e' un'API JSON pura.
    Usato per decidere se applicare i controlli sui security header.
    """
    ctype = (response.headers.get("content-type") or "").lower()
    return "text/html" in ctype or "application/xhtml+xml" in ctype


# ----------------------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------------------

# Endpoint candidati per leggere audit_log. Se nessuno risponde con 2xx,
# l'helper skippa il check con motivo esplicito (no false-fail).
_AUDIT_QUERY_ENDPOINTS = (
    f"{API_PREFIX}/audit/logs",
    f"{API_PREFIX}/audit-logs",
    f"{API_PREFIX}/admin/audit-logs",
    f"{API_PREFIX}/admin/audit/logs",
)


def assert_audit_logged(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    *,
    action: str,
    resource_id: str,
    actor: str | None = None,
    within_seconds: int = 5,
) -> dict[str, Any]:
    """Verifica che ci sia un'entry recente in audit_log per (action, resource_id).

    Ritorna l'entry trovata. Solleva AssertionError se l'entry non c'e' ma
    l'endpoint e' raggiungibile. Usa pytest.skip se nessuno degli endpoint
    audit candidati e' esposto (404/405 su tutti).
    """
    last_status: int | None = None
    candidates_tried: list[str] = []

    for url in _AUDIT_QUERY_ENDPOINTS:
        try:
            r = http_client.get(
                url,
                headers=auth_headers,
                params={"action": action, "resource_id": resource_id, "limit": 50},
            )
        except httpx.HTTPError as exc:
            candidates_tried.append(f"{url}: {exc.__class__.__name__}")
            continue

        candidates_tried.append(f"{url}: {r.status_code}")
        last_status = r.status_code

        if r.status_code in (404, 405):
            continue
        if r.status_code == 401 or r.status_code == 403:
            pytest.skip(
                f"Endpoint audit {url!r} richiede privilegi superiori "
                f"(HTTP {r.status_code}); audit check non eseguibile"
            )
        if r.status_code >= 500:
            continue
        if r.status_code >= 400:
            # Endpoint c'e' ma ha rifiutato i params; consideriamo skip
            pytest.skip(
                f"Endpoint audit {url!r} ha rifiutato la query "
                f"(HTTP {r.status_code}): {_safe_response_text(r)[:200]}"
            )

        body = _safe_response_json(r)
        entries: list[dict[str, Any]] = []
        if isinstance(body, list):
            entries = [e for e in body if isinstance(e, dict)]
        elif isinstance(body, dict):
            for key in ("items", "results", "data", "logs", "entries"):
                v = body.get(key)
                if isinstance(v, list):
                    entries = [e for e in v if isinstance(e, dict)]
                    break

        threshold = time.time() - max(within_seconds, 1)
        for entry in entries:
            entry_action = str(
                entry.get("action") or entry.get("event") or ""
            )
            entry_resource = str(
                entry.get("resource_id")
                or entry.get("resource")
                or entry.get("target_id")
                or ""
            )
            if entry_action != action:
                continue
            if entry_resource != str(resource_id):
                continue
            if actor is not None:
                entry_actor = str(
                    entry.get("actor") or entry.get("user") or entry.get("actor_id") or ""
                )
                if entry_actor != actor:
                    continue
            ts_raw = entry.get("created_at") or entry.get("timestamp") or entry.get("ts")
            if isinstance(ts_raw, (int, float)) and ts_raw < threshold:
                continue
            return entry

        raise AssertionError(
            f"Audit entry non trovata per action={action!r} "
            f"resource_id={resource_id!r} entro {within_seconds}s "
            f"(endpoint {url}, {len(entries)} entries esaminate)"
        )

    pytest.skip(
        "Nessun endpoint audit_log esposto in plenora "
        f"(provati: {', '.join(candidates_tried) or 'n/a'}; "
        f"ultimo status: {last_status})"
    )


# ----------------------------------------------------------------------------
# Request ID propagation
# ----------------------------------------------------------------------------

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LEN = 64


def assert_request_id_propagated(
    response: httpx.Response, *, expected: str | None = None
) -> str:
    """Verifica che la response abbia header X-Request-ID.

    Se `expected` e' dato, deve combaciare (truncate a 64 char). Ritorna
    il request_id presente nella response.
    """
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid, (
        f"Response priva di header {REQUEST_ID_HEADER!r} "
        f"(status={response.status_code}, url={response.request.url if response.request else 'n/a'})"
    )
    assert len(rid) <= REQUEST_ID_MAX_LEN, (
        f"{REQUEST_ID_HEADER} {rid!r} supera i {REQUEST_ID_MAX_LEN} char "
        f"(len={len(rid)})"
    )
    if expected is not None:
        expected_truncated = expected[:REQUEST_ID_MAX_LEN]
        assert rid == expected_truncated, (
            f"{REQUEST_ID_HEADER} non echo: atteso {expected_truncated!r}, "
            f"ricevuto {rid!r}"
        )
    return rid


# ----------------------------------------------------------------------------
# PII / secret leak
# ----------------------------------------------------------------------------

# Pattern per rilevare PII / secret in chiaro nel body o nei log.
# Match volutamente lasco: cerca campi sensibili con valori non vuoti.
PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"password"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"api_key"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"client_secret"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"access_token"\s*:\s*"(?!REDACTED)[^"]+"', re.IGNORECASE),
    re.compile(r'"refresh_token"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"secret_key"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"private_key"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r'"aws_secret_access_key"\s*:\s*"[^"]+"', re.IGNORECASE),
    re.compile(r"Bearer\s+(?!<REDACTED>)[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
)

# Valori che indicano redaction/anonimizzazione e quindi non sono un leak.
_REDACTION_TOKENS = ("REDACTED", "***", "<redacted>", "MASKED", "<masked>")


def _looks_redacted(value: str) -> bool:
    upper = value.upper()
    return any(token.upper() in upper for token in _REDACTION_TOKENS)


def _scan_for_pii(text: str) -> list[str]:
    """Ritorna la lista delle (prime) occorrenze PII non redacted in `text`."""
    if not text:
        return []
    hits: list[str] = []
    for pattern in PII_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        snippet = match.group(0)
        if _looks_redacted(snippet):
            continue
        hits.append(snippet[:120])
    return hits


def assert_no_pii_in_response(response: httpx.Response) -> None:
    """Verifica che il body della response NON contenga PII/secret in chiaro.

    Skippa il check se la response e' streaming (no body materializzato) o
    se il content-type non e' testuale.
    """
    ctype = (response.headers.get("content-type") or "").lower()
    if not ctype:
        return
    if not any(t in ctype for t in ("json", "text", "xml", "yaml")):
        return
    text = _safe_response_text(response)
    hits = _scan_for_pii(text)
    assert not hits, (
        f"Response leak PII/secret in chiaro: {hits!r} "
        f"(url={response.request.url if response.request else 'n/a'})"
    )


def assert_no_pii_in_logs(logs_text: str) -> None:
    """Verifica che un blob di log non contenga PII."""
    hits = _scan_for_pii(logs_text)
    assert not hits, f"Log leak PII/secret in chiaro: {hits!r}"


# ----------------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------------

IDEMPOTENCY_HEADER = "Idempotency-Key"


def assert_idempotent_post(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    *,
    url: str,
    json: dict[str, Any],
    idempotency_key: str | None = None,
) -> tuple[httpx.Response, httpx.Response]:
    """Esegue 2 POST identici (con stessa Idempotency-Key se data) e
    verifica che il secondo ritorni lo stesso resource_id, NON ne crei
    uno nuovo. Ritorna (response_first, response_second).

    Se la prima POST fallisce, fallisce l'asserzione (problema upstream).
    Se entrambe ritornano 2xx ma resource_id differisce, fallisce.
    Se la seconda risponde 409 con shape `{existing_id}`, lo accettiamo
    come comportamento idempotente alternativo.
    """
    key = idempotency_key or f"e2e-idem-{uuid.uuid4().hex}"
    headers = {**auth_headers, IDEMPOTENCY_HEADER: key}

    first = http_client.post(url, json=json, headers=headers)
    assert first.status_code < 400, (
        f"Prima POST idempotente fallita (HTTP {first.status_code}): "
        f"{_safe_response_text(first)[:200]}"
    )

    body_first = _safe_response_json(first) or {}
    rid_first = _extract_resource_id(body_first)
    assert rid_first, (
        f"Prima response idempotente senza resource_id: {body_first!r}"
    )

    second = http_client.post(url, json=json, headers=headers)
    if second.status_code == 409:
        body_second = _safe_response_json(second) or {}
        existing = _extract_resource_id(body_second)
        if existing:
            assert existing == rid_first, (
                f"409 idempotente con id diverso: prima={rid_first!r}, "
                f"conflict={existing!r}"
            )
            return first, second
        # Non possiamo verificare: alziamo errore esplicito.
        raise AssertionError(
            f"Seconda POST 409 ma senza resource_id ricavabile: {body_second!r}"
        )

    assert second.status_code < 400, (
        f"Seconda POST idempotente fallita (HTTP {second.status_code}): "
        f"{_safe_response_text(second)[:200]}"
    )
    body_second = _safe_response_json(second) or {}
    rid_second = _extract_resource_id(body_second)
    assert rid_second == rid_first, (
        f"Idempotency violata: stessa Idempotency-Key={key!r} ha prodotto "
        f"id diversi (prima={rid_first!r}, seconda={rid_second!r})"
    )
    return first, second


def _extract_resource_id(body: Any) -> str | None:
    """Estrae il resource_id da una response plenora con shape eterogenea."""
    if not isinstance(body, dict):
        return None
    for key in (
        "id",
        "connection_id",
        "datasource_id",
        "api_id",
        "api_connection_id",
        "project_id",
        "canvas_id",
        "group_id",
        "resource_id",
        "existing_id",
    ):
        v = body.get(key)
        if v:
            return str(v)
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        return _extract_resource_id(metadata)
    return None


# ----------------------------------------------------------------------------
# Security headers
# ----------------------------------------------------------------------------

# Header obbligatori su response browser-facing (HTML).
# Per response JSON-only API i security header sono nice-to-have:
# `assert_security_headers` puo' essere chiamato esplicitamente nei test
# che vogliono il controllo strict.
REQUIRED_SECURITY_HEADERS: dict[str, tuple[str, ...]] = {
    "X-Content-Type-Options": ("nosniff",),
    "X-Frame-Options": ("DENY", "SAMEORIGIN"),
}


def assert_security_headers(response: httpx.Response) -> None:
    """Verifica che la response abbia gli header di security minimi.

    Per ogni header richiesto, valida che sia presente e che il suo valore
    rientri tra quelli accettati (case-insensitive).
    """
    for header, allowed in REQUIRED_SECURITY_HEADERS.items():
        value = response.headers.get(header)
        assert value, (
            f"Header sicurezza {header!r} mancante "
            f"(status={response.status_code})"
        )
        upper = value.strip().upper()
        allowed_upper = tuple(a.upper() for a in allowed)
        assert upper in allowed_upper, (
            f"Header {header}={value!r} non in {allowed!r}"
        )


# ----------------------------------------------------------------------------
# Rate limit headers
# ----------------------------------------------------------------------------

_RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
)


def assert_rate_limit_headers_present(response: httpx.Response) -> dict[str, int]:
    """Verifica X-RateLimit-Limit/Remaining/Reset; ritorna dict valori (int)."""
    out: dict[str, int] = {}
    for header in _RATE_LIMIT_HEADERS:
        value = response.headers.get(header)
        assert value is not None, (
            f"Header rate-limit {header!r} mancante "
            f"(status={response.status_code})"
        )
        try:
            out[header] = int(value)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                f"Header {header}={value!r} non e' un intero"
            ) from exc
    return out


# ----------------------------------------------------------------------------
# Permission boundary
# ----------------------------------------------------------------------------

def assert_403_for_role(
    http_client: httpx.Client,
    *,
    method: str,
    url: str,
    role_token: str,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """Esegue richiesta come `role_token` (es. viewer), aspetta 403.

    Accetta anche 401 come outcome valido (ruolo non autenticato del tutto).
    Ritorna la response per ispezione opzionale.
    """
    method_upper = method.upper()
    headers = {"Authorization": f"Bearer {role_token}"}
    response = http_client.request(method_upper, url, json=json, headers=headers)
    assert response.status_code in (401, 403), (
        f"Atteso 401/403 per ruolo {role_token[:8]}... su {method_upper} {url}, "
        f"ricevuto HTTP {response.status_code}: "
        f"{_safe_response_text(response)[:200]}"
    )
    return response


# ----------------------------------------------------------------------------
# Cleanup verification
# ----------------------------------------------------------------------------

def assert_resource_deleted(
    http_client: httpx.Client,
    auth_headers: dict[str, str],
    *,
    resource_url: str,
) -> None:
    """GET sulla risorsa deve ritornare 404 (eventualmente 410 Gone)."""
    response = http_client.get(resource_url, headers=auth_headers)
    assert response.status_code in (404, 410), (
        f"Risorsa {resource_url!r} ancora visibile "
        f"(atteso 404/410, ricevuto {response.status_code})"
    )


# ----------------------------------------------------------------------------
# No flaky timing
# ----------------------------------------------------------------------------

def poll_until(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float = 30.0,
    interval_seconds: float = 0.5,
    error_msg: str = "Condition not met within timeout",
) -> Any:
    """Polling con timeout esplicito invece di sleep arbitrari.

    Chiama `fn()` ripetutamente: ritorna il primo valore truthy ottenuto.
    Solleva AssertionError dopo `timeout_seconds`.
    """
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    last_value: Any = None
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = fn()
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - vogliamo davvero re-tentare
            last_exc = exc
            value = None
        if value:
            return value
        last_value = value
        time.sleep(max(interval_seconds, 0.01))
    raise AssertionError(
        f"{error_msg} (timeout={timeout_seconds}s, "
        f"last_value={last_value!r}, last_exc={last_exc!r})"
    )


# ----------------------------------------------------------------------------
# Autouse fixture: cross-cutting su ogni response
# ----------------------------------------------------------------------------

# Marker pytest da usare nei test che NON vogliono i check globali.
NO_GLOBAL_ASSERTIONS_MARKER = "no_global_assertions"


@pytest.fixture(autouse=True)
def _global_response_audit(
    request: pytest.FixtureRequest,
) -> Any:
    """Hook che applica asserzioni cross-cutting a ogni response HTTP fatta
    durante un test che richiede l'app plenora.

    Comportamento:
    - skippa per test marcati `@pytest.mark.no_global_assertions`;
    - skippa per test smoke che non usano la fixture http_client (no app);
    - per i test che USANO `http_client`, monkey-patcha il metodo `.send()`
      del client httpx in modo che ogni response 2xx passi attraverso
      `assert_request_id_propagated` e `assert_no_pii_in_response`.

    Il monkey-patch viene rimosso al teardown del test (scope function).
    Niente side effect persistente.
    """
    # Opt-out via marker
    if request.node.get_closest_marker(NO_GLOBAL_ASSERTIONS_MARKER) is not None:
        yield
        return

    # Per non rompere i test smoke (no app, no http_client) controlliamo
    # se la fixture http_client e' richiesta (direttamente o transitivamente).
    fixturenames = set(getattr(request.node, "fixturenames", ()) or ())
    if "http_client" not in fixturenames:
        yield
        return

    # Recupera il client gia' istanziato (session-scope nel conftest); se la
    # fixture http_client non e' ancora stata risolta saltiamo: significa che
    # il test la usa solo opzionalmente.
    try:
        client: httpx.Client = request.getfixturevalue("http_client")
    except pytest.FixtureLookupError:
        yield
        return

    if not isinstance(client, httpx.Client):
        yield
        return

    original_send = client.send

    def _wrapped_send(httpx_request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        response = original_send(httpx_request, *args, **kwargs)
        # Applichiamo asserzioni soft: una violazione interrompe il test.
        try:
            if 200 <= response.status_code < 300:
                # Request ID echo obbligatorio sui 2xx
                assert_request_id_propagated(response)
            # PII check su qualunque response con body testuale
            assert_no_pii_in_response(response)
        except AssertionError:
            # Lasciamo propagare: il test che ha generato la response deve
            # vedere il fallimento del check globale.
            raise
        return response

    client.send = _wrapped_send  # type: ignore[method-assign]
    try:
        yield
    finally:
        client.send = original_send  # type: ignore[method-assign]


def pytest_configure(config: pytest.Config) -> None:
    """Registra il marker `no_global_assertions` se questo modulo viene
    caricato come plugin pytest (alternativa a importarlo dal conftest).

    NB: il modulo non e' un plugin attivo di default - importarlo nel
    conftest.py per attivare la fixture autouse e' compito del runner.
    Questa funzione viene comunque invocata se pytest la trova.
    """
    config.addinivalue_line(
        "markers",
        f"{NO_GLOBAL_ASSERTIONS_MARKER}: disattiva le asserzioni "
        "cross-cutting (request_id, PII) per il test marcato",
    )
