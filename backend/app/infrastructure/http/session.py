"""Shared requests session with connection pooling."""

from __future__ import annotations

import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

_shared_session: Optional[requests.Session] = None
_shared_session_lock = threading.Lock()


def get_shared_session() -> requests.Session:
    """Return a shared requests.Session configured for connection pooling."""
    global _shared_session
    if _shared_session is None:
        with _shared_session_lock:
            if _shared_session is None:
                sess = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=50,
                    max_retries=0,
                    pool_block=False,
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
                _shared_session = sess
    return _shared_session
