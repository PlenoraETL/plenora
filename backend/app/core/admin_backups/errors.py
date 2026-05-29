from __future__ import annotations

from app.shared.error_codes import (
    AWS_CREDENTIALS_EXPIRED,
    AWS_CRT_MISSING,
    OBJECT_STORAGE_DISABLED,
)

KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    OBJECT_STORAGE_DISABLED,
    AWS_CRT_MISSING,
    AWS_CREDENTIALS_EXPIRED,
})
