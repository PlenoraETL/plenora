"""Fail-closed namespace for tenant-owned runtime objects.

New objects live below ``tenants/{group_id}/{domain}/...``.  Legacy keys are
accepted only at read boundaries that have already resolved the owner from
PostgreSQL; callers must opt in explicitly with ``allow_legacy=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
from typing import Iterable

from app.shared.resources.storage_layout import TENANTS_PREFIX, TENANT_DOMAINS


class TenantStorageKeyError(ValueError):
    """Raised when a storage key escapes its expected tenant/domain."""


def safe_segment(value: object, *, label: str) -> str:
    segment = str(value or "").strip()
    if not segment:
        raise TenantStorageKeyError(f"{label} mancante")
    if segment in {".", ".."} or any(
        char in segment for char in ("/", "\\", "\r", "\n", "\x00")
    ):
        raise TenantStorageKeyError(f"{label} non valido per object storage")
    return segment


def tenant_prefix(group_id: str) -> str:
    return f"{TENANTS_PREFIX}/{safe_segment(group_id, label='group_id')}"


def build_tenant_key(
    group_id: str,
    domain: str,
    *segments: object,
) -> str:
    clean_domain = safe_segment(domain, label="storage domain")
    if clean_domain not in TENANT_DOMAINS:
        raise TenantStorageKeyError(f"storage domain non supportato: {clean_domain}")
    clean_segments = [safe_segment(value, label="storage key segment") for value in segments]
    suffix = "/".join(clean_segments)
    base = f"{tenant_prefix(group_id)}/{clean_domain}"
    return f"{base}/{suffix}" if suffix else base


@dataclass(frozen=True, slots=True)
class ParsedTenantKey:
    group_id: str
    domain: str
    relative_segments: tuple[str, ...]


def parse_tenant_key(key: str) -> ParsedTenantKey | None:
    parts = str(key or "").split("/")
    if len(parts) < 3 or parts[0] != TENANTS_PREFIX:
        return None
    group_id, domain = parts[1], parts[2]
    try:
        safe_segment(group_id, label="group_id")
        safe_segment(domain, label="storage domain")
    except TenantStorageKeyError:
        return None
    if domain not in TENANT_DOMAINS or any(not part or part in {".", ".."} for part in parts[3:]):
        return None
    return ParsedTenantKey(group_id, domain, tuple(parts[3:]))


def legacy_domain(key: str) -> str | None:
    first = str(key or "").split("/", 1)[0]
    return first if first in TENANT_DOMAINS else None


def assert_tenant_key(
    key: str,
    *,
    expected_group_id: str,
    expected_domain: str | None = None,
    allow_legacy: bool = False,
) -> ParsedTenantKey | None:
    """Validate that ``key`` belongs to the expected tenant.

    Legacy keys carry no tenant segment.  They are accepted only when the
    caller explicitly opts in after an owner-scoped DB lookup.
    """
    expected_group = safe_segment(expected_group_id, label="expected_group_id")
    parsed = parse_tenant_key(key)
    if parsed is not None:
        if not hmac.compare_digest(parsed.group_id, expected_group):
            raise TenantStorageKeyError("chiave object storage di un altro tenant")
        if expected_domain and parsed.domain != expected_domain:
            raise TenantStorageKeyError("dominio object storage inatteso")
        return parsed

    domain = legacy_domain(key)
    if allow_legacy and domain is not None:
        if expected_domain and domain != expected_domain:
            raise TenantStorageKeyError("dominio object storage legacy inatteso")
        return None
    raise TenantStorageKeyError("chiave object storage non tenant-safe")


def assert_runtime_key(key: str, *, allow_legacy: bool = True) -> None:
    """Validate an operator-supplied key without granting tenant access."""
    if parse_tenant_key(key) is not None:
        return
    if allow_legacy and legacy_domain(key) is not None:
        return
    raise TenantStorageKeyError("chiave fuori dal runtime object storage")


def tenant_domain_prefix(group_id: str, domain: str) -> str:
    return build_tenant_key(group_id, domain) + "/"


def iter_legacy_candidates(key: str, *, expected_group_id: str) -> Iterable[str]:
    """Yield the canonical key and its legacy relative equivalent."""
    parsed = assert_tenant_key(
        key,
        expected_group_id=expected_group_id,
        allow_legacy=True,
    )
    yield key
    if parsed is not None:
        suffix = "/".join(parsed.relative_segments)
        legacy = f"{parsed.domain}/{suffix}" if suffix else parsed.domain
        if legacy != key:
            yield legacy


__all__ = [
    "ParsedTenantKey",
    "TenantStorageKeyError",
    "assert_runtime_key",
    "assert_tenant_key",
    "build_tenant_key",
    "iter_legacy_candidates",
    "legacy_domain",
    "parse_tenant_key",
    "safe_segment",
    "tenant_domain_prefix",
    "tenant_prefix",
]
