"""Authentication endpoints.

Covers every auth scheme that plenora's REST connectors must handle:
api_key (query and header), Bearer, Basic, OAuth2 client_credentials, and
ArcGIS' ``generateToken`` flow (which returns 200 + body error on failure).
"""

from __future__ import annotations

import base64
import time
from typing import Optional

from fastapi import APIRouter, Form, Header, Query, Request
from fastapi.responses import JSONResponse

from . import (
    API_KEY,
    ARCGIS_PASS,
    ARCGIS_TOKEN,
    ARCGIS_USER,
    BASIC_PASS,
    BASIC_USER,
    BEARER_TOKEN,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN,
)

router = APIRouter()


@router.get("/api_key_query")
def api_key_query(api_key: Optional[str] = Query(default=None)) -> JSONResponse:
    if api_key != API_KEY:
        return JSONResponse(status_code=401, content={"error": "invalid_api_key"})
    return JSONResponse(content={"ok": True, "auth": "api_key_query"})


@router.get("/api_key_header")
def api_key_header(x_api_key: Optional[str] = Header(default=None)) -> JSONResponse:
    if x_api_key != API_KEY:
        return JSONResponse(status_code=401, content={"error": "invalid_api_key"})
    return JSONResponse(content={"ok": True, "auth": "api_key_header"})


@router.get("/bearer")
def bearer(authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    expected = f"Bearer {BEARER_TOKEN}"
    if authorization != expected:
        return JSONResponse(status_code=401, content={"error": "invalid_bearer"})
    return JSONResponse(content={"ok": True, "auth": "bearer"})


@router.get("/basic")
def basic(authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    challenge = {"WWW-Authenticate": 'Basic realm="fake"'}
    if not authorization or not authorization.lower().startswith("basic "):
        return JSONResponse(
            status_code=401,
            content={"error": "missing_basic_auth"},
            headers=challenge,
        )
    try:
        decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
        user, _, pwd = decoded.partition(":")
    except Exception:
        return JSONResponse(
            status_code=401,
            content={"error": "malformed_basic_auth"},
            headers=challenge,
        )
    if user != BASIC_USER or pwd != BASIC_PASS:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_basic_credentials"},
            headers=challenge,
        )
    return JSONResponse(content={"ok": True, "auth": "basic", "user": user})


@router.post("/oauth2/token")
def oauth2_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    username: Optional[str] = Form(default=None),
    password: Optional[str] = Form(default=None),
) -> JSONResponse:
    """OAuth2 token endpoint supporting both ``client_credentials`` and
    ``password`` grants. Both flows return the same token shape (and the
    same static ``OAUTH_FAKE_TOKEN``) so downstream tests can re-use one
    fixture regardless of the grant they exercise.
    """
    if grant_type == "client_credentials":
        if client_id != OAUTH_CLIENT_ID or client_secret != OAUTH_CLIENT_SECRET:
            return JSONResponse(status_code=401, content={"error": "invalid_client"})
        return JSONResponse(
            content={
                "access_token": OAUTH_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )
    if grant_type == "password":
        # Resource Owner Password Credentials grant. Client credentials are
        # still required (RFC 6749 § 4.3.2) so we validate both pairs.
        if client_id != OAUTH_CLIENT_ID or client_secret != OAUTH_CLIENT_SECRET:
            return JSONResponse(status_code=401, content={"error": "invalid_client"})
        if not username or not password:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request"},
            )
        if username != BASIC_USER or password != BASIC_PASS:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "invalid_grant",
                    "error_description": "Bad credentials",
                },
            )
        return JSONResponse(
            content={
                "access_token": OAUTH_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )
    return JSONResponse(
        status_code=400,
        content={"error": "unsupported_grant_type"},
    )


@router.get("/oauth2/protected")
def oauth2_protected(authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    expected = f"Bearer {OAUTH_TOKEN}"
    if authorization != expected:
        return JSONResponse(status_code=401, content={"error": "invalid_token"})
    return JSONResponse(content={"ok": True, "auth": "oauth2"})


@router.post("/arcgis/generateToken")
async def arcgis_generate_token(request: Request) -> JSONResponse:
    """ArcGIS-style token endpoint: 200 + body error on bad credentials."""
    # ArcGIS clients post either form-encoded or url-query params; accept both.
    form = {}
    try:
        form_data = await request.form()
        form = {k: str(v) for k, v in form_data.items()}
    except Exception:
        form = {}
    params = dict(request.query_params)
    payload = {**params, **form}

    username = payload.get("username")
    password = payload.get("password")

    if username != ARCGIS_USER or password != ARCGIS_PASS:
        # ArcGIS pattern: HTTP 200 with error body.
        return JSONResponse(
            content={"error": {"code": 400, "message": "Invalid credentials"}}
        )

    expires_ms = int(time.time() * 1000) + 3_600_000
    return JSONResponse(content={"token": ARCGIS_TOKEN, "expires": expires_ms})
