"""
Fake HTTP service per test funzionali di `api_connector`.

Espone pochi endpoint deterministici utili per simulare sistemi esterni:
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response


app = FastAPI(title="PLENORA Functional Fake API")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/json/ok")
async def json_ok() -> Dict[str, Any]:
    return {"ok": True, "data": [{"value": 1}, {"value": 2}]}


@app.get("/json/enrich")
async def json_enrich(city: str = "", country: str = "") -> Dict[str, Any]:
    normalized_city = city.strip() or "unknown"
    normalized_country = country.strip() or "NA"
    return {
        "enriched": f"{normalized_city}-ok",
        "country_echo": normalized_country,
    }


@app.get("/json/error")
async def json_error() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": "invalid payload"},
    )


@app.get("/text/error")
async def text_error() -> PlainTextResponse:
    return PlainTextResponse("upstream failure", status_code=500)


@app.get("/slow")
async def slow(delay_s: float = 2.0) -> Dict[str, Any]:
    await asyncio.sleep(delay_s)
    return {"ok": True, "delay_s": delay_s}


@app.post("/download/bin")
async def download_bin() -> Response:
    return Response(
        content=b"fake-binary-payload",
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="fake.bin"'},
    )


@app.post("/multipart/echo")
async def multipart_echo(
    uploaded: UploadFile = File(...),
    note: str = Form(""),
) -> Dict[str, Any]:
    content = await uploaded.read()
    return {
        "filename": uploaded.filename,
        "content_type": uploaded.content_type,
        "size": len(content),
        "note": note,
    }


@app.post("/multipart/fail")
async def multipart_fail() -> None:
    raise HTTPException(status_code=400, detail="multipart rejected")
