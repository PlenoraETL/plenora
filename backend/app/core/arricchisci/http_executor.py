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
    raw_body: Any = None
    body_type: str = "json"
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

    ``GET`` uses query params. ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE`` use
    the configured body encoder.
    """

    _SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    def execute(self, spec: RequestSpec) -> NormalizedResponse:
        if spec.validate_url:
            validate_external_url(spec.url)

        method = (spec.method or "GET").upper()
        if method not in self._SUPPORTED_METHODS:
            raise ValueError(f"Metodo HTTP non supportato: {method}")

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
            request_fn = getattr(session, method.lower())
            response = request_fn(
                spec.url,
                **self._encoded_body_kwargs(spec),
                **kwargs,
            )

        return NormalizedResponse(
            status_code=response.status_code,
            headers=CaseInsensitiveDict(response.headers),
            text=response.text or "",
            elapsed_ms=(time.time() - started) * 1000,
            raw_response=response,
        )

    @staticmethod
    def _payload_from_spec(spec: RequestSpec) -> Any:
        if spec.json_body is not None:
            return spec.json_body
        return dict(spec.params or {})

    @classmethod
    def _encoded_body_kwargs(cls, spec: RequestSpec) -> dict[str, Any]:
        body_type = normalize_body_type(spec.body_type)
        if body_type == "none":
            return {"params": dict(spec.params or {})}

        payload = cls._payload_from_spec(spec)
        if body_type == "json":
            return {"json": payload}
        if body_type == "form-urlencoded":
            return {"data": payload}
        if body_type == "raw":
            body = spec.raw_body if spec.raw_body is not None else payload
            if not isinstance(body, (str, bytes)):
                body = str(body)
            return {"data": body}

        raise ValueError(f"body_type non supportato: {spec.body_type}")


def normalize_body_type(body_type: str | None) -> str:
    """Normalize REST connector body type aliases."""

    normalized = (body_type or "json").strip().lower().replace("_", "-")
    aliases = {
        "": "json",
        "form": "form-urlencoded",
        "x-www-form-urlencoded": "form-urlencoded",
        "application/x-www-form-urlencoded": "form-urlencoded",
        "text": "raw",
        "raw/text": "raw",
        "raw-text": "raw",
        "text/plain": "raw",
        "no-body": "none",
        "none": "none",
    }
    return aliases.get(normalized, normalized)


def body_type_from_config(
    request_config: Mapping[str, Any] | None,
    *,
    default: str = "json",
) -> str:
    """Read the REST body encoder from a connection request config."""

    request_cfg = dict(request_config or {})
    return normalize_body_type(request_cfg.get("body_type", default))


def raw_body_from_config(request_config: Mapping[str, Any] | None) -> Any:
    """Read raw/text body content from a connection request config."""

    request_cfg = dict(request_config or {})
    if "raw_body" in request_cfg:
        return request_cfg["raw_body"]
    return request_cfg.get("body")


def allow_redirects_from_config(
    options: Mapping[str, Any] | None = None,
    request_config: Mapping[str, Any] | None = None,
    *,
    default: bool = False,
) -> bool:
    """Resolve allow_redirects from request config, then options, then default."""

    request_cfg = dict(request_config or {})
    if "allow_redirects" in request_cfg:
        return _config_bool(request_cfg["allow_redirects"])
    opts = dict(options or {})
    if "allow_redirects" in opts:
        return _config_bool(opts["allow_redirects"])
    return default


def _config_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
