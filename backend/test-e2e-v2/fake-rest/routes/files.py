"""File-download / file-upload endpoints.

CSV and JSON are generated inline from the fixtures. The shapefile endpoint
falls back to a placeholder ZIP containing a single ``README.txt`` if
``pyshp`` (the ``shapefile`` package) is not installed; the placeholder
still exercises the binary-download path.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from . import API_KEY, load_fixture

router = APIRouter()


def _check_auth(api_key: Optional[str]) -> None:
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _csv_response(rows: list[dict], filename: str) -> Response:
    if not rows:
        body = ""
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        body = buf.getvalue()
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/csv/customers")
def csv_customers(api_key: Optional[str] = Query(default=None)) -> Response:
    _check_auth(api_key)
    return _csv_response(load_fixture("customers.json"), "customers.csv")


@router.get("/csv/cities")
def csv_cities(api_key: Optional[str] = Query(default=None)) -> Response:
    """CSV with explicit lat/lng columns (separate from geom)."""
    _check_auth(api_key)
    return _csv_response(load_fixture("cities.json"), "cities.csv")


@router.get("/json/customers")
def json_customers(api_key: Optional[str] = Query(default=None)) -> Response:
    _check_auth(api_key)
    body = json.dumps(load_fixture("customers.json"), ensure_ascii=False, indent=2)
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="customers.json"'},
    )


def _build_shapefile_zip() -> bytes:
    """Build an in-memory ZIP containing a tiny shapefile.

    Uses ``pyshp`` when present (produces real .shp/.shx/.dbf bytes). If the
    package is missing the function falls back to a placeholder ZIP that
    still triggers the same binary-download/unzip code path on the client.
    """
    try:
        import shapefile  # type: ignore[import-untyped]
    except Exception:
        # Fallback placeholder: a single text file.
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "README.txt",
                "Shapefile fallback: pyshp not installed in fake-rest image.\n"
                "Endpoint still serves a valid ZIP for download-path tests.\n",
            )
        return zip_buf.getvalue()

    shp_buf = io.BytesIO()
    shx_buf = io.BytesIO()
    dbf_buf = io.BytesIO()
    writer = shapefile.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf, shapeType=shapefile.POINT)
    writer.field("id", "N", size=10)
    writer.field("name", "C", size=64)
    cities = load_fixture("cities.json")
    for c in cities:
        writer.point(c["longitude"], c["latitude"])
        writer.record(c["id"], c["name"])
    writer.close()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cities.shp", shp_buf.getvalue())
        zf.writestr("cities.shx", shx_buf.getvalue())
        zf.writestr("cities.dbf", dbf_buf.getvalue())
    return zip_buf.getvalue()


@router.get("/zip/shapefile")
def zip_shapefile(api_key: Optional[str] = Query(default=None)) -> Response:
    _check_auth(api_key)
    payload = _build_shapefile_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="cities.zip"'},
    )


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    return {
        "filename": file.filename,
        "size": len(raw),
        "content_type": file.content_type,
    }


# Re-export so static checkers see StreamingResponse as used (fastapi
# may import it lazily); this keeps imports tidy and ready for streaming
# variants if added later.
__all__ = ["router", "StreamingResponse"]
