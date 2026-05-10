"""Data-shape endpoints.

These endpoints all return the same source dataset (12 customers from the
seed SQL) wrapped in different envelope shapes so the REST connector can be
exercised against several JSON layouts (root array, deep nesting, nulls,
single object, etc.). Each requires either ``X-API-Key`` header or
``?api_key=`` query parameter (chooser left to the test).

The ``/data/sink/*`` family emulates a write sink (POST/PUT/PATCH/DELETE)
used by ``app/core/carica/rest_write_boundary.py`` /
``app/core/features/rest_sink.py``. State is in-memory per-process and can
be cleared by sending ``X-Test-Reset: 1`` on any sink endpoint.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from . import API_KEY, load_fixture

router = APIRouter()


def _check_auth(api_key_q: Optional[str], api_key_h: Optional[str]) -> None:
    if api_key_q != API_KEY and api_key_h != API_KEY:
        raise HTTPException(status_code=401, detail="invalid_api_key")


# ---------------------------------------------------------------------------
# REST write sink state (in-memory, per-process)
# ---------------------------------------------------------------------------

_sink_lock = threading.Lock()
_sink_records: dict[int, dict[str, Any]] = {}
_sink_versions: dict[int, int] = {}
_sink_next_id: int = 1000
_sink_batch_idempotency: dict[str, dict[str, Any]] = {}


def _sink_reset() -> None:
    global _sink_next_id
    with _sink_lock:
        _sink_records.clear()
        _sink_versions.clear()
        _sink_batch_idempotency.clear()
        _sink_next_id = 1000


def _sink_check_auth(x_api_key: Optional[str]) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _maybe_sink_reset(x_test_reset: Optional[str]) -> None:
    if x_test_reset == "1":
        _sink_reset()


def _customers() -> list[dict]:
    # Return a deep copy so per-route mutation cannot leak across requests.
    return copy.deepcopy(load_fixture("customers.json"))


@router.get("/object_root")
def object_root(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    items = _customers()
    return JSONResponse(
        content={
            "data": items,
            "count": len(items),
            "fetched_at": "2026-01-01T00:00:00Z",
        }
    )


@router.get("/array_root")
def array_root(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    return JSONResponse(content=_customers())


@router.get("/nested_deep")
def nested_deep(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    items = _customers()
    return JSONResponse(
        content={
            "response": {
                "items": {
                    "records": items,
                    "meta": {"total": len(items)},
                }
            }
        }
    )


@router.get("/with_nulls")
def with_nulls(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    items = _customers()[:5]
    # Inject nulls in different fields to exercise null-handling.
    items[0]["email"] = None
    items[1]["age"] = None
    items[2]["status"] = None
    items[3]["city_id"] = None
    items[4]["signup_date"] = None
    return JSONResponse(content=items)


@router.get("/empty_array")
def empty_array(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    return JSONResponse(content={"data": []})


@router.get("/single_object")
def single_object(
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    return JSONResponse(content=_customers()[0])


@router.get("/by_id/{item_id}")
def by_id(
    item_id: int = Path(..., ge=1),
    api_key: Optional[str] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> JSONResponse:
    _check_auth(api_key, x_api_key)
    items = _customers()
    for row in items:
        if row["id"] == item_id:
            return JSONResponse(content=row)
    raise HTTPException(status_code=404, detail="customer_not_found")


# ---------------------------------------------------------------------------
# REST write sink endpoints
# ---------------------------------------------------------------------------


@router.post("/sink/records")
async def sink_create_records(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Bulk create records.

    Body: ``{"records": [{...}, ...]}``. Returns 201 with
    ``{"created": N, "ids": [...]}`` where ids are sequential integers
    starting from 1000 (per-process counter).
    """
    _sink_check_auth(x_api_key)
    _maybe_sink_reset(x_test_reset)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")
    records = body.get("records")
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="records_must_be_list")

    global _sink_next_id
    new_ids: list[int] = []
    with _sink_lock:
        for rec in records:
            if not isinstance(rec, dict):
                raise HTTPException(status_code=400, detail="record_must_be_object")
            new_id = _sink_next_id
            _sink_next_id += 1
            stored = dict(rec)
            stored["id"] = new_id
            _sink_records[new_id] = stored
            _sink_versions[new_id] = 1
            new_ids.append(new_id)

    payload: dict[str, Any] = {"created": len(new_ids), "ids": new_ids}
    if x_test_reset == "1":
        payload["ok"] = True
    return JSONResponse(status_code=201, content=payload)


