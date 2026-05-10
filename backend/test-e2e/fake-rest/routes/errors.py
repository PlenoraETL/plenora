"""Error endpoints.

Each endpoint produces a specific failure mode that the REST connector must
handle: standard HTTP status codes, malformed JSON, wrong content-type,
empty body, and a redirect loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

router = APIRouter()


@router.get("/401")
def err_401() -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "unauthorized"})


@router.get("/403")
def err_403() -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "forbidden"})


@router.get("/404")
def err_404() -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found"})


@router.get("/422")
def err_422() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": ["query", "city_id"],
                    "msg": "value is not a valid integer",
                    "type": "type_error.integer",
                }
            ]
        },
    )


@router.get("/429")
def err_429() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": "5"},
    )


@router.get("/500")
def err_500() -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})


@router.get("/503")
def err_503() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "service_unavailable"},
        headers={"Retry-After": "10"},
    )


@router.get("/malformed_json")
def malformed_json() -> Response:
    # Truncated JSON, but advertise as application/json.
    return Response(
        content=b'{"data": [1, 2,',
        media_type="application/json",
        status_code=200,
    )


@router.get("/wrong_content_type")
def wrong_content_type() -> PlainTextResponse:
    # Valid JSON body, but Content-Type lies.
    return PlainTextResponse(
        content='{"ok": true, "note": "served as text/plain"}',
        status_code=200,
    )


@router.get("/empty_body")
def empty_body() -> Response:
    return Response(content=b"", status_code=200)


@router.get("/redirect_loop")
def redirect_loop(request: Request) -> Response:
    """Redirect to self up to 5 times, then return 200.

    The hop counter rides in a query parameter so well-behaved clients with a
    redirect cap can be exercised without crashing the server.
    """
    hop = int(request.query_params.get("hop", "0"))
    if hop >= 5:
        return JSONResponse(status_code=200, content={"hops": hop, "done": True})
    target = f"{request.url.path}?hop={hop + 1}"
    return RedirectResponse(url=target, status_code=302)
