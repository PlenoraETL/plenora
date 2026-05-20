"""Slow / partial-response endpoints.

Used to exercise client-side timeout handling, retry logic, and
partial-read recovery.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()

MAX_DELAY_SEC = 30


@router.get("/delay/{seconds}")
async def delay(seconds: int = Path(..., ge=0, le=MAX_DELAY_SEC)) -> JSONResponse:
    await asyncio.sleep(seconds)
    return JSONResponse(content={"slept": seconds})


@router.get("/partial_body")
async def partial_body() -> StreamingResponse:
    """Send half a JSON body, then close the connection mid-stream."""

    async def generator() -> AsyncGenerator[bytes, None]:
        yield b'{"data": [1, 2, 3'
        await asyncio.sleep(0.1)
        # Closing the stream mid-payload triggers the partial-read failure
        # mode on the client. Raising here aborts the response cleanly from
        # uvicorn's perspective.
        raise RuntimeError("partial_body: simulated mid-stream failure")

    return StreamingResponse(generator(), media_type="application/json")


@router.get("/timeout_simulator")
async def timeout_simulator(ms: int = Query(default=3000, ge=0, le=MAX_DELAY_SEC * 1000)) -> JSONResponse:
    await asyncio.sleep(ms / 1000.0)
    return JSONResponse(content={"slept_ms": ms})
