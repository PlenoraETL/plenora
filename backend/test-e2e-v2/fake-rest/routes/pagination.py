"""Pagination endpoints.

Implements the four pagination flavours that the REST connector must
recognise: offset+limit, page+size, opaque cursor, and Link-header (GitHub
style). Plus a ``no_meta`` variant where the client must infer end-of-list
from a short page.
"""

from __future__ import annotations

import base64
import copy
import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import load_fixture

router = APIRouter()


def _customers() -> list[dict]:
    return copy.deepcopy(load_fixture("customers.json"))


@router.get("/offset")
def offset(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=100),
) -> JSONResponse:
    items = _customers()
    sliced = items[offset : offset + limit]
    return JSONResponse(
        content={
            "data": sliced,
            "offset": offset,
            "limit": limit,
            "total": len(items),
        }
    )


@router.get("/page_size")
def page_size(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=5, ge=1, le=100),
) -> JSONResponse:
    items = _customers()
    total = len(items)
    total_pages = max(1, math.ceil(total / size))
    start = (page - 1) * size
    sliced = items[start : start + size]
    return JSONResponse(
        content={
            "items": sliced,
            "page": page,
            "size": size,
            "total_pages": total_pages,
            "total": total,
        }
    )


def _encode_cursor(off: int) -> str:
    return base64.urlsafe_b64encode(str(off).encode("ascii")).decode("ascii")


def _decode_cursor(cur: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cur.encode("ascii")).decode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_cursor") from exc


@router.get("/cursor")
def cursor(
    cursor: Optional[str] = Query(default=None),
    page_size: int = Query(default=5, ge=1, le=100, alias="page_size"),
) -> JSONResponse:
    items = _customers()
    off = 0 if not cursor else _decode_cursor(cursor)
    if off < 0 or off > len(items):
        raise HTTPException(status_code=400, detail="invalid_cursor")
    sliced = items[off : off + page_size]
    next_off = off + page_size
    has_more = next_off < len(items)
    next_cursor = _encode_cursor(next_off) if has_more else None
    return JSONResponse(
        content={
            "items": sliced,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }
    )


@router.get("/link_header")
def link_header(
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=5, ge=1, le=100),
) -> JSONResponse:
    items = _customers()
    total = len(items)
    total_pages = max(1, math.ceil(total / size))
    start = (page - 1) * size
    sliced = items[start : start + size]

    base = str(request.url).split("?", 1)[0]
    parts: list[str] = []
    if page < total_pages:
        parts.append(f'<{base}?page={page + 1}&size={size}>; rel="next"')
    if page > 1:
        parts.append(f'<{base}?page={page - 1}&size={size}>; rel="prev"')
    parts.append(f'<{base}?page=1&size={size}>; rel="first"')
    parts.append(f'<{base}?page={total_pages}&size={size}>; rel="last"')

    return JSONResponse(content=sliced, headers={"Link": ", ".join(parts)})


@router.get("/no_meta")
def no_meta(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=5, ge=1, le=100),
) -> JSONResponse:
    items = _customers()
    start = (page - 1) * size
    sliced = items[start : start + size]
    return JSONResponse(content=sliced)
