from __future__ import annotations


OBJECT_STORAGE_DISABLED = "object_storage_disabled"
AWS_CRT_MISSING = "aws_crt_missing"
AWS_CREDENTIALS_EXPIRED = "aws_credentials_expired"


KNOWN_DOMAIN_CODES: frozenset[str] = frozenset({
    OBJECT_STORAGE_DISABLED,
    AWS_CRT_MISSING,
    AWS_CREDENTIALS_EXPIRED,
})
