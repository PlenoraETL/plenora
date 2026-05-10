"""SharePoint REST API + Azure AD (AADSTS) emulation.

Implements the minimal slice of SharePoint Online + Azure AD that plenora's
SharePoint connector exercises (see ``app/core/datasources/sharepoint.py``):

* AAD-style token endpoint at
  ``/sharepoint/login.microsoftonline.com/{tenant}/oauth2/v2.0/token``
  (client_credentials only).
* Realistic AADSTS error paths under
  ``/sharepoint/login.microsoftonline.com/error/{aadsts_code}`` for the codes
  the connector classifies (50034, 50053, 900023, 50126, 700016).
* A handful of SharePoint REST descriptor endpoints used by the
  ``office365-rest-python-client`` library: ``/_api/web``,
  ``getbytitle('...')/items``, ``getfolderbyserverrelativeurl('...')/files``
  and ``getfilebyserverrelativeurl('...')/$value`` for binary downloads.
* Resilience helpers: a 429 ``/throttle`` endpoint with ``Retry-After`` and a
  ``/transient_5xx`` endpoint that fails twice (503, 502) before succeeding.

All "normal" SharePoint endpoints require
``Authorization: Bearer SHAREPOINT_FAKE_TOKEN`` (or ``?access_token=...``);
missing / wrong tokens return ``401`` with ``{"error": "invalid_token"}``.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Form, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from . import (
    SHAREPOINT_FAKE_TOKEN,
    SHAREPOINT_OAUTH_CLIENT_ID,
    SHAREPOINT_OAUTH_CLIENT_SECRET,
    load_fixture,
)

router = APIRouter()


# ── Auth helpers ─────────────────────────────────────────────────────────


def _token_ok(authorization: Optional[str], access_token: Optional[str]) -> bool:
    if access_token == SHAREPOINT_FAKE_TOKEN:
        return True
    if authorization == f"Bearer {SHAREPOINT_FAKE_TOKEN}":
        return True
    return False


def _invalid_token() -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "invalid_token"})


# ── AADSTS error catalogue ───────────────────────────────────────────────
# Codes the SharePoint connector explicitly classifies in production. The
# messages are paraphrased from Microsoft's public AADSTS reference so tests
# can grep the canonical wording.
_AADSTS_MESSAGES: dict[int, tuple[str, str]] = {
    50034: ("invalid_grant", "The user account does not exist in the directory."),
    50053: ("invalid_grant", "Account is locked because the user tried to sign in too many times with an incorrect user ID or password."),
    900023: ("invalid_client", "Specified tenant identifier is neither a valid DNS name, nor a valid external domain."),
    50126: ("invalid_grant", "Error validating credentials due to invalid username or password."),
    700016: ("unauthorized_client", "Application with identifier was not found in the directory."),
}


def _aadsts_payload(code: int) -> dict:
    err_kind, message = _AADSTS_MESSAGES.get(
        code, ("invalid_grant", "Generic Azure AD authentication failure.")
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    trace_id = uuid.uuid4().hex
    correlation_id = uuid.uuid4().hex
    return {
        "error": err_kind,
        "error_description": f"AADSTS{code}: {message} Trace ID: {trace_id} Correlation ID: {correlation_id} Timestamp: {now}",
        "error_codes": [code],
        "timestamp": now,
        "trace_id": trace_id,
        "correlation_id": correlation_id,
    }


# ── AAD-style OAuth2 token endpoint ──────────────────────────────────────


@router.post("/sharepoint/login.microsoftonline.com/{tenant}/oauth2/v2.0/token")
def aad_token(
    tenant: str,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: Optional[str] = Form(default=None),
) -> JSONResponse:
    """Azure AD v2.0 token endpoint (client_credentials grant only).

    Mirrors the request shape that ``msal`` / ``office365-rest-python-client``
    sends when authenticating against ``login.microsoftonline.com``.
    """
    del tenant, scope  # tenant is informative only; scope is accepted but unused
    if grant_type != "client_credentials":
        return JSONResponse(
            status_code=400,
            content={
                "error": "unsupported_grant_type",
                "error_description": "Only client_credentials is supported by the fake AAD endpoint.",
            },
        )
    if (
        client_id != SHAREPOINT_OAUTH_CLIENT_ID
        or client_secret != SHAREPOINT_OAUTH_CLIENT_SECRET
    ):
        return JSONResponse(
            status_code=401,
            content=_aadsts_payload(900023),
        )
    return JSONResponse(
        content={
            "access_token": SHAREPOINT_FAKE_TOKEN,
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )


@router.post("/sharepoint/login.microsoftonline.com/error/{aadsts_code}")
async def aad_error(aadsts_code: int, request: Request) -> JSONResponse:
    """Force-return an AADSTS error response.

    Tests can hit this endpoint directly to drive the SharePoint connector's
    error-classification path through every supported AADSTS code without
    fiddling with bad credentials. The request body is read and discarded so
    the endpoint accepts the same form/JSON shape as the real token endpoint.
    """
    try:
        await request.body()
    except Exception:
        pass
    return JSONResponse(status_code=401, content=_aadsts_payload(aadsts_code))


# ── SharePoint REST descriptor endpoints ────────────────────────────────


@router.get("/sharepoint/_api/web")
def sp_web(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> JSONResponse:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    return JSONResponse(
        content={
            "d": {
                "Title": "Plenora Test Site",
                "Url": "http://fake/sharepoint",
            }
        }
    )


@router.get("/sharepoint/_api/web/lists/getbytitle('{title}')/items")
def sp_list_items(
    title: str,
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> JSONResponse:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    items = [
        {
            "Id": 1,
            "FileLeafRef": "customers.csv",
            "Length": 4096,
            "TimeLastModified": "2026-01-01T00:00:00Z",
        },
        {
            "Id": 2,
            "FileLeafRef": "products.json",
            "Length": 2048,
            "TimeLastModified": "2026-01-02T00:00:00Z",
        },
        {
            "Id": 3,
            "FileLeafRef": "cities.geojson",
            "Length": 8192,
            "TimeLastModified": "2026-01-03T00:00:00Z",
        },
    ]
    return JSONResponse(
        content={
            "d": {
                "results": items,
                "__list": title,
            }
        }
    )


@router.get("/sharepoint/_api/web/getfolderbyserverrelativeurl('{path:path}')/files")
def sp_folder_files(
    path: str,
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> JSONResponse:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    files = [
        {
            "Name": "customers.csv",
            "ServerRelativeUrl": f"{path}/customers.csv",
            "Length": 4096,
            "TimeLastModified": "2026-01-01T00:00:00Z",
        },
        {
            "Name": "products.json",
            "ServerRelativeUrl": f"{path}/products.json",
            "Length": 2048,
            "TimeLastModified": "2026-01-02T00:00:00Z",
        },
    ]
    return JSONResponse(content={"d": {"results": files}})


def _customers_csv_bytes() -> bytes:
    rows = load_fixture("customers.json")
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


@router.get("/sharepoint/_api/web/getfilebyserverrelativeurl('{path:path}')/$value")
def sp_file_value(
    path: str,
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> Response:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    # Pick the payload by file extension. Default to the CSV fixture so any
    # arbitrary path still streams a valid binary download.
    lower = path.lower()
    if lower.endswith(".json"):
        body = json.dumps(load_fixture("customers.json"), ensure_ascii=False).encode("utf-8")
        media_type = "application/json"
        filename = path.rsplit("/", 1)[-1] or "file.json"
    elif lower.endswith(".geojson"):
        body = json.dumps(load_fixture("cities.geojson"), ensure_ascii=False).encode("utf-8")
        media_type = "application/geo+json"
        filename = path.rsplit("/", 1)[-1] or "file.geojson"
    else:
        body = _customers_csv_bytes()
        media_type = "text/csv; charset=utf-8"
        filename = path.rsplit("/", 1)[-1] or "customers.csv"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Resilience helpers (throttle + transient 5xx) ────────────────────────


@router.get("/sharepoint/throttle")
def sp_throttle(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
) -> JSONResponse:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    return JSONResponse(
        status_code=429,
        content={"error": "throttled"},
        headers={"Retry-After": "5"},
    )


_transient_lock = threading.Lock()
_transient_state = {"calls": 0}


def _maybe_reset_transient(x_test_reset: Optional[str]) -> None:
    if x_test_reset == "1":
        with _transient_lock:
            _transient_state["calls"] = 0


@router.get("/sharepoint/transient_5xx")
def sp_transient_5xx(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> JSONResponse:
    if not _token_ok(authorization, access_token):
        return _invalid_token()
    _maybe_reset_transient(x_test_reset)
    with _transient_lock:
        _transient_state["calls"] += 1
        n = _transient_state["calls"]
    if n == 1:
        return JSONResponse(status_code=503, content={"error": "service_unavailable"})
    if n == 2:
        return JSONResponse(status_code=502, content={"error": "bad_gateway"})
    return JSONResponse(content={"ok": True})
