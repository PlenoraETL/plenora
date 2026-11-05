"""Domain exceptions for auth."""

from __future__ import annotations


class AuthDomainError(Exception):
    """Base class for auth-domain failures."""


class AuthAccessDeniedError(AuthDomainError):
    """Raised when an authenticated actor cannot access a resource."""


class AuthResourceNotFoundError(AuthDomainError):
    """Raised when an auth-owned resource is not visible to the actor."""


class UsernameInvalidError(AuthDomainError, ValueError):
    """Username con spazi iniziali/finali, caratteri di controllo o vuoto
    (``shared.contracts.auth.username_form_error``)."""


class UsernameTakenError(AuthDomainError, ValueError):
    """Esiste gia' un utente con lo stesso username in forma normalizzata
    (trim + casefold, ``shared.contracts.auth.normalized_username``)."""


__all__ = [
    "AuthAccessDeniedError",
    "AuthDomainError",
    "AuthResourceNotFoundError",
    "UsernameInvalidError",
    "UsernameTakenError",
]
