"""Shared HTTP execution boundary for Arricchisci REST connectors."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Optional

import requests
from requests.structures import CaseInsensitiveDict
from urllib.parse import urljoin, urlparse

from app.shared.security.outbound_url import resolve_external_url, validate_external_url
from app.infrastructure.http.session import (
    DEFAULT_OUTBOUND_RESPONSE_MAX_BYTES,
    OutboundResponseTooLarge,
    build_pinned_session,
    get_shared_session,
)


SUPPORTED_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
SUPPORTED_BODY_TYPES = frozenset({
    "json",
    "form-urlencoded",
    "multipart",
    "raw",
    "none",
})


@dataclass(frozen=True)
class RequestSpec:
    """Normalized HTTP request used by REST connector call sites."""

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Optional[Mapping[str, Any]] = None
    json_body: Any = None
    form_data: Optional[Mapping[str, Any]] = None
    files: Optional[Mapping[str, Any]] = None
    raw_body: Any = None
    body_type: str = "json"
    timeout: Any = None
    verify: Any = None
    allow_redirects: bool = False
    stream: bool = False
    validate_url: bool = True
    max_response_bytes: int = DEFAULT_OUTBOUND_RESPONSE_MAX_BYTES
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

    def execute(self, spec: RequestSpec) -> NormalizedResponse:
        method, _body_type = validate_request_spec_config(
            spec.method,
            spec.body_type,
        )
        started = time.time()
        response = self._execute_with_safe_redirects(spec, method)

        if not spec.stream:
            self._buffer_response_bounded(response, spec.max_response_bytes)

        return NormalizedResponse(
            status_code=response.status_code,
            headers=CaseInsensitiveDict(response.headers),
            text="" if spec.stream else (response.text or ""),
            elapsed_ms=(time.time() - started) * 1000,
            raw_response=response,
        )

    def _execute_with_safe_redirects(self, spec: RequestSpec, method: str) -> Any:
        current_url = spec.url
        current_method = method
        original_host = urlparse(spec.url).hostname
        for redirect_count in range(6):
            response = self._execute_once(
                spec,
                method=current_method,
                url=current_url,
            )
            location = response.headers.get("location")
            is_redirect = bool(
                getattr(
                    response,
                    "is_redirect",
                    response.status_code in (301, 302, 303, 307, 308),
                )
            )
            if not (spec.allow_redirects and is_redirect and location):
                return response
            if redirect_count >= 5:
                raise ValueError("Troppi redirect HTTP (massimo 5)")

            next_url = urljoin(current_url, location)
            next_host = urlparse(next_url).hostname
            if next_host != original_host:
                raise ValueError("Redirect cross-host non consentito")
            close_response = getattr(response, "close", None)
            if callable(close_response):
                close_response()
            current_url = next_url
            if response.status_code in (302, 303) or (
                response.status_code == 301 and current_method == "POST"
            ):
                current_method = "GET"
        raise ValueError("Troppi redirect HTTP")

    def _execute_once(self, spec: RequestSpec, *, method: str, url: str) -> Any:
        shared_session = get_shared_session()
        request_url = url
        headers = dict(spec.headers or {})
        session = shared_session
        if spec.validate_url:
            if isinstance(shared_session, requests.Session):
                target = resolve_external_url(url)
                request_url = target.pinned_url
                headers["Host"] = target.host_header
                session = build_pinned_session(
                    shared_session,
                    hostname=target.hostname,
                    pinned_url=target.pinned_url,
                )
            else:
                # Injectable non-network session used by unit/adapter runtimes.
                # Production always supplies requests.Session and takes the
                # DNS-pinned branch above.
                validate_external_url(url)

        kwargs: MutableMapping[str, Any] = {
            "headers": headers,
            "timeout": spec.timeout,
            "allow_redirects": False,
            # Always stream at the requests boundary. Non-streaming callers are
            # buffered explicitly with a hard byte cap in ``execute``.
            "stream": True,
        }
        if spec.verify is not None:
            kwargs["verify"] = spec.verify
        if method == "GET":
            response = session.get(
                request_url,
                params=dict(spec.params or {}),
                **kwargs,
            )
        else:
            request_fn = getattr(session, method.lower())
            response = request_fn(
                request_url,
                **self._encoded_body_kwargs(spec),
                **kwargs,
            )
        if session is not shared_session:
            # Close the isolated adapter together with the response, including
            # redirect and over-limit paths.
            original_close = getattr(response, "close", lambda: None)
            closed = False

            def close_with_session() -> None:
                nonlocal closed
                if closed:
                    return
                closed = True
                try:
                    original_close()
                finally:
                    session.close()

            response.close = close_with_session
        return response

    @staticmethod
    def _buffer_response_bounded(response: Any, limit: int) -> None:
        limit = int(limit)
        if limit <= 0:
            raise ValueError("max_response_bytes deve essere positivo")

        declared_size = response.headers.get("Content-Length")
        if declared_size:
            try:
                parsed_size = int(declared_size)
            except (TypeError, ValueError):
                parsed_size = None
            if parsed_size is not None and parsed_size > limit:
                response.close()
                raise OutboundResponseTooLarge(
                    f"Risposta esterna oltre il limite di {limit} byte"
                )

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raw = (getattr(response, "text", "") or "").encode("utf-8")
            if len(raw) > limit:
                raise OutboundResponseTooLarge(
                    f"Risposta esterna oltre il limite di {limit} byte"
                )
            return

        content = bytearray()
        try:
            for chunk in iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > limit:
                    raise OutboundResponseTooLarge(
                        f"Risposta esterna oltre il limite di {limit} byte"
                    )
            response._content = bytes(content)
            response._content_consumed = True
        finally:
            response.close()

    @staticmethod
    def _payload_from_spec(spec: RequestSpec) -> Any:
        if spec.json_body is not None:
            return spec.json_body
        return dict(spec.params or {})

    @classmethod
    def _encoded_body_kwargs(cls, spec: RequestSpec) -> Mapping[str, Any]:
        body_type = normalize_body_type(spec.body_type)
        if body_type == "none":
            return {"params": dict(spec.params or {})}

        payload = cls._payload_from_spec(spec)
        if body_type == "json":
            return {"json": payload}
        if body_type == "form-urlencoded":
            return {"data": payload}
        if body_type == "multipart":
            return {
                "files": dict(spec.files or {}),
                "data": dict(spec.form_data or spec.params or {}),
            }
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
        "multipart/form-data": "multipart",
        "text": "raw",
        "raw/text": "raw",
        "raw-text": "raw",
        "text/plain": "raw",
        "no-body": "none",
        "none": "none",
    }
    return aliases.get(normalized, normalized)


def validate_request_spec_config(
    method: str | None,
    body_type: str | None = "json",
) -> tuple[str, str]:
    """Validate and normalize method/body config for a REST RequestSpec."""

    normalized_method = (method or "GET").upper()
    if normalized_method not in SUPPORTED_HTTP_METHODS:
        raise ValueError(f"Metodo HTTP non supportato: {normalized_method}")

    normalized_body_type = normalize_body_type(body_type)
    if (
        normalized_method != "GET"
        and normalized_body_type not in SUPPORTED_BODY_TYPES
    ):
        raise ValueError(f"body_type non supportato: {body_type}")

    return normalized_method, normalized_body_type


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
