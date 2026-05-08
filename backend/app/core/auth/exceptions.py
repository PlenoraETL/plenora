"""Domain exceptions for auth."""

from __future__ import annotations


class AuthDomainError(Exception):
    """Base class for auth-domain failures."""


class AuthAccessDeniedError(AuthDomainError):
    """Raised when an authenticated actor cannot access a resource."""


class AuthResourceNotFoundError(AuthDomainError):
    """Raised when an auth-owned resource is not visible to the actor."""


__all__ = [
    "AuthAccessDeniedError",
    "AuthDomainError",
    "AuthResourceNotFoundError",
]
