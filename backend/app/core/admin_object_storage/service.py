"""Read-only catalog service for runtime object storage."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.core.io.object_storage import get_runtime_object_storage
from app.core.io.tenant_namespace import (
    legacy_domain,
    parse_tenant_key,
    safe_segment,
    tenant_domain_prefix,
    tenant_prefix,
)
from app.shared.resources.storage_layout import TENANT_DOMAINS


class StorageObjectEntry(BaseModel):
    key: str
    size_bytes: int
    last_modified: datetime
    tenant_group_id: Optional[str] = None
    domain: str
    resource_id: Optional[str] = None
    legacy: bool = False


class StorageObjectPage(BaseModel):
    bucket: str
    scope: Literal["canonical", "legacy"]
    prefix: str
    objects: list[StorageObjectEntry]
    next_cursor: Optional[str] = None


def _resource_id(domain: str, segments: tuple[str, ...]) -> str | None:
    if not segments:
        return None
    if domain == "intermediate":
        project = next(
            (part.split("=", 1)[1] for part in segments if part.startswith("project=")),
            None,
        )
        return project
    return segments[0]


def _entry(raw: dict) -> StorageObjectEntry | None:
    key = str(raw.get("key") or "")
    parsed = parse_tenant_key(key)
    if parsed is not None:
        return StorageObjectEntry(
            key=key,
            size_bytes=int(raw.get("size") or 0),
            last_modified=raw["last_modified"],
            tenant_group_id=parsed.group_id,
            domain=parsed.domain,
            resource_id=_resource_id(parsed.domain, parsed.relative_segments),
            legacy=False,
        )
    domain = legacy_domain(key)
    if domain is None:
        return None
    segments = tuple(key.split("/")[1:])
    return StorageObjectEntry(
        key=key,
        size_bytes=int(raw.get("size") or 0),
        last_modified=raw["last_modified"],
        domain=domain,
        resource_id=_resource_id(domain, segments),
        legacy=True,
    )


def list_runtime_objects(
    *,
    scope: Literal["canonical", "legacy"] = "canonical",
    group_id: str | None = None,
    domain: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> StorageObjectPage:
    if domain is not None:
        domain = safe_segment(domain, label="storage domain")
        if domain not in TENANT_DOMAINS:
            raise ValueError("storage domain non supportato")
    if scope == "canonical":
        if group_id:
            prefix = (
                tenant_domain_prefix(group_id, domain)
                if domain else tenant_prefix(group_id) + "/"
            )
        else:
            prefix = "tenants/"
    else:
        if group_id:
            raise ValueError("le chiavi legacy non contengono group_id")
        prefix = f"{domain}/" if domain else ""

    storage = get_runtime_object_storage()
    page = storage.list_objects_page(
        prefix,
        limit=max(1, min(int(limit), 500)),
        cursor=cursor,
    )
    entries = [entry for raw in page["objects"] if (entry := _entry(raw))]
    if scope == "legacy":
        entries = [entry for entry in entries if entry.legacy]
    elif domain and not group_id:
        entries = [entry for entry in entries if entry.domain == domain]
    return StorageObjectPage(
        bucket=str(getattr(storage, "bucket", "runtime")),
        scope=scope,
        prefix=prefix,
        objects=entries,
        next_cursor=page.get("next_cursor"),
    )


__all__ = [
    "StorageObjectEntry",
    "StorageObjectPage",
    "list_runtime_objects",
]
