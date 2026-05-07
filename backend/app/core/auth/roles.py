"""
Auth role constants.

Small shared boundary used by auth routers without importing the whole
"""

from __future__ import annotations


VALID_ROLES: tuple[str, ...] = (
    "esecutore",
    "viewer",
    "user",
    "admin",
    "superadmin",
)

ROLES_ADMIN_OR_ABOVE: tuple[str, ...] = ("admin", "superadmin")
ROLES_ABOVE_VIEWER: tuple[str, ...] = ("user", "admin", "superadmin")
ROLES_KIOSK_ONLY: tuple[str, ...] = ("esecutore",)
