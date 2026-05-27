"""TLS-bypass policy primitives shared by outbound connection clients."""

from __future__ import annotations

import os
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

_PROD_ENVS = ("production", "prod", "staging", "stage")
_DEV_ENVS = ("development", "dev", "test")


def _env_name() -> str:
    return os.environ.get("ENVIRONMENT", os.environ.get("ENV", "")).lower()


def is_prod_like_env() -> bool:
    """True when ENVIRONMENT/ENV identifies production or staging."""
    return _env_name() in _PROD_ENVS


def tls_bypass_allowed() -> bool:
    """Return whether verify=False is allowed for outbound TLS calls."""
    if is_prod_like_env():
        return False
    if os.environ.get("PLENORA_ALLOW_TLS_BYPASS", "").strip() == "1":
        return True
    if _env_name() in _DEV_ENVS:
        return True
    return False


def resolve_tls_verify(verify_ssl_cfg: Any, *, context: str) -> bool:
    """Apply the deployment TLS policy to a user-supplied verify flag."""
    requested = bool(verify_ssl_cfg) if verify_ssl_cfg is not None else True
    if requested:
        return True
    if not tls_bypass_allowed():
        logger.warning(
            "[SECURITY] %s: verify_ssl=False ignorato (TLS bypass non "
            "consentito in questo ambiente) - forzato verify=True per "
            "evitare canale MITM-able su credenziali.",
            context,
        )
        return True
    logger.warning(
        "[SECURITY] %s: verify_ssl=False attivo (bypass abilitato via env). "
        "Le credenziali viaggiano senza verifica TLS - accettabile solo in "
        "dev/CI.",
        context,
    )
    return False

