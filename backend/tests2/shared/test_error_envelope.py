"""Contract test BE-GO-READY-ERROR-CONTRACT-002 — global error
envelope dual-shape.

Lock i 10 requirement del pilot:

1. envelope ha top-level `error`, `message`, `details`;
2. envelope include `detail` mirror backward-compatible;
3. status code preservato;
4. `HTTPException` con `detail: str` → `error: http_<status>`;
5. `HTTPException` con `detail: dict` (error+message) → normalizza
   a envelope;
6. eccezione generica → `error: internal_error`;
7. `CanvasCorruptedError` → `error: canvas_corrupted`;
8. CORS header preservati dove l'handler legacy li preservava;
9. request_id resta nell'header `X-Request-ID`, non duplicato nel
   body;
10. test 409-shape esistenti continuano a passare (verificato in
    suite separata: vedi `tests2/connections/test_delete_connection_409_shape.py`,
    `tests2/datasources/test_delete_datasource_409_shape.py`,
    `tests2/arricchisci/unit/test_delete_api_connection_409_shape.py`).

Pure unit tests sull'helper + integration tests su
`TestClient(app)` con endpoint sintetici che alzano eccezioni
note. NESSUN router reale toccato.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.shared import error_codes
from app.shared.error_envelope import (
    build_error_envelope_body,
    build_error_response,
    normalize_http_exception_detail,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Helper unit tests — envelope shape, dual-shape, status code
# ---------------------------------------------------------------------------


def test_envelope_body_has_top_level_error_message_keys():
    body = build_error_envelope_body(
        error="some_code",
        message="Messaggio utente",
        details=None,
    )
    assert body["error"] == "some_code"
    assert body["message"] == "Messaggio utente"


def test_envelope_body_includes_empty_details_when_none():
    body = build_error_envelope_body(
        error="x",
        message="y",
        details=None,
    )
    assert body["details"] == {}


def test_envelope_body_includes_details_when_provided():
    body = build_error_envelope_body(
        error="x",
        message="y",
        details={"a": 1, "b": [1, 2]},
    )
    assert body["details"] == {"a": 1, "b": [1, 2]}


def test_envelope_body_dual_shape_detail_mirror_present():
    body = build_error_envelope_body(
        error="some_code",
        message="msg",
        details={"k": "v"},
    )
    assert "detail" in body
    mirror = body["detail"]
    assert mirror["error"] == "some_code"
    assert mirror["message"] == "msg"
    assert mirror["details"] == {"k": "v"}


def test_envelope_body_mirror_includes_empty_details_when_none():
    body = build_error_envelope_body(error="x", message="y", details=None)
    assert body["detail"] == {"error": "x", "message": "y", "details": {}}


def test_build_error_response_preserves_status_code():
    resp = build_error_response(
        status_code=409,
        error="conflict",
        message="msg",
        details=None,
    )
    assert resp.status_code == 409


def test_build_error_response_attaches_headers():
    resp = build_error_response(
        status_code=500,
        error="internal_error",
        message="boom",
        details=None,
        headers={"X-Custom": "yes"},
    )
    assert resp.headers["X-Custom"] == "yes"


# ---------------------------------------------------------------------------
# 2. Helper unit tests — normalize_http_exception_detail
# ---------------------------------------------------------------------------


def test_normalize_string_detail_yields_http_status_code():
    norm = normalize_http_exception_detail(404, "Not Found")
    assert norm["error"] == "http_404"
    assert norm["message"] == "Not Found"
    assert norm["details"] is None


def test_normalize_empty_string_detail_falls_back():
    norm = normalize_http_exception_detail(500, "")
    assert norm["error"] == "http_500"
    assert norm["message"] == "HTTP 500"
    assert norm["details"] is None


def test_normalize_none_detail_falls_back():
    norm = normalize_http_exception_detail(503, None)
    assert norm["error"] == "http_503"
    assert norm["message"] == "HTTP 503"
    assert norm["details"] is None


def test_normalize_structured_dict_detail_extracts_error_and_message():
    detail = {
        "error": "connection_in_use",
        "message": "Connessione referenziata.",
        "dependencies": [{"id": 1}, {"id": 2}],
    }
    norm = normalize_http_exception_detail(409, detail)
    assert norm["error"] == "connection_in_use"
    assert norm["message"] == "Connessione referenziata."
    assert norm["details"] == {"dependencies": [{"id": 1}, {"id": 2}]}


def test_normalize_dict_with_explicit_details_preserves_them():
    detail = {
        "error": "x",
        "message": "y",
        "details": {"explicit": True},
    }
    norm = normalize_http_exception_detail(409, detail)
    assert norm["details"] == {"explicit": True}


def test_normalize_dict_without_error_or_message_falls_back():
    detail = {"foo": "bar"}
    norm = normalize_http_exception_detail(400, detail)
    assert norm["error"] == "http_400"
    assert norm["details"] == {"foo": "bar"}


def test_normalize_dict_without_extras_yields_none_details():
    detail = {"error": "x", "message": "y"}
    norm = normalize_http_exception_detail(409, detail)
    assert norm["details"] is None


# ---------------------------------------------------------------------------
# 3. Integration tests — handler wired su un'app minima
# ---------------------------------------------------------------------------


@pytest.fixture
def envelope_app() -> FastAPI:
    """Mini-app FastAPI con i 3 handler del pilot wired:
    - HTTPException
    - generic Exception
    - CanvasCorruptedError

    Replica della logica di `app/main.py` ma senza dipendere
    dall'app produttiva (no DB, no startup heavy).
    """
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.shared.exceptions import CanvasCorruptedError

    app = FastAPI()

    @app.exception_handler(Exception)
    async def _generic(request, exc):
        return build_error_response(
            status_code=500,
            error=error_codes.INTERNAL_ERROR,
            message="Errore interno del server",
            details=None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc):
        norm = normalize_http_exception_detail(exc.status_code, exc.detail)
        return build_error_response(
            status_code=exc.status_code,
            error=norm["error"],
            message=norm["message"],
            details=norm["details"],
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(CanvasCorruptedError)
    async def _canvas(request, exc):
        return build_error_response(
            status_code=500,
            error=error_codes.CANVAS_CORRUPTED,
            message=(
                f"Canvas del progetto '{exc.project_id}' corrotto "
                f"({exc.reason}). Contattare l'amministratore per il "
                f"ripristino da backup."
            ),
            details={"project_id": exc.project_id, "reason": exc.reason},
        )

    @app.get("/raise-string-detail")
    def raise_string_detail():
        raise HTTPException(status_code=404, detail="Risorsa non trovata")

    @app.get("/raise-dict-detail")
    def raise_dict_detail():
        raise HTTPException(
            status_code=409,
            detail={
                "error": "connection_in_use",
                "message": "Connessione referenziata.",
                "dependencies": [{"id": 1}, {"id": 2}],
            },
        )

    @app.get("/raise-generic")
    def raise_generic():
        raise RuntimeError("boom inattesa")

    @app.get("/raise-canvas")
    def raise_canvas():
        raise CanvasCorruptedError(
            project_id="proj_X",
            reason="json_decode_error",
        )

    @app.get("/raise-http-headers")
    def raise_http_headers():
        raise HTTPException(
            status_code=401,
            detail="auth required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return app


def _client(envelope_app):
    return TestClient(envelope_app, raise_server_exceptions=False)


def test_string_detail_is_wrapped_into_envelope(envelope_app):
    r = _client(envelope_app).get("/raise-string-detail")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "http_404"
    assert body["message"] == "Risorsa non trovata"
    assert body["details"] == {}
    # backward compat
    assert body["detail"]["error"] == "http_404"
    assert body["detail"]["message"] == "Risorsa non trovata"
    assert body["detail"]["details"] == {}


def test_dict_detail_is_normalized_into_envelope(envelope_app):
    r = _client(envelope_app).get("/raise-dict-detail")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "connection_in_use"
    assert body["message"] == "Connessione referenziata."
    assert body["details"] == {"dependencies": [{"id": 1}, {"id": 2}]}
    # backward compat — i test 409-shape pre-existing leggono
    # `body["detail"]["error"]` ecc.
    assert body["detail"]["error"] == "connection_in_use"
    assert body["detail"]["message"] == "Connessione referenziata."
    assert body["detail"]["details"] == {
        "dependencies": [{"id": 1}, {"id": 2}],
    }


def test_dict_detail_legacy_409_shape_still_accessible(envelope_app):
    """Replica del pattern dei test contract pre-existing
    (`tests2/connections/test_delete_connection_409_shape.py`):
    `detail = response.json()["detail"]`, `assert
    detail["error"] == "..."`. Deve restare supportato dal
    dual-shape envelope."""
    r = _client(envelope_app).get("/raise-dict-detail")
    detail = r.json()["detail"]
    assert detail["error"] == "connection_in_use"
    assert isinstance(detail["message"], str) and detail["message"]
    # `details` mirror nel legacy detail
    assert detail["details"]["dependencies"][0]["id"] == 1


def test_generic_exception_maps_to_internal_error(envelope_app):
    r = _client(envelope_app).get("/raise-generic")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error"
    assert body["message"] == "Errore interno del server"
    assert body["detail"]["error"] == "internal_error"


def test_canvas_corrupted_maps_to_envelope_with_details(envelope_app):
    r = _client(envelope_app).get("/raise-canvas")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "canvas_corrupted"
    assert "Canvas del progetto 'proj_X' corrotto" in body["message"]
    assert body["details"]["project_id"] == "proj_X"
    assert body["details"]["reason"] == "json_decode_error"
    # backward compat
    assert body["detail"]["error"] == "canvas_corrupted"
    assert body["detail"]["details"]["project_id"] == "proj_X"


def test_http_exception_headers_propagated(envelope_app):
    """Headers attached to `HTTPException` (es. WWW-Authenticate)
    devono essere preservati nella response, allineato al
    comportamento Starlette default."""
    r = _client(envelope_app).get("/raise-http-headers")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"
    body = r.json()
    assert body["error"] == "http_401"


def test_response_body_does_not_contain_request_id(envelope_app):
    """Lock requirement #9: il request_id resta nell'header
    `X-Request-ID` (gestito dal middleware), NON deve essere
    duplicato nel body envelope.

    Il modulo `error_envelope` non ha alcuna conoscenza del
    request_id e non puo' inserirlo nel body. Verifica che
    nessuna chiave `request_id` compaia in `error/message/details`
    o nel mirror `detail`."""
    r = _client(envelope_app).get("/raise-canvas")
    body = r.json()
    flat_str = str(body)
    assert "request_id" not in body
    assert "request_id" not in body["details"]
    assert "request_id" not in body["detail"]
    # check nullo anche se per qualche ragione la chiave fosse
    # nidificata altrove
    assert "X-Request-ID" not in flat_str


def test_envelope_response_is_json_content_type(envelope_app):
    r = _client(envelope_app).get("/raise-string-detail")
    assert r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# 4. Source-bound checks — i 3 handler sono registrati in main.py
# ---------------------------------------------------------------------------


def test_main_py_registers_envelope_handlers():
    """Lock source-bound: `app/main.py` deve registrare i 3 handler
    target del pilot. Drift detection se qualcuno rimuove o
    rinomina i decoratori."""
    from pathlib import Path

    main_py = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    assert "@app.exception_handler(Exception)" in src
    assert "@app.exception_handler(StarletteHTTPException)" in src
    assert "@app.exception_handler(CanvasCorruptedError)" in src
    # Helper imports
    assert "from app.shared.error_envelope import" in src
    assert "from app.shared import error_codes" in src
