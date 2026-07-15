"""Shared requests session with connection pooling."""

from __future__ import annotations

import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from app.shared.security.outbound_url import resolve_external_url

_shared_session: Optional[requests.Session] = None
_shared_session_lock = threading.Lock()
DEFAULT_OUTBOUND_RESPONSE_MAX_BYTES = 32 * 1024 * 1024


class OutboundResponseTooLarge(ValueError):
    """Raised before an untrusted response can exhaust process memory."""


class PinnedHostAdapter(HTTPAdapter):
    """Connect to an IP URL while verifying TLS for the original hostname."""

    def __init__(self, hostname: str, **kwargs) -> None:
        self._hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_hostname"] = self._hostname
        pool_kwargs["server_hostname"] = self._hostname
        return super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["assert_hostname"] = self._hostname
        proxy_kwargs["server_hostname"] = self._hostname
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def build_pinned_session(
    base_session: requests.Session,
    *,
    hostname: str,
    pinned_url: str,
) -> requests.Session:
    """Create an isolated session whose most-specific adapter is DNS-pinned."""
    session = requests.Session()
    session.headers.update(base_session.headers)
    session.cookies.update(base_session.cookies)
    session.auth = base_session.auth
    session.proxies.update(base_session.proxies)
    session.trust_env = base_session.trust_env
    prefix = pinned_url.split("/", 3)[:3]
    mount_prefix = "/".join(prefix) + "/"
    session.mount(
        mount_prefix,
        PinnedHostAdapter(
            hostname,
            pool_connections=1,
            pool_maxsize=1,
            max_retries=0,
            pool_block=True,
        ),
    )
    return session


def get_shared_session() -> requests.Session:
    """Return a shared requests.Session configured for connection pooling.

    Policy:
      - `pool_maxsize=50`: cap dei connection slot riservati al pool.
      - `pool_block=True`: i thread che eccedono il pool si bloccano in
        attesa di uno slot libero invece di aprire connessioni TCP unbounded.
      - `max_retries=0`: retry policy gestita esplicitamente al call
        site (es. `arcgis_auth._request_with_token`), non mascherata
        dall'adapter.
    """
    global _shared_session
    if _shared_session is None:
        with _shared_session_lock:
            if _shared_session is None:
                sess = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=50,
                    max_retries=0,
                    pool_block=True,
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                _shared_session = sess
    return _shared_session


def request_pinned(method: str, url: str, **kwargs) -> requests.Response:
    """Execute a fully-buffered outbound request with DNS pinning.

    Streaming callers must keep using their domain executor so the isolated
    session can live as long as the response stream. This helper intentionally
    covers the small JSON/token calls that previously validated and connected
    in two separate steps.
    """
    if kwargs.pop("stream", False):
        raise ValueError("request_pinned non supporta risposte streaming")
    max_response_bytes = int(
        kwargs.pop("max_response_bytes", DEFAULT_OUTBOUND_RESPONSE_MAX_BYTES)
    )
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes deve essere positivo")
    target = resolve_external_url(url)
    headers = dict(kwargs.pop("headers", None) or {})
    headers["Host"] = target.host_header
    kwargs["allow_redirects"] = False
    base_session = get_shared_session()
    session = build_pinned_session(
        base_session,
        hostname=target.hostname,
        pinned_url=target.pinned_url,
    )
    try:
        response = session.request(
            method,
            target.pinned_url,
            headers=headers,
            stream=True,
            **kwargs,
        )
        declared_size = response.headers.get("Content-Length")
        if declared_size:
            try:
                parsed_size = int(declared_size)
            except (TypeError, ValueError):
                parsed_size = None
            if parsed_size is not None and parsed_size > max_response_bytes:
                response.close()
                raise OutboundResponseTooLarge(
                    f"Risposta esterna oltre il limite di {max_response_bytes} byte"
                )

        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_response_bytes:
                response.close()
                raise OutboundResponseTooLarge(
                    f"Risposta esterna oltre il limite di {max_response_bytes} byte"
                )
        response._content = bytes(content)
        response._content_consumed = True
        return response
    finally:
        session.close()


__all__ = [
    "PinnedHostAdapter",
    "OutboundResponseTooLarge",
    "DEFAULT_OUTBOUND_RESPONSE_MAX_BYTES",
    "build_pinned_session",
    "get_shared_session",
    "request_pinned",
]