@router.put("/sink/by_id/{item_id}")
async def sink_put_record(
    request: Request,
    item_id: int = Path(..., ge=1),
    x_api_key: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Full replace of a sink record.

    Returns ``{"updated": id, "version": <int>}``. The version counter is
    incremented on every PUT/PATCH for the same id.
    """
    _sink_check_auth(x_api_key)
    _maybe_sink_reset(x_test_reset)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")

    with _sink_lock:
        if item_id not in _sink_records and x_test_reset != "1":
            # Allow PUT to upsert: emulate stricter behaviour by 404 when
            # the id is unknown. Matches the typical REST sink contract.
            raise HTTPException(status_code=404, detail="record_not_found")
        replaced = dict(body)
        replaced["id"] = item_id
        _sink_records[item_id] = replaced
        _sink_versions[item_id] = _sink_versions.get(item_id, 0) + 1
        version = _sink_versions[item_id]

    payload: dict[str, Any] = {"updated": item_id, "version": version}
    if x_test_reset == "1":
        payload["ok"] = True
    return JSONResponse(status_code=200, content=payload)


@router.patch("/sink/by_id/{item_id}")
async def sink_patch_record(
    request: Request,
    item_id: int = Path(..., ge=1),
    x_api_key: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Partial update of a sink record. Returns the merged body."""
    _sink_check_auth(x_api_key)
    _maybe_sink_reset(x_test_reset)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")

    with _sink_lock:
        if item_id not in _sink_records:
            raise HTTPException(status_code=404, detail="record_not_found")
        merged = dict(_sink_records[item_id])
        merged.update(body)
        merged["id"] = item_id
        _sink_records[item_id] = merged
        _sink_versions[item_id] = _sink_versions.get(item_id, 0) + 1
        result = dict(merged)
        result["version"] = _sink_versions[item_id]

    if x_test_reset == "1":
        result["ok"] = True
    return JSONResponse(status_code=200, content=result)


@router.delete("/sink/by_id/{item_id}")
def sink_delete_record(
    item_id: int = Path(..., ge=1),
    x_api_key: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> Response:
    """Delete a sink record. 204 on success, 404 if absent (idempotent
    semantics: the underlying state is the same after both outcomes)."""
    _sink_check_auth(x_api_key)
    _maybe_sink_reset(x_test_reset)

    with _sink_lock:
        existed = item_id in _sink_records
        _sink_records.pop(item_id, None)
        _sink_versions.pop(item_id, None)

    if not existed:
        # Per spec: 404 when id was unknown. Reset short-circuit returns ok.
        if x_test_reset == "1":
            return JSONResponse(status_code=200, content={"ok": True, "deleted": False})
        raise HTTPException(status_code=404, detail="record_not_found")

    if x_test_reset == "1":
        return JSONResponse(status_code=200, content={"ok": True, "deleted": True})
    return Response(status_code=204)


@router.post("/sink/batch")
async def sink_batch(
    request: Request,
    x_api_key: Optional[str] = Header(default=None),
    x_test_reset: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Idempotent batch enqueue.

    Body: ``{"batch_records_key": "<uuid>", "records": [...]}``. Replays
    return the exact same response (idempotency map keyed by
    ``batch_records_key``).
    """
    _sink_check_auth(x_api_key)
    _maybe_sink_reset(x_test_reset)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")

    key = body.get("batch_records_key")
    if not isinstance(key, str) or not key:
        raise HTTPException(status_code=400, detail="batch_records_key_required")
    records = body.get("records")
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="records_must_be_list")

    with _sink_lock:
        cached = _sink_batch_idempotency.get(key)
        if cached is not None:
            payload = dict(cached)
            if x_test_reset == "1":
                payload["ok"] = True
            return JSONResponse(status_code=202, content=payload)
        payload = {"batch_id": key, "queued": len(records)}
        _sink_batch_idempotency[key] = payload

    out = dict(payload)
    if x_test_reset == "1":
        out["ok"] = True
    return JSONResponse(status_code=202, content=out)
