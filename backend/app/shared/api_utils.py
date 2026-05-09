"""
app/shared/api_utils.py

Compatibility shim for historical API helper imports.
"""

from typing import Dict

from app.shared.connections import oauth as _oauth
from app.shared.connections.oauth import (
    get_arcgis_token,
    get_cached_oauth2_token,
    invalidate_oauth2_token,
)
from app.core.security.outbound_url import (
    _warn_urby_loopback_alias_once,
    logger,
    validate_external_url,
    validate_external_url_silent,
)
from app.infrastructure.http.session import get_shared_session

__all__ = [
    "get_shared_session",
    "get_arcgis_token",
    "get_cached_oauth2_token",
    "invalidate_oauth2_token",
    "prepare_headers",
    "validate_external_url",
    "validate_external_url_silent",
]


def prepare_headers(auth: Dict, custom_headers: Dict = None) -> Dict:
    """Prepara headers HTTP per chiamata API REST."""
    return _oauth.prepare_headers(
        auth,
        custom_headers,
        oauth2_token_getter=get_cached_oauth2_token,
    )
