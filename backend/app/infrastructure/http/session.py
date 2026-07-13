"""Shared requests session with connection pooling."""

from __future__ import annotations

import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

_shared_session: Optional[requests.Session] = None
_shared_session_lock = threading.Lock()


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


__all__ = ["PinnedHostAdapter", "build_pinned_session", "get_shared_session"]
