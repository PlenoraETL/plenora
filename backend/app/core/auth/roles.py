"""Compatibility re-exports for auth role constants."""

from app.shared.contracts.auth import (
    ROLES_ABOVE_VIEWER,
    ROLES_ADMIN_OR_ABOVE,
    ROLES_KIOSK_ONLY,
    VALID_ROLES,
)

__all__ = [
    "VALID_ROLES",
    "ROLES_ADMIN_OR_ABOVE",
    "ROLES_ABOVE_VIEWER",
    "ROLES_KIOSK_ONLY",
]
