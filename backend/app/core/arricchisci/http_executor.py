"""Shared HTTP execution boundary for Arricchisci REST connectors."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import requests
from requests.structures import CaseInsensitiveDict

from app.core.security.outbound_url import validate_external_url
from app.infrastructure.http.session import get_shared_session


@dataclass(frozen=True)
class RequestSpec:
    """Normalized HTTP request used by REST connector call sites."""

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Optional[Mapping[str, Any]] = None
    json_body: Any = None
    timeout: Any = None
    verify: Any = None
    allow_redirects: bool = False
    validate_url: bool = True
    context: str = "REST connector request"


@dataclass(frozen=True)
class NormalizedResponse:
    """Thin response wrapper with timing metadata."""

    status_code: int
    headers: CaseInsensitiveDict
    text: str
    elapsed_ms: float
    raw_response: requests.Response

    def json(self) -> Any:
        return self.raw_response.json()


class HttpExecutor:
    """Execute Arricchisci REST requests through one shared dispatch path.

    Phase 0 intentionally preserves the existing method semantics:
    ``GET`` uses query params; every other method still uses POST with a JSON
    body. Phase 1 can replace that compatibility rule with explicit verb
    dispatch once the call sites are unified.
    """

    def execute(self, spec: RequestSpec) -> NormalizedResponse:
        if spec.validate_url:
            validate_external_url(spec.url)

        method = (spec.method or "GET").upper()
        session = get_shared_session()
        started = time.time()

        kwargs: dict[str, Any] = {
            "headers": dict(spec.headers or {}),
            "timeout": spec.timeout,
            "allow_redirects": spec.allow_redirects,
        }
        if spec.verify is not None:
            kwargs["verify"] = spec.verify

        if method == "GET":
            response = session.get(spec.url, params=dict(spec.params or {}), **kwargs)
        else:
            payload = spec.json_body if spec.json_body is not None else dict(spec.params or {})
            response = session.post(spec.url, json=payload, **kwargs)

        return NormalizedResponse(
            status_code=response.status_code,
            headers=CaseInsensitiveDict(response.headers),
            text=response.text or "",
            elapsed_ms=(time.time() - started) * 1000,
            raw_response=response,
        )


_DEFAULT_EXECUTOR = HttpExecutor()


def timeout_from_options(
    options: Mapping[str, Any] | None,
    *,
    default_read_timeout: int | float,
    default_connect_timeout: int | float | None = None,
) -> Any:
    """Build a requests timeout value from connector options.

    ``options.timeout`` remains the compatibility read-timeout field; callers
    may opt into split ``connect_timeout`` / ``request_timeout`` without
    changing the execution path again.
    """

    opts = dict(options or {})
    read_timeout = opts.get("request_timeout", opts.get("timeout", default_read_timeout))
    connect_timeout = opts.get("connect_timeout", default_connect_timeout)
    if connect_timeout is None:
        return read_timeout
    return (connect_timeout, read_timeout)


def execute_http_request(spec: RequestSpec) -> NormalizedResponse:
    """Execute a request through the module-level Arricchisci HTTP executor."""

    return _DEFAULT_EXECUTOR.execute(spec)
