"""Helper HTTP minimali per i test funzionali."""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import requests


def _try_parse_json_body(raw: bytes) -> Any:
    """Best-effort JSON parsing del body di una response.

    Sequenza di fallback:
      1. body vuoto → None
      2. JSON valido → dict/list parsato
      3. body decodificabile come utf-8 → string raw (text/plain, html, csv...)
      4. body binario non utf-8 → bytes raw (zip, parquet, immagini, ...)

    Senza questa fallback chain il caller esplodeva con `JSONDecodeError`
    o `UnicodeDecodeError` su qualunque endpoint non-JSON (es. download
    binario `/api/tasks/{id}/download`), prima ancora di arrivare ad
    asserire sullo status code.
    """
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


def http_json_response(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 5.0,
) -> tuple[int, Any, dict]:
    """Invia una request JSON e ritorna ``(status_code, body, headers)``.

    Il body e' parsato come JSON quando possibile; per response non-JSON
    (testo, CSV, binari) ricade su stringa o bytes via
    `_try_parse_json_body`. Pensato per testare endpoint misti dove non
    sai a priori se il server ti darà JSON o un download binario.
    """
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return (
                getattr(resp, "status", 200),
                _try_parse_json_body(raw),
                dict(resp.headers),
            )
    except HTTPError as exc:
        return exc.code, _try_parse_json_body(exc.read()), dict(exc.headers)


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 5.0,
) -> Any:
    """Invia una request JSON e ritorna solo il body su risposta 2xx/3xx."""
    status, body, _ = http_json_response(
        url,
        method=method,
        payload=payload,
        headers=headers,
        timeout=timeout,
    )
    if not (200 <= status < 400):
        raise RuntimeError(f"Unexpected HTTP status {status} for {url}")
    return body


def http_multipart_response(
    url: str,
    *,
    method: str = "POST",
    data: Optional[dict] = None,
    files: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 10.0,
) -> tuple[int, Any, dict]:
    """Invia una request multipart/form-data e ritorna ``(status, body, headers)``."""
    resp = requests.request(
        method=method,
        url=url,
        data=data or {},
        files=files or {},
        headers=headers or {},
        timeout=timeout,
    )
    return resp.status_code, _try_parse_json_body(resp.content), dict(resp.headers)


def poll_json_until(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: float = 20.0,
    interval: float = 0.5,
    request_timeout: Optional[float] = None,
    predicate=None,
) -> Any:
    """Polla un endpoint JSON finche' `predicate(body)` diventa True."""
    deadline = time.time() + timeout
    last_body = None
    while time.time() < deadline:
        status, body, _ = http_json_response(
            url,
            headers=headers,
            timeout=request_timeout if request_timeout is not None else min(interval + 1.0, 5.0),
        )
        last_body = {"status_code": status, "body": body}
        if predicate is None:
            if 200 <= status < 400:
                return body
        elif predicate(body):
            return body
        time.sleep(interval)
    raise RuntimeError(f"poll_json_until timeout for {url}: last={last_body}")
